"""Схемы API витрины. Все суммы — в копейках (минимальных единицах валюты)."""

from pydantic import BaseModel, Field

from app.db.models import DeliveryMethod


class ShopOut(BaseModel):
    name: str
    currency: str
    delivery_price: int
    free_delivery_from: int
    pickup_address: str
    support_contact: str
    demo_payments: bool


class CategoryOut(BaseModel):
    id: int
    title: str
    emoji: str


class ProductOut(BaseModel):
    id: int
    category_id: int
    title: str
    description: str
    price: int
    stock: int
    emoji: str
    image_url: str | None


class CatalogOut(BaseModel):
    shop: ShopOut
    categories: list[CategoryOut]
    products: list[ProductOut]


class CartLineIn(BaseModel):
    product_id: int
    qty: int = Field(ge=1, le=99)


class CheckoutIn(BaseModel):
    items: list[CartLineIn] = Field(min_length=1, max_length=50)
    delivery: DeliveryMethod
    name: str = Field(max_length=100)
    phone: str = Field(max_length=32)
    address: str | None = Field(default=None, max_length=255)
    comment: str | None = Field(default=None, max_length=500)


class OrderCreatedOut(BaseModel):
    order_id: int
    total: int
    invoice_link: str | None  # None в демо-режиме: оплата через /demo-pay


class OrderItemOut(BaseModel):
    title: str
    qty: int
    amount: int


class OrderOut(BaseModel):
    id: int
    status: str
    status_label: str
    total: int
    created_at: str
    items: list[OrderItemOut]
