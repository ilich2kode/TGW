from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import os
from todo.pyrog.tgbot import tg_router

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
app.include_router(tg_router) # Включите router в ваше приложение

# Пример маршрута
@app.get("/")
async def read_root(request: Request):  # Добавьте параметр request
    return templates.TemplateResponse("index.html", {"request": request})  # Используйте request

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000, reload=True)
