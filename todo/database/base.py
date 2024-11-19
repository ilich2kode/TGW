import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
import logging
from ..config import settings


logger = logging.getLogger(__name__)

Base = declarative_base()
#metadata = Base.metadata

# Асинхронный движок для работы с таблицами
engine = create_async_engine(settings.db_postgre_url, echo=True)

# Синхронный движок для работы с базой данных
sync_db_url = settings.db_postgre_url.replace("asyncpg", "psycopg2")
sync_engine = create_engine(sync_db_url)

SessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def create_database_if_not_exists(sync_db_url):
    """
    Проверяет существование базы данных и создает её, если она отсутствует.
    """
    db_name = sync_db_url.rsplit("/", 1)[-1]  # Получаем имя базы данных
    db_host = sync_db_url.rsplit("/", 1)[0]  # Получаем хост без имени базы данных
    engine_without_db = create_engine(db_host, isolation_level="AUTOCOMMIT")

    try:
        with engine_without_db.connect() as connection:
            # Используем sqlalchemy.text для выполнения SQL-запросов
            result = connection.execute(text(f"SELECT 1 FROM pg_database WHERE datname = :db_name"), {"db_name": db_name})
            if not result.scalar():
                connection.execute(text(f"CREATE DATABASE {db_name}"))
                print(f"База данных '{db_name}' успешно создана.")
            else:
                print(f"База данных '{db_name}' уже существует.")
    except Exception as e:
        print(f"Ошибка при создании базы данных: {e}")


async def init_db():
    from todo.models import Base
    from .base import sync_db_url, create_database_if_not_exists

    # Создаём базу данных, если её нет
    create_database_if_not_exists(sync_db_url)

    # Инициализируем таблицы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


