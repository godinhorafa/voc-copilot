import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import SessionDep, require_role
from app.db.models import Job, Role
from app.schemas.job import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_role(Role.INGEST))])


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, session: SessionDep) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
