from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services import notify, orders


async def cancel_unpaid_orders(bot: Bot, session_pool: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """Раз в минуту: отменяет заказы, не оплаченные за ORDER_TTL_MINUTES, и сообщает покупателю."""
    async with session_pool() as session:
        cancelled = await orders.cancel_stale(session, settings)
    for order in cancelled:
        await notify.notify_client(
            bot,
            order.user_id,
            f"⌛ Заказ №{order.id} отменён: оплата не поступила за {settings.order_ttl_minutes} мин.\n"
            "Товары можно снова собрать в корзину в магазине.",
        )
