# seed_roles.py
from extensions import db
from models import Role


def run():
    role_names = [
        "member",
        "admin",
        "super_admin",
        "manager",
        "finance",
        "hr"
        # add more roles as needed
    ]

    for name in role_names:
        if not Role.query.filter_by(name=name).first():
            new_role = Role(name=name)
            db.session.add(new_role)

    db.session.commit()
    print("Roles seeded successfully.")

if __name__ == "__main__":
    from app import app
    with app.app_context():
        run()
