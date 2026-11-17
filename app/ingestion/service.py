"""Write side of ingestion: persist uploads idempotently and create jobs.

Upload semantics, keyed by `external_id`:

* new                 -> insert, queue for processing
* same content hash   -> no-op ("unchanged"), unless it was never processed
                         successfully, in which case it is queued again
* different content   -> replace turns, queue for reprocessing

Re-uploading is therefore always safe, and it is also the recovery path: if a
job was lost (e.g. enqueueing failed after commit), uploading the same batch
again queues whatever is still unprocessed.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Conversation, Customer, Job, Turn
from app.privacy.redaction import Redactor
from app.schemas.conversation import ConversationIn
from app.schemas.customer import CustomerIn


@dataclass
class IngestResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    to_process: list[uuid.UUID] = field(default_factory=list)
    job_id: uuid.UUID | None = None


def _turns(item: ConversationIn, redactor: Redactor | None) -> tuple[list[Turn], dict[str, int]]:
    """Turns to store, with PII replaced when a redactor is given.

    Redaction happens here, before anything is persisted, so original text is
    never stored, embedded or sent to an LLM. The content hash is still taken
    from the original payload, so re-uploads are recognized as unchanged.
    """
    texts = [t.text for t in item.turns]
    counts: dict[str, int] = {}
    if redactor is not None:
        redaction = redactor.redact(texts)
        texts, counts = redaction.texts, redaction.counts
    turns = [
        Turn(idx=i, speaker=t.speaker, text=text, offset_s=t.offset_s)
        for i, (t, text) in enumerate(zip(item.turns, texts, strict=True))
    ]
    return turns, counts


async def ingest_conversations(
    session: AsyncSession, items: list[ConversationIn], *, redactor: Redactor | None = None
) -> IngestResult:
    """Upsert conversations and create a job for those that need processing.

    Commits on success. The caller enqueues `result.job_id` afterwards.
    """
    existing = {
        c.external_id: c
        for c in await session.scalars(
            select(Conversation)
            .where(Conversation.external_id.in_([i.external_id for i in items]))
            .options(selectinload(Conversation.turns))
        )
    }

    result = IngestResult()
    pending: list[Conversation] = []
    for item in items:
        content_hash = item.content_hash()
        conv = existing.get(item.external_id)
        if conv is None:
            turns, redactions = _turns(item, redactor)
            conv = Conversation(
                external_id=item.external_id,
                customer_id=item.customer_id,
                industry=item.industry,
                channel=item.channel,
                started_at=item.started_at,
                content_hash=content_hash,
                redactions=redactions,
                turns=turns,
            )
            session.add(conv)
            result.created += 1
        elif conv.content_hash != content_hash:
            conv.customer_id = item.customer_id
            conv.industry = item.industry
            conv.channel = item.channel
            conv.started_at = item.started_at
            conv.content_hash = content_hash
            conv.processing_error = None
            # Delete old turns before inserting new ones: same (conversation_id, idx) keys.
            conv.turns.clear()
            await session.flush()
            turns, conv.redactions = _turns(item, redactor)
            conv.turns.extend(turns)
            result.updated += 1
        else:
            result.unchanged += 1
            if conv.is_processed:
                continue
        pending.append(conv)

    await session.flush()  # assigns ids to new conversations
    result.to_process = [c.id for c in pending]
    if result.to_process:
        job = Job(conversation_ids=result.to_process, total=len(result.to_process))
        session.add(job)
        await session.flush()
        result.job_id = job.id

    await session.commit()
    return result


async def upsert_customers(session: AsyncSession, items: list[CustomerIn]) -> int:
    if not items:
        return 0
    stmt = insert(Customer).values([c.model_dump() for c in items])
    stmt = stmt.on_conflict_do_update(
        index_elements=[Customer.id],
        set_={
            col: stmt.excluded[col]
            for col in ("industry", "plan_code", "signup_at", "churned_at", "churn_reason")
        }
        | {"updated_at": func.now()},
    )
    await session.execute(stmt)
    await session.commit()
    return len(items)
