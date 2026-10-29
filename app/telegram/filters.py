from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from app.config import Settings


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        return event.from_user is not None and settings.is_admin(event.from_user.id)
