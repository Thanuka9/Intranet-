from extensions import db
from models import Department

def run():
    departments = [
        "Billing",
        "Posting",
        "VOB",
        "Collections",
        "Denial Management",
        "Process Improvement",
        "Human Resources",
        "IT",
        "RPA",
        "Operations"
    ]

    for dept_name in departments:
        existing_department = Department.query.filter_by(name=dept_name).first()
        if not existing_department:
            new_dept = Department(name=dept_name)
            db.session.add(new_dept)
            print(f"Added department: {dept_name}")
        else:
            print(f"Department already exists: {dept_name}")

    db.session.commit()
    print("Seed data added successfully!")

if __name__ == "__main__":
    from app import app  # Only import here to avoid circular import
    with app.app_context():
        run()