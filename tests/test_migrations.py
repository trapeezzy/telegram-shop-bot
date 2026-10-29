from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.db.migrate import alembic_config
from app.db.models import Base


def test_migrations_match_models(tmp_path):
    """Если поменять модель и забыть создать миграцию — этот тест упадёт."""
    db = tmp_path / "migrations.db"
    config = alembic_config(f"sqlite+aiosqlite:///{db}")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []

    command.downgrade(config, "base")  # откат тоже должен работать
