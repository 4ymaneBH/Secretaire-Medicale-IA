"""Knowledge base chunk model, embedded for semantic search (pgvector)."""
from sqlalchemy import Column, String, Integer, Text, DateTime
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from app.db.base import Base

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small


class KnowledgeChunk(Base):
    """A chunk of practice knowledge-base content, embedded for retrieval."""
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    language = Column(String(5), nullable=False, index=True)
    embedding = Column(Vector(EMBEDDING_DIMENSIONS), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<KnowledgeChunk(id={self.id}, title={self.title!r}, language={self.language})>"
