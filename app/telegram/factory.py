import logging

from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.telegram.handlers import get_routers
from app.telegram.middlewares import DbSessionMiddleware, ThrottlingMiddleware

logger = logging.getLogger(__name__)


def create_storage(settings: Settings) -> BaseStorage:
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url)
    return MemoryStorage()


def create_dispatcher(settings: Settings, session_pool: async_sessionmaker[AsyncSession]) -> Dispatcher:
    # Всё, что передано в Dispatcher(...), доступно в хендлерах по имени аргумента (settings)
    dp = Dispatcher(storage=create_storage(settings), settings=settings)
    # Антифлуд только для сообщений и кнопок: платёжные апдейты (pre_checkout_query) отбрасывать нельзя
    throttling = ThrottlingMiddleware(settings.throttle_seconds)
    dp.message.outer_middleware(throttling)
    dp.callback_query.outer_middleware(throttling)
    dp.update.middleware(DbSessionMiddleware(session_pool))
    dp.include_routers(*get_routers())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        # Нажатие, сделанное пока бот был выключен: ответить на него уже нельзя, но само действие выполнено.
        # Накопившиеся апдейты не отбрасываем — среди них может быть уведомление об оплате
        if isinstance(event.exception, TelegramBadRequest) and "query is too old" in event.exception.message:
            logger.info("Ответ на устаревшее нажатие пропущен (апдейт %s)", event.update.update_id)
            return
        logger.exception("Ошибка при обработке апдейта %s", event.update.update_id, exc_info=event.exception)

    return dp
