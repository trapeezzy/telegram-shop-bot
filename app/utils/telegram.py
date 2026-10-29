from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup


async def edit_or_answer(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Редактирует сообщение с кнопкой. Если текст не изменился, Telegram отвечает ошибкой —
    её безопасно игнорировать. Если сообщение слишком старое — отправляем новое."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in e.message:
            return
        await callback.message.answer(text, reply_markup=reply_markup)
