"""Telegram Payments. Порядок: покупатель жмёт «Оплатить» → Telegram присылает pre_checkout_query
(у нас 10 секунд, чтобы подтвердить, что заказ ещё можно продать) → списание денег →
сообщение successful_payment."""

import logging

from aiogram import Bot, F, Router
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.services import notify, orders
from app.services.orders import OrderError
from app.services.payments import parse_payload

logger = logging.getLogger(__name__)
router = Router(name="payments")


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, session: AsyncSession, settings: Settings) -> None:
    order_id = parse_payload(query.invoice_payload)
    try:
        if order_id is None:
            raise OrderError("Заказ не найден")
        await orders.ensure_payable(session, order_id, query.from_user.id, settings, amount=query.total_amount)
    except OrderError as e:
        await query.answer(ok=False, error_message=str(e))
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, session: AsyncSession, settings: Settings, bot: Bot) -> None:
    payment = message.successful_payment
    order_id = parse_payload(payment.invoice_payload)
    order = await repo.get_order(session, order_id) if order_id else None
    if order is None:
        # Деньги списаны, а заказа нет — такого быть не должно, но молча терять платёж нельзя
        logger.error("Оплата без заказа: payload=%s charge=%s", payment.invoice_payload, payment.telegram_payment_charge_id)
        await notify.notify_admins(
            bot, settings, f"⚠️ Получена оплата без заказа: <code>{payment.telegram_payment_charge_id}</code>"
        )
        return

    changed = await orders.mark_paid(
        session,
        order,
        telegram_charge_id=payment.telegram_payment_charge_id,
        provider_charge_id=payment.provider_payment_charge_id,
    )
    if changed:
        await notify.order_paid(bot, order, settings)
