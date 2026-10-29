"""HTTP API для Mini App + раздача самой витрины (статические файлы).

Каталог открыт всем, а всё, что связано с заказами, требует заголовок `Authorization: tma <initData>` —
подписанные Telegram данные пользователя. Документация: /api/docs."""

from datetime import timedelta
from pathlib import Path
from typing import Annotated

from aiogram import Bot
from aiogram.utils.web_app import WebAppUser
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import repo
from app.services import media, notify, orders, payments
from app.services.initdata import InitDataError, validate_init_data
from app.services.orders import CartLine, Checkout, OrderError
from app.utils.formatting import fmt_dt
from app.web.schemas import (
    CatalogOut,
    CategoryOut,
    CheckoutIn,
    OrderCreatedOut,
    OrderItemOut,
    OrderOut,
    ProductOut,
    ShopOut,
)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings, session_pool: async_sessionmaker[AsyncSession], bot: Bot) -> FastAPI:
    app = FastAPI(
        title=f"{settings.shop_name} — API витрины",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    async def get_session():
        async with session_pool() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(get_session)]

    async def current_user(session: Session, authorization: Annotated[str | None, Header()] = None) -> WebAppUser:
        if not authorization or not authorization.startswith("tma "):
            raise HTTPException(401, "Откройте магазин из Telegram, чтобы оформить заказ")
        try:
            user = validate_init_data(
                authorization.removeprefix("tma "),
                settings.bot_token.get_secret_value(),
                max_age=timedelta(hours=settings.initdata_ttl_hours),
            )
        except InitDataError:
            raise HTTPException(401, "Сессия устарела — закройте и снова откройте магазин") from None
        full_name = " ".join(filter(None, [user.first_name, user.last_name])) or str(user.id)
        await repo.upsert_user(session, user.id, full_name, user.username)
        return user

    CurrentUser = Annotated[WebAppUser, Depends(current_user)]

    @app.get("/api/catalog", response_model=CatalogOut, tags=["Каталог"])
    async def catalog(session: Session) -> CatalogOut:
        products = await repo.list_products(session)
        return CatalogOut(
            shop=ShopOut(
                name=settings.shop_name,
                currency=settings.currency,
                delivery_price=settings.delivery_price_minor,
                free_delivery_from=settings.free_delivery_from_minor,
                pickup_address=settings.pickup_address,
                support_contact=settings.support_contact,
                demo_payments=settings.demo_payments,
            ),
            categories=[CategoryOut(id=c.id, title=c.title, emoji=c.emoji) for c in await repo.list_categories(session)],
            products=[
                ProductOut(
                    id=p.id,
                    category_id=p.category_id,
                    title=p.title,
                    description=p.description,
                    price=p.price,
                    stock=p.stock,
                    emoji=p.emoji,
                    image_url=f"/api/products/{p.id}/image?v={v}" if (v := media.image_version(p)) else None,
                )
                for p in products
            ],
        )

    @app.get("/api/products/{product_id}/image", tags=["Каталог"])
    async def product_image(product_id: int, session: Session) -> FileResponse:
        product = await repo.get_product(session, product_id)
        path = await media.product_image(bot, product, settings.media_dir) if product else None
        if path is None:
            raise HTTPException(404, "Фото нет")
        # Версия в URL меняется вместе с фото, поэтому кэшировать можно надолго
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})

    @app.post("/api/orders", response_model=OrderCreatedOut, tags=["Заказы"])
    async def create_order(body: CheckoutIn, user: CurrentUser, session: Session) -> OrderCreatedOut:
        checkout = Checkout(
            lines=[CartLine(product_id=i.product_id, qty=i.qty) for i in body.items],
            delivery=body.delivery,
            name=body.name,
            phone=body.phone,
            address=body.address,
            comment=body.comment,
        )
        try:
            order = await orders.create_order(session, user.id, checkout, settings)
        except OrderError as e:
            raise HTTPException(400, str(e)) from None
        link = None if settings.demo_payments else await payments.create_invoice_link(bot, order, settings)
        return OrderCreatedOut(order_id=order.id, total=order.total, invoice_link=link)

    @app.post("/api/orders/{order_id}/demo-pay", tags=["Заказы"])
    async def demo_pay(order_id: int, user: CurrentUser, session: Session) -> dict:
        """Оплата без денег — работает, только пока не задан PAYMENT_PROVIDER_TOKEN."""
        if not settings.demo_payments:
            raise HTTPException(404)
        try:
            order = await orders.ensure_payable(session, order_id, user.id, settings)
        except OrderError as e:
            raise HTTPException(400, str(e)) from None
        if await orders.mark_paid(session, order, telegram_charge_id="demo"):
            await notify.order_paid(bot, order, settings)
        return {"ok": True}

    @app.get("/api/orders", response_model=list[OrderOut], tags=["Заказы"])
    async def my_orders(user: CurrentUser, session: Session) -> list[OrderOut]:
        return [
            OrderOut(
                id=o.id,
                status=o.status,
                status_label=notify.status_label(o),
                total=o.total,
                created_at=fmt_dt(o.created_at, settings.tz),
                items=[OrderItemOut(title=i.title, qty=i.qty, amount=i.amount) for i in o.items],
            )
            for o in await repo.user_orders(session, user.id)
        ]

    # Витрина — последней, чтобы не перехватывать /api/*
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
