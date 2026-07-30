"""Cross-call conversation memory ("mémoire de la conversation"), backed by pgvector.

Two retrieval paths, used at two different points:
- get_recent_context(): most-recent summaries by recency, injected into the
  system prompt once at call start (cheap, no query text available yet).
- recall_context(): a real vector search over past summaries, invoked by the
  model mid-call via the recall_context tool when the patient references
  something specific from a previous visit.
"""
from typing import List, Optional

from openai import AsyncOpenAI
from sqlalchemy import select, desc

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.db.session import get_session_context
from app.models.conversation_memory import PatientMemory

settings = get_settings()
logger = get_logger(__name__)

RECENT_CONTEXT_LIMIT = 3
RECALL_TOP_K = 3


class MemoryService:
    def __init__(self):
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.embedding_model = settings.openai_embedding_model

    async def _embed(self, text: str) -> List[float]:
        response = await self.openai_client.embeddings.create(
            model=self.embedding_model, input=text
        )
        return response.data[0].embedding

    async def store_memory(self, patient_id: int, summary: str) -> None:
        """Embed and store a short summary of a completed call."""
        if not patient_id or not summary:
            return
        try:
            embedding = await self._embed(summary)
            async with get_session_context() as db:
                db.add(PatientMemory(patient_id=patient_id, summary=summary, embedding=embedding))
                await db.commit()
        except Exception as exc:
            logger.warning(f"[Memory] Failed to store memory for patient {patient_id}: {exc}")

    async def get_recent_context(self, patient_id: int) -> Optional[str]:
        """Return the patient's most recent call summaries, most recent first."""
        if not patient_id:
            return None
        try:
            async with get_session_context() as db:
                result = await db.execute(
                    select(PatientMemory)
                    .where(PatientMemory.patient_id == patient_id)
                    .order_by(desc(PatientMemory.created_at))
                    .limit(RECENT_CONTEXT_LIMIT)
                )
                memories = result.scalars().all()
            if not memories:
                return None
            return "\n".join(f"- {m.summary}" for m in memories)
        except Exception as exc:
            logger.warning(f"[Memory] Failed to fetch recent context for patient {patient_id}: {exc}")
            return None

    async def recall_context(self, patient_id: int, query: str) -> Optional[str]:
        """Vector-search this patient's past call summaries for one relevant to `query`."""
        if not patient_id or not query:
            return None
        try:
            query_embedding = await self._embed(query)
            async with get_session_context() as db:
                result = await db.execute(
                    select(PatientMemory)
                    .where(PatientMemory.patient_id == patient_id)
                    .order_by(PatientMemory.embedding.cosine_distance(query_embedding))
                    .limit(RECALL_TOP_K)
                )
                memories = result.scalars().all()
            if not memories:
                return None
            return "\n".join(f"- {m.summary}" for m in memories)
        except Exception as exc:
            logger.warning(f"[Memory] Recall failed for patient {patient_id}: {exc}")
            return None


def get_memory_service() -> MemoryService:
    return MemoryService()
