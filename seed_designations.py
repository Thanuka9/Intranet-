# seed_designations.py
from extensions import db
from models import Designation

designations = [
    {"title": "Trainee", "starting_level": 1},
    {"title": "Specialist", "starting_level": 2},
    {"title": "Senior Specialist", "starting_level": 3},
    {"title": "Assistant Team Lead", "starting_level": 4},
    {"title": "Deputy Team Lead", "starting_level": 5},
    {"title": "Team Lead", "starting_level": 6},
    {"title": "Senior Team Lead", "starting_level": 7},
    {"title": "Assistant Manager", "starting_level": 8},
    {"title": "Manager", "starting_level": 9},
    {"title": "Senior Manager", "starting_level": 10},
    {"title": "Director", "starting_level": 11},
]

def run():
    for idx, data in enumerate(designations, start=1):
        designation = Designation(
            id=idx,
            title=data["title"],
            starting_level=data["starting_level"]
        )
        db.session.merge(designation)
    db.session.commit()
    print(f"Seeded {len(designations)} designations with starting levels")

if __name__ == "__main__":
    from app import app
    with app.app_context():
        run()
