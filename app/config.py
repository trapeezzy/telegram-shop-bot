from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки читаются из переменных окружения или файла .env (см. .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    admin_ids: str = ""  # Telegram ID администраторов через запятую
    database_url: str = "sqlite+aiosqlite:///shop.db"
    redis_url: str | None = None

    # Mini App: публичный HTTPS-адрес, по которому Telegram откроет витрину
    webapp_url: str = "http://localhost:8080"
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    media_dir: str = "media"  # кэш фото товаров, скачанных из Telegram

    # Оплата: токен провайдера из @BotFather → Payments. Пусто — демо-оплата без денег
    payment_provider_token: SecretStr | None = None
    currency: str = "RUB"

    shop_name: str = "Зерно · обжарочная"
    pickup_address: str = "Москва, ул. Примерная, 1"
    support_contact: str = "@support"
    delivery_price: int = 300  # ₽
    free_delivery_from: int = 3000  # ₽, бесплатная доставка от этой суммы
    order_ttl_minutes: int = 30  # неоплаченный заказ отменяется через это время

    timezone: str = "Europe/Moscow"
    initdata_ttl_hours: int = 24  # сколько живёт подпись Mini App
    throttle_seconds: float = 0.5

    @property
    def admins(self) -> set[int]:
        return {int(part) for part in self.admin_ids.replace(" ", "").split(",") if part}

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admins

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def demo_payments(self) -> bool:
        return self.payment_provider_token is None or not self.payment_provider_token.get_secret_value()

    @property
    def webapp_enabled(self) -> bool:
        """Telegram открывает Mini App только по HTTPS."""
        return self.webapp_url.startswith("https://")

    # Деньги внутри приложения — в копейках (минимальных единицах валюты), как в Telegram Payments
    @property
    def delivery_price_minor(self) -> int:
        return self.delivery_price * 100

    @property
    def free_delivery_from_minor(self) -> int:
        return self.free_delivery_from * 100
