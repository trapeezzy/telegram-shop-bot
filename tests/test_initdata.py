import time
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode

import pytest

from app.services.initdata import InitDataError, validate_init_data
from tests.conftest import CLIENT, TOKEN, make_init_data

DAY = timedelta(hours=24)


def test_valid_init_data():
    user = validate_init_data(make_init_data(CLIENT), TOKEN, DAY)
    assert user.id == CLIENT["id"]
    assert user.first_name == "Иван"


def test_tampered_user_is_rejected():
    # Злоумышленник подменил id в подписанных данных, чтобы оформить заказ от чужого имени
    data = dict(parse_qsl(make_init_data(CLIENT)))
    data["user"] = data["user"].replace('"id":1', '"id":2')
    with pytest.raises(InitDataError):
        validate_init_data(urlencode(data), TOKEN, DAY)


def test_signature_from_another_bot_is_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(make_init_data(CLIENT, token="43:OTHER"), TOKEN, DAY)


def test_expired_init_data_is_rejected():
    two_days_ago = int(time.time()) - 2 * 24 * 3600
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(make_init_data(CLIENT, auth_date=two_days_ago), TOKEN, DAY)


def test_garbage_is_rejected():
    with pytest.raises(InitDataError):
        validate_init_data("not-init-data", TOKEN, DAY)
