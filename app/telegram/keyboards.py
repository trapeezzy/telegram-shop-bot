from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.config import Settings
from app.db.models import Category, DeliveryMethod, Order, OrderStatus, Product
from app.services.orders import TRANSITIONS
from app.telegram.callbacks import AdminCb, AdminOrderCb, CategoryCb, OrderCb, ProductCb
from app.utils.formatting import fmt_money


class Btn:
    SHOP = "🛍 Магазин"
    ORDERS = "📦 Мои заказы"
    SUPPORT = "💬 Поддержка"
    ADMIN = "⚙️ Админка"


MENU_BUTTONS = {Btn.SHOP, Btn.ORDERS, Btn.SUPPORT, Btn.ADMIN}


def not_menu_button(message: Message) -> bool:
    """Для хендлеров, ждущих ввод: кнопка меню посреди диалога — это навигация, а не ответ."""
    return message.text not in MENU_BUTTONS


def main_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    # «Магазин» — обычная текстовая кнопка, а не web_app: Mini App, открытая с кнопки клавиатуры,
    # не получает initData (подпись пользователя), и оформить заказ из неё нельзя.
    # В ответ бот присылает inline-кнопку — с неё витрина открывается с подписью
    builder = ReplyKeyboardBuilder()
    builder.button(text=Btn.SHOP)
    builder.button(text=Btn.ORDERS)
    builder.button(text=Btn.SUPPORT)
    if is_admin:
        builder.button(text=Btn.ADMIN)
    builder.adjust(1, 2, 1)
    return builder.as_markup(resize_keyboard=True)


def open_shop_kb(settings: Settings) -> InlineKeyboardMarkup | None:
    if not settings.webapp_enabled:
        return None
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 Открыть магазин", web_app=WebAppInfo(url=settings.webapp_url))
    return builder.as_markup()


def my_orders_kb(orders: Sequence[Order], settings: Settings) -> InlineKeyboardMarkup | None:
    pending = [o for o in orders if o.status == OrderStatus.PENDING]
    if not pending:
        return None
    builder = InlineKeyboardBuilder()
    pay_label = "💳 Оплатить (демо)" if settings.demo_payments else "💳 Оплатить"
    for order in pending:
        builder.button(text=f"{pay_label} №{order.id}", callback_data=OrderCb(action="pay", id=order.id))
        builder.button(text=f"❌ Отменить №{order.id}", callback_data=OrderCb(action="cancel", id=order.id))
    builder.adjust(2)
    return builder.as_markup()


# ---------- Админка ----------

def status_action_label(order: Order, status: OrderStatus) -> str:
    if status == OrderStatus.SHIPPED:
        return "📦 Готов к выдаче" if order.delivery_method == DeliveryMethod.PICKUP else "🚚 Передан курьеру"
    return {OrderStatus.DONE: "✅ Выполнен", OrderStatus.CANCELLED: "❌ Отменить"}[status]


def admin_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Заказы в работе", callback_data=AdminCb(action="orders"))
    builder.button(text="🛍 Товары", callback_data=AdminCb(action="products"))
    builder.button(text="📊 Статистика", callback_data=AdminCb(action="stats"))
    builder.button(text="📤 Выгрузка CSV", callback_data=AdminCb(action="export"))
    builder.adjust(1, 1, 2)
    return builder.as_markup()


def back_kb(action: str = "menu") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data=AdminCb(action=action))
    return builder.as_markup()


def admin_orders_kb(orders: Sequence[Order]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        mark = "⚠️ " if order.stock_issue else ""
        builder.button(
            text=f"{mark}№{order.id} · {order.customer_name} · {fmt_money(order.total)}",
            callback_data=AdminOrderCb(id=order.id, status="open"),
        )
    builder.button(text="← Назад", callback_data=AdminCb(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def admin_order_kb(order: Order, *, with_back: bool = False) -> InlineKeyboardMarkup | None:
    """Кнопки только для разрешённых переходов статуса — неверный переход просто не нажать.
    Неоплаченный заказ админ не трогает: он отменится сам по таймауту."""
    statuses = sorted(TRANSITIONS[order.status], key=lambda s: s == OrderStatus.CANCELLED)  # «Отменить» — последней
    builder = InlineKeyboardBuilder()
    if order.status != OrderStatus.PENDING:
        for status in statuses:
            builder.button(
                text=status_action_label(order, status), callback_data=AdminOrderCb(id=order.id, status=status)
            )
    if with_back:
        builder.button(text="← К заказам", callback_data=AdminCb(action="orders"))
    builder.adjust(1)
    return builder.as_markup() if len(builder.export()) else None


def admin_products_kb(products: Sequence[Product]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in products:
        mark = "" if p.is_active else "🚫 "
        builder.button(
            text=f"{mark}{p.emoji} {p.title} · {fmt_money(p.price)} · {p.stock} шт.",
            callback_data=ProductCb(id=p.id, action="open"),
        )
    builder.button(text="➕ Добавить товар", callback_data=AdminCb(action="add_product"))
    builder.button(text="← Назад", callback_data=AdminCb(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def product_admin_kb(product: Product) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Цена", callback_data=ProductCb(id=product.id, action="price"))
    builder.button(text="📦 Остаток", callback_data=ProductCb(id=product.id, action="stock"))
    builder.button(text="🖼 Фото", callback_data=ProductCb(id=product.id, action="photo"))
    builder.button(
        text="🙈 Скрыть из витрины" if product.is_active else "👁 Вернуть на витрину",
        callback_data=ProductCb(id=product.id, action="toggle"),
    )
    builder.button(text="← К товарам", callback_data=AdminCb(action="products"))
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def categories_kb(categories: Sequence[Category]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for c in categories:
        builder.button(text=f"{c.emoji} {c.title}", callback_data=CategoryCb(id=c.id))
    builder.button(text="← Отмена", callback_data=AdminCb(action="products"))
    builder.adjust(1)
    return builder.as_markup()
