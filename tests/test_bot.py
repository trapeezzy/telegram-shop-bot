"""Сквозные тесты бота: апдейты идут через настоящий Dispatcher, Telegram API подменён FakeSession."""

import itertools
from datetime import datetime, timedelta
from typing import Any

import pytest
from aiogram.methods import AnswerPreCheckoutQuery, SendDocument, SendMessage
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.db import repo
from app.db.models import OrderStatus
from app.services.jobs import cancel_unpaid_orders
from app.telegram.factory import create_dispatcher
from tests.conftest import ADMIN, ADMIN_ID, CLIENT, place_order


class Harness:
    def __init__(self, settings, session_pool, bot) -> None:
        self.bot = bot
        self.log = bot.session
        self.dp = create_dispatcher(settings, session_pool)
        self._ids = itertools.count(1)

    async def send(self, user: dict, text: str | None = None, **extra: Any) -> None:
        message = {"message_id": next(self._ids), "date": int(datetime.now().timestamp()),
                   "chat": {"id": user["id"], "type": "private"}, "from": user, **extra}
        if text is not None:
            message["text"] = text
            if text.startswith("/"):
                message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self._feed({"message": message})

    async def click(self, user: dict, button_text: str) -> None:
        for row in self.last_markup(user["id"]).inline_keyboard:
            for button in row:
                if button_text in button.text:
                    await self._feed({"callback_query": {
                        "id": str(next(self._ids)), "from": user, "chat_instance": "t", "data": button.callback_data,
                        "message": {"message_id": 1, "date": 0, "chat": {"id": user["id"], "type": "private"}, "text": "."},
                    }})
                    return
        raise AssertionError(f"Кнопка «{button_text}» не найдена")

    async def pre_checkout(self, order_id: int, amount: int, user: dict = CLIENT) -> AnswerPreCheckoutQuery:
        await self._feed({"pre_checkout_query": {
            "id": str(next(self._ids)), "from": user, "currency": "RUB",
            "total_amount": amount, "invoice_payload": f"order:{order_id}",
        }})
        return self.log.sent(AnswerPreCheckoutQuery)[-1]

    async def pay(self, order_id: int, amount: int, user: dict = CLIENT) -> None:
        await self.send(user, successful_payment={
            "currency": "RUB", "total_amount": amount, "invoice_payload": f"order:{order_id}",
            "telegram_payment_charge_id": f"tg_{order_id}", "provider_payment_charge_id": f"prov_{order_id}",
        })

    def last_text(self, chat_id: int) -> str:
        return self.log.texts(chat_id)[-1]

    def last_markup(self, chat_id: int) -> InlineKeyboardMarkup:
        for m in reversed(self.log.requests):
            if getattr(m, "chat_id", None) == chat_id and isinstance(getattr(m, "reply_markup", None), InlineKeyboardMarkup):
                return m.reply_markup
        raise AssertionError("Нет сообщений с inline-кнопками")

    async def _feed(self, update: dict) -> None:
        await self.dp.feed_raw_update(self.bot, {"update_id": next(self._ids), **update})


@pytest.fixture
def make_harness(settings, session_pool, fake_bot):
    created: list[Harness] = []

    def factory(**overrides: Any) -> Harness:
        created.append(Harness(settings.model_copy(update=overrides), session_pool, fake_bot))
        return created[-1]

    yield factory
    # Роутеры объявлены на уровне модулей и прикрепляются только к одному диспетчеру — отцепляем
    for h in created:
        for router in h.dp.sub_routers:
            router._parent_router = None


@pytest.fixture
def h(make_harness) -> Harness:
    return make_harness()


async def test_start_shows_menu(h):
    await h.send(CLIENT, "/start")
    assert "Привет, Иван" in h.log.texts(CLIENT["id"])[0]


async def test_pre_checkout_accepts_valid_order(h, session, settings):
    order = await place_order(session, settings)
    answer = await h.pre_checkout(order.id, order.total)
    assert answer.ok is True


@pytest.mark.parametrize("case", ["wrong_amount", "sold_out", "foreign", "unknown"])
async def test_pre_checkout_rejects(h, session, settings, case):
    order = await place_order(session, settings, product_id=4, qty=2)
    amount, user, order_id = order.total, CLIENT, order.id
    if case == "wrong_amount":
        amount -= 100
    elif case == "sold_out":
        (await repo.get_product(session, 4)).stock = 1
        await session.commit()
    elif case == "foreign":
        user = {**CLIENT, "id": 2}
    else:
        order_id = 10_000
    answer = await h.pre_checkout(order_id, amount, user)
    assert answer.ok is False
    assert answer.error_message  # Telegram покажет покупателю причину отказа


async def test_successful_payment_flow(h, session, settings):
    order = await place_order(session, settings, qty=2)
    await h.pay(order.id, order.total)
    await h.pay(order.id, order.total)  # Telegram прислал повторно

    await session.refresh(order)
    assert order.status == OrderStatus.PAID
    assert order.telegram_charge_id == f"tg_{order.id}"
    assert (await repo.get_product(session, 1)).stock == 23  # списали ровно один раз
    assert sum("Спасибо за покупку" in t for t in h.log.texts(CLIENT["id"])) == 1
    assert sum("Новый оплаченный заказ" in t for t in h.log.texts(ADMIN_ID)) == 1


async def test_payment_is_never_throttled(make_harness, session, settings):
    h = make_harness(throttle_seconds=60)
    order = await place_order(session, settings)
    await h.send(CLIENT, "/start")  # сразу за ним — оплата: антифлуд не должен её съесть
    await h.pay(order.id, order.total)
    await session.refresh(order)
    assert order.status == OrderStatus.PAID


async def test_admin_moves_order_and_client_is_notified(h, session, settings):
    order = await place_order(session, settings)
    await h.pay(order.id, order.total)

    await h.click(ADMIN, "Передан курьеру")
    assert "передан курьеру" in h.last_text(CLIENT["id"])
    await h.click(ADMIN, "Выполнен")
    assert "выполнен" in h.last_text(CLIENT["id"])

    await session.refresh(order)
    assert order.status == OrderStatus.DONE
    assert h.last_markup(ADMIN_ID).inline_keyboard[-1][0].text == "← К заказам"  # статусных кнопок больше нет


async def test_client_pays_from_chat_in_demo_mode(h, session, settings):
    order = await place_order(session, settings)
    await h.send(CLIENT, "📦 Мои заказы")
    assert "Ждёт оплаты" in h.last_text(CLIENT["id"])
    await h.click(CLIENT, "Оплатить (демо)")

    await session.refresh(order)
    assert order.status == OrderStatus.PAID
    assert any("Спасибо за покупку" in t for t in h.log.texts(CLIENT["id"]))


async def test_client_cancels_pending_order(h, session, settings):
    order = await place_order(session, settings)
    await h.send(CLIENT, "/orders")
    await h.click(CLIENT, "Отменить")
    await session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


async def test_unpaid_orders_are_cancelled_by_job(h, session, session_pool, settings):
    order = await place_order(session, settings)
    order.created_at -= timedelta(hours=1)
    await session.commit()

    await cancel_unpaid_orders(h.bot, session_pool, settings)
    await session.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    assert "оплата не поступила" in h.last_text(CLIENT["id"])


async def test_admin_adds_product_with_photo(h, session):
    await h.send(ADMIN, "/admin")
    await h.click(ADMIN, "Товары")
    await h.click(ADMIN, "Добавить товар")
    await h.click(ADMIN, "Аксессуары")
    await h.send(ADMIN, "Весы с таймером")
    await h.send(ADMIN, "Точность 0,1 г")
    await h.send(ADMIN, "2 490")
    await h.send(ADMIN, "7")
    await h.send(ADMIN, photo=[{"file_id": "AgACphoto", "file_unique_id": "u1", "width": 800, "height": 800}])

    assert "Товар добавлен" in h.last_text(ADMIN_ID)
    product = (await repo.list_products(session))[-1]
    assert (product.title, product.price, product.stock, product.photo_file_id) == (
        "Весы с таймером", 249000, 7, "AgACphoto"
    )


async def test_admin_panel_is_hidden_from_clients(h):
    await h.send(CLIENT, "/admin")
    assert "Панель администратора" not in h.last_text(CLIENT["id"])


async def test_admin_exports_csv(h, session, settings):
    order = await place_order(session, settings)
    await h.pay(order.id, order.total)
    await h.send(ADMIN, "/admin")
    await h.click(ADMIN, "Выгрузка CSV")
    document = h.log.sent(SendDocument, ADMIN_ID)[-1]
    assert "Заказы за 30 дней: 1" in document.caption


async def test_stats(h, session, settings):
    order = await place_order(session, settings, qty=2)
    await h.pay(order.id, order.total)
    await h.send(ADMIN, "/admin")
    await h.click(ADMIN, "Статистика")
    stats = h.last_text(ADMIN_ID)
    assert "Оплаченных заказов: 1" in stats
    assert "Эфиопия Иргачеффе — 2 шт." in stats


async def test_shop_opens_via_inline_button_with_signature(make_harness):
    # С кнопки клавиатуры Telegram не передаёт initData — поэтому витрина открывается только inline-кнопкой
    h = make_harness(webapp_url="https://shop.example.com")
    await h.send(CLIENT, "/start")
    menu = next(m.reply_markup for m in h.log.sent(SendMessage, CLIENT["id"]) if isinstance(m.reply_markup, ReplyKeyboardMarkup))
    assert all(button.web_app is None for row in menu.keyboard for button in row)

    await h.send(CLIENT, "🛍 Магазин")
    button = h.last_markup(CLIENT["id"]).inline_keyboard[0][0]
    assert button.web_app.url == "https://shop.example.com"
