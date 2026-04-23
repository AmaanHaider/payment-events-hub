from __future__ import annotations

import argparse
import json
from pathlib import Path

from peh.db import SessionLocal
from peh.schemas import EventIn
from peh.services.ingestion import ingest_payment_event


def main() -> None:
    p = argparse.ArgumentParser(description="Bulk-load events JSON (array of objects) into the database.")
    p.add_argument("path", nargs="?", default="sample_events.json")
    p.add_argument("--commit-every", type=int, default=500)
    args = p.parse_args()

    path = Path(args.path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("Expected a JSON array of events")

    db = SessionLocal()
    accepted = 0
    duplicates = 0
    try:
        for i, obj in enumerate(raw, start=1):
            body = EventIn.model_validate(obj)
            res = ingest_payment_event(db, body)
            if res.accepted:
                accepted += 1
            else:
                duplicates += 1
                db.rollback()
                continue

            if i % args.commit_every == 0:
                db.commit()

        db.commit()
    finally:
        db.close()

    print(f"loaded={len(raw)} accepted={accepted} duplicates={duplicates}")


if __name__ == "__main__":
    main()
