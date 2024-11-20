from todo.database.base import Base  # Используем Base из base.py
from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, TIMESTAMP, event
from sqlalchemy.orm import relationship, Session
from sqlalchemy.sql import func
from contextvars import ContextVar
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, object_session
from sqlalchemy import BigInteger

# Настройка логирования
logger = logging.getLogger(__name__)

# Локальный флаг для предотвращения рекурсии
sync_in_progress = ContextVar("sync_in_progress", default=False)

class Chat(Base):
    __tablename__ = "chats"

    id = Column(BigInteger, primary_key=True, index=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    title = Column(String, nullable=True)
    last_updated = Column(TIMESTAMP(timezone=True), server_default=func.now())
    is_title_changed = Column(Boolean, default=False)
    is_tracked = Column(Boolean, default=False)
    history = relationship("ChatNameHistory", back_populates="chat")


class ChatNameHistory(Base):
    __tablename__ = "chat_name_history"

    id = Column(BigInteger, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("chats.id"), nullable=False)
    old_title = Column(String, nullable=True)
    new_title = Column(String, nullable=True)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    is_title_changed = Column(Boolean, default=True)
    is_tracked = Column(Boolean, default=False)
    chat = relationship("Chat", back_populates="history")


# Событие: Синхронизация is_tracked
@event.listens_for(Chat, "after_update")
def sync_chat_to_history(mapper, connection, target):
    logger.info(f"Triggered sync_chat_to_history for Chat ID {target.id}")
    if sync_in_progress.get():
        return
    try:
        sync_in_progress.set(True)
        # Обновляем данные, используя connection
        connection.execute(
            ChatNameHistory.__table__.update()
            .where(ChatNameHistory.chat_id == target.id)
            .values(is_tracked=target.is_tracked)
        )
    finally:
        sync_in_progress.set(False)


# Событие: Синхронизация is_title_changed
@event.listens_for(ChatNameHistory, "after_update")
def sync_history_to_chat(mapper, connection, target):
    logger.info(f"Triggered sync_history_to_chat for ChatNameHistory ID {target.id}")
    if sync_in_progress.get():
        return
    try:
        sync_in_progress.set(True)
        if target.is_title_changed:
            # Обновляем данные, используя connection
            connection.execute(
                Chat.__table__.update()
                .where(Chat.id == target.chat_id)
                .values(is_title_changed=True)
            )
    finally:
        sync_in_progress.set(False)
