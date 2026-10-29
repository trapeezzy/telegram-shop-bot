"""«Мои заказы»: список, оплата и отмена неоплаченного заказа прямо из чата."""

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import OrderStatus
from app.services import notify, orders, payments
from app.services.orders import OrderError
from app.telegram.callbacks import OrderCb
from app.telegram.keyboards import Btn, my_orders_kb, open_shop_kb
from app.utils.formatting import fmt_money
from app.utils.telegram import edit_or_answer

router = Router(name="orders")


@router.message(F.text == Btn.ORDERS)
@router.message(Command("orders"))
async def my_orders(message: Message, session: AsyncSession, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    text, kb = await _render(session, message.from_user.id, settings)
    await message.answer(text, reply_markup=kb)


@router.callback_query(OrderCb.filter(F.action == "pay"))
async def pay_order(
    callback: CallbackQuery, callback_data: OrderCb, session: AsyncSession, settings: Settings, bot: Bot
) -> None:
    try:
        order = await orders.ensure_payable(session, callback_data.id, callback.from_user.id, settings)
    except OrderError as e:
        await callback.answer(str(e), show_alert=True)
        await edit_or_answer(callback, *await _render(session, callback.from_user.id, settings))
        return

    if settings.demo_payments:
        # Без платёжного провайдера заказ оплачивается «понарошку» — путь заказа тот же самый
        await orders.mark_paid(session, order, telegram_charge_id="demo")
        await callback.answer("Демо-оплата прошла ✅")
        await edit_or_answer(callback, *await _render(session, callback.from_user.id, settings))
        await notify.order_paid(bot, order, settings)
        return

    await payments.send_invoice(bot, callback.from_user.id, order, settings)
    await callback.answer()


@router.callback_query(OrderCb.filter(F.action == "cancel"))
async def cancel_order(
    callback: CallbackQuery, callback_data: OrderCb, session: AsyncSession, settings: Settings
) -> None:
    order = await repo.get_order(session, callback_data.id)
    if order is None or order.user_id != callback.from_user.id or order.status != OrderStatus.PENDING:
        await callback.answer("Этот заказ уже нельзя отменить", show_alert=True)
    else:
        await orders.change_status(session, order, OrderStatus.CANCELLED)
        await callback.answer("Заказ отменён")
    await edit_or_answer(callback, *await _render(session, callback.from_user.id, settings))


async def _render(
    session: AsyncSession, user_id: int, settings: Settings
) -> tuple[str, InlineKeyboardMarkup | None]:
    user_orders = await repo.user_orders(session, user_id)
    if not user_orders:
        return "У вас пока нет заказов. Самое время выбрать кофе ☕", open_shop_kb(settings)
    lines = ["📦 <b>Ваши заказы</b>", ""]
    lines += [f"№{o.id} · {fmt_money(o.total)} · {notify.status_label(o)}" for o in user_orders]
    if any(o.status == OrderStatus.PENDING for o in user_orders):
        lines += ["", f"Неоплаченные заказы отменяются через {settings.order_ttl_minutes} мин."]
    return "\n".join(lines), my_orders_kb(user_orders, settings)
