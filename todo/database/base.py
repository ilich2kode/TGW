import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
import logging
from ..config import settings

# Логирование
logger = logging.getLogger(__name__)

Base = declarative_base()

# Асинхронный движок
engine = create_async_engine(
    settings.db_postgre_url,
    echo=True,           # Логирование запросов
    pool_size=10,        # Размер пула соединений
    max_overflow=20,     # Дополнительные соединения при высокой нагрузке
    pool_timeout=30,     # Тайм-аут ожидания соединения
)

# Синхронный движок для работы с базой данных (создание базы)
sync_db_url = settings.db_postgre_url.replace("asyncpg", "psycopg2")
sync_engine = create_engine(sync_db_url, isolation_level="AUTOCOMMIT")

# Сессии
SessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db():
    """Получить асинхронную сессию базы данных."""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

def create_database_if_not_exists(sync_db_url):
    """
    Проверяет существование базы данных и создаёт её, если она отсутствует.
    """
    db_name = sync_db_url.rsplit("/", 1)[-1]  # Имя базы данных
    db_host = sync_db_url.rsplit("/", 1)[0]  # Хост без имени базы данных
    engine_without_db = create_engine(db_host, isolation_level="AUTOCOMMIT")

    try:
        with engine_without_db.connect() as connection:
            # Проверяем существование базы
            db_exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                {"db_name": db_name},
            ).scalar()

            if not db_exists:
                connection.execute(text(f"CREATE DATABASE {db_name}"))
                logger.info(f"База данных '{db_name}' успешно создана.")
            else:
                logger.info(f"База данных '{db_name}' уже существует.")
    except Exception as e:
        logger.error(f"Ошибка при создании базы данных: {e}")
        raise

async def init_db():
    """
    Инициализация базы данных и таблиц.
    """
    from todo.models import Base  # Импорты модели
    from .base import sync_db_url, create_database_if_not_exists

    # Асинхронно создаём базу данных
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, create_database_if_not_exists, sync_db_url)

    # Создаём таблицы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

