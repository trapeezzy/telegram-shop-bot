"""Бизнес-логика заказов. Не зависит ни от Telegram, ни от веба: её используют бот, API Mini App
и обработчики оплаты — поэтому правила описаны в одном месте и покрыты тестами."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import DeliveryMethod, Order, OrderItem, OrderStatus, utcnow
from app.utils.formatting import normalize_phone

MAX_QTY_PER_PRODUCT = 20
MAX_LINES = 30

TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DONE, OrderStatus.CANCELLED},
    OrderStatus.DONE: set(),
    OrderStatus.CANCELLED: set(),
}


class OrderError(Exception):
    """Ошибка, текст которой можно показать покупателю как есть."""


@dataclass(frozen=True)
class CartLine:
    product_id: int
    qty: int


@dataclass(frozen=True)
class Checkout:
    lines: Sequence[CartLine]
    delivery: DeliveryMethod
    name: str
    phone: str
    address: str | None = None
    comment: str | None = None


def delivery_cost(items_total: int, method: DeliveryMethod, settings: Settings) -> int:
    if method == DeliveryMethod.PICKUP or items_total >= settings.free_delivery_from_minor:
        return 0
    return settings.delivery_price_minor


async def create_order(session: AsyncSession, user_id: int, checkout: Checkout, settings: Settings) -> Order:
    """Создаёт заказ в статусе «ждёт оплаты». Цены и наличие берутся из БД — данным из корзины не доверяем."""
    qty_by_product: dict[int, int] = {}
    for line in checkout.lines:
        qty_by_product[line.product_id] = qty_by_product.get(line.product_id, 0) + line.qty
    if not qty_by_product:
        raise OrderError("Корзина пуста")
    if len(qty_by_product) > MAX_LINES:
        raise OrderError("Слишком много позиций в одном заказе")
    if any(not 1 <= qty <= MAX_QTY_PER_PRODUCT for qty in qty_by_product.values()):
        raise OrderError(f"Можно заказать от 1 до {MAX_QTY_PER_PRODUCT} шт. одного товара")

    products = await repo.get_products(session, set(qty_by_product))
    items = []
    for product_id, qty in qty_by_product.items():
        product = products.get(product_id)
        if product is None or not product.is_active:
            raise OrderError("Один из товаров больше не продаётся — обновите корзину")
        if product.stock < qty:
            raise OrderError(
                f"«{product.title}»: в наличии только {product.stock} шт."
                if product.stock
                else f"«{product.title}» закончился"
            )
        items.append(OrderItem(product_id=product.id, title=product.title, price=product.price, qty=qty))

    name = checkout.name.strip()
    if not 2 <= len(name) <= 100:
        raise OrderError("Укажите имя получателя")
    if (phone := normalize_phone(checkout.phone)) is None:
        raise OrderError("Проверьте номер телефона")
    address = (checkout.address or "").strip() or None
    if checkout.delivery == DeliveryMethod.COURIER and (address is None or len(address) < 5):
        raise OrderError("Укажите адрес доставки")
    if checkout.delivery == DeliveryMethod.PICKUP:
        address = None

    items_total = sum(item.amount for item in items)
    delivery = delivery_cost(items_total, checkout.delivery, settings)
    order = Order(
        user_id=user_id,
        delivery_method=checkout.delivery,
        customer_name=name,
        phone=phone,
        address=address[:255] if address else None,
        comment=(checkout.comment or "").strip()[:500] or None,
        items_total=items_total,
        delivery_price=delivery,
        total=items_total + delivery,
        items=items,
    )
    session.add(order)
    await session.commit()
    return order


def is_expired(order: Order, settings: Settings) -> bool:
    return order.created_at < utcnow() - timedelta(minutes=settings.order_ttl_minutes)


async def ensure_payable(
    session: AsyncSession, order_id: int, user_id: int, settings: Settings, *, amount: int | None = None
) -> Order:
    """Проверка перед списанием денег (pre_checkout). Бросает OrderError с причиной отказа."""
    order = await repo.get_order(session, order_id)
    if order is None or order.user_id != user_id:
        raise OrderError("Заказ не найден")
    if order.status != OrderStatus.PENDING:
        raise OrderError("Этот заказ уже оплачен или отменён")
    if is_expired(order, settings):
        raise OrderError("Время на оплату истекло — оформите заказ заново")
    if amount is not None and amount != order.total:
        raise OrderError("Сумма заказа изменилась — оформите заказ заново")

    products = await repo.get_products(session, {item.product_id for item in order.items})
    for item in order.items:
        product = products.get(item.product_id)
        if product is None or product.stock < item.qty:
            raise OrderError(f"«{item.title}» закончился, пока вы оформляли заказ")
    return order


async def mark_paid(
    session: AsyncSession,
    order: Order,
    *,
    telegram_charge_id: str | None = None,
    provider_charge_id: str | None = None,
) -> bool:
    """Отмечает заказ оплаченным и списывает остатки. Возвращает False, если заказ уже был оплачен:
    Telegram может прислать уведомление повторно, и второй раз списывать товар нельзя."""
    if order.paid_at is not None:
        return False
    # Деньги пришли — заказ засчитываем, даже если его успели отменить по таймауту
    for item in order.items:
        if not await repo.take_stock(session, item.product_id, item.qty):
            order.stock_issue = True  # купили параллельно — админ свяжется с клиентом
    order.status = OrderStatus.PAID
    order.paid_at = utcnow()
    order.telegram_charge_id = telegram_charge_id
    order.provider_charge_id = provider_charge_id
    await session.commit()
    return True


async def change_status(session: AsyncSession, order: Order, new_status: OrderStatus) -> None:
    if new_status not in TRANSITIONS[order.status]:
        raise OrderError("Заказ нельзя перевести в этот статус")
    # Отмена оплаченного заказа возвращает товар на склад. При stock_issue остатки правятся вручную:
    # неясно, какие позиции успели списаться
    if new_status == OrderStatus.CANCELLED and order.paid_at is not None and not order.stock_issue:
        for item in order.items:
            await repo.return_stock(session, item.product_id, item.qty)
    order.status = new_status
    await session.commit()


async def cancel_stale(session: AsyncSession, settings: Settings) -> Sequence[Order]:
    """Отменяет заказы, не оплаченные вовремя. Остатки не трогаем — они списываются только при оплате."""
    cutoff = utcnow() - timedelta(minutes=settings.order_ttl_minutes)
    orders = await repo.stale_pending_orders(session, cutoff)
    for order in orders:
        order.status = OrderStatus.CANCELLED
    await session.commit()
    return orders
