from fastapi import APIRouter

from app.answering.rag import RagAnswerer
from app.api.deps import EmbeddingsDep, LLMDep, SessionDep
from app.retrieval.types import SearchFilters
from app.schemas.ask import AskRequest, AskResponse, Citation, Usage

router = APIRouter(prefix="/ask", tags=["ask"])


@router.post("", response_model=AskResponse)
async def ask(
    body: AskRequest, session: SessionDep, llm: LLMDep, embeddings: EmbeddingsDep
) -> AskResponse:
    """Answer a question from customer conversations, citing the sources."""
    filters = SearchFilters(industry=body.industry, start=body.start, end=body.end)
    result = await RagAnswerer(llm, embeddings, k=body.k).answer(session, body.question, filters)
    return AskResponse(
        answer=result.answer,
        citations=[
            Citation(conversation_id=c.external_id, excerpt=c.quote) for c in result.citations
        ],
        tools_used=["search_conversations"],
        sources_retrieved=len(result.sources),
        dropped_citations=len(result.dropped_citations),
        model=result.model,
        usage=Usage(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=result.latency_ms,
        ),
    )
