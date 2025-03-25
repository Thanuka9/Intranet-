from flask import Flask, render_template, jsonify
from extensions import db, login_manager, mail
from auth_routes import auth_routes  # Authentication Blueprint
from general_routes import general_routes  # General routes Blueprint
from profile_routes import profile_routes  # Profile management Blueprint
from task_routes import task_routes  # Task management Blueprint
from exams_routes import exams_routes  # Exams routes Blueprint
from study_material_routes import study_material_routes  # Study Material Blueprint
from admin_routes import admin_routes  # Admin routes Blueprint
from management_routes import management_routes # Management routes Blueprint


# NEW: import your special exams blueprint
from special_exams_routes import special_exams_routes

from models import User
from flask_wtf import CSRFProtect
from flask_migrate import Migrate
from mongodb_operations import initialize_mongodb, setup_collections  # MongoDB initialization
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize Flask app
app = Flask(__name__)

# CSRF Protection
csrf = CSRFProtect()
csrf.init_app(app)

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-default-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL', 'postgresql://postgres:root@localhost/collectivercm'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Flask-Mail configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'thanuka.ellepola@gmail.com')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'gtkz lpyc ygon rbul')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'thanuka.ellepola@gmail.com')

# Initialize Extensions
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'auth_routes.login'
mail.init_app(app)
migrate = Migrate(app, db)

# MongoDB Initialization
try:
    mongo_client, mongo_db = initialize_mongodb()
    setup_collections(mongo_db)
    logging.info("MongoDB initialized successfully.")
except Exception as e:
    logging.critical(f"Error initializing MongoDB: {e}")
    raise SystemExit(f"Failed to initialize MongoDB: {e}")

# Register Blueprints
app.register_blueprint(auth_routes, url_prefix='/auth')
app.register_blueprint(general_routes)
app.register_blueprint(profile_routes, url_prefix='/profile')
app.register_blueprint(task_routes, url_prefix='/tasks')
app.register_blueprint(exams_routes, url_prefix='/exams')
app.register_blueprint(study_material_routes, url_prefix='/study_materials')
app.register_blueprint(admin_routes, url_prefix='/admin')
app.register_blueprint(management_routes, url_prefix='/management')

# IMPORTANT: register the special exams blueprint
app.register_blueprint(special_exams_routes)

# Root Route
@app.route('/')
def root():
    """
    Render the home page.
    """
    return render_template('home.html')

# User Loader for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    """
    Load user for Flask-Login.
    """
    try:
        return db.session.get(User, int(user_id))
    except Exception as e:
        logging.error(f"Error loading user: {e}")
        return None

# Error Handlers
@app.errorhandler(404)
def page_not_found(e):
    """
    Render custom 404 error page.
    """
    logging.warning("404 - Page not found.")
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    """
    Render custom 500 error page.
    """
    logging.error(f"500 - Internal server error: {e}")
    return render_template('500.html'), 500

@app.route('/routes')
def list_routes():
    return jsonify([
        {'endpoint': rule.endpoint, 'url': rule.rule}
        for rule in app.url_map.iter_rules()
    ])

# Main Entry Point
if __name__ == '__main__':
    app.run(debug=True)
