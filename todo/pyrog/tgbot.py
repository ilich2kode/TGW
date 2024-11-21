from fastapi import APIRouter, Request, Form, Response, Depends, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from fastapi import Depends

from sqlalchemy.orm import Session
from typing import List
from sqlalchemy import update
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError

from dotenv import load_dotenv, set_key
from itsdangerous import URLSafeSerializer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from todo.database.base import get_db
from todo.models import Chat, ChatNameHistory
from datetime import datetime
import asyncio
import os
import logging
from fastapi import Request

# Настройка логирования
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Загрузка переменных из файла .env
load_dotenv()

SECRET_KEY = "7632972461643986eqwqewq1231231"  # Секретный ключ
serializer = URLSafeSerializer(SECRET_KEY)

tg_router = APIRouter()
templates = Jinja2Templates(directory='todo/templates')

# Telegram Client Manager
class TelegramClientManager:
    def __init__(self, session_name, api_id, api_hash):
        self.client = TelegramClient(session_name, api_id=api_id, api_hash=api_hash)
        self.lock = asyncio.Lock()

    async def start(self):
        if not self.client.is_connected():
            await self.client.connect()
            logger.info("Telegram client connected.")
            # Проверяем авторизацию
            if not await self.client.is_user_authorized():
                logger.info("Клиент не авторизован.")

    async def stop(self):
        if self.client.is_connected():
            await self.client.disconnect()
            logger.info("Telegram client disconnected.")

    async def safe_call(self, coro):
        async with self.lock:
            if not self.client.is_connected():
                logger.info("Клиент не подключен. Подключаем...")
                await self.start()
            if callable(coro):
                coro = coro()
            logger.info(f"Выполнение корутины: {coro}")
            return await coro

        async with self.lock:
            if not self.client.is_connected():
                logger.info("Клиент не подключен. Подключаем...")
                await self.start()
            if callable(coro):
                coro = coro()
            logger.info(f"Выполнение корутины: {coro}")
            return await coro


# Глобальная настройка менеджера клиента
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
session_name = "session_my_account"
telegram_manager = TelegramClientManager(session_name, api_id, api_hash)

# FastAPI приложение
app = FastAPI()

# Подключение роутера к приложению
app.include_router(tg_router)

@app.on_event("startup")
async def startup_event():
    await telegram_manager.start()
    telegram_manager.run_in_background()

@app.on_event("shutdown")
async def shutdown_event():
    await telegram_manager.stop()

client_data = {}


async def update_is_title_changed(chat: Chat, db: AsyncSession):
    """
    Обновляет поле is_title_changed для заданного чата.
    """
    result_history = await db.execute(
        select(ChatNameHistory)
        .where(ChatNameHistory.chat_id == chat.id)
        .order_by(ChatNameHistory.updated_at.desc())
    )
    has_name_change = result_history.scalars().first() is not None

    # Обновляем поле is_title_changed в таблице Chat
    await db.execute(
        update(Chat)
        .where(Chat.id == chat.id)
        .values(is_title_changed=has_name_change)
    )
    await db.commit()


async def save_in_batches(session: AsyncSession, objects, batch_size: int = 100):
    """
    Сохраняет объекты в базу данных пакетами.
    
    Args:
        session (AsyncSession): Асинхронная сессия SQLAlchemy.
        objects (List[Any]): Список объектов для сохранения.
        batch_size (int): Размер батча.
    """
    for i in range(0, len(objects), batch_size):
        batch = objects[i:i + batch_size]
        session.add_all(batch)
        await session.commit()


async def fetch_new_chats_periodically(db: AsyncSession, interval: int = 60):
    """
    Периодически проверяет новые чаты и добавляет их в базу данных.
    """
    while True:
        try:
            logger.info("Запуск проверки новых чатов...")
            dialogs = []
            
            # Загружаем все диалоги из Telegram
            async with telegram_manager.client:
                async for dialog in telegram_manager.client.iter_dialogs():
                    chat_id = dialog.id
                    title = dialog.name or "Без названия"
                    dialogs.append({"chat_id": chat_id, "title": title.strip()})

            # Получаем все существующие чаты из базы одним запросом
            chat_ids = [dialog["chat_id"] for dialog in dialogs]
            existing_chats_result = await db.execute(
                select(Chat).where(Chat.chat_id.in_(chat_ids))
            )
            existing_chats = {chat.chat_id: chat for chat in existing_chats_result.scalars()}

            # Списки для добавления и обновления чатов
            new_chats = []
            updated_chats = []
            chat_histories = []

            for dialog in dialogs:
                chat_id = dialog["chat_id"]
                title = dialog["title"]

                if not title:
                    logger.warning(f"Пропущен диалог с chat_id: {chat_id}, так как отсутствует название.")
                    continue

                existing_chat = existing_chats.get(chat_id)
                if existing_chat:
                    # Обновляем только если название изменилось
                    if existing_chat.title.strip().lower() != title.lower():
                        logger.info(f"Обновление названия чата: {existing_chat.title} -> {title}")
                        chat_histories.append(
                            ChatNameHistory(
                                chat_id=existing_chat.id,
                                old_title=existing_chat.title,
                                new_title=title,
                                updated_at=datetime.utcnow(),
                                is_title_changed=True,
                            )
                        )
                        existing_chat.title = title
                        existing_chat.last_updated = datetime.utcnow()
                        updated_chats.append(existing_chat)
                else:
                    # Добавляем новый чат
                    logger.info(f"Добавление нового чата: {title}")
                    new_chats.append(
                        Chat(
                            chat_id=chat_id,
                            title=title,
                            last_updated=datetime.utcnow(),
                            is_title_changed=False,
                        )
                    )

            # Сохранение данных в базу
            if new_chats:
                logger.info(f"Сохранение {len(new_chats)} новых чатов пакетами.")
                await save_in_batches(db, new_chats, batch_size=100)

            if updated_chats:
                logger.info(f"Сохранение {len(updated_chats)} обновлённых чатов пакетами.")
                await save_in_batches(db, updated_chats, batch_size=100)

            if chat_histories:
                logger.info(f"Сохранение {len(chat_histories)} записей истории чатов пакетами.")
                await save_in_batches(db, chat_histories, batch_size=100)

            logger.info("Проверка завершена.")
        except Exception as e:
            logger.error(f"Ошибка при обновлении чатов: {e}")
            await db.rollback()
        finally:
            # Ждём перед следующей проверкой
            await asyncio.sleep(interval)







@tg_router.get("/login", response_class=HTMLResponse, name="login")
async def login(response: Response):
    try:
        async with TelegramClient(session_name, api_id, api_hash) as client:
            user = await client.get_me()
            if user:
                auth_token = serializer.dumps({"authenticated": True})
                response = RedirectResponse(url="/success", status_code=303)
                response.set_cookie(key="auth_token", value=auth_token)
                return response
            else:
                return JSONResponse(content={"status": "Сессия не авторизована. Пожалуйста, выполните авторизацию заново."}, status_code=400)
    except Exception as e:
        logger.error(f"Ошибка при подключении с использованием сессии: {e}")
        return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)


@tg_router.post("/send_code", response_class=HTMLResponse)
async def send_code(request: Request):
    logger.info("Маршрут /send_code вызван.")

    form = await request.form()
    phone = form.get("phone")
    logger.info(f"Номер телефона: {phone}")

    if not phone:
        logger.error("Телефон не указан.")
        return JSONResponse(content={"status": "Введите номер телефона."}, status_code=400)

    try:
        # Отправка кода через Telethon
        sent_code = await telegram_manager.safe_call(lambda: telegram_manager.client.send_code_request(phone))
        logger.info(f"Код подтверждения отправлен: {sent_code.phone_code_hash}")

        # Сохраняем информацию о сессии
        client_data["phone_code_hash"] = sent_code.phone_code_hash
        client_data["phone"] = phone

        return RedirectResponse(url=f"/verify?phone={phone}", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при отправке кода: {e}")
        return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)



@tg_router.post("/verify_code", response_class=HTMLResponse)
async def verify_code(request: Request):
    form = await request.form()
    phone = form.get("phone")
    code = form.get("code")

    if not phone or not code:
        return JSONResponse(content={"status": "Введите номер телефона и код."}, status_code=400)

    phone_code_hash = client_data.get("phone_code_hash")
    if not phone_code_hash:
        return JSONResponse(content={"status": "Код не найден. Отправьте код снова."}, status_code=400)

    try:
        # Подтверждаем код
        await telegram_manager.safe_call(lambda: telegram_manager.client.sign_in(phone=phone, code=code))
        logger.info("Авторизация успешна!")

        auth_token = serializer.dumps({"authenticated": True})
        response = RedirectResponse(url=f"/success?phone={phone}", status_code=303)
        response.set_cookie(key="auth_token", value=auth_token)
        return response
    except Exception as e:
        logger.error(f"Ошибка при подтверждении кода: {e}")
        return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)


@tg_router.get("/success", response_class=HTMLResponse, name="success")
async def success_page(request: Request, db: AsyncSession = Depends(get_db)):
    auth_token = request.cookies.get("auth_token")
    if not auth_token:
        return HTMLResponse(content="Не авторизован.", status_code=401)

    try:
        data = serializer.loads(auth_token)
        if not data.get("authenticated", False):
            return HTMLResponse(content="Не авторизован.", status_code=401)
    except Exception:
        return HTMLResponse(content="Не авторизован.", status_code=401)

    chat_list = []

    try:
        # Подключение к Telegram API
        dialogs = []
        async with TelegramClient(session_name, api_id, api_hash) as client:
            logger.info("Получение списка чатов...")
            async for dialog in client.iter_dialogs():
                chat_id = str(dialog.id)  # Преобразуем в строку
                title = dialog.name or "Без названия"
                dialogs.append({"chat_id": chat_id, "title": title})

        # Обработка данных в транзакции
        async with db.begin():  # Убедимся, что транзакция завершится корректно
            for dialog in dialogs:
                chat_id = int(dialog["chat_id"])  # Приведение типа
                title = dialog["title"].strip()

                if not title:
                    logger.warning(f"Пропущен диалог с chat_id: {chat_id}, так как отсутствует название.")
                    continue

                # Проверяем существование чата
                result = await db.execute(
                    select(Chat).where(Chat.chat_id == chat_id).options(selectinload(Chat.history))
                )
                existing_chat = result.scalar_one_or_none()

                if existing_chat:
                    # Обновляем только если название изменилось
                    if existing_chat.title.strip().lower() != title.lower():
                        logger.info(f"Обновление названия чата: {existing_chat.title} -> {title}")
                        chat_history = ChatNameHistory(
                            chat_id=existing_chat.id,
                            old_title=existing_chat.title,
                            new_title=title,
                            updated_at=datetime.utcnow(),
                            is_title_changed=True,
                        )
                        existing_chat.title = title
                        db.add(chat_history)
                else:
                    # Добавление нового чата
                    logger.info(f"Добавление нового чата: {title}")
                    new_chat = Chat(
                        chat_id=chat_id,
                        title=title,
                        last_updated=datetime.utcnow(),
                        is_title_changed=False,
                    )
                    db.add(new_chat)

                # Определяем, отслеживается ли чат
                is_tracked = existing_chat.is_tracked if existing_chat else False

                # Добавляем в список для отображения
                chat_list.append({"id": chat_id, "title": title, "is_tracked": is_tracked})

        logger.info("Фиксация транзакции...")
        await db.commit()  # Сохраняем изменения

    except Exception as e:
        logger.error(f"Ошибка при обработке: {e}")
        return HTMLResponse(content="Ошибка получения чатов.", status_code=500)

    return templates.TemplateResponse("user/success.html", {"request": request, "chat_list": chat_list})







# Роут для отображения страницы подтверждения кода
@tg_router.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request):
    phone = request.query_params.get("phone")
    return templates.TemplateResponse("user/verify.html", {"request": request, "phone": phone})


# Дополнительные роуты
@tg_router.get("/logout", response_class=HTMLResponse)
async def logout(response: Response):
    response = RedirectResponse(url="/")
    response.delete_cookie("auth_token")
    return response


@tg_router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    auth_token = request.cookies.get("auth_token")
    is_authenticated = False
    if auth_token:
        try:
            data = serializer.loads(auth_token)
            is_authenticated = data.get("authenticated", False)
        except Exception:
            pass
    return templates.TemplateResponse('main/index.html', {"request": request, "is_authenticated": is_authenticated})


@tg_router.get("/register", response_class=HTMLResponse)
async def register(request: Request):
    try:
        #await init_db()  # Создаем таблицы, если их нет
        logger.info("Инициализация базы данных завершена.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        return HTMLResponse(content="Ошибка при инициализации базы данных.", status_code=500)

    return templates.TemplateResponse("user/authotg.html", {"request": request})





from fastapi.responses import RedirectResponse
from fastapi.requests import Request

@tg_router.post("/update_tracked")
async def update_tracked_chats(
    request: Request,
    chat_ids: list[int] = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Обновление статуса выбранных чатов (is_tracked).
    Устанавливает is_tracked=True для выбранных чатов и is_tracked=False для остальных.
    """
    try:
        # Устанавливаем is_tracked=True для выбранных чатов
        if chat_ids:
            await db.execute(
                update(Chat)
                .where(Chat.chat_id.in_(chat_ids))
                .values(is_tracked=True, last_updated=datetime.utcnow())
            )
        
        # Устанавливаем is_tracked=False для остальных чатов
        await db.execute(
            update(Chat)
            .where(~Chat.chat_id.in_(chat_ids))  # Чаты, не входящие в выбранные
            .values(is_tracked=False, last_updated=datetime.utcnow())
        )
        
        await db.commit()

    except Exception as e:
        logger.error(f"Ошибка обновления отслеживаемых чатов: {e}")
        return HTMLResponse(content="Ошибка обновления отслеживаемых чатов.", status_code=500)

    # Перенаправляем на главную страницу
    return RedirectResponse(url="/", status_code=302)


@tg_router.api_route("/all_chat", methods=["GET", "POST"], response_class=HTMLResponse)
async def all_chat(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Обрабатывает отображение и обновление статуса отслеживания чатов.
    """
    if request.method == "GET":
        # Обработка GET-запроса: отображение списка чатов
        try:
            result = await db.execute(select(Chat))
            chats = result.scalars().all()
            chat_list = [
                {
                    "id": chat.id,
                    "chat_id": chat.chat_id,
                    "title": chat.title,
                    "is_tracked": chat.is_tracked,
                    "is_title_changed": chat.is_title_changed,
                }
                for chat in chats
            ]
            return templates.TemplateResponse(
                "post/all_chat.html", 
                {"request": request, "chat_list": chat_list}
            )
        except Exception as e:
            logger.error(f"Ошибка при загрузке списка чатов: {e}")
            return HTMLResponse(content="Ошибка загрузки списка чатов.", status_code=500)

    elif request.method == "POST":
        # Обработка POST-запроса: обновление статуса отслеживания
        try:
            form = await request.form()
            selected_chat_ids = form.getlist("chat_ids")
            selected_chat_ids = [int(chat_id) for chat_id in selected_chat_ids]

            # Устанавливаем is_tracked=True для выбранных чатов
            if selected_chat_ids:
                await db.execute(
                    update(Chat)
                    .where(Chat.id.in_(selected_chat_ids))
                    .values(is_tracked=True, last_updated=datetime.utcnow())
                )

            # Устанавливаем is_tracked=False для остальных чатов
            await db.execute(
                update(Chat)
                .where(~Chat.id.in_(selected_chat_ids))
                .values(is_tracked=False, last_updated=datetime.utcnow())
            )

            # Фиксируем изменения
            await db.commit()

            return RedirectResponse(url="/all_chat", status_code=303)
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса чатов: {e}")
            return HTMLResponse(content="Ошибка обновления статуса чатов.", status_code=500)








@tg_router.get("/chat_is_tracked", response_class=HTMLResponse)
async def get_tracked_chats(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Возвращает страницу со списком чатов, где is_tracked=True,
    и обновляет информацию о смене названия.
    """
    try:
        # Извлекаем чаты с is_tracked=True
        result = await db.execute(select(Chat).where(Chat.is_tracked == True))
        tracked_chats = result.scalars().all()

        # Список для обновления статуса is_title_changed
        updated_chats = []

        for chat in tracked_chats:
            # Проверяем, есть ли записи об изменении названия
            result_history = await db.execute(
                select(ChatNameHistory)
                .where(ChatNameHistory.chat_id == chat.id)
                .order_by(ChatNameHistory.updated_at.desc())
            )
            has_name_change = result_history.scalars().first() is not None

            # Обновляем поле is_title_changed, если оно изменилось
            if chat.is_title_changed != has_name_change:
                chat.is_title_changed = has_name_change
                updated_chats.append(chat)

        # Сохраняем обновления в базе данных одним коммитом
        if updated_chats:
            db.add_all(updated_chats)
            await db.commit()

        # Создаём список чатов для отображения
        chat_list = [
            {
                "id": chat.id,
                "chat_id": chat.chat_id,
                "title": chat.title,
                "is_tracked": chat.is_tracked,
                "is_title_changed": chat.is_title_changed,
            }
            for chat in tracked_chats
        ]

        # Передаём данные в шаблон
        return templates.TemplateResponse(
            "post/chat_is_tracked.html",
            {"request": request, "chat_list": chat_list}
        )
    except Exception as e:
        logger.error(f"Ошибка при загрузке отслеживаемых чатов: {e}")
        return HTMLResponse(content="Ошибка загрузки отслеживаемых чатов.", status_code=500)



@tg_router.get("/chat_name_history", response_class=HTMLResponse)
async def chat_name_history_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    date_from: str = None,
    date_to: str = None,
    chat_id: str = None  # Новый параметр для фильтрации по ID чата
):
    """
    Отображение страницы с историей изменённых чатов.

    Аргументы:
        request (Request): Запрос FastAPI.
        db (AsyncSession): Сессия базы данных.
        date_from (str): Дата начала фильтрации в формате ISO (опционально).
        date_to (str): Дата окончания фильтрации в формате ISO (опционально).
        chat_id (str): ID чата для фильтрации (опционально).

    Возвращает:
        HTMLResponse: Шаблон с историей изменённых чатов.
    """
    try:
        # Преобразуем даты из строки в datetime (если указаны)
        date_from_dt = datetime.fromisoformat(date_from) if date_from else None
        date_to_dt = datetime.fromisoformat(date_to) if date_to else None

        # Создаём запрос к таблицам Chat и ChatNameHistory
        query = (
            select(Chat, ChatNameHistory)
            .join(ChatNameHistory, Chat.id == ChatNameHistory.chat_id)
            .where(ChatNameHistory.is_title_changed == True)
        )

        # Добавляем фильтрацию по дате, если указано
        if date_from_dt:
            query = query.where(ChatNameHistory.updated_at >= date_from_dt)
        if date_to_dt:
            query = query.where(ChatNameHistory.updated_at <= date_to_dt)

        # Добавляем фильтрацию по chat_id, если указано
        if chat_id:
            query = query.where(Chat.chat_id == chat_id)

        # Выполняем запрос
        result = await db.execute(query)
        rows = result.all()

        # Формируем список истории чатов для шаблона
        chat_history_list = [
            {
                "chat_id": chat.chat_id,
                "current_title": chat.title,
                "old_title": history.old_title,
                "new_title": history.new_title,
                "updated_at": history.updated_at,
                "is_tracked": chat.is_tracked,
            }
            for chat, history in rows
        ]

        # Передаём данные в шаблон
        return templates.TemplateResponse(
            "post/chat_name_history.html",
            {
                "request": request,
                "chat_history_list": chat_history_list,
                "date_from": date_from,
                "date_to": date_to,
                "chat_id": chat_id,  # Передаём chat_id в шаблон
            },
        )

    except Exception as e:
        logger.error(f"Ошибка при загрузке истории чатов: {e}")
        return HTMLResponse(content="Ошибка загрузки истории чатов.", status_code=500)







