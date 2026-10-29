from aiogram import F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.telegram.keyboards import Btn, main_menu, open_shop_kb

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    user = message.from_user
    await repo.upsert_user(session, user.id, user.full_name, user.username)
    await message.answer(
        f"Привет, {html.quote(user.first_name)}! ☕\n\n"
        f"Это <b>{html.quote(settings.shop_name)}</b> — свежеобжаренный кофе с доставкой.\n"
        "Каталог, корзина и оплата — прямо в Telegram.",
        reply_markup=main_menu(settings.is_admin(user.id)),
    )
    if kb := open_shop_kb(settings):
        await message.answer("Загляните в магазин 👇", reply_markup=kb)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await message.answer(
        "Действие отменено.", reply_markup=main_menu(settings.is_admin(message.from_user.id))
    )


@router.message(F.text == Btn.SUPPORT)
@router.message(Command("support"))
async def support(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await message.answer(
        f"Вопросы по заказу, доставке или выбору кофе — пишите: {html.quote(settings.support_contact)}\n"
        f"🏪 Самовывоз: {html.quote(settings.pickup_address)}"
    )


@router.message(F.text == Btn.SHOP)
async def open_shop(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    if kb := open_shop_kb(settings):
        await message.answer("Каталог, корзина и оплата — внутри 👇", reply_markup=kb)
        return
    await message.answer(
        "Витрина пока недоступна: Telegram открывает Mini App только по HTTPS.\n"
        "Укажите публичный адрес в WEBAPP_URL (см. README → «Локально»)."
    )
