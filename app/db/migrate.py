from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    # Через attributes, а не set_main_option: в пароле может быть '%', который ломает configparser
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    return config


def upgrade_db(database_url: str) -> None:
    """Применяет все новые миграции. Бот вызывает это при старте, отдельная команда не нужна."""
    command.upgrade(alembic_config(database_url), "head")
