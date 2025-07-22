# seed_clients.py
from extensions import db
from models import Client
from app import app  # or from your_app_factory import create_app; app = create_app()

def run():
    client_names = [
        "MTW",
        "HA",
        "HT",
        "TSH",
        "AHHC",
        "QFD",
        "Assure",
        "US Neuro",
        "Neuro Alert",
        "IONM Help",
        "TXPH",
        "TPC",
        "Other",
        "ConFidas"
    ]

    for name in client_names:
        existing_client = Client.query.filter_by(name=name).first()
        if not existing_client:
            new_client = Client(name=name)
            db.session.add(new_client)

    db.session.commit()
    print("Clients seeded successfully.")

if __name__ == "__main__":
    from app import app
    with app.app_context():
        run()
