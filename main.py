from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import asyncio
import os
from todo.pyrog.tgbot import tg_router, telegram_manager, fetch_new_chats_periodically 
from todo.pyrog.tgbot import fetch_missing_messages, setup_message_handler
from todo.database.base import init_db, get_db
from todo.database.base import SessionLocal  # Импорт существующей фабрики сессий


from todo.models import initialize_database, handle_temp_table

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




@app.on_event("startup")
async def on_startup():
    """
    Событие запуска приложения.
    """
    # Инициализация базы данных
    await init_db()

    # Старт Telegram клиента
    await telegram_manager.start()

    # Работа с базой данных через сессию
    async with SessionLocal() as session:
        # Создание хранимой процедуры process_chat_data
        await initialize_database(session)

        # Работа с временной таблицей
        await handle_temp_table(session)

        # Первоначальная загрузка недостающих сообщений
        #await fetch_missing_messages(session)

        # Настройка обработчика новых сообщений
        #await setup_message_handler(session)

        # Передача сессии в фоновую задачу
        asyncio.create_task(fetch_new_chats_periodically(session, interval=60))  # Интервал 60 секунд (1 минута)
        print("запуск fetch_new_chats_periodically")











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
