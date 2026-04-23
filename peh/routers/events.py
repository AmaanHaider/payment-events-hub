from fastapi import APIRouter

from peh.deps import DbSession
from peh.schemas import EventIn, EventIngestResponse
from peh.services.ingestion import ingest_payment_event

router = APIRouter(tags=["events"])


@router.post("/events", response_model=EventIngestResponse)
def post_event(db: DbSession, body: EventIn) -> EventIngestResponse:
    result = ingest_payment_event(db, body)
    if result.accepted:
        db.commit()
        return EventIngestResponse(accepted=True, duplicate=False, transaction_id=body.transaction_id)

    db.rollback()
    return EventIngestResponse(
        accepted=False,
        duplicate=result.duplicate,
        transaction_id=body.transaction_id,
        message=result.message,
    )
