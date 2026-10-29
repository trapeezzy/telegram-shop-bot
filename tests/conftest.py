import hashlib
import hmac
import itertools
import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import (
    CreateInvoiceLink,
    EditMessageText,
    SendDocument,
    SendInvoice,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import Chat, InlineKeyboardMarkup, Message

from app.config import Settings
from app.db import repo
from app.db.engine import create_session_pool
from app.db.models import Base, DeliveryMethod
from app.services import orders
from app.services.orders import CartLine, Checkout

TOKEN = "42:TEST"
ADMIN_ID = 999
CLIENT = {"id": 1, "is_bot": False, "first_name": "Иван", "last_name": "Петров", "username": "ivan"}
ADMIN = {"id": ADMIN_ID, "is_bot": False, "first_name": "Админ"}


@pytest.fixture
def settings() -> Settings:
    return Settings(bot_token=TOKEN, admin_ids=str(ADMIN_ID), throttle_seconds=0, _env_file=None)


@pytest.fixture
async def session_pool(tmp_path):
    engine, pool = create_session_pool(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    # Тестам схема нужна быстро — по моделям. Совпадение с миграциями проверяет test_migrations.py
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with pool() as session:
        await repo.seed_catalog(session)
    yield pool
    await engine.dispose()


@pytest.fixture
async def session(session_pool):
    async with session_pool() as session:
        yield session


async def place_order(session, settings, user_id=CLIENT["id"], qty=1, product_id=1, delivery=DeliveryMethod.COURIER):
    await repo.upsert_user(session, user_id, "Иван Петров", "ivan")
    checkout = Checkout(
        lines=[CartLine(product_id=product_id, qty=qty)],
        delivery=delivery,
        name="Иван Петров",
        phone="+7 900 123-45-67",
        address="ул. Ленина, 1, кв. 5" if delivery == DeliveryMethod.COURIER else None,
    )
    return await orders.create_order(session, user_id, checkout, settings)


def make_init_data(user: dict, token: str = TOKEN, auth_date: int | None = None) -> str:
    """initData так, как её подписывает Telegram (https://core.telegram.org/bots/webapps)."""
    data = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAHtest",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class FakeSession(BaseSession):
    """Вместо Telegram API: записывает исходящие запросы и отвечает правдоподобными объектами."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.blocked_by: set[int] = set()
        self._ids = itertools.count(1000)

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.requests.append(method)
        if getattr(method, "chat_id", None) in self.blocked_by:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if isinstance(method, CreateInvoiceLink):
            return f"https://t.me/$invoice{next(self._ids)}"
        if isinstance(method, (SendMessage, EditMessageText, SendInvoice, SendDocument)):
            markup = getattr(method, "reply_markup", None)
            return Message(
                message_id=next(self._ids),
                date=datetime.now(),
                chat=Chat(id=method.chat_id or 0, type="private"),
                text=getattr(method, "text", None),
                reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None,
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        pass

    def sent(self, method_type: type, chat_id: int | None = None) -> list:
        return [
            m for m in self.requests
            if isinstance(m, method_type) and (chat_id is None or getattr(m, "chat_id", None) == chat_id)
        ]

    def texts(self, chat_id: int) -> list[str]:
        return [m.text for m in self.requests if isinstance(m, (SendMessage, EditMessageText)) and m.chat_id == chat_id]


@pytest.fixture
def fake_bot() -> Bot:
    return Bot(TOKEN, session=FakeSession(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
