from fastapi import APIRouter

from app.answering.rag import RagAnswerer
from app.api.deps import EmbeddingsDep, LLMDep, SessionDep, SettingsDep
from app.retrieval.rerank import LLMReranker
from app.retrieval.retriever import Retriever
from app.retrieval.types import SearchFilters
from app.schemas.ask import AskRequest, AskResponse, Citation, Usage

router = APIRouter(prefix="/ask", tags=["ask"])


@router.post("", response_model=AskResponse)
async def ask(
    body: AskRequest,
    session: SessionDep,
    llm: LLMDep,
    embeddings: EmbeddingsDep,
    settings: SettingsDep,
) -> AskResponse:
    """Answer a question from customer conversations, citing the sources."""
    filters = SearchFilters(industry=body.industry, start=body.start, end=body.end)
    reranker = LLMReranker(llm) if settings.retrieval_rerank else None
    retriever = Retriever(
        embeddings,
        strategy=settings.retrieval_strategy,
        reranker=reranker,
        candidates=settings.retrieval_candidates,
    )
    result = await RagAnswerer(llm, retriever, k=body.k).answer(session, body.question, filters)
    usage = result.usage + reranker.usage if reranker else result.usage
    return AskResponse(
        answer=result.answer,
        citations=[
            Citation(conversation_id=c.external_id, excerpt=c.quote) for c in result.citations
        ],
        tools_used=["search_conversations"],
        retrieval=retriever.name,
        sources_retrieved=len(result.sources),
        dropped_citations=len(result.dropped_citations),
        model=result.model,
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=result.latency_ms,
        ),
    )
