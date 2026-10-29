from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Время в БД хранится в UTC без tzinfo — одинаково для SQLite и Postgres."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class OrderStatus(StrEnum):
    PENDING = "pending"  # создан, ждёт оплаты
    PAID = "paid"
    SHIPPED = "shipped"  # передан в доставку / готов к выдаче
    DONE = "done"
    CANCELLED = "cancelled"


class DeliveryMethod(StrEnum):
    COURIER = "courier"
    PICKUP = "pickup"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # Telegram ID
    full_name: Mapped[str] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64))
    is_blocked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="🛍")
    sort: Mapped[int] = mapped_column(default=0)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int]  # в копейках
    stock: Mapped[int] = mapped_column(default=0)
    emoji: Mapped[str] = mapped_column(String(8), default="☕")  # заглушка, пока нет фото
    photo_file_id: Mapped[str | None] = mapped_column(String(255))  # фото, загруженное админом в бота
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    category: Mapped[Category] = relationship(lazy="joined")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(default=OrderStatus.PENDING, index=True)
    delivery_method: Mapped[DeliveryMethod]
    customer_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(String(500))
    # Суммы фиксируются при оформлении — изменение цен не влияет на созданные заказы
    items_total: Mapped[int]
    delivery_price: Mapped[int]
    total: Mapped[int]
    # Оплата прошла, но товара уже не хватило (купили параллельно) — админ решает вручную
    stock_issue: Mapped[bool] = mapped_column(default=False)
    telegram_charge_id: Mapped[str | None] = mapped_column(String(255))
    provider_charge_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    paid_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(lazy="joined")
    items: Mapped[list["OrderItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    title: Mapped[str] = mapped_column(String(100))  # снимок названия и цены на момент заказа
    price: Mapped[int]
    qty: Mapped[int]

    @property
    def amount(self) -> int:
        return self.price * self.qty
