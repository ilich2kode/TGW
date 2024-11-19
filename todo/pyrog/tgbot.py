from fastapi import APIRouter, Request, Form, Response, Depends, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from fastapi import Depends


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

# Настройка логирования
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

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

    try:
        chat_list = []
        async with TelegramClient(session_name, api_id, api_hash) as client:
            logger.info("Получение списка чатов...")
            async for dialog in client.iter_dialogs():
                chat_id = str(dialog.id)
                title = dialog.name or "Без названия"

                # Проверяем наличие чата в базе
                result = await db.execute(
                    select(Chat).where(Chat.chat_id == chat_id).options(selectinload(Chat.history))
                )
                existing_chat = result.scalar_one_or_none()

                if existing_chat:
                    # Если название чата изменилось, обновляем
                    if existing_chat.title != title:
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
                    # Если чата нет, создаем его
                    new_chat = Chat(
                        chat_id=chat_id,
                        title=title,
                        last_updated=datetime.utcnow(),
                        is_title_changed=False,
                    )
                    db.add(new_chat)

                # Фиксируем изменения после обработки каждого чата
                await db.commit()

                chat_list.append({"id": chat_id, "title": title})

    except Exception as e:
        logger.error(f"Ошибка при получении чатов: {e}")
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


