import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import QueueDep, RedactorDep, SessionDep
from app.core.rate_limit import rate_limit
from app.db.models import Chunk, Conversation
from app.ingestion.service import ingest_conversations
from app.schemas.conversation import (
    ConversationOut,
    IngestRequest,
    IngestResponse,
    InsightOut,
    ProcessingStatus,
    TurnOut,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])
logger = structlog.get_logger(__name__)


@router.post(
    "",
    dependencies=[Depends(rate_limit("ingest", lambda s: s.rate_limit_ingest))],
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": IngestResponse, "description": "Nothing needed processing"}},
)
async def upload_conversations(
    body: IngestRequest,
    session: SessionDep,
    queue: QueueDep,
    redactor: RedactorDep,
    response: Response,
) -> IngestResponse:
    """Upload conversations. Idempotent by `external_id`; processing is asynchronous."""
    try:
        result = await ingest_conversations(session, body.conversations, redactor=redactor)
    except IntegrityError as exc:
        # Another request inserted one of these external_ids concurrently.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Concurrent upload of the same conversations; retry"
        ) from exc

    if result.job_id is None:
        response.status_code = status.HTTP_200_OK
    else:
        try:
            await queue.enqueue_ingestion(result.job_id)
        except Exception as exc:
            # Data is committed; re-uploading the same batch queues it again.
            logger.error("enqueue_failed", job_id=str(result.job_id), error=repr(exc))
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Conversations were saved but could not be queued; retry the upload",
            ) from exc

    return IngestResponse(
        job_id=result.job_id,
        created=result.created,
        updated=result.updated,
        unchanged=result.unchanged,
        queued=len(result.to_process),
    )


@router.get("/{external_id}", response_model=ConversationOut)
async def get_conversation(external_id: str, session: SessionDep) -> ConversationOut:
    conv = await session.scalar(
        select(Conversation)
        .where(Conversation.external_id == external_id)
        .options(selectinload(Conversation.turns), selectinload(Conversation.insight))
    )
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    chunk_count = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.conversation_id == conv.id)
    )
    state: ProcessingStatus = (
        "processed" if conv.is_processed else "failed" if conv.processing_error else "pending"
    )
    return ConversationOut(
        id=conv.id,
        external_id=conv.external_id,
        customer_id=conv.customer_id,
        industry=conv.industry,
        channel=conv.channel,
        started_at=conv.started_at,
        status=state,
        processing_error=conv.processing_error,
        chunk_count=chunk_count or 0,
        redactions=conv.redactions or {},
        insight=InsightOut.model_validate(conv.insight) if conv.insight else None,
        turns=[TurnOut.model_validate(t) for t in conv.turns],
    )
