
"""Appointment model: booking slot proposed/confirmed via the AI secretary."""
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.db.base import Base, value_enum


class AppointmentStatus(str, enum.Enum):
    """Appointment lifecycle status."""
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CANCELED = "canceled"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class Appointment(Base):
    """A booked or proposed appointment slot."""
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)

    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes = Column(Integer, default=30)
    reason = Column(String(255), nullable=True)
    status = Column(value_enum(AppointmentStatus), nullable=False, default=AppointmentStatus.PROPOSED)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    patient = relationship("Patient", back_populates="appointments")
    call_sessions = relationship("CallSession", back_populates="appointment")

    def __repr__(self):
        return f"<Appointment(id={self.id}, scheduled_at={self.scheduled_at}, status={self.status})>"
