import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings


load_dotenv()


class Settings(BaseSettings):
    app_name: str = os.getenv('NAME_APP')
    #db_sqlite_url: str = os.getenv('SQLALCHEMY_DATABASE_URI')
    db_postgre_url: str = os.getenv('POSTGRES_DB')

    class Config:
        env_file: str = '../.env'


settings = Settings()

#print(settings.app_name)  # Должно вывести имя приложения
#print(settings.db_postgre_url)  # Должно вывести строку подключения к PostgreSQL
print(f"App Name: {settings.app_name}, DB URL: {settings.db_postgre_url}")
