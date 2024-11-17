from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory='todo/templates')

# Роут для страницы регистрации
@router.get("/register", response_class=HTMLResponse)
async def register(request: Request):
    return templates.TemplateResponse("user/authotg.html", {"request": request})

# Домашняя страница
@router.get('/', response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse('main/index.html', {
        'request': request,
        'app_name': 'ToDo FastAPI - Твой менеджер задач',
        'todo_list': []
    })


