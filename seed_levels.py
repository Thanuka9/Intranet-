# seed_levels.py

from app import app  # Import the Flask app from your main application file
from models import db, Level

def run():
    levels = [
        {"level_number": 1, "title": "Beginner"},
        {"level_number": 2, "title": "Intermediate"},
        {"level_number": 3, "title": "Advanced"},
        {"level_number": 4, "title": "Expert"},
        {"level_number": 5, "title": "Master"}
    ]

    for level in levels:
        existing_level = Level.query.filter_by(level_number=level["level_number"]).first()
        if not existing_level:
            new_level = Level(level_number=level["level_number"], title=level["title"])
            db.session.add(new_level)
            print(f"Added Level: {level['title']}")

    db.session.commit()
    print("Levels seeded successfully!")

if __name__ == "__main__":
    # Wrap the seed function in the app context
    from app import app
    with app.app_context():
        run()
