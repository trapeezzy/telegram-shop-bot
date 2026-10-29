import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base

config = context.config
target_metadata = Base.metadata

# Когда миграции запускает сам бот, логирование уже настроено — не перетираем его
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def _database_url() -> str:
    if url := config.attributes.get("database_url"):
        return url
    from app.config import Settings

    return Settings().database_url


def _configure(**kwargs) -> None:
    # render_as_batch: SQLite не умеет ALTER COLUMN, Alembic пересоздаёт таблицу целиком
    context.configure(target_metadata=target_metadata, render_as_batch=True, **kwargs)


def do_run_migrations(connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    _configure(url=_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async_migrations())
