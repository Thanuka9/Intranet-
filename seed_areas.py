#!/usr/bin/env python
"""
Seed script to populate the `areas` table with a predefined list of Area names,
syncing the PostgreSQL sequence to avoid primary-key conflicts.

Run this script from the project root:
    $ python seed_areas.py

Assumes:
  - Your Flask app instance lives in `app.py` as `app`.
  - `db` is imported from `extensions.py`.
  - `Area` model is imported from `models.py`.
"""
from app import app
from extensions import db
from models import Area
from sqlalchemy import text

# List of area names to seed into the database
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

def seed_areas():
    with app.app_context():
        # Synchronize the PostgreSQL sequence for the `areas.id` column
        seq_name = db.session.execute(
            text("SELECT pg_get_serial_sequence('areas', 'id')")
        ).scalar()
        max_id = db.session.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM areas")
        ).scalar()
        if seq_name:
            # Set sequence to max(id) so next insert will use max_id+1
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
    seed_areas()
