from sqlalchemy import (
    Column, String, Integer, Boolean, ForeignKey, TIMESTAMP, BigInteger, JSON, func, event, text
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.asyncio import AsyncSession
from contextvars import ContextVar
import logging
from todo.database.base import Base  # Используем Base из base.py

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
    from_id = Column(BigInteger, nullable=True)  # ID отправителя
    text = Column(String, nullable=True)  # Текст сообщения
    date = Column(TIMESTAMP(timezone=True), nullable=False)  # Дата отправки
    edit_date = Column(TIMESTAMP(timezone=True), nullable=True)  # Дата редактирования
    reply_to = Column(BigInteger, nullable=True)  # Ответ на сообщение
    is_forward = Column(Boolean, default=False)  # Флаг пересылки
    is_reply = Column(Boolean, default=False)  # Флаг ответа
    media = Column(JSON, nullable=True)  # JSON с вложениями
    via_bot_id = Column(BigInteger, nullable=True)  # ID бота, через которого отправлено сообщение
    is_pinned = Column(Boolean, default=False)  # Закреплено ли сообщение
    post_author = Column(String, nullable=True)  # Автор поста
    grouped_id = Column(BigInteger, nullable=True)  # Группировка медиа

    # Связь с таблицей chats
    chat = relationship("Chat", back_populates="messages")

    # Связь с MessageEdit
    edits = relationship("MessageEdit", back_populates="message", cascade="all, delete-orphan")


# Модель MessageEdit
class MessageEdit(Base):
    __tablename__ = "message_edits"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)  # Уникальный идентификатор записи
    message_id = Column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)  # Привязка к сообщениям
    old_date = Column(TIMESTAMP(timezone=True), nullable=False)  # Дата и время старого сообщения
    edit_date = Column(TIMESTAMP(timezone=True), nullable=False)  # Дата и время редактирования
    old_text = Column(String, nullable=True)  # Старый текст сообщения
    new_text = Column(String, nullable=True)  # Новый текст сообщения
    old_media = Column(JSON, nullable=True)  # Старое вложение (если изменилось)
    new_media = Column(JSON, nullable=True)  # Новое вложение (если изменилось)

    # Связь с таблицей Message
    message = relationship("Message", back_populates="edits")


# События SQLAlchemy для синхронизации
from sqlalchemy.dialects.postgresql import insert

@event.listens_for(Chat, "after_update")
def sync_chat_events(mapper, connection, target):
    """
    Объединяет логику синхронизации данных между Chat, ChatNameHistory и TrackedChat.
    """
    logger.info(f"Triggered sync_chat_events for Chat ID {target.id}")
    print(f"Triggered sync_chat_events for Chat ID {target.id}")
    
    if sync_in_progress.get():
        logger.info("Sync already in progress. Skipping.")
        print("Sync already in progress. Skipping.")
        return
    
    try:
        sync_in_progress.set(True)
        
        # Синхронизация с ChatNameHistory
        logger.info(f"Updating ChatNameHistory for chat_id {target.id} with is_tracked={target.is_tracked}")
        print(f"Updating ChatNameHistory for chat_id {target.id} with is_tracked={target.is_tracked}")
        result_history = connection.execute(
            ChatNameHistory.__table__.update()
            .where(ChatNameHistory.chat_id == target.id)
            .values(is_tracked=target.is_tracked)
        )
        logger.info(f"Rows affected in ChatNameHistory: {result_history.rowcount}")
        print(f"Rows affected in ChatNameHistory: {result_history.rowcount}")
        
        # Синхронизация с TrackedChat
        if target.is_tracked:
            logger.info(f"Adding/updating TrackedChat for chat_id {target.chat_id}")
            print(f"Adding/updating TrackedChat for chat_id {target.chat_id}")
            
            # Используем insert с конфликтной политикой
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
            result_tracked = connection.execute(update_stmt)
            logger.info(f"TrackedChat rows affected: {result_tracked.rowcount}")
            print(f"TrackedChat rows affected: {result_tracked.rowcount}")
        else:
            logger.info(f"Deleting from TrackedChat for chat_id {target.chat_id}")
            print(f"Deleting from TrackedChat for chat_id {target.chat_id}")
            result_delete = connection.execute(
                TrackedChat.__table__.delete()
                .where(TrackedChat.chat_id == target.chat_id)
            )
            logger.info(f"Rows deleted from TrackedChat: {result_delete.rowcount}")
            print(f"Rows deleted from TrackedChat: {result_delete.rowcount}")
    
    finally:
        sync_in_progress.set(False)
        logger.info("Sync complete.")
        print("Sync complete.")





@event.listens_for(ChatNameHistory, "after_update")
def sync_history_to_chat(mapper, connection, target):
    logger.info(f"Triggered sync_history_to_chat for ChatNameHistory ID {target.id}")
    if sync_in_progress.get():
        return
    try:
        sync_in_progress.set(True)
        if target.is_title_changed:
            connection.execute(
                Chat.__table__.update()
                .where(Chat.id == target.chat_id)
                .values(is_title_changed=True)
            )
    finally:
        sync_in_progress.set(False)










# Хранимая процедура и временные таблицы
async def initialize_database(session: AsyncSession):
    """
    Создает или обновляет хранимую процедуру process_chat_data.
    """
    try:
        create_procedure_query = text("""
            CREATE OR REPLACE PROCEDURE process_chat_data(temp_table_name TEXT)
            LANGUAGE plpgsql
            AS $$
            BEGIN
                -- Вставка изменений в историю названий чатов
                EXECUTE format(
                    'INSERT INTO chat_name_history (chat_id, old_title, new_title, updated_at, is_title_changed)
                     SELECT chats.id, chats.title, temp.title, NOW(), true
                     FROM chats
                     JOIN %I temp ON chats.chat_id = temp.chat_id
                     WHERE TRIM(BOTH FROM LOWER(chats.title)) != TRIM(BOTH FROM LOWER(temp.title))',
                    temp_table_name
                );

                -- Обновление существующих чатов
                EXECUTE format(
                    'UPDATE chats
                     SET title = temp.title,
                         last_updated = NOW(),
                         is_title_changed = temp.is_title_changed
                     FROM %I temp
                     WHERE chats.chat_id = temp.chat_id
                       AND TRIM(BOTH FROM LOWER(chats.title)) != TRIM(BOTH FROM LOWER(temp.title))',
                    temp_table_name
                );

                -- Вставка новых чатов
                EXECUTE format(
                    'INSERT INTO chats (chat_id, title, last_updated, is_title_changed)
                     SELECT chat_id, title, NOW(), is_title_changed
                     FROM %I
                     WHERE chat_id NOT IN (SELECT chat_id FROM chats)',
                    temp_table_name
                );
            END;
            $$;
        """)

        await session.execute(create_procedure_query)
        await session.commit()
        print("Хранимая процедура process_chat_data успешно создана.")
    except Exception as e:
        print(f"Ошибка при создании хранимой процедуры: {e}")
        await session.rollback()


async def handle_temp_table(session: AsyncSession):
    """
    Пример работы с временной таблицей.
    """
    try:
        # Удаление временной таблицы, если она существует
        drop_temp_table_query = text("DROP TABLE IF EXISTS temp_chats")
        await session.execute(drop_temp_table_query)

        # Создание временной таблицы
        create_temp_table_query = text("""
            CREATE TEMP TABLE temp_chats (
                chat_id BIGINT,
                title TEXT,
                is_title_changed BOOLEAN
            )
        """)
        await session.execute(create_temp_table_query)

        # Вставка данных в временную таблицу
        insert_temp_data_query = text("""
            INSERT INTO temp_chats (chat_id, title, is_title_changed)
            VALUES (:chat_id, :title, :is_title_changed)
        """)
        await session.execute(insert_temp_data_query, {
            "chat_id": 12345,
            "title": "Example Chat",
            "is_title_changed": True
        })

        # Вызов хранимой процедуры
        call_procedure_query = text("CALL process_chat_data('temp_chats')")
        await session.execute(call_procedure_query)

        await session.commit()
        print("Данные успешно обработаны через временную таблицу.")
    except Exception as e:
        print(f"Ошибка при обработке временной таблицы: {e}")
        await session.rollback()
