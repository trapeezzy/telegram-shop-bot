from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_session_pool(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    # expire_on_commit=False: после commit объекты остаются читаемыми без повторного запроса
    return engine, async_sessionmaker(engine, expire_on_commit=False)
