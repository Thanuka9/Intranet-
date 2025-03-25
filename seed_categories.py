from extensions import db  # Import db from extensions
from models import Category  # Import the Category model

# List of categories to seed
categories = [
    "Billing",
    "Posting",
    "VOB",
    "Collection",
    "Denial Management"
    "Introduction"
]

# Seed the categories into the database
def seed_categories():
    for index, name in enumerate(categories, start=1):
        category = Category(id=index, name=name)
        db.session.merge(category)  # merge is used to prevent duplicates
    db.session.commit()
    print("Categories seeded successfully.")

if __name__ == "__main__":
    from app import app  # Import the Flask app instance

    with app.app_context():  # Ensure we have the application context
        seed_categories()
