"""Call session model for active call tracking."""
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Enum, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class CallStatus(str, enum.Enum):
    """Call status enumeration."""
    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    TRANSFERRED = "transferred"
    CANCELED = "canceled"


class CallDirection(str, enum.Enum):
    """Call direction enumeration."""
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallSession(Base):
    """A single phone call handled by the AI secretary."""
    __tablename__ = "call_sessions"

    id = Column(Integer, primary_key=True, index=True)
    call_sid = Column(String(34), unique=True, nullable=False, index=True)
    phone_number = Column(String(20), nullable=False, index=True)
    direction = Column(Enum(CallDirection), nullable=False)
    status = Column(Enum(CallStatus), nullable=False, default=CallStatus.INITIATED)

    language = Column(String(5), default="fr")
    duration_seconds = Column(Integer, default=0)
    recording_url = Column(String(500), nullable=True)
    recording_consent_given = Column(Boolean, default=False)

    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    transcripts = relationship("Transcript", back_populates="call_session", cascade="all, delete-orphan")
    outcome = relationship("Outcome", back_populates="call_session", uselist=False, cascade="all, delete-orphan")
    patient = relationship("Patient", back_populates="call_sessions")
    appointment = relationship("Appointment", back_populates="call_sessions")

    __table_args__ = (
        Index("ix_call_sessions_created_at", "created_at"),
        Index("ix_call_sessions_status_created", "status", "created_at"),
    )

    def __repr__(self):
        return f"<CallSession(id={self.id}, call_sid={self.call_sid}, status={self.status})>"


class Patient(Base):
    """Patient/contact profile."""
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)

    phone_number = Column(String(20), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    language_preference = Column(String(5), default="fr")

    total_calls = Column(Integer, default=0)
    average_sentiment = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    call_sessions = relationship("CallSession", back_populates="patient")
    appointments = relationship("Appointment", back_populates="patient")

    def __repr__(self):
        return f"<Patient(id={self.id}, phone={self.phone_number})>"
