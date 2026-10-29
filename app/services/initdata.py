"""Аутентификация запросов из Mini App.

Telegram подписывает данные о пользователе (initData) HMAC-ключом, производным от токена бота.
Сервер проверяет подпись — значит, user.id в запросе нельзя подделать, даже вызвав API вручную."""

from datetime import UTC, datetime, timedelta

from aiogram.utils.web_app import WebAppUser, safe_parse_webapp_init_data


class InitDataError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age: timedelta, now: datetime | None = None) -> WebAppUser:
    try:
        data = safe_parse_webapp_init_data(bot_token, init_data)
    except ValueError as e:
        raise InitDataError("invalid signature") from e

    auth_date = data.auth_date if data.auth_date.tzinfo else data.auth_date.replace(tzinfo=UTC)
    # Без проверки срока перехваченную подпись можно было бы использовать бесконечно
    if (now or datetime.now(UTC)) - auth_date > max_age:
        raise InitDataError("init data expired")
    if data.user is None:
        raise InitDataError("no user in init data")
    return data.user
