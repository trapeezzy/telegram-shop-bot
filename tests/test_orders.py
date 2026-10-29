"""Бизнес-правила заказов: суммы, доставка, остатки, статусы, оплата."""

from datetime import timedelta

import pytest

from app.db import repo
from app.db.models import DeliveryMethod, OrderStatus
from app.services import orders
from app.services.orders import CartLine, Checkout, OrderError
from tests.conftest import CLIENT, place_order


def checkout(**overrides) -> Checkout:
    fields = {
        "lines": [CartLine(product_id=1, qty=1)],
        "delivery": DeliveryMethod.COURIER,
        "name": "Иван",
        "phone": "89001234567",
        "address": "ул. Ленина, 1",
    }
    return Checkout(**{**fields, **overrides})


async def test_totals_are_calculated_from_db_prices(session, settings):
    order = await place_order(session, settings, qty=2)  # Эфиопия, 990 ₽
    assert order.items_total == 2 * 99000
    assert order.delivery_price == settings.delivery_price_minor
    assert order.total == 2 * 99000 + 30000
    assert order.phone == "+79001234567"
    assert order.status == OrderStatus.PENDING


async def test_free_delivery_from_threshold(session, settings):
    order = await place_order(session, settings, qty=4)  # 3 960 ₽ ≥ 3 000 ₽
    assert order.delivery_price == 0


async def test_pickup_has_no_delivery_and_no_address(session, settings):
    await repo.upsert_user(session, 1, "Иван", None)
    order = await orders.create_order(session, 1, checkout(delivery=DeliveryMethod.PICKUP), settings)
    assert order.delivery_price == 0
    assert order.address is None


async def test_duplicate_lines_are_merged(session, settings):
    await repo.upsert_user(session, 1, "Иван", None)
    order = await orders.create_order(
        session, 1, checkout(lines=[CartLine(1, 1), CartLine(1, 2)]), settings
    )
    assert [(i.product_id, i.qty) for i in order.items] == [(1, 3)]


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"lines": []}, "Корзина пуста"),
        ({"lines": [CartLine(1, 21)]}, "от 1 до 20"),
        ({"lines": [CartLine(999, 1)]}, "больше не продаётся"),
        ({"lines": [CartLine(4, 13)]}, "в наличии только"),  # Кения АА: на складе 12
        ({"phone": "позвоните"}, "номер телефона"),
        ({"address": ""}, "адрес доставки"),
        ({"name": " "}, "имя получателя"),
    ],
)
async def test_invalid_checkout(session, settings, overrides, error):
    await repo.upsert_user(session, 1, "Иван", None)
    with pytest.raises(OrderError, match=error):
        await orders.create_order(session, 1, checkout(**overrides), settings)


async def test_hidden_product_cannot_be_ordered(session, settings):
    product = await repo.get_product(session, 1)
    product.is_active = False
    await session.commit()
    await repo.upsert_user(session, 1, "Иван", None)
    with pytest.raises(OrderError, match="больше не продаётся"):
        await orders.create_order(session, 1, checkout(), settings)


async def test_mark_paid_takes_stock_once(session, settings):
    order = await place_order(session, settings, qty=3)
    assert await orders.mark_paid(session, order, telegram_charge_id="tg_1") is True
    assert await orders.mark_paid(session, order, telegram_charge_id="tg_1") is False  # повтор от Telegram

    await session.refresh(product := await repo.get_product(session, 1))
    assert product.stock == 25 - 3
    assert order.status == OrderStatus.PAID and order.paid_at is not None


async def test_stock_issue_when_sold_out_in_parallel(session, settings):
    order = await place_order(session, settings, product_id=4, qty=10)  # Кения: 12 шт.
    rival = await place_order(session, settings, product_id=4, qty=5, user_id=2)
    await orders.mark_paid(session, rival)

    await orders.mark_paid(session, order)  # деньги уже списаны — заказ засчитываем, но с пометкой
    assert order.status == OrderStatus.PAID
    assert order.stock_issue is True
    await session.refresh(product := await repo.get_product(session, 4))
    assert product.stock == 7  # не ушли в минус


async def test_ensure_payable_rules(session, settings):
    order = await place_order(session, settings)
    assert await orders.ensure_payable(session, order.id, CLIENT["id"], settings, amount=order.total)

    with pytest.raises(OrderError, match="не найден"):
        await orders.ensure_payable(session, order.id, 777, settings)
    with pytest.raises(OrderError, match="Сумма"):
        await orders.ensure_payable(session, order.id, CLIENT["id"], settings, amount=order.total - 1)

    order.created_at -= timedelta(minutes=settings.order_ttl_minutes + 1)
    await session.commit()
    with pytest.raises(OrderError, match="истекло"):
        await orders.ensure_payable(session, order.id, CLIENT["id"], settings)


async def test_ensure_payable_checks_stock(session, settings):
    order = await place_order(session, settings, product_id=4, qty=12)
    product = await repo.get_product(session, 4)
    product.stock = 3
    await session.commit()
    with pytest.raises(OrderError, match="закончился"):
        await orders.ensure_payable(session, order.id, CLIENT["id"], settings)


async def test_status_transitions_and_stock_return(session, settings):
    order = await place_order(session, settings, qty=2)
    await orders.mark_paid(session, order)
    with pytest.raises(OrderError):
        await orders.change_status(session, order, OrderStatus.DONE)  # нельзя «выполнить», не отправив

    await orders.change_status(session, order, OrderStatus.SHIPPED)
    await orders.change_status(session, order, OrderStatus.CANCELLED)
    await session.refresh(product := await repo.get_product(session, 1))
    assert product.stock == 25  # товар вернулся на склад


async def test_cancel_stale_only_old_pending(session, settings):
    old = await place_order(session, settings)
    fresh = await place_order(session, settings)
    paid = await place_order(session, settings)
    await orders.mark_paid(session, paid)
    for order in (old, paid):
        order.created_at -= timedelta(hours=1)
    await session.commit()

    cancelled = await orders.cancel_stale(session, settings)
    assert [o.id for o in cancelled] == [old.id]
    assert fresh.status == OrderStatus.PENDING and paid.status == OrderStatus.PAID


async def test_payment_after_timeout_still_counts(session, settings):
    order = await place_order(session, settings)
    order.created_at -= timedelta(hours=1)
    await session.commit()
    await orders.cancel_stale(session, settings)

    assert await orders.mark_paid(session, order, telegram_charge_id="late") is True
    assert order.status == OrderStatus.PAID
