"""API Mini App: через настоящее FastAPI-приложение, Telegram подменён FakeSession."""

import pytest
from aiogram.methods import CreateInvoiceLink, SendMessage
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.db import repo
from app.web.api import create_app
from tests.conftest import ADMIN_ID, CLIENT, make_init_data

AUTH = {"Authorization": f"tma {make_init_data(CLIENT)}"}
ORDER = {
    "items": [{"product_id": 1, "qty": 2}, {"product_id": 7, "qty": 1}],
    "delivery": "courier",
    "name": "Иван Петров",
    "phone": "+7 900 123-45-67",
    "address": "ул. Ленина, 1, кв. 5",
}


@pytest.fixture
def make_client(session_pool, fake_bot):
    def factory(settings):
        app = create_app(settings, session_pool, fake_bot)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    return factory


@pytest.fixture
async def client(make_client, settings):
    async with make_client(settings) as client:
        yield client


async def test_catalog_is_public(client):
    response = await client.get("/api/catalog")
    assert response.status_code == 200
    data = response.json()
    assert len(data["categories"]) == 3
    assert len(data["products"]) == 9
    assert data["products"][0]["price"] == 99000  # копейки
    assert data["shop"]["demo_payments"] is True


async def test_storefront_is_served(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "telegram-web-app.js" in response.text


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "tma garbage"}, {"Authorization": f"tma {make_init_data(CLIENT, token='43:OTHER')}"}],
)
async def test_orders_require_valid_signature(client, headers):
    response = await client.post("/api/orders", json=ORDER, headers=headers)
    assert response.status_code == 401


async def test_client_cannot_set_price(client):
    # Цену из запроса сервер не принимает вовсе — сумма всегда считается по БД
    body = {**ORDER, "items": [{"product_id": 1, "qty": 2, "price": 1}]}
    response = await client.post("/api/orders", json=body, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["total"] == 2 * 99000 + 30000  # 2 × 990 ₽ + доставка 300 ₽


async def test_business_errors_are_readable(client):
    response = await client.post("/api/orders", json={**ORDER, "address": ""}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["detail"] == "Укажите адрес доставки"


async def test_demo_payment_flow(client, session_pool, fake_bot):
    created = (await client.post("/api/orders", json=ORDER, headers=AUTH)).json()
    assert created["invoice_link"] is None  # демо-режим

    response = await client.post(f"/api/orders/{created['order_id']}/demo-pay", headers=AUTH)
    assert response.status_code == 200

    async with session_pool() as session:
        assert (await repo.get_product(session, 1)).stock == 25 - 2
    session_log = fake_bot.session
    assert any("Спасибо за покупку" in m.text for m in session_log.sent(SendMessage, CLIENT["id"]))
    assert any("Новый оплаченный заказ" in m.text for m in session_log.sent(SendMessage, ADMIN_ID))

    orders = (await client.get("/api/orders", headers=AUTH)).json()
    assert orders[0]["id"] == created["order_id"]
    assert orders[0]["status"] == "paid"


async def test_cannot_pay_someone_elses_order(client):
    created = (await client.post("/api/orders", json=ORDER, headers=AUTH)).json()
    stranger = {"Authorization": f"tma {make_init_data({**CLIENT, 'id': 2})}"}
    response = await client.post(f"/api/orders/{created['order_id']}/demo-pay", headers=stranger)
    assert response.status_code == 400
    assert (await client.get("/api/orders", headers=stranger)).json() == []


async def test_real_payments_return_invoice_link(make_client, settings, fake_bot):
    paid_settings = settings.model_copy(update={"payment_provider_token": SecretStr("TEST:provider")})
    async with make_client(paid_settings) as client:
        created = (await client.post("/api/orders", json=ORDER, headers=AUTH)).json()
        assert created["invoice_link"].startswith("https://t.me/$")
        # Демо-оплата при настоящем провайдере отключена
        assert (await client.post(f"/api/orders/{created['order_id']}/demo-pay", headers=AUTH)).status_code == 404

    invoice = fake_bot.session.sent(CreateInvoiceLink)[0]
    assert invoice.payload == f"order:{created['order_id']}"
    assert sum(p.amount for p in invoice.prices) == created["total"]


async def test_image_404_without_photo(client):
    assert (await client.get("/api/products/1/image")).status_code == 404
