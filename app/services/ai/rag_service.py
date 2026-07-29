"""Retrieval-Augmented Generation (RAG) service backed by pgvector.

Provides a simple knowledge-base lookup so the AI secretary can answer
general questions about the practice (opening hours, location, general
policies, etc.) grounded in real content instead of guessing.
"""
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.db.session import get_session_context
from app.models.knowledge_chunk import KnowledgeChunk

settings = get_settings()
logger = get_logger(__name__)


class RAGError(Exception):
    pass


class RAGService:
    """Simple embedding-based knowledge retrieval over Postgres/pgvector."""

    def __init__(self):
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.embedding_model = settings.openai_embedding_model

    async def generate_embedding(self, text: str) -> List[float]:
        try:
            response = await self.openai_client.embeddings.create(
                model=self.embedding_model, input=text
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.error(f"Embedding generation failed: {exc}")
            raise RAGError(f"Failed to generate embedding: {exc}")

    async def ingest_document(
        self,
        title: str,
        content: str,
        language: str,
    ) -> KnowledgeChunk:
        """Embed and store a single knowledge-base entry."""
        embedding = await self.generate_embedding(content)
        async with get_session_context() as db:
            chunk = KnowledgeChunk(
                title=title, content=content, language=language, embedding=embedding
            )
            db.add(chunk)
            await db.commit()
            await db.refresh(chunk)
            logger.info(f"Ingested knowledge chunk: {title!r} ({language})")
            return chunk

    async def retrieve_context(
        self,
        query: str,
        language: str = "fr",
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieve the most relevant knowledge-base chunks for a query.

        Returns an empty list on failure - RAG is optional and must not
        block the call flow.
        """
        try:
            query_embedding = await self.generate_embedding(query)

            async with get_session_context() as db:
                result = await db.execute(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.language == language)
                    .order_by(KnowledgeChunk.embedding.cosine_distance(query_embedding))
                    .limit(top_k)
                )
                chunks = result.scalars().all()

            return [
                {"title": c.title, "text": c.content}
                for c in chunks
            ]
        except Exception as exc:
            logger.warning(f"[RAG] Retrieval failed (non-critical): {exc}")
            return []

    async def get_relevant_context(self, query: str, language: str, max_context_length: int = 2000) -> str:
        """Retrieve context formatted with citations, for injection into the system prompt."""
        results = await self.retrieve_context(query, language)
        if not results:
            return ""

        context_parts: List[str] = []
        current_length = 0
        for result in results:
            citation = f"[{result['title']}]"
            content = result["text"].strip()
            chunk_len = len(content) + len(citation) + 5
            if current_length + chunk_len > max_context_length:
                break
            context_parts.append(f"{citation}\n{content}")
            current_length += chunk_len

        return "\n\n".join(context_parts)


def get_rag_service() -> RAGService:
    return RAGService()
