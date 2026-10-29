"""Тексты о заказах и рассылка уведомлений клиенту и админам."""

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.config import Settings
from app.db.models import DeliveryMethod, Order, OrderStatus
from app.telegram.keyboards import admin_order_kb
from app.utils.formatting import fmt_dt, fmt_money, user_link

logger = logging.getLogger(__name__)


def status_label(order: Order) -> str:
    if order.status == OrderStatus.SHIPPED:
        return "📦 Готов к выдаче" if order.delivery_method == DeliveryMethod.PICKUP else "🚚 Передан курьеру"
    return {
        OrderStatus.PENDING: "⏳ Ждёт оплаты",
        OrderStatus.PAID: "✅ Оплачен, собираем",
        OrderStatus.DONE: "🎉 Выполнен",
        OrderStatus.CANCELLED: "❌ Отменён",
    }[order.status]


def order_text(order: Order, settings: Settings, *, for_admin: bool = False) -> str:
    lines = [f"<b>Заказ №{order.id}</b> · {status_label(order)}", f"🕒 {fmt_dt(order.created_at, settings.tz)}", ""]
    lines += [f"• {html.escape(i.title)} × {i.qty} — {fmt_money(i.amount)}" for i in order.items]
    if order.delivery_method == DeliveryMethod.COURIER:
        delivery = fmt_money(order.delivery_price) if order.delivery_price else "бесплатно"
        lines.append(f"• Доставка курьером — {delivery}")
    lines += ["", f"<b>Итого: {fmt_money(order.total)}</b>", ""]

    if order.delivery_method == DeliveryMethod.COURIER:
        lines.append(f"🚚 {html.escape(order.address or '')}")
    else:
        lines.append(f"🏪 Самовывоз: {html.escape(settings.pickup_address)}")
    if for_admin:
        lines.append(f"👤 {user_link(order.user_id, order.customer_name)}, {order.phone}")
    if order.comment:
        lines.append(f"💬 {html.escape(order.comment)}")
    if for_admin and order.stock_issue:
        lines += ["", "⚠️ <b>Оплачен, но товара не хватило</b> — свяжитесь с клиентом и поправьте остатки."]
    return "\n".join(lines)


async def notify_admins(bot: Bot, settings: Settings, text: str, **kwargs) -> None:
    for admin_id in settings.admins:
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except TelegramAPIError:
            logger.warning("Не удалось уведомить администратора %s", admin_id)


async def notify_client(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        logger.warning("Не удалось отправить сообщение клиенту %s", user_id)


async def order_paid(bot: Bot, order: Order, settings: Settings) -> None:
    await notify_client(
        bot,
        order.user_id,
        f"Спасибо за покупку! 💛\n\n{order_text(order, settings)}\n\nСообщим, когда заказ будет в пути.",
    )
    await notify_admins(
        bot, settings, "🆕 <b>Новый оплаченный заказ</b>\n\n" + order_text(order, settings, for_admin=True),
        reply_markup=admin_order_kb(order),
    )


STATUS_MESSAGES = {
    OrderStatus.DONE: "🎉 Заказ №{id} выполнен. Спасибо, что выбрали нас — ждём снова!",
    OrderStatus.CANCELLED: "❌ Заказ №{id} отменён. Если остались вопросы — напишите нам: {support}",
}


async def status_changed(bot: Bot, order: Order, settings: Settings) -> None:
    if order.status == OrderStatus.SHIPPED:
        if order.delivery_method == DeliveryMethod.PICKUP:
            text = f"📦 Заказ №{order.id} готов к выдаче!\nЖдём вас: {html.escape(settings.pickup_address)}"
        else:
            text = f"🚚 Заказ №{order.id} передан курьеру и скоро будет у вас."
    elif template := STATUS_MESSAGES.get(order.status):
        text = template.format(id=order.id, support=html.escape(settings.support_contact))
    else:
        return
    await notify_client(bot, order.user_id, text)
