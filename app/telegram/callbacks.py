from aiogram.filters.callback_data import CallbackData


class OrderCb(CallbackData, prefix="ord"):
    action: str  # pay | cancel
    id: int


class AdminCb(CallbackData, prefix="adm"):
    action: str  # menu | orders | products | add_product | stats | export


class AdminOrderCb(CallbackData, prefix="aord"):
    id: int
    status: str  # open — показать карточку, иначе — новый статус заказа


class ProductCb(CallbackData, prefix="prod"):
    id: int
    action: str  # open | toggle | price | stock | photo


class CategoryCb(CallbackData, prefix="cat"):
    id: int
