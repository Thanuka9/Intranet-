#!/usr/bin/env python

from extensions import db
from models import Area
from sqlalchemy import text
from app import app  # ✅ Import the app to use app context

# Define all area names to seed
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
        # Automatically get the serial sequence for the areas.id column
        seq_name = db.session.execute(
            text("SELECT pg_get_serial_sequence('areas', 'id')")
        ).scalar()

        # Determine the current max id
        max_id = db.session.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM areas")
        ).scalar()

        # Ensure sequence is set to at least 1
        if seq_name:
            next_id = max(max_id, 1)
            db.session.execute(
                text(f"SELECT setval('{seq_name}', {next_id}, false)")
            )
            db.session.commit()

        # Seed unique area names
        added = 0
        for name in AREA_NAMES:
            if not Area.query.filter_by(name=name).first():
                db.session.add(Area(name=name))
                added += 1
        db.session.commit()

        print(f"[+] Added {added} new areas. ({len(AREA_NAMES)} total possible)")

if __name__ == "__main__":
    run()
