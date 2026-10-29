from aiogram.fsm.state import State, StatesGroup


class AddProduct(StatesGroup):
    category = State()
    title = State()
    description = State()
    price = State()
    stock = State()
    photo = State()


class EditProduct(StatesGroup):
    price = State()
    stock = State()
    photo = State()
