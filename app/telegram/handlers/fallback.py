"""Подключается последним: ловит всё, что не обработали остальные роутеры."""

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.telegram.keyboards import main_menu

router = Router(name="fallback")


@router.callback_query()
async def stale_button(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка устарела. Начните заново из меню 👇", show_alert=True)


@router.message()
async def unknown_message(message: Message, settings: Settings) -> None:
    await message.answer(
        "Не совсем понял 🤔 Воспользуйтесь меню ниже.",
        reply_markup=main_menu(settings.is_admin(message.from_user.id)),
    )
