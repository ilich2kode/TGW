from fastapi import APIRouter, Request, Form, Response, Depends, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from fastapi.requests import Request

from telethon.tl.types import PeerUser, PeerChannel
from telethon.errors import ChatAdminRequiredError, UserNotParticipantError
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from fastapi import Depends

from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload
from typing import List
from sqlalchemy import update
from sqlalchemy.sql.expression import update, delete
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.expression import bindparam
from sqlalchemy.sql import text
from sqlalchemy.util import await_only
from sqlalchemy.sql import func


from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from dotenv import load_dotenv, set_key
from itsdangerous import URLSafeSerializer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from todo.database.base import get_db
from todo.database.base import SessionLocal  # Импорт существующей фабрики сессий

from todo.models import Chat, ChatNameHistory, TrackedChat, Message, MessageEdit
from datetime import datetime, timedelta, timezone
from tzlocal import get_localzone
import asyncio
import os
import logging
from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Загрузка переменных из файла .env
load_dotenv()

SECRET_KEY = "7632972461643986eqwqewq1231231"  # Секретный ключ
serializer = URLSafeSerializer(SECRET_KEY)

tg_router = APIRouter()
templates = Jinja2Templates(directory='todo/templates')

# FastAPI приложение
app = FastAPI()

# Подключение роутера к приложению
app.include_router(tg_router)









# Telegram Client Manager
class TelegramClientManager:
    def __init__(self, session_name, api_id, api_hash):
        self.client = TelegramClient(session_name, api_id=api_id, api_hash=api_hash)
        self.lock = asyncio.Lock()

    async def start(self):
        if not self.client.is_connected():
            await self.client.connect()
            print(f"-Запуск ТГ и соединение из класса TelegramClientManager, сессия: {session_name}")
            logger.info("Telegram client connected.")
            # Проверяем авторизацию
            if not await self.client.is_user_authorized():
                logger.info("Клиент не авторизован.")

    async def stop(self):
        if self.client.is_connected():
            await self.client.disconnect()
            print("Выкл ТГ и разрыв соединения из класса TelegramClientManager")
            logger.info("Telegram client disconnected.")

    async def safe_call(self, coro):
        async with self.lock:
            if not self.client.is_connected():
                logger.info("Клиент не подключен. Подключаем...")
                print("проверка подключения не подключены из класса TelegramClientManager")
                await self.start()
                print("соединение восстановлено из класса TelegramClientManager")
            if callable(coro):
                coro = coro()
            logger.info(f"Выполнение корутины: {coro}")
            print(f"Выполнение корутины: {coro} из класса TelegramClientManager")
            return await coro




# Глобальная настройка менеджера клиента
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
session_name = "session_my_account"
telegram_manager = TelegramClientManager(session_name, api_id, api_hash)

client_data = {}

###############################################################################


async def sync_chats_with_db(session: AsyncSession, telegram_manager: TelegramClientManager):
    """
    Синхронизация чатов из Telegram с базой данных.
    """
    print("Начинается синхронизация чатов...")
    try:
        # Получение всех чатов из Telegram
        dialogs = await telegram_manager.safe_call(telegram_manager.client.get_dialogs)
        telegram_chats = {dialog.id: getattr(dialog, 'title', 'Без названия') for dialog in dialogs}

        # Загрузка всех chat_id и title из базы данных
        existing_chats_query = await session.execute(select(Chat.chat_id, Chat.title))
        existing_chats = {row.chat_id: row.title for row in existing_chats_query.fetchall()}

        # Обновление существующих записей
        for chat_id, new_title in telegram_chats.items():
            if chat_id in existing_chats and existing_chats[chat_id] != new_title:
                # Обновляем название чата
                await session.execute(
                    update(Chat)
                    .where(Chat.chat_id == chat_id)
                    .values(title=new_title, last_updated=datetime.utcnow())
                )
                print(f"Обновлено название чата: {existing_chats[chat_id]} -> {new_title} (ID: {chat_id})")

        # Добавление новых записей
        new_chats = [
            Chat(chat_id=chat_id, title=title, last_updated=datetime.utcnow(), is_tracked=True)
            for chat_id, title in telegram_chats.items()
            if chat_id not in existing_chats
        ]
        if new_chats:
            session.add_all(new_chats)
            print(f"Добавлено новых чатов: {len(new_chats)}")

        # Фиксация всех изменений
        await session.commit()
        print("Синхронизация чатов завершена.")

    except Exception as e:
        await session.rollback()  # Откат транзакции при ошибке
        print(f"Ошибка при синхронизации чатов: {e}")
        raise




###############################################################################


MESSAGE_FETCH_LIMIT = 50

def prepare_message_data(chat_id, message):
    """
    Подготавливает данные сообщения для вставки в базу данных.
    """
    def get_from_id(from_id):
        """Извлекает идентификатор из объекта PeerUser или PeerChannel."""
        if from_id is None:
            return None
        if isinstance(from_id, (PeerUser, PeerChannel)):
            return from_id.user_id if hasattr(from_id, 'user_id') else from_id.channel_id
        return from_id

    return {
        "chat_id": chat_id,
        "message_id": message.id,
        "unique_message": Message.generate_combined(chat_id, message.id),
        "from_id": get_from_id(message.from_id),
        "text": message.message or "",
        "date": message.date,
        "edit_date": message.edit_date,
        "reply_to": message.reply_to_msg_id,
        "is_forward": bool(message.fwd_from),
        "is_reply": bool(message.reply_to_msg_id),
        "is_pinned": message.pinned,
        "post_author": message.post_author,
        "grouped_id": message.grouped_id,
        "has_media": bool(message.media),
    }
    
    

async def fetch_missing_messages(session):
    print("Инициализация: Проверка и загрузка недостающих сообщений")
    error_log_path = "chat_access_errors.txt"

    try:
        # Получение всех отслеживаемых чатов
        tracked_chats = await session.execute(TrackedChat.__table__.select())
        tracked_chats = tracked_chats.fetchall()

        if not tracked_chats:
            print("Нет отслеживаемых чатов.")
            return

        print(f"Найдено {len(tracked_chats)} отслеживаемых чатов.")

        for chat in tracked_chats:
            chat_id = chat.chat_id
            print(f"Проверка сообщений для чата: {chat_id}")

            try:
                entity = await telegram_manager.client.get_entity(chat_id)
                chat_title = getattr(entity, 'title', 'Без названия')
                print(f"Доступ к чату {chat_id} есть: {chat_title}")
            except (ChatAdminRequiredError, UserNotParticipantError) as e:
                error_message = f"[{datetime.now()}] Ошибка доступа к чату {chat_id}: {e}\n"
                print(error_message.strip())
                try:
                    with open(error_log_path, "a") as file:
                        file.write(error_message)
                except Exception as log_error:
                    print(f"Ошибка записи в лог файл: {log_error}")
                continue

            # Получаем последнее сообщение
            last_message = await session.execute(
                Message.__table__.select()
                .where(Message.chat_id == chat_id)
                .order_by(Message.message_id.desc())
                .limit(1)
            )
            last_message = last_message.first()

            # Обрабатываем корректно None
            min_id = last_message.message_id + 1 if last_message else 0
            print(f"Загрузка сообщений для чата {chat_id} начиная с ID: {min_id}")

            messages = []

            async def fetch_messages():
                async for message in telegram_manager.client.iter_messages(
                    chat_id, limit=MESSAGE_FETCH_LIMIT, min_id=min_id
                ):
                    messages.append(prepare_message_data(chat_id, message))

            await telegram_manager.safe_call(fetch_messages)

            if messages:
                try:
                    insert_stmt = pg_insert(Message).values(messages).on_conflict_do_nothing(
                        index_elements=["unique_message"]
                    )
                    await session.execute(insert_stmt)
                    print(f"Сообщения для чата {chat_id} успешно сохранены.")
                except Exception as e:
                    print(f"Ошибка сохранения сообщений для чата {chat_id}: {e}")

        await session.commit()
    except Exception as e:
        print(f"Ошибка при загрузке недостающих сообщений: {e}")
        await session.rollback()


async def setup_message_handler(session):
    """
    Настраивает обработчик для отслеживания событий сообщений.
    """
    print("Запуск обработчиков сообщений")

    @telegram_manager.client.on(events.NewMessage())
    async def new_message_handler(event):
        async with SessionLocal() as session:
            try:
                chat_id = event.chat_id
                message = event.message
                print(f"Новое сообщение из чата {chat_id}: {message.id}")

                tracked_chat = await session.execute(
                    TrackedChat.__table__.select().where(TrackedChat.chat_id == chat_id)
                )
                tracked_chat = tracked_chat.scalar_one_or_none()

                if not tracked_chat:
                    print(f"Чат {chat_id} не отслеживается. Игнорирование сообщения.")
                    return

                message_data = prepare_message_data(chat_id, message)
                insert_stmt = pg_insert(Message).values(message_data).on_conflict_do_nothing(
                    index_elements=["unique_message"]
                )
                await session.execute(insert_stmt)
                await session.commit()
                print(f"Сообщение {message.id} из чата {chat_id} успешно сохранено.")
            except Exception as e:
                print(f"Ошибка обработки нового сообщения: {e}")
                await session.rollback()


    @telegram_manager.client.on(events.MessageEdited())
    async def message_edited_handler(event):
        async with SessionLocal() as session:
            try:
                chat_id = event.chat_id
                message = event.message

                print(f"Изменено сообщение из чата {chat_id}: {message.id}")
                unique_message = Message.generate_combined(chat_id, message.id)

                # Корректный запрос для получения оригинального сообщения
                original_message = await session.execute(
                    select(Message).where(Message.unique_message == unique_message)
                )
                original_message = original_message.scalar_one_or_none()

                if not original_message:
                    print(f"Сообщение {unique_message} не найдено в базе. Добавляем как новое.")
                    
                    # Добавляем сообщение как новое, без записи об изменении
                    new_message = Message(
                        chat_id=chat_id,
                        message_id=message.id,
                        unique_message=unique_message,
                        from_id=message.from_id,
                        text=message.text,
                        date=message.date,
                        edit_date=message.edit_date,
                        has_media=message.media is not None,
                    )
                    session.add(new_message)
                    await session.commit()
                    print(f"Новое сообщение {message.id} добавлено в базу.")
                    return

                # Сравниваем и обновляем данные только если сообщение существовало
                old_text = original_message.text
                new_text = message.text
                has_media_changed = original_message.has_media != (message.media is not None)

                if old_text == new_text and not has_media_changed:
                    print(f"Изменений в тексте или медиа у сообщения {message.id} не найдено.")
                    return

                # Добавляем запись об изменении в MessageEdit
                edit_record = MessageEdit(
                    unique_message=unique_message,
                    old_date=original_message.date,
                    edit_date=message.edit_date or message.date,
                    old_text=old_text,
                    new_text=new_text,
                    has_media_changed=has_media_changed
                )
                session.add(edit_record)

                # Обновляем оригинальное сообщение
                original_message.text = new_text
                original_message.edit_date = message.edit_date or message.date
                original_message.has_media = message.media is not None
                session.add(original_message)

                await session.commit()
                print(f"Изменение сообщения {message.id} успешно сохранено.")
            except Exception as e:
                print(f"Ошибка обработки изменения сообщения: {e}")
                await session.rollback()


    @telegram_manager.client.on(events.MessageDeleted())
    async def message_deleted_handler(event):
        async with SessionLocal() as session:
            try:
                deleted_ids = event.deleted_ids
                chat_id = event.chat_id

                print(f"Удалены сообщения из чата {chat_id}: {deleted_ids}")
                for message_id in deleted_ids:
                    unique_message = Message.generate_combined(chat_id, message_id)

                    # Получаем оригинальное сообщение
                    original_message = await session.execute(
                        select(Message).where(Message.unique_message == unique_message)
                    )
                    original_message = original_message.scalar_one_or_none()

                    if not original_message:
                        print(f"Сообщение {unique_message} не найдено в базе.")
                        continue
                    
                    # Определяем локальную временную зону
                    local_tz = get_localzone()
                    # Получаем текущую дату и время в локальной зоне
                    local_time = datetime.now(local_tz)
                    # Определяем смещение от UTC
                    utc_offset = local_time.utcoffset()
                    
                    # Добавляем запись в MessageEdit, чтобы зафиксировать удаление
                    edit_record = MessageEdit(
                        unique_message=unique_message,
                        old_date=original_message.date,
                        edit_date=datetime.utcnow().replace(microsecond=0) + utc_offset,  # Используем скорректированное время
                        old_text=original_message.text,
                        new_text="Сообщение удалено",  # Указываем, что сообщение удалено
                        has_media_changed=original_message.has_media  # Сохраняем состояние медиа
                    )
                    session.add(edit_record)

                await session.commit()
                print(f"Обработка удаления сообщений {deleted_ids} завершена.")
            except Exception as e:
                print(f"Ошибка обработки удаления сообщения: {e}")
                await session.rollback()

        # Обработчик событий ChatAction: добавление нового чата и обновление названия существующего.
    
    @telegram_manager.client.on(events.ChatAction())
    async def handle_chat_action(event):
        """
        Обрабатывает события ChatAction: добавление нового чата и обновление названия существующего.
        """
        try:
            chat_id = event.chat_id
            new_title = event.chat.title if hasattr(event.chat, 'title') else None
            if not new_title:
                logger.warning(f"Название для чата {chat_id} отсутствует. Игнорирование.")
                return

            async with SessionLocal() as session:
                try:
                    # Проверяем, существует ли чат
                    result = await session.execute(
                        select(Chat).where(Chat.chat_id == chat_id)
                    )
                    chat = result.scalar_one_or_none()

                    if not chat:
                        # Чат не существует, добавляем как новый
                        new_chat = Chat(
                            chat_id=chat_id,
                            title=new_title,
                            is_tracked=False,  # Чат добавляется, но не отслеживается
                            last_updated=func.now(),
                            is_title_changed=False
                        )
                        session.add(new_chat)
                        await session.flush()  # Обновляем сессию, чтобы получить id нового чата
                        logger.info(f"Новый чат с ID {chat_id} и названием '{new_title}' добавлен.")
                    else:
                        # Чат существует, проверяем необходимость обновления названия
                        if chat.title == new_title:
                            logger.info(f"Название чата {chat_id} не изменилось.")
                            return

                        old_title = chat.title
                        chat.title = new_title
                        chat.last_updated = func.now()
                        chat.is_title_changed = True
                        is_tracked = chat.is_tracked
                        logger.info(f"Название чата обновлено: {old_title} -> {new_title}")

                    # Логирование переменных перед записью в историю
                    logger.info(f"chat.id: {chat.id}")
                    logger.info(f"old_title: {old_title}")
                    logger.info(f"new_title: {new_title}")
                    logger.info(f"is_title_changed: True")
                    logger.info(f"is_tracked: {is_tracked}")

                    # Сохранение изменения в истории
                    history_record = ChatNameHistory(
                        chat_id=chat.id,  # Используем id, а не chat_id
                        old_title=old_title,
                        new_title=new_title,
                        is_title_changed=True,
                        is_tracked=is_tracked
                    )
                    session.add(history_record)
                    logger.info(f"Добавлена запись в историю для чата ID={chat.id}.")

                    # Явный коммит для фиксации всех изменений
                    await session.commit()
                    logger.info(f"Изменения зафиксированы в базе данных для чата ID={chat.id}.")
                except Exception as e:
                    logger.error(f"Ошибка внутри транзакции: {e}")
                    await session.rollback()
                    raise

        except SQLAlchemyError as e:
            logger.error(f"Ошибка работы с базой данных: {e}")
        except Exception as e:
            logger.error(f"Ошибка обработки события ChatAction: {e}")
        
###############################################################################



#Отвечает за авторизацию (нужно добавить проверок на сессию и на подключение)
@tg_router.get("/login", response_class=HTMLResponse, name="login")
async def login(response: Response, manager: TelegramClientManager = Depends(lambda: telegram_manager)):
    try:
        # Используем safe_call для безопасного вызова client.get_me
        user = await manager.safe_call(manager.client.get_me)
        
        if user:
            # Формируем сообщение с датой, временем и упоминанием роутера
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message_text = f"Сообщение из роутера /login\nДата и время: {current_time}"

            # Отправляем сообщение в "Избранное"
            await manager.safe_call(
                lambda: manager.client.send_message("me", message_text)
            )

            # Авторизация успешна, перенаправляем
            auth_token = serializer.dumps({"authenticated": True})
            redirect_response = RedirectResponse(url="/", status_code=303)
            redirect_response.set_cookie(key="auth_token", value=auth_token)
            return redirect_response
        else:
            # Сессия не авторизована
            return JSONResponse(
                content={"status": "Сессия не авторизована. Пожалуйста, выполните авторизацию заново."},
                status_code=400
            )
    except Exception as e:
        logger.error(f"Ошибка при подключении через TelegramClientManager: {e}")
        return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)




#Открывает страницу регистрации.
@tg_router.get("/register", response_class=HTMLResponse)
async def register(request: Request, manager: TelegramClientManager = Depends(lambda: telegram_manager)):
    try:
        await manager.safe_call(manager.start)  # Убедимся, что клиент подключен
        logger.info("Инициализация Telegram клиента завершена.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации Telegram клиента: {e}")
        return HTMLResponse(content="Ошибка при инициализации Telegram клиента.", status_code=500)

    return templates.TemplateResponse("user/authotg.html", {"request": request})

#Принимает номер телефона, отправляет код подтверждения и перенаправляет на /verify.
@tg_router.post("/send_code", response_class=HTMLResponse)
async def send_code(request: Request, manager: TelegramClientManager = Depends(lambda: telegram_manager)):
    logger.info("Маршрут /send_code вызван.")

    form = await request.form()
    phone = form.get("phone")
    logger.info(f"Номер телефона: {phone}")

    if not phone:
        logger.error("Телефон не указан.")
        return JSONResponse(content={"status": "Введите номер телефона."}, status_code=400)

    try:
        # Отправка кода через Telethon
        sent_code = await manager.safe_call(lambda: manager.client.send_code_request(phone))
        logger.info(f"Код подтверждения отправлен: {sent_code.phone_code_hash}")

        # Сохраняем информацию о сессии
        client_data["phone_code_hash"] = sent_code.phone_code_hash
        client_data["phone"] = phone

        return RedirectResponse(url=f"/verify?phone={phone}", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при отправке кода: {e}")
        return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)



# Показывает страницу ввода кода подтверждения с указанным номером телефона.
@tg_router.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request):
    phone = request.query_params.get("phone")
    return templates.TemplateResponse("user/verify.html", {"request": request, "phone": phone})



""" Принимает код подтверждения и авторизует пользователя. 
    Успешная авторизация перенаправляет на главную страницу."""
@tg_router.post("/verify_code", response_class=HTMLResponse)
async def verify_code(request: Request, manager: TelegramClientManager = Depends(lambda: telegram_manager)):
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
        await manager.safe_call(lambda: manager.client.sign_in(phone=phone, code=code))
        logger.info("Авторизация успешна!")

        # Устанавливаем cookie и перенаправляем на главную
        auth_token = serializer.dumps({"authenticated": True})
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="auth_token", value=auth_token)
        return response
    except Exception as e:
        logger.error(f"Ошибка при подтверждении кода: {e}")
        return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)





# Закрытие сессии Telegram, Перенаправляет пользователя на главную страницу
@tg_router.get("/logout", response_class=HTMLResponse)
async def logout(response: Response, manager: TelegramClientManager = Depends(lambda: telegram_manager)):
    try:
        # Закрываем сессию Telegram через TelegramClientManager
        await manager.safe_call(manager.stop)
        logger.info("Сессия Telegram успешно закрыта.")
    except Exception as e:
        logger.error(f"Ошибка при закрытии сессии Telegram: {e}")
        # Даже если ошибка произошла, продолжим выполнение, чтобы удалить cookie

    # Удаляем cookie авторизации и перенаправляем на главную страницу
    redirect_response = RedirectResponse(url="/", status_code=303)
    redirect_response.delete_cookie("auth_token")
    return redirect_response


#Переход на главную
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










#обращения к БД и вывод таблиц


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

            # Извлекаем все чаты для изменения
            result = await db.execute(select(Chat))
            chats = result.scalars().all()

            # Обновляем is_tracked для выбранных и остальных чатов
            for chat in chats:
                if chat.id in selected_chat_ids:
                    chat.is_tracked = True
                else:
                    chat.is_tracked = False
                chat.last_updated = datetime.utcnow()

            # Фиксируем изменения
            await db.commit()

            return RedirectResponse(url="/all_chat", status_code=303)
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса чатов: {e}")
            return HTMLResponse(content="Ошибка обновления статуса чатов.", status_code=500)





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


@tg_router.get("/tracked_chats", response_class=HTMLResponse)
async def tracked_chats_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = None,
    sort_by: str = "chat_id",  # Поле для сортировки
    order: str = "asc",       # Порядок сортировки: "asc" или "desc"
):
    """
    Отображение страницы с отслеживаемыми чатами.

    Аргументы:
        request (Request): Запрос FastAPI.
        db (AsyncSession): Сессия базы данных.
        search (str): Поисковый запрос для фильтрации чатов (опционально).
        sort_by (str): Поле для сортировки (по умолчанию chat_id).
        order (str): Порядок сортировки ("asc" или "desc").

    Возвращает:
        HTMLResponse: Шаблон с отслеживаемыми чатами.
    """
    try:
        # Создаём базовый запрос к таблице TrackedChat
        query = select(TrackedChat)

        # Добавляем фильтрацию по поисковому запросу, если указано
        if search:
            query = query.where(
                (TrackedChat.title.ilike(f"%{search}%")) |
                (TrackedChat.chat_id.ilike(f"%{search}%"))
            )

        # Добавляем сортировку
        if sort_by in ["chat_id", "title", "last_updated"]:
            order_by_field = getattr(TrackedChat, sort_by)
            query = query.order_by(order_by_field.asc() if order == "asc" else order_by_field.desc())

        # Выполняем запрос
        result = await db.execute(query)
        rows = result.all()

        # Формируем список чатов для шаблона
        chat_list = [
            {
                "chat_id": chat.chat_id,
                "title": chat.title,
                "last_updated": chat.last_updated,
                "is_title_changed": chat.is_title_changed,
            }
            for chat, in rows  # Кома обязательна, чтобы извлечь кортеж (TrackedChat,)
        ]

        # Передаём данные в шаблон
        return templates.TemplateResponse(
            "post/tracked_chats.html",
            {
                "request": request,
                "chat_history_list": chat_list,
                "search": search,
                "sort_by": sort_by,
                "order": order,
            },
        )

    except Exception as e:
        logger.error(f"Ошибка при загрузке отслеживаемых чатов: {e}")
        return HTMLResponse(content="Ошибка загрузки отслеживаемых чатов.", status_code=500)
