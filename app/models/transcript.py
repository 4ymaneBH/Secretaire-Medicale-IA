"""Transcript model for conversation text + metadata."""
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Transcript(Base):
    """Conversation transcript model with text + metadata."""
    __tablename__ = "transcripts"
    
    id = Column(Integer, primary_key=True, index=True)
    call_session_id = Column(Integer, ForeignKey("call_sessions.id"), nullable=False, index=True)
    
    # Speaker identification
    speaker = Column(String(20), nullable=False)  # 'user' or 'assistant'
    
    # Transcript content
    text = Column(Text, nullable=False)
    language = Column(String(5), nullable=False)
    
    # Sentiment analysis
    sentiment_score = Column(Float, nullable=True)  # -1.0 to 1.0
    sentiment_label = Column(String(20), nullable=True)  # positive/neutral/negative
    
    # Timing
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    duration_ms = Column(Integer, nullable=True)
    
    # Metadata
    confidence = Column(Float, nullable=True)  # STT confidence score
    intent = Column(String(100), nullable=True)
    entities = Column(JSON, nullable=True)
    
    # Relationships
    call_session = relationship("CallSession", back_populates="transcripts")
    
    # Indexes
    __table_args__ = (
        Index("ix_transcripts_call_timestamp", "call_session_id", "timestamp"),
    )
    
    def __repr__(self):
        return f"<Transcript(id={self.id}, call_session_id={self.call_session_id}, speaker={self.speaker})>"
