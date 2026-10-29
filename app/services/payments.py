from aiogram import Bot
from aiogram.types import LabeledPrice

from app.config import Settings
from app.db.models import Order

PAYLOAD_PREFIX = "order:"


def invoice_payload(order_id: int) -> str:
    return f"{PAYLOAD_PREFIX}{order_id}"


def parse_payload(payload: str) -> int | None:
    if not payload.startswith(PAYLOAD_PREFIX):
        return None
    tail = payload.removeprefix(PAYLOAD_PREFIX)
    return int(tail) if tail.isdigit() else None


def invoice_prices(order: Order) -> list[LabeledPrice]:
    """Позиции счёта: Telegram покажет их покупателю списком, сумма должна сойтись с order.total."""
    prices = [LabeledPrice(label=f"{item.title} × {item.qty}"[:64], amount=item.amount) for item in order.items]
    if order.delivery_price:
        prices.append(LabeledPrice(label="Доставка", amount=order.delivery_price))
    return prices


def _invoice_fields(order: Order, settings: Settings) -> dict:
    items = ", ".join(f"{item.title} × {item.qty}" for item in order.items)
    return {
        "title": f"Заказ №{order.id}",
        "description": f"{settings.shop_name}: {items}"[:255],
        "payload": invoice_payload(order.id),
        "provider_token": settings.payment_provider_token.get_secret_value(),
        "currency": settings.currency,
        "prices": invoice_prices(order),
    }


async def create_invoice_link(bot: Bot, order: Order, settings: Settings) -> str:
    """Ссылка на оплату для Mini App — её открывает Telegram.WebApp.openInvoice()."""
    return await bot.create_invoice_link(**_invoice_fields(order, settings))


async def send_invoice(bot: Bot, chat_id: int, order: Order, settings: Settings) -> None:
    """Счёт сообщением в чат — если покупатель закрыл Mini App, не заплатив."""
    await bot.send_invoice(chat_id, **_invoice_fields(order, settings))
