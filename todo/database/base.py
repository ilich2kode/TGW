import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

from ..config import settings

#Создание папки для БД
BASE_DIR = os.path.dirname(os.path.abspath(__name__))  # Исправлено на __file__ для получения пути текущего файла __name__
db_path = os.path.join(BASE_DIR, 'todo', 'database', 'DB')
if not os.path.exists(db_path):
    os.makedirs(db_path)
    
Base = declarative_base()


engine = create_async_engine(settings.db_postgre_url, echo=True)

SessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db():
    async with SessionLocal() as session:
        yield session


