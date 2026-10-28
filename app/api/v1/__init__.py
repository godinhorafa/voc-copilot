from fastapi import APIRouter, Depends

from app.api.v1 import conversations, customers, jobs
from app.core.security import require_api_key

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
router.include_router(conversations.router)
router.include_router(customers.router)
router.include_router(jobs.router)
