# Импорты
from todo.database.base import Base  # Используем Base из base.py
from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, TIMESTAMP, BigInteger, event, func
from sqlalchemy.orm import relationship
from contextvars import ContextVar
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

# Настройка логирования
logger = logging.getLogger(__name__)

# Локальный флаг для предотвращения рекурсии
sync_in_progress = ContextVar("sync_in_progress", default=False)

# Модели данных
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


# События SQLAlchemy
@event.listens_for(Chat, "after_update")
def sync_chat_to_history(mapper, connection, target):
    logger.info(f"Triggered sync_chat_to_history for Chat ID {target.id}")
    if sync_in_progress.get():
        return
    try:
        sync_in_progress.set(True)
        connection.execute(
            ChatNameHistory.__table__.update()
            .where(ChatNameHistory.chat_id == target.id)
            .values(is_tracked=target.is_tracked)
        )
    finally:
        sync_in_progress.set(False)


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





async def initialize_database(session: AsyncSession):
    """
    Создает или обновляет хранимую процедуру process_chat_data.
    Если буду делать алембик, то нужно будет переписать эту часть.
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