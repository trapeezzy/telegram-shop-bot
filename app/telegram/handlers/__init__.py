from aiogram import Router

from app.telegram.handlers import admin, common, fallback, orders, payments


def get_routers() -> list[Router]:
    # Порядок важен: апдейт уходит в первый подходящий хендлер, fallback — всегда последним
    return [common.router, payments.router, orders.router, admin.router, fallback.router]
