from fastapi import APIRouter, Request, Form, Response, Depends
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeExpired, FloodWait
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import logging
from dotenv import load_dotenv
from itsdangerous import URLSafeSerializer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload
from todo.database.base import get_db
from todo.models import Chat, ChatNameHistory
from datetime import datetime
from dotenv import set_key
from sqlalchemy.orm import selectinload


# Настройка логирования
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

# Загрузка переменных из файла .env
load_dotenv()

SECRET_KEY = "7632972461643986eqwqewq1231231"  # Замените на свой секретный ключ
serializer = URLSafeSerializer(SECRET_KEY)

tg_router = APIRouter()
templates = Jinja2Templates(directory='todo/templates')

# Глобальный клиент Pyrogram
client = None
session_name = "session_my_account"
session_directory = os.getcwd()  # Используем текущий рабочий каталог
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
client_data = {}

# Функция для создания клиента с сессией на диске
def create_client():
    global client
    if api_id is None or api_hash is None:
        raise ValueError("api_id и api_hash должны быть установлены перед созданием клиента.")
    session_path = os.path.join(session_directory, session_name)
    client = Client(session_path, api_id=int(api_id), api_hash=api_hash)

# Новый роут для входа с использованием существующей сессии
@tg_router.get("/login", response_class=HTMLResponse, name="login")
async def login(response: Response):
    global client

    # Проверяем, существует ли файл сессии
    session_path = os.path.join(session_directory, f"{session_name}.session")
    if not os.path.exists(session_path):
        return JSONResponse(content={"status": "Файл сессии не найден. Пожалуйста, сначала зарегистрируйтесь."}, status_code=400)

    # Создаем клиента с использованием сессии
    if client is None:
        create_client()

    try:
        # Подключаемся используя существующую сессию
        await client.connect()
        user = await client.get_me()
        if user:
            # Устанавливаем куки для авторизованного пользователя
            auth_token = serializer.dumps({"authenticated": True})
            response = RedirectResponse(url="/success", status_code=303)
            response.set_cookie(key="auth_token", value=auth_token)
            return response
        else:
            return JSONResponse(content={"status": "Сессия не авторизована. Пожалуйста, выполните авторизацию заново."}, status_code=400)
    except Exception as e:
        logger.error(f"Ошибка при подключении с использованием сессии: {e}")
        return JSONResponse(content={"status": f"Ошибка при подключении с использованием сессии: {str(e)}"}, status_code=500)

# Роут для отправки кода на указанный номер телефона
@tg_router.post("/send_code", response_class=HTMLResponse)
async def send_code(request: Request):
    global client_data, api_id, api_hash
    form = await request.form()
    phone = form.get("phone")
    api_id = form.get("api_id")
    api_hash = form.get("api_hash")

    if phone and api_id and api_hash:
        # Функция для обновления значения в .env файле
        def update_env(key, value):
            set_key(".env", key, value)

        # Использование функции update_env для сохранения API_ID и API_HASH
        update_env("API_ID", api_id)
        update_env("API_HASH", api_hash)
        
        # Сохраняем API_ID и API_HASH в файл .env
        #with open(".env", "w") as env_file:
        #    env_file.write(f"API_ID={api_id}\n")
        #    env_file.write(f"API_HASH={api_hash}\n")

        # Перезагружаем переменные окружения
        load_dotenv()

        # Создаем новый клиент с сохранением сессии на диске
        create_client()
        try:
            await client.connect()
            sent_code = await client.send_code(phone)
            phone_code_hash = sent_code.phone_code_hash  # Извлекаем phone_code_hash из объекта SentCode
            client_data = {"phone_code_hash": phone_code_hash, "phone": phone}
        except FloodWait as e:
            logger.error(f"Ошибка при отправке кода: {e}")
            return JSONResponse(content={"status": f"Ошибка при отправке кода: Пожалуйста, подождите {e.value} секунд, прежде чем повторить попытку."}, status_code=429)
        except Exception as e:
            logger.error(f"Ошибка при отправке кода: {e}")
            return JSONResponse(content={"status": f"Ошибка при отправке кода: {str(e)}"}, status_code=500)
        
        # Перенаправляем на страницу подтверждения кода
        return RedirectResponse(url=f"/verify?phone={phone}", status_code=303)
    else:
        return JSONResponse(content={"status": "Введите телефон, api_id и api_hash"}, status_code=400)

# Роут для подтверждения кода и завершения авторизации
@tg_router.post("/verify_code", response_class=HTMLResponse)
async def verify_code(request: Request):
    global client_data
    form = await request.form()
    phone = form.get("phone")
    code = form.get("code")

    if phone and code:
        if client is None:
            create_client()
            await client.connect()

        try:
            # Используем phone_code_hash при вызове метода sign_in
            phone_code_hash = client_data.get("phone_code_hash")
            if not phone_code_hash:
                return JSONResponse(content={"status": "Не удалось найти активную сессию. Пожалуйста, начните с отправки кода."}, status_code=400)

            await client.sign_in(phone_number=phone, phone_code=code, phone_code_hash=phone_code_hash)
            # Оставляем клиента подключенным после успешного входа
            client_data["authorized"] = True
            # Устанавливаем куки для авторизованного пользователя
            auth_token = serializer.dumps({"authenticated": True})
            response = RedirectResponse(url=f"/success?phone={phone}", status_code=303)
            response.set_cookie(key="auth_token", value=auth_token)
            return response
        except PhoneCodeExpired:
            return JSONResponse(content={"status": "Код подтверждения истек. Пожалуйста, запросите новый код."}, status_code=400)
        except SessionPasswordNeeded:
            return JSONResponse(content={"status": "Необходим пароль 2FA"}, status_code=400)
        except Exception as e:
            logger.error(f"Ошибка при подтверждении кода: {e}")
            return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)
    else:
        return JSONResponse(content={"status": "Введите все необходимые данные"}, status_code=400)

# Роут для страницы успешного входа и списка чатов
@tg_router.get("/success", response_class=HTMLResponse, name="success")
async def success_page(request: Request, db: AsyncSession = Depends(get_db)):
    is_authenticated = False
    auth_token = request.cookies.get("auth_token")
    if auth_token:
        try:
            data = serializer.loads(auth_token)
            is_authenticated = data.get("authenticated", False)
        except Exception:
            pass

    if client is None or not client.is_connected:
        return HTMLResponse(content="Не удалось найти сессию клиента. Пожалуйста, авторизуйтесь снова.", status_code=400)

    try:
        chat_list = []
        async for dialog in client.get_dialogs():
            chat_id = str(dialog.chat.id)
            title = dialog.chat.title or dialog.chat.first_name or "Без названия"

            # Проверяем, существует ли уже чат в базе данных
            result = await db.execute(
                select(Chat)
                .where(Chat.chat_id == chat_id)
                .options(selectinload(Chat.history))
            )
            existing_chat = result.scalar_one_or_none()


            if existing_chat:
                # Если название изменилось, добавляем в историю
                if existing_chat.title != title:
                    chat_history = ChatNameHistory(
                        chat_id=existing_chat.id,
                        old_title=existing_chat.title,
                        new_title=title,
                        updated_at=datetime.utcnow()
                    )
                    existing_chat.title = title
                    existing_chat.last_updated = datetime.utcnow()
                    db.add(chat_history)
                    await db.commit()
            else:
                # Если чат не существует, добавляем его в базу данных
                new_chat = Chat(
                    chat_id=chat_id,
                    title=title,
                    last_updated=datetime.utcnow()
                )
                db.add(new_chat)
                await db.commit()

            chat_list.append({
                "index": len(chat_list) + 1,
                "id": chat_id,
                "title": title
            })
    except Exception as e:
        logger.error(f"Ошибка при получении чатов: {e}")
        return HTMLResponse(content=f"Ошибка при получении чатов: {str(e)}", status_code=500)

    return templates.TemplateResponse("user/success.html", {"request": request, "chat_list": chat_list, "is_authenticated": is_authenticated})

# Роут для отображения страницы подтверждения кода
@tg_router.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request):
    phone = request.query_params.get("phone")
    return templates.TemplateResponse("user/verify.html", {"request": request, "phone": phone})

# Роут для выхода из системы
@tg_router.get("/logout", response_class=HTMLResponse)
async def logout(response: Response):
    global client
    if client and client.is_connected:
        await client.disconnect()
    # Удаляем куки авторизации
    response = RedirectResponse(url="/")
    response.delete_cookie("auth_token")
    return response

# Роут для домашней страницы
@tg_router.get('/', response_class=HTMLResponse)
async def home(request: Request):
    is_authenticated = False
    auth_token = request.cookies.get("auth_token")
    if auth_token:
        try:
            data = serializer.loads(auth_token)
            is_authenticated = data.get("authenticated", False)
        except Exception:
            pass

    return templates.TemplateResponse('main/index.html', {
        'request': request,
        'app_name': 'ToDo FastAPI - Твой менеджер задач',
        'is_authenticated': is_authenticated
    })

# Роут для страницы регистрации
@tg_router.get("/register", response_class=HTMLResponse)
async def register(request: Request):
    return templates.TemplateResponse("user/authotg.html", {"request": request})
