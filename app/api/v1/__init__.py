from fastapi import APIRouter, Depends

from app.api.auth import require_principal
from app.api.v1 import api_keys, ask, conversations, customers, insights, jobs, metrics, users

# Every route also declares the roles it allows (app.api.auth.require_role).
router = APIRouter(prefix="/v1", dependencies=[Depends(require_principal)])
router.include_router(conversations.router)
router.include_router(customers.router)
router.include_router(jobs.router)
router.include_router(ask.router)
router.include_router(metrics.router)
router.include_router(insights.router)
router.include_router(api_keys.router)
router.include_router(users.me_router)
router.include_router(users.router)
