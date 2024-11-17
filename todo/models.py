from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class Chat(Base):
    __tablename__ = 'chats'
    
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, unique=True, index=True)
    title = Column(String)
    last_updated = Column(DateTime, default=datetime.utcnow)
    history = relationship("ChatNameHistory", back_populates="chat")

class ChatNameHistory(Base):
    __tablename__ = 'chat_name_history'
    
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'))
    old_title = Column(String)
    new_title = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    chat = relationship("Chat", back_populates="history")
