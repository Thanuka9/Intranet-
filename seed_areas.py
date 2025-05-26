#!/usr/bin/env python

from extensions import db
from models import Area
from sqlalchemy import text
from app import app  # ✅ Import app globally

AREA_NAMES = [
    "Billing",
    "Posting",
    "VOB",
    "Billing & Coding",
    "Denial Management",
    "Collections",
    "Client Materials",
    "Study Materials"
]

def run():
    with app.app_context():
        # Sync PostgreSQL sequence
        seq_name = db.session.execute(
            text("SELECT pg_get_serial_sequence('areas', 'id')")
        ).scalar()
        max_id = db.session.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM areas")
        ).scalar()
        if seq_name:
            db.session.execute(
                text(f"SELECT setval('{seq_name}', {max_id}, true)")
            )
            db.session.commit()

        added = 0
        for name in AREA_NAMES:
            if not Area.query.filter_by(name=name).first():
                db.session.add(Area(name=name))
                added += 1
        db.session.commit()
        print(f"[+] Added {added} new areas. ({len(AREA_NAMES)} total possible)")

if __name__ == "__main__":
    run()
