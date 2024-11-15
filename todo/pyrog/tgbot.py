from fastapi import APIRouter, Request, Form
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeExpired, FloodWait
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import logging

# Настройка логирования
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

tg_router = APIRouter()
templates = Jinja2Templates(directory='todo/templates')

# Глобальный клиент Pyrogram
client = None
session_name = "session_my_account"
api_id = None
api_hash = None
client_data = {}

# Функция для создания клиента с сессией на диске
def create_client():
    global client
    client = Client(session_name, api_id=int(api_id), api_hash=api_hash)

# Роут для отправки кода на указанный номер телефона
@tg_router.post("/send_code", response_class=HTMLResponse)
async def send_code(request: Request):
    global api_id, api_hash, client_data
    form = await request.form()
    phone = form.get("phone")
    api_id = form.get("api_id")
    api_hash = form.get("api_hash")

    if phone and api_id and api_hash:
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
            # Перенаправляем на страницу успешного входа
            return RedirectResponse(url=f"/success?phone={phone}", status_code=303)
        except PhoneCodeExpired:
            return JSONResponse(content={"status": "Код подтверждения истек. Пожалуйста, запросите новый код."}, status_code=400)
        except SessionPasswordNeeded:
            return JSONResponse(content={"status": "Необходим пароль 2FA"}, status_code=400)
        except Exception as e:
            logger.error(f"Ошибка при подтверждении кода: {e}")
            return JSONResponse(content={"status": f"Ошибка: {str(e)}"}, status_code=500)
    else:
        return JSONResponse(content={"status": "Введите все необходимые данные"}, status_code=400)

# Роут для отображения страницы подтверждения кода
@tg_router.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request):
    phone = request.query_params.get("phone")
    return templates.TemplateResponse("user/verify.html", {"request": request, "phone": phone})

# Роут для отображения страницы успешного входа и списка чатов
@tg_router.get("/success", response_class=HTMLResponse)
async def success_page(request: Request):
    if client is None or not client.is_connected:
        return HTMLResponse(content="Не удалось найти сессию клиента. Пожалуйста, авторизуйтесь снова.", status_code=400)

    try:
        chat_list = []
        async for dialog in client.get_dialogs():
            chat_list.append({
                "index": len(chat_list) + 1,
                "id": dialog.chat.id,
                "title": dialog.chat.title or dialog.chat.first_name or "Без названия"
            })
    except Exception as e:
        logger.error(f"Ошибка при получении чатов: {e}")
        return HTMLResponse(content=f"Ошибка при получении чатов: {str(e)}", status_code=500)

    return templates.TemplateResponse("user/success.html", {"request": request, "chat_list": chat_list})
