import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DbSessionMiddleware(BaseMiddleware):
    """Открывает сессию БД на каждый апдейт и передаёт её в хендлер аргументом `session`."""

    def __init__(self, session_pool: async_sessionmaker[AsyncSession]) -> None:
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session
            return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Антифлуд: молча отбрасывает действия пользователя, если они приходят чаще, чем раз в `rate` секунд."""

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        # Сообщение об успешной оплате нельзя отбрасывать ни при каких условиях — иначе деньги без заказа
        is_payment = isinstance(event, Message) and event.successful_payment is not None
        if user is None or self.rate <= 0 or is_payment:
            return await handler(event, data)

        now = time.monotonic()
        if now - self._last_seen.get(user.id, 0.0) < self.rate:
            if isinstance(event, CallbackQuery):
                await event.answer()  # иначе на кнопке будут крутиться «часики»
            return None

        self._last_seen[user.id] = now
        if len(self._last_seen) > 10_000:  # не даём словарю расти бесконечно
            self._last_seen = {uid: t for uid, t in self._last_seen.items() if now - t < self.rate}
        return await handler(event, data)
