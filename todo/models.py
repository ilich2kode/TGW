from sqlalchemy import (
    Column, String, Integer, Boolean, ForeignKey, TIMESTAMP, BigInteger, JSON, func, event, text
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.asyncio import AsyncSession
from contextvars import ContextVar
import logging
from todo.database.base import Base  # Используем Base из base.py
from sqlalchemy.dialects.postgresql import insert

# Настройка логирования
logger = logging.getLogger(__name__)

# Локальный флаг для предотвращения рекурсии
sync_in_progress = ContextVar("sync_in_progress", default=False)

# Модель Chat
class Chat(Base):
    __tablename__ = "chats"

    id = Column(BigInteger, primary_key=True, index=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    title = Column(String, nullable=True)
    last_updated = Column(TIMESTAMP(timezone=True), server_default=func.now())
    is_title_changed = Column(Boolean, default=False)
    is_tracked = Column(Boolean, default=False)

    # Связь с ChatNameHistory
    history = relationship("ChatNameHistory", back_populates="chat", cascade="all, delete-orphan")

    # Связь с Message
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")
    
    # Связь с TrackedChat (добавлено)
    tracked_chats = relationship("TrackedChat", back_populates="chat", cascade="all, delete-orphan")


# Модель ChatNameHistory
class ChatNameHistory(Base):
    __tablename__ = "chat_name_history"

    id = Column(BigInteger, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    old_title = Column(String, nullable=True)
    new_title = Column(String, nullable=True)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    is_title_changed = Column(Boolean, default=True)
    is_tracked = Column(Boolean, default=False)

    # Связь с Chat
    chat = relationship("Chat", back_populates="history")

class TrackedChat(Base):
    __tablename__ = "TrackedChat"

    id = Column(BigInteger, primary_key=True, index=True)  # Уникальный идентификатор
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False, unique=True)  # Уникальный chat_id
    title = Column(String, nullable=True)  # Название чата
    last_updated = Column(TIMESTAMP(timezone=True), server_default=func.now())  # Дата последнего обновления
    is_title_changed = Column(Boolean, default=False)  # Флаг изменения названия

    # Связь с таблицей Chat
    chat = relationship("Chat", back_populates="tracked_chats")


# Модель Message
class Message(Base):
    __tablename__ = "messages"

    id = Column(BigInteger, primary_key=True, index=True)  # Уникальный ID сообщения
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)  # Привязка к таблице chats
    message_id = Column(BigInteger, nullable=False)  # Неуникальный идентификатор сообщения
    unique_message = Column(String, nullable=False, unique=True, index=True)  # Поле для хранения комбинированного значения chat_id и message_id
    from_id = Column(BigInteger, nullable=True)  # ID отправителя
    text = Column(String, nullable=True)  # Текст сообщения
    date = Column(TIMESTAMP(timezone=True), nullable=False)  # Дата отправки
    edit_date = Column(TIMESTAMP(timezone=True), nullable=True)  # Дата редактирования
    reply_to = Column(BigInteger, nullable=True)  # Ответ на сообщение
    is_forward = Column(Boolean, default=False)  # Флаг пересылки
    is_reply = Column(Boolean, default=False)  # Флаг ответа
    has_media = Column(Boolean, default=False)  # Есть ли медиа в сообщении
    via_bot_id = Column(BigInteger, nullable=True)  # ID бота, через которого отправлено сообщение
    is_pinned = Column(Boolean, default=False)  # Закреплено ли сообщение
    post_author = Column(String, nullable=True)  # Автор поста
    grouped_id = Column(BigInteger, nullable=True)  # Группировка медиа

    # Связь с таблицей chats
    chat = relationship("Chat", back_populates="messages")

    # Связь с MessageEdit
    edits = relationship("MessageEdit", back_populates="message", cascade="all, delete-orphan")

    # Генерация комбинированного поля
    @staticmethod
    def generate_combined(chat_id, message_id):
        return f"{chat_id}_{message_id}"

    # Метод, который выполняется перед сохранением
    @validates('chat_id', 'message_id')
    def update_combined_field(self, key, value):
        setattr(self, key, value)
        if self.chat_id is not None and self.message_id is not None:
            self.unique_message = self.generate_combined(self.chat_id, self.message_id)
        return value


# Модель MessageEdit
class MessageEdit(Base):
    __tablename__ = "message_edits"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)  # Уникальный идентификатор записи
    unique_message = Column(String, ForeignKey("messages.unique_message", ondelete="CASCADE"), nullable=False)  # Привязка к сообщениям по уникальному идентификатору
    old_date = Column(TIMESTAMP(timezone=True), nullable=False)  # Дата и время старого сообщения
    edit_date = Column(TIMESTAMP(timezone=True), nullable=False)  # Дата и время редактирования
    old_text = Column(String, nullable=True)  # Старый текст сообщения
    new_text = Column(String, nullable=True)  # Новый текст сообщения
    has_media_changed = Column(Boolean, default=False)  # Изменение состояния наличия медиа

    # Связь с таблицей Message
    message = relationship("Message", back_populates="edits")




@event.listens_for(Chat, "after_update")
def sync_chat_events(mapper, connection, target):
    logger.info(f"Triggered sync_chat_events for Chat ID {target.id}")
    
    if sync_in_progress.get():
        logger.info("Sync already in progress. Skipping.")
        return
    
    try:
        sync_in_progress.set(True)
        logger.info(f"Preparing to sync: chat_id={target.chat_id}, title={target.title}")
        
        if target.is_tracked:
            logger.info(f"Updating/inserting TrackedChat for chat_id {target.chat_id}")
            
            insert_stmt = insert(TrackedChat.__table__).values(
                chat_id=target.chat_id,
                title=target.title,
                last_updated=target.last_updated,
                is_title_changed=target.is_title_changed
            )
            update_stmt = insert_stmt.on_conflict_do_update(
                index_elements=["chat_id"],
                set_={
                    "title": target.title,
                    "last_updated": target.last_updated,
                    "is_title_changed": target.is_title_changed,
                }
            )
            result = connection.execute(update_stmt)
            logger.info(f"Rows affected in TrackedChat: {result.rowcount}")
        else:
            logger.info(f"Deleting TrackedChat for chat_id {target.chat_id}")
            result = connection.execute(
                TrackedChat.__table__.delete()
                .where(TrackedChat.chat_id == target.chat_id)
            )
            logger.info(f"Rows deleted from TrackedChat: {result.rowcount}")
        
        connection.commit()
    finally:
        sync_in_progress.set(False)
        logger.info("Sync complete.")








@event.listens_for(ChatNameHistory, "after_update")
def sync_history_to_chat(mapper, connection, target):
    logger.info(f"Triggered sync_history_to_chat for ChatNameHistory ID {target.id}")
    
    if sync_in_progress.get():
        return
    
    try:
        sync_in_progress.set(True)
        
        if target.is_title_changed:
            logger.info(f"Updating Chat and TrackedChat for chat_id {target.chat_id}")
            
            # Обновление таблицы Chat
            connection.execute(
                Chat.__table__.update()
                .where(Chat.id == target.chat_id)
                .values(is_title_changed=True)
            )
            
            # Обновление таблицы TrackedChat
            result = connection.execute(
                TrackedChat.__table__.update()
                .where(TrackedChat.chat_id == target.chat_id)
                .values(
                    title=target.new_title,
                    is_title_changed=True
                )
            )
            logger.info(f"Rows updated in TrackedChat: {result.rowcount}")
    finally:
        sync_in_progress.set(False)











