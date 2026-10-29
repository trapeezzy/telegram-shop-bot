"""Точка входа: бот (polling), API + витрина Mini App (uvicorn) и планировщик — в одном процессе
и одном event loop, с общими подключениями к БД и Telegram."""

import asyncio
import logging

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonCommands, MenuButtonWebApp, WebAppInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.db import repo
from app.db.engine import create_session_pool
from app.db.migrate import upgrade_db
from app.services.jobs import cancel_unpaid_orders
from app.telegram.factory import create_dispatcher
from app.web.api import create_app

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="orders", description="Мои заказы"),
    BotCommand(command="support", description="Связаться с нами"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
]


async def setup_bot_ui(bot: Bot, settings: Settings) -> None:
    await bot.set_my_commands(COMMANDS)
    if settings.webapp_enabled:
        # Кнопка «Магазин» слева от поля ввода — витрина открывается в одно касание
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Магазин", web_app=WebAppInfo(url=settings.webapp_url))
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.warning("WEBAPP_URL не HTTPS (%s) — Mini App в Telegram не откроется", settings.webapp_url)
    if settings.demo_payments:
        logger.warning("PAYMENT_PROVIDER_TOKEN не задан — включена демо-оплата без списания денег")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()

    # Alembic внутри вызывает asyncio.run, поэтому запускаем его в отдельном потоке
    await asyncio.to_thread(upgrade_db, settings.database_url)
    engine, session_pool = create_session_pool(settings.database_url)
    async with session_pool() as session:
        await repo.seed_catalog(session)

    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = create_dispatcher(settings, session_pool)
    web = uvicorn.Server(
        uvicorn.Config(create_app(settings, session_pool, bot), host=settings.web_host, port=settings.web_port)
    )
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        cancel_unpaid_orders, "interval", minutes=1,
        kwargs={"bot": bot, "session_pool": session_pool, "settings": settings},
    )

    await setup_bot_ui(bot, settings)
    scheduler.start()
    # Сигналы (Ctrl+C, SIGTERM от Docker) обрабатывает uvicorn; если упадёт бот — гасим и веб,
    # чтобы процесс завершился и Docker его перезапустил, а не висел «полуживым»
    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    polling.add_done_callback(lambda _: setattr(web, "should_exit", True))
    try:
        await web.serve()
    finally:
        if not polling.done():
            await dp.stop_polling()
        scheduler.shutdown(wait=False)
        try:
            await polling  # пробрасываем ошибку бота, если она была
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
