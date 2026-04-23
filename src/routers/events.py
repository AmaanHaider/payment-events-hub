from fastapi import APIRouter
from sqlalchemy.exc import IntegrityError

from src.deps import DbSession
from src.schemas import EventIn, EventIngestResponse
from src.services.ingestion import ingest_payment_event

router = APIRouter(tags=["events"])


@router.post(
    "/events",
    response_model=EventIngestResponse,
    summary="Ingest a payment lifecycle event",
    description=(
        "**Idempotent** on `event_id`. Identical replay → `duplicate=true` without DB change. "
        "Same `event_id` with different payload → `duplicate=true`, `conflict=true`, `conflict_fields` set.\n\n"
        "**409** if `transaction_id` already exists for another `merchant_id`."
    ),
    response_description=(
        "`accepted` / `duplicate` / `conflict` flags — HTTP 200 for all handled outcomes; see schema."
    ),
    responses={
        409: {
            "description": "`transaction_id` already linked to a different `merchant_id`",
        },
        422: {"description": "Request body validation failed"},
    },
)
def post_event(db: DbSession, body: EventIn) -> EventIngestResponse:
    result = ingest_payment_event(db, body)
    if result.accepted:
        try:
            db.commit()
        except IntegrityError:
            # Concurrency-safe idempotency: another request inserted the same event_id first.
            db.rollback()
            return EventIngestResponse(
                accepted=False,
                duplicate=True,
                transaction_id=body.transaction_id,
                message="Duplicate event_id",
            )
        return EventIngestResponse(accepted=True, duplicate=False, transaction_id=body.transaction_id)

    db.rollback()
    return EventIngestResponse(
        accepted=False,
        duplicate=result.duplicate,
        transaction_id=body.transaction_id,
        message=result.message,
        conflict=getattr(result, "conflict", False),
        conflict_fields=getattr(result, "conflict_fields", None),
    )
