"""Запросы к БД. Бизнес-правила (как считать заказ, когда его можно оплатить) — в app/services."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Order, OrderItem, OrderStatus, Product, User

# Статусы, в которых деньги уже получены
PAID_STATUSES = (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DONE)


# ---------- Пользователи ----------

async def upsert_user(session: AsyncSession, user_id: int, full_name: str, username: str | None) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, full_name=full_name, username=username)
        session.add(user)
    else:
        user.full_name, user.username, user.is_blocked = full_name, username, False
    await session.commit()
    return user


# ---------- Каталог ----------

SEED_CATALOG = {
    ("Кофе в зёрнах", "🫘"): [
        ("Эфиопия Иргачеффе", "Цитрус, бергамот, жасмин. Светлая обжарка, 250 г", 99000, 25, "🌸"),
        ("Колумбия Супремо", "Карамель, орех, мягкая кислотность. Средняя обжарка, 250 г", 85000, 30, "🌰"),
        ("Бразилия Сантос", "Шоколад и орехи — классика для эспрессо. 250 г", 79000, 40, "🍫"),
        ("Кения АА", "Чёрная смородина, яркая сладость. Светлая обжарка, 250 г", 115000, 12, "🍇"),
    ],
    ("Дрип-пакеты", "💧"): [
        ("Дрип-пакеты «Ассорти», 10 шт", "По 2 пакета пяти сортов — попробовать всё", 65000, 50, "🎁"),
        ("Дрип-пакеты «Эфиопия», 10 шт", "Заварите в чашке за 3 минуты", 70000, 35, "☕"),
    ],
    ("Аксессуары", "⚙️"): [
        ("Воронка V60", "Керамика, размер 02", 149000, 8, "🔻"),
        ("Ручная кофемолка", "Стальные жернова, 20 ступеней помола", 390000, 5, "⚙️"),
        ("Фильтры для V60, 100 шт", "Бумажные, размер 02", 45000, 60, "📄"),
    ],
}


async def seed_catalog(session: AsyncSession) -> None:
    if await session.scalar(select(func.count()).select_from(Category)):
        return
    for sort, ((title, emoji), products) in enumerate(SEED_CATALOG.items()):
        category = Category(title=title, emoji=emoji, sort=sort)
        session.add(category)
        await session.flush()
        session.add_all(
            Product(category_id=category.id, title=t, description=d, price=p, stock=s, emoji=e)
            for t, d, p, s, e in products
        )
    await session.commit()


async def list_categories(session: AsyncSession) -> Sequence[Category]:
    return (await session.scalars(select(Category).order_by(Category.sort, Category.id))).all()


async def list_products(session: AsyncSession, *, only_active: bool = True) -> Sequence[Product]:
    stmt = select(Product).join(Category).order_by(Category.sort, Product.id)
    if only_active:
        stmt = stmt.where(Product.is_active.is_(True))
    return (await session.scalars(stmt)).all()


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def get_products(session: AsyncSession, ids: set[int]) -> dict[int, Product]:
    products = await session.scalars(select(Product).where(Product.id.in_(ids)))
    return {p.id: p for p in products}


async def add_product(session: AsyncSession, **fields) -> Product:
    product = Product(**fields)
    session.add(product)
    await session.commit()
    return product


async def take_stock(session: AsyncSession, product_id: int, qty: int) -> bool:
    """Атомарно списывает остаток. False — товара не хватило (его успели купить)."""
    result = await session.execute(
        update(Product).where(Product.id == product_id, Product.stock >= qty).values(stock=Product.stock - qty)
    )
    return result.rowcount == 1


async def return_stock(session: AsyncSession, product_id: int, qty: int) -> None:
    await session.execute(update(Product).where(Product.id == product_id).values(stock=Product.stock + qty))


# ---------- Заказы ----------

async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


async def user_orders(session: AsyncSession, user_id: int, limit: int = 10) -> Sequence[Order]:
    stmt = select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(limit)
    return (await session.scalars(stmt)).all()


async def orders_by_status(session: AsyncSession, statuses: Sequence[OrderStatus], limit: int = 30) -> Sequence[Order]:
    stmt = select(Order).where(Order.status.in_(statuses)).order_by(Order.id).limit(limit)
    return (await session.scalars(stmt)).all()


async def stale_pending_orders(session: AsyncSession, created_before: datetime) -> Sequence[Order]:
    stmt = select(Order).where(Order.status == OrderStatus.PENDING, Order.created_at < created_before)
    return (await session.scalars(stmt)).all()


async def orders_since(session: AsyncSession, since: datetime) -> Sequence[Order]:
    stmt = select(Order).where(Order.created_at >= since).order_by(Order.id)
    return (await session.scalars(stmt)).all()


# ---------- Статистика ----------

@dataclass(frozen=True)
class Stats:
    users: int
    paid_orders: int
    revenue: int
    avg_check: int
    in_progress: int
    top_products: list[tuple[str, int]]


async def get_stats(session: AsyncSession, now: datetime, days: int = 30) -> Stats:
    since = now - timedelta(days=days)
    paid = Order.status.in_(PAID_STATUSES) & (Order.paid_at >= since)
    count, revenue = (await session.execute(select(func.count(), func.coalesce(func.sum(Order.total), 0)).where(paid))).one()
    top = await session.execute(
        select(OrderItem.title, func.sum(OrderItem.qty).label("qty"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(paid)
        .group_by(OrderItem.title)
        .order_by(func.sum(OrderItem.qty).desc())
        .limit(3)
    )
    return Stats(
        users=await session.scalar(select(func.count()).select_from(User)) or 0,
        paid_orders=count,
        revenue=revenue,
        avg_check=revenue // count if count else 0,
        in_progress=await session.scalar(
            select(func.count()).where(Order.status.in_((OrderStatus.PAID, OrderStatus.SHIPPED)))
        )
        or 0,
        top_products=[(title, qty) for title, qty in top],
    )
