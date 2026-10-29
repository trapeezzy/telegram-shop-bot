import csv
import html
import io
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import DeliveryMethod, OrderStatus, Product, utcnow
from app.services import notify, orders
from app.services.orders import OrderError
from app.telegram.callbacks import AdminCb, AdminOrderCb, CategoryCb, ProductCb
from app.telegram.filters import IsAdmin
from app.telegram.keyboards import (
    Btn,
    admin_menu_kb,
    admin_order_kb,
    admin_orders_kb,
    admin_products_kb,
    back_kb,
    categories_kb,
    not_menu_button,
    product_admin_kb,
)
from app.telegram.states import AddProduct, EditProduct
from app.utils.formatting import fmt_dt, fmt_money, parse_rubles
from app.utils.telegram import edit_or_answer

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MENU_TEXT = "⚙️ <b>Панель администратора</b>"
SKIP = "-"


@router.message(F.text == Btn.ADMIN)
@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=admin_menu_kb())


@router.callback_query(AdminCb.filter(F.action == "menu"))
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(callback, MENU_TEXT, admin_menu_kb())
    await callback.answer()


# ---------- Заказы ----------

@router.callback_query(AdminCb.filter(F.action == "orders"))
async def show_orders(callback: CallbackQuery, session: AsyncSession) -> None:
    active = await repo.orders_by_status(session, (OrderStatus.PAID, OrderStatus.SHIPPED))
    text = "📋 <b>Заказы в работе</b>" if active else "📋 Заказов в работе нет — всё отправлено 🎉"
    await edit_or_answer(callback, text, admin_orders_kb(active))
    await callback.answer()


@router.callback_query(AdminOrderCb.filter(F.status == "open"))
async def open_order(
    callback: CallbackQuery, callback_data: AdminOrderCb, session: AsyncSession, settings: Settings
) -> None:
    if (order := await repo.get_order(session, callback_data.id)) is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await edit_or_answer(
        callback, notify.order_text(order, settings, for_admin=True), admin_order_kb(order, with_back=True)
    )
    await callback.answer()


@router.callback_query(AdminOrderCb.filter())
async def set_order_status(
    callback: CallbackQuery, callback_data: AdminOrderCb, session: AsyncSession, settings: Settings, bot: Bot
) -> None:
    if (order := await repo.get_order(session, callback_data.id)) is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    try:
        await orders.change_status(session, order, OrderStatus(callback_data.status))
    except (OrderError, ValueError):
        await callback.answer("Статус уже изменился — обновил карточку", show_alert=True)
    else:
        await callback.answer("Готово, клиент уведомлён")
        await notify.status_changed(bot, order, settings)
    await edit_or_answer(
        callback, notify.order_text(order, settings, for_admin=True), admin_order_kb(order, with_back=True)
    )


# ---------- Товары ----------

@router.callback_query(AdminCb.filter(F.action == "products"))
async def show_products(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    products = await repo.list_products(session, only_active=False)
    await edit_or_answer(callback, "🛍 <b>Товары</b>\n\nНажмите на товар, чтобы изменить его.", admin_products_kb(products))
    await callback.answer()


@router.callback_query(ProductCb.filter(F.action == "open"))
async def open_product(callback: CallbackQuery, callback_data: ProductCb, session: AsyncSession) -> None:
    if (product := await repo.get_product(session, callback_data.id)) is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await edit_or_answer(callback, *_render_product(product))
    await callback.answer()


@router.callback_query(ProductCb.filter(F.action == "toggle"))
async def toggle_product(callback: CallbackQuery, callback_data: ProductCb, session: AsyncSession) -> None:
    if (product := await repo.get_product(session, callback_data.id)) is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    product.is_active = not product.is_active
    await session.commit()
    await edit_or_answer(callback, *_render_product(product))
    await callback.answer("Товар на витрине" if product.is_active else "Товар скрыт")


EDIT_PROMPTS = {
    "price": (EditProduct.price, "Новая цена в рублях, например 990:"),
    "stock": (EditProduct.stock, "Сколько штук на складе?"),
    "photo": (EditProduct.photo, "Отправьте фото товара (лучше квадратное):"),
}


@router.callback_query(ProductCb.filter(F.action.in_(EDIT_PROMPTS)))
async def edit_product_start(callback: CallbackQuery, callback_data: ProductCb, state: FSMContext) -> None:
    new_state, prompt = EDIT_PROMPTS[callback_data.action]
    await state.set_state(new_state)
    await state.update_data(product_id=callback_data.id)
    await callback.message.answer(f"{prompt}\n\n/cancel — отмена")
    await callback.answer()


@router.message(EditProduct.price, F.text, not_menu_button)
async def edit_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if (price := parse_rubles(message.text)) is None:
        await message.answer("Введите цену числом, например 990")
        return
    await _update_product(message, session, state, price=price)


@router.message(EditProduct.stock, F.text, not_menu_button)
async def edit_stock(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.text.isdigit() or int(message.text) > 100_000:
        await message.answer("Введите целое число от 0 до 100 000")
        return
    await _update_product(message, session, state, stock=int(message.text))


@router.message(EditProduct.photo, F.photo)
async def edit_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    # Храним только file_id: сам файл лежит на серверах Telegram, API витрины скачает и закэширует его
    await _update_product(message, session, state, photo_file_id=message.photo[-1].file_id)


async def _update_product(message: Message, session: AsyncSession, state: FSMContext, **fields) -> None:
    product_id = (await state.get_data())["product_id"]
    await state.clear()
    if (product := await repo.get_product(session, product_id)) is None:
        await message.answer("Товар не найден")
        return
    for name, value in fields.items():
        setattr(product, name, value)
    await session.commit()
    text, kb = _render_product(product)
    await message.answer(f"✅ Сохранено\n\n{text}", reply_markup=kb)


def _render_product(product: Product) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"{product.emoji} <b>{html.escape(product.title)}</b>\n"
        f"{product.category.emoji} {html.escape(product.category.title)}\n\n"
        f"{html.escape(product.description) or '<i>без описания</i>'}\n\n"
        f"💰 {fmt_money(product.price)} · 📦 {product.stock} шт.\n"
        f"🖼 Фото: {'загружено' if product.photo_file_id else 'нет (на витрине будет эмодзи)'}\n"
        f"{'👁 На витрине' if product.is_active else '🙈 Скрыт с витрины'}"
    )
    return text, product_admin_kb(product)


# ---------- Новый товар ----------

@router.callback_query(AdminCb.filter(F.action == "add_product"))
async def add_product_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.set_state(AddProduct.category)
    await edit_or_answer(callback, "➕ Новый товар. Выберите категорию:", categories_kb(await repo.list_categories(session)))
    await callback.answer()


@router.callback_query(AddProduct.category, CategoryCb.filter())
async def add_product_category(callback: CallbackQuery, callback_data: CategoryCb, state: FSMContext) -> None:
    await state.update_data(category_id=callback_data.id)
    await state.set_state(AddProduct.title)
    await callback.message.answer("Название товара:\n\n/cancel — отмена")
    await callback.answer()


@router.message(AddProduct.title, F.text, not_menu_button)
async def add_product_title(message: Message, state: FSMContext) -> None:
    if not 2 <= len(title := message.text.strip()) <= 100:
        await message.answer("Название — от 2 до 100 символов")
        return
    await state.update_data(title=title)
    await state.set_state(AddProduct.description)
    await message.answer(f"Описание (вкус, вес, особенности) или «{SKIP}», чтобы пропустить:")


@router.message(AddProduct.description, F.text, not_menu_button)
async def add_product_description(message: Message, state: FSMContext) -> None:
    description = "" if message.text.strip() == SKIP else message.text.strip()[:1000]
    await state.update_data(description=description)
    await state.set_state(AddProduct.price)
    await message.answer("Цена в рублях, например 990:")


@router.message(AddProduct.price, F.text, not_menu_button)
async def add_product_price(message: Message, state: FSMContext) -> None:
    if (price := parse_rubles(message.text)) is None:
        await message.answer("Введите цену числом, например 990")
        return
    await state.update_data(price=price)
    await state.set_state(AddProduct.stock)
    await message.answer("Сколько штук на складе?")


@router.message(AddProduct.stock, F.text, not_menu_button)
async def add_product_stock(message: Message, state: FSMContext) -> None:
    if not message.text.isdigit() or int(message.text) > 100_000:
        await message.answer("Введите целое число от 0 до 100 000")
        return
    await state.update_data(stock=int(message.text))
    await state.set_state(AddProduct.photo)
    await message.answer(f"Отправьте фото товара или «{SKIP}», чтобы пропустить:")


@router.message(AddProduct.photo, F.photo)
async def add_product_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await _create_product(message, session, state, photo_file_id=message.photo[-1].file_id)


@router.message(AddProduct.photo, F.text == SKIP)
async def add_product_no_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await _create_product(message, session, state, photo_file_id=None)


async def _create_product(message: Message, session: AsyncSession, state: FSMContext, photo_file_id: str | None) -> None:
    data = await state.get_data()
    await state.clear()
    categories = {c.id: c for c in await repo.list_categories(session)}
    category = categories[data["category_id"]]
    product = await repo.add_product(
        session,
        category_id=category.id,
        title=data["title"],
        description=data["description"],
        price=data["price"],
        stock=data["stock"],
        emoji=category.emoji,
        photo_file_id=photo_file_id,
    )
    product = await repo.get_product(session, product.id)  # подгружаем категорию для карточки
    text, kb = _render_product(product)
    await message.answer(f"✅ Товар добавлен и уже на витрине\n\n{text}", reply_markup=kb)


# ---------- Статистика и выгрузка ----------

@router.callback_query(AdminCb.filter(F.action == "stats"))
async def show_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    s = await repo.get_stats(session, utcnow())
    top = "\n".join(f"{i}. {html.escape(title)} — {qty} шт." for i, (title, qty) in enumerate(s.top_products, 1))
    text = (
        "📊 <b>Статистика за 30 дней</b>\n\n"
        f"✅ Оплаченных заказов: {s.paid_orders}\n"
        f"💰 Выручка: {fmt_money(s.revenue)}\n"
        f"🧾 Средний чек: {fmt_money(s.avg_check)}\n"
        f"📦 В работе сейчас: {s.in_progress}\n"
        f"👥 Покупателей в базе: {s.users}\n\n"
        f"<b>Топ товаров</b>\n{top or '—'}"
    )
    await edit_or_answer(callback, text, back_kb())
    await callback.answer()


@router.callback_query(AdminCb.filter(F.action == "export"))
async def export_orders(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    rows = await repo.orders_since(session, utcnow() - timedelta(days=30))
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")  # «;» — чтобы русский Excel сразу разложил по колонкам
    writer.writerow(["№", "Создан", "Статус", "Клиент", "Телефон", "Доставка", "Адрес", "Состав", "Сумма, ₽"])
    for o in rows:
        writer.writerow([
            o.id,
            fmt_dt(o.created_at, settings.tz),
            notify.status_label(o),
            o.customer_name,
            o.phone,
            "курьер" if o.delivery_method == DeliveryMethod.COURIER else "самовывоз",
            o.address or "",
            ", ".join(f"{i.title} × {i.qty}" for i in o.items),
            f"{o.total / 100:.2f}".replace(".", ","),
        ])
    document = BufferedInputFile(buffer.getvalue().encode("utf-8-sig"), filename="orders_30d.csv")
    await callback.message.answer_document(document, caption=f"📤 Заказы за 30 дней: {len(rows)}")
    await callback.answer()
