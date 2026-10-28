from fastapi import APIRouter

from app.api.deps import SessionDep
from app.ingestion.service import upsert_customers
from app.schemas.customer import CustomersUpsertRequest, CustomersUpsertResponse

router = APIRouter(prefix="/customers", tags=["customers"])


@router.put("", response_model=CustomersUpsertResponse)
async def put_customers(
    body: CustomersUpsertRequest, session: SessionDep
) -> CustomersUpsertResponse:
    """Upsert customer records (plan, churn) from the CRM or billing system."""
    return CustomersUpsertResponse(upserted=await upsert_customers(session, body.customers))
