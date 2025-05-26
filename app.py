import os
import logging
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from extensions import db, login_manager, mail, scheduler
from auth_routes import auth_routes
from general_routes import general_routes
from profile_routes import profile_routes
from task_routes import task_routes
from exams_routes import exams_routes
from study_material_routes import study_material_routes
from admin_routes import admin_routes
from management_routes import management_routes
from special_exams_routes import special_exams_routes
from models import User
from flask_wtf import CSRFProtect
from flask_migrate import Migrate, upgrade as migrate_upgrade
from mongodb_operations import initialize_mongodb, setup_collections
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load environment variables from .env file
load_dotenv(override=True)

# Initialize Flask app
app = Flask(
    __name__,
    static_folder='static',
    static_url_path='/static'
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ----------------------------------------------------------------------
# Enable the Scheduler API 
# ----------------------------------------------------------------------
app.config['SCHEDULER_API_ENABLED'] = True
app.config['SCHEDULER_API_PREFIX'] = '/jobs'
app.config['SCHEDULER_TIMEZONE'] = 'UTC'

# ----------------------------------------------------------------------
# Set up global rate limiting using Redis
# ----------------------------------------------------------------------
raw_redis_uri = os.getenv("REDIS_URI", "redis://localhost:6379")
if raw_redis_uri.startswith("REDIS_URI="):
    logging.warning("REDIS_URI appears to contain redundant prefix. Attempting to sanitize.")
    raw_redis_uri = raw_redis_uri.split("=", 1)[1]
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri=raw_redis_uri
)
limiter.init_app(app)

# ----------------------------------------------------------------------
# Enable CSRF Protection
# ----------------------------------------------------------------------
csrf = CSRFProtect()
csrf.init_app(app)

# ----------------------------------------------------------------------
# Application Configuration
# ----------------------------------------------------------------------
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'fallback-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL', 'postgresql://postgres:root@localhost/collectivercm'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ----------------------------------------------------------------------
# Flask-Mail Configuration
# ----------------------------------------------------------------------
app.config['MAIL_SERVER']         = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT']           = int(os.getenv('MAIL_PORT'))
app.config['MAIL_USE_TLS']        = os.getenv('MAIL_USE_TLS') == 'True'
app.config['MAIL_USERNAME']       = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

# ----------------------------------------------------------------------
# Initialize Extensions
# ----------------------------------------------------------------------
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'auth_routes.login'
mail.init_app(app)
migrate = Migrate(app, db)

# --- automatically apply any pending migrations at startup ---
with app.app_context():
    import models

# ----------------------------------------------------------------------
# MongoDB Initialization
# ----------------------------------------------------------------------
try:
    mongo_client, mongo_db = initialize_mongodb()
    setup_collections(mongo_db)
    logging.info("MongoDB initialized successfully.")
except Exception as e:
    logging.critical(f"Error initializing MongoDB: {e}")
    raise SystemExit(f"Failed to initialize MongoDB: {e}")

# ----------------------------------------------------------------------
# APScheduler
# ----------------------------------------------------------------------
scheduler.init_app(app)
scheduler.start()

# ----------------------------------------------------------------------
# Register Blueprints
# ----------------------------------------------------------------------
app.register_blueprint(auth_routes, url_prefix='/auth')
app.register_blueprint(general_routes)
app.register_blueprint(profile_routes, url_prefix='/profile')
app.register_blueprint(task_routes, url_prefix='/tasks')
app.register_blueprint(exams_routes, url_prefix='/exams')
app.register_blueprint(study_material_routes, url_prefix='/study_materials')
app.register_blueprint(admin_routes, url_prefix='/admin')
app.register_blueprint(management_routes, url_prefix='/management')
app.register_blueprint(special_exams_routes)

# ----------------------------------------------------------------------
# Root Route
# ----------------------------------------------------------------------
@app.route('/')
def root():
    return render_template('home.html')

# ----------------------------------------------------------------------
# Security Headers
# ----------------------------------------------------------------------
@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# ----------------------------------------------------------------------
# User Loader
# ----------------------------------------------------------------------
@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception as e:
        logging.error(f"Error loading user: {e}")
        return None

# ----------------------------------------------------------------------
# Error Handlers
# ----------------------------------------------------------------------
@app.errorhandler(404)
def page_not_found(e):
    logging.warning("404 - Page not found.")
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    logging.error(f"500 - Internal server error: {e}")
    return render_template('500.html'), 500

# ----------------------------------------------------------------------
# Timeout Handling
# ----------------------------------------------------------------------
@app.before_request
def check_afk_timeout():
    # Don't check AFK for static files or login/logout routes
    if request.endpoint in ['static', 'auth_routes.login', 'auth_routes.logout']:
        return

    if 'user_id' in session:
        now = datetime.utcnow()
        last_activity = session.get('last_activity')
        
        if last_activity:
            last_activity = datetime.strptime(last_activity, '%Y-%m-%d %H:%M:%S.%f')
            if now - last_activity > timedelta(minutes=15):
                session.clear()
                return redirect(url_for('auth_routes.login'))
        
        session['last_activity'] = now.strftime('%Y-%m-%d %H:%M:%S.%f')

@app.route('/ping', methods=['POST'])
def ping():
    if 'user_id' in session:
        session['last_activity'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')
    return '', 204

# ----------------------------------------------------------------------
# List All Routes
# ----------------------------------------------------------------------
@app.route('/routes')
def list_routes():
    return jsonify([
        {'endpoint': rule.endpoint, 'url': rule.rule}
        for rule in app.url_map.iter_rules()
    ])

# ----------------------------------------------------------------------
# Seed Runner (call once before app.run)
# ----------------------------------------------------------------------
def run_seed_once():
    lock_file = "seed.lock"
    if not os.path.exists(lock_file):
        try:
            from seed_all import run_all_seeds
            run_all_seeds()
            with open(lock_file, 'w') as f:
                f.write("seeded")
        except Exception as e:
            print("Seeding failed:", e)

# ----------------------------------------------------------------------
# Main Entry Point
# ----------------------------------------------------------------------
if __name__ == '__main__':
    with app.app_context():
        run_seed_once()
    env = os.getenv('FLASK_ENV', 'development')
    debug_mode = True if env == 'development' else False
    app.run(debug=debug_mode)