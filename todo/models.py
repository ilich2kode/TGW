from todo.database.base import Base  # Используем Base из base.py
from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from datetime import datetime


class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=True)
    last_updated = Column(TIMESTAMP, default=datetime.utcnow)
    is_title_changed = Column(Boolean, default=False)
    is_tracked = Column(Boolean, default=True)
    history = relationship("ChatNameHistory", back_populates="chat")

class ChatNameHistory(Base):
    __tablename__ = "chat_name_history"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    old_title = Column(String, nullable=True)
    new_title = Column(String, nullable=True)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow)
    is_title_changed = Column(Boolean, default=True)
    is_tracked = Column(Boolean, default=False)
    chat = relationship("Chat", back_populates="history")
