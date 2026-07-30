"""Cross-call conversation memory: a short embedded summary per past call, per patient."""
from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from app.db.base import Base

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small


class PatientMemory(Base):
    """A short summary of one past call with a patient, embedded for recall in future calls."""
    __tablename__ = "patient_memories"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    patient = relationship("Patient", back_populates="memories")

    def __repr__(self):
        return f"<PatientMemory(id={self.id}, patient_id={self.patient_id})>"
