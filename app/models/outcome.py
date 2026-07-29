"""Outcome model for call results: resolved/escalated/no_answer."""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Enum, JSON, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class OutcomeType(str, enum.Enum):
    """Call outcome types."""
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    TRANSFERRED = "transferred"
    FAILED = "failed"
    ABANDONED = "abandoned"


class TransferReason(str, enum.Enum):
    """Reason for escalation/transfer."""
    LOW_SENTIMENT = "low_sentiment"
    INCOMPREHENSION = "incomprehension"
    DURATION_EXCEEDED = "duration_exceeded"
    EXPLICIT_REQUEST = "explicit_request"
    URGENT_KEYWORD = "urgent_keyword"
    COMPLEX_QUERY = "complex_query"


class Outcome(Base):
    """Call outcome model tracking resolution status."""
    __tablename__ = "outcomes"
    
    id = Column(Integer, primary_key=True, index=True)
    call_session_id = Column(Integer, ForeignKey("call_sessions.id"), unique=True, nullable=False, index=True)
    
    # Outcome classification
    outcome_type = Column(Enum(OutcomeType), nullable=False, index=True)
    outcome_details = Column(Text, nullable=True)
    
    # Resolution tracking
    issue_resolved = Column(String(20), nullable=True)  # 'yes', 'no', 'partial'
    customer_satisfied = Column(String(20), nullable=True)  # 'yes', 'no', 'unknown'
    
    # Transfer/escalation details
    transfer_reason = Column(Enum(TransferReason), nullable=True)
    transfer_to = Column(String(100), nullable=True)  # operator_id, queue_name
    escalation_notes = Column(Text, nullable=True)
    
    # Intent and context
    detected_intent = Column(String(100), nullable=True)
    intent_confidence = Column(String, nullable=True)
    key_entities = Column(JSON, nullable=True)
    
    # Metadata
    meta_data = Column(JSON, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    call_session = relationship("CallSession", back_populates="outcome")
    
    # Indexes
    __table_args__ = (
        Index("ix_outcomes_type_created", "outcome_type", "created_at"),
    )
    
    def __repr__(self):
        return f"<Outcome(id={self.id}, call_session_id={self.call_session_id}, type={self.outcome_type})>"
