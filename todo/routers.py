from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from todo.database.base import get_db
from todo.models import ToDo
from todo.config import settings

import asyncio
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory='todo/templates')


# Роут для страницы регистрации
@router.get("/register", response_class=HTMLResponse)
async def register(request: Request):
    return templates.TemplateResponse("user/authotg.html", {"request": request})

# Домашняя страница
@router.get('/', response_class=HTMLResponse)
async def home(request: Request, db_session: AsyncSession = Depends(get_db)):
    result = await db_session.execute(select(ToDo))
    todos = result.scalars().all()
    return templates.TemplateResponse('main/index.html', {
        'request': request,
        'app_name': settings.app_name,
        'todo_list': todos
    })

# Роут для добавления задачи
@router.post('/add', response_class=RedirectResponse)
async def add(title: str = Form(...), db_session: AsyncSession = Depends(get_db)):
    new_todo = ToDo(title=title)
    db_session.add(new_todo)
    await db_session.commit()
    return RedirectResponse(url=router.url_path_for('home'), status_code=303)

# Роут для обновления статуса задачи
@router.get('/update/{todo_id}', response_class=RedirectResponse)
async def update(todo_id: int, db_session: AsyncSession = Depends(get_db)):
    result = await db_session.execute(select(ToDo).where(ToDo.id == todo_id))
    todo = result.scalar_one_or_none()
    if todo:
        todo.is_complete = not todo.is_complete
        await db_session.commit()
    return RedirectResponse(url=router.url_path_for('home'), status_code=302)

# Роут для удаления задачи
@router.get('/delete/{todo_id}', response_class=RedirectResponse)
async def delete(todo_id: int, db_session: AsyncSession = Depends(get_db)):
    result = await db_session.execute(select(ToDo).where(ToDo.id == todo_id))
    todo = result.scalar_one_or_none()
    if todo:
        await db_session.delete(todo)
        await db_session.commit()
    return RedirectResponse(url=router.url_path_for('home'), status_code=302)
