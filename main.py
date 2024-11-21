from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import asyncio
import os
from todo.pyrog.tgbot import tg_router, telegram_manager, fetch_new_chats_periodically
from todo.database.base import init_db, get_db
from todo.models import initialize_database, handle_temp_table
import logging
from globals import telegram_client_ready, session_name, session_file


logger = logging.getLogger(__name__)

app = FastAPI()

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Настройка статических файлов и шаблонов
static_dir = os.path.join(os.path.dirname(__file__), 'todo/static')
app.mount('/static', StaticFiles(directory=static_dir), name='static')

# Импортируйте шаблоны
templates = Jinja2Templates(directory='todo/templates')  # Создайте экземпляр Jinja2Templates

# Импортируйте роутеры после создания экземпляра приложения
app.include_router(tg_router)  # Включите router в ваше приложение

import os
import asyncio
from logging import getLogger

logger = getLogger(__name__)



    


@app.on_event("startup")
async def on_startup():
    """
    Событие запуска приложения.
    """
    global telegram_client_ready  # Указываем, что используем глобальную переменную

    try:
        # Инициализация базы данных
        await init_db()
        logger.info("Инициализация базы данных завершена.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        raise

    # Проверка наличия файла сессии
    try:
        # Установим ограничение на время ожидания файла сессии (например, 5 минут)
        timeout = 300
        start_time = asyncio.get_event_loop().time()
        while not telegram_client_ready:
            if os.path.exists(session_file):
                logger.info(f"Файл сессии '{session_file}' найден. Ожидание завершено.")
                telegram_client_ready = True  # Устанавливаем глобальную переменную
            else:
                elapsed_time = asyncio.get_event_loop().time() - start_time
                if elapsed_time > timeout:
                    raise TimeoutError(f"Файл сессии '{session_file}' не появился за {timeout} секунд.")
                logger.warning(f"Файл сессии '{session_file}' не найден. Ожидание...")
                await asyncio.sleep(10)
    except Exception as e:
        logger.error(f"Ошибка при ожидании файла сессии: {e}")
        raise

    # Запуск Telegram клиента
    try:
        await telegram_manager.start()
        logger.info("Telegram клиент успешно запущен.")
    except Exception as e:
        logger.error(f"Ошибка при запуске Telegram клиента: {e}")
        raise












@app.on_event("shutdown")
async def on_shutdown():
    """
    Событие остановки приложения.
    """
    print("Приложение завершает работу...")
    # Остановка Telegram-менеджера
    await telegram_manager.stop()

# Пример маршрута
@app.get("/")
async def read_root(request: Request):  # Добавьте параметр request
    return templates.TemplateResponse("index.html", {"request": request})  # Используйте request

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000, reload=True)
