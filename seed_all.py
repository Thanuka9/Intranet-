# seed_all.py

def run_all_seeds():
    from seed_roles import run as seed_roles
    from seed_designations import run as seed_designations
    from seed_departments import run as seed_departments
    from seed_clients import run as seed_clients
    from seed_levels import run as seed_levels
    from seed_areas import run as seed_areas
    from seed_categories import run as seed_categories

    print("Seeding roles...")
    seed_roles()
    print("Seeding designations...")
    seed_designations()
    print("Seeding departments...")
    seed_departments()
    print("Seeding clients...")
    seed_clients()
    print("Seeding levels...")
    seed_levels()
    print("Seeding areas...")
    seed_areas()
    print("Seeding categories...")
    seed_categories()

if __name__ == "__main__":
    from app import app
    with app.app_context():
        run_all_seeds()
