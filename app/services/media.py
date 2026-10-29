"""Фото товаров. Админ загружает фото в бота, в БД хранится только file_id. Витрине нужен URL —
поэтому API один раз скачивает файл из Telegram и дальше отдаёт его с диска."""

import hashlib
from pathlib import Path

from aiogram import Bot

from app.db.models import Product


def image_version(product: Product) -> str | None:
    """Короткий хэш file_id: новое фото → новый URL, и браузер не покажет старое из кэша."""
    if not product.photo_file_id:
        return None
    return hashlib.sha1(product.photo_file_id.encode()).hexdigest()[:12]


async def product_image(bot: Bot, product: Product, media_dir: str) -> Path | None:
    if (version := image_version(product)) is None:
        return None
    path = Path(media_dir) / f"product_{product.id}_{version}.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")  # не отдадим недокачанный файл параллельному запросу
        await bot.download(product.photo_file_id, destination=partial)
        partial.replace(path)
    return path
