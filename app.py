import os
import logging
import click
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask.cli import with_appcontext
from flask_wtf import CSRFProtect
# Optional rate-limiter import
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    rate_limiting_available = True
except ImportError:
    logging.warning("Flask-Limiter not installed; skipping rate limiting.")
    rate_limiting_available = False
from flask_migrate import Migrate, upgrade as migrate_upgrade
from flask_login import LoginManager, login_required, current_user
from dotenv import load_dotenv
from datetime import datetime, timedelta

from extensions import db, mail, scheduler
from auth_routes import auth_routes
from general_routes import general_routes
from profile_routes import profile_routes
from task_routes import task_routes
from exams_routes import exams_routes
from study_material_routes import study_material_routes
from admin_routes import admin_routes
from management_routes import management_routes
from special_exams_routes import special_exams_routes
from models import User, FailedLogin, AuditLog
from mongodb_operations import initialize_mongodb, setup_collections
from utils.email_utils import init_scheduler
from audit import log_event  # our helper

# Load environment variables
# load_dotenv(override=True)

# Initialize Flask app
app = Flask(
    __name__,
    static_folder='static',
    static_url_path='/static'
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ----------------------------------------------------------------------
# CLI: Backfill historical failed-logins
# ----------------------------------------------------------------------
@click.command('backfill-failures')
@with_appcontext
def backfill_failures():
    """
    Migrate existing FailedLogin records into audit_log.
    """
    for fl in FailedLogin.query.order_by(FailedLogin.timestamp).all():
        entry = AuditLog(
            event_type  = 'FAILED_LOGIN',
            ip_address  = fl.ip_address,
            description = {'email': fl.email, 'user_agent': fl.user_agent},
            created_at  = fl.timestamp
        )
        db.session.add(entry)
    db.session.commit()
    click.echo('✅ Backfilled failed-logins into audit_log.')

# Register CLI command
app.cli.add_command(backfill_failures)

# ----------------------------------------------------------------------
# Scheduler API
# ----------------------------------------------------------------------
app.config['SCHEDULER_API_ENABLED'] = True
app.config['SCHEDULER_API_PREFIX'] = '/jobs'
app.config['SCHEDULER_TIMEZONE'] = 'UTC'

# ----------------------------------------------------------------------
# Rate limiting (if available)
# ----------------------------------------------------------------------
if rate_limiting_available:
    raw_redis_uri = os.getenv("REDIS_URI", "redis://localhost:6379")
    if raw_redis_uri.startswith("REDIS_URI="):
        logging.warning("Redis URI contains redundant prefix; sanitizing.")
        raw_redis_uri = raw_redis_uri.split("=",1)[1]
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["1000 per day", "200 per hour"],
        storage_uri=raw_redis_uri
    )
    limiter.init_app(app)

# ----------------------------------------------------------------------
# CSRF Protection
# ----------------------------------------------------------------------
csrf = CSRFProtect(app)

# ─── Exempt our keep-alive ping from CSRF ──────────────────────────
@csrf.exempt
@app.route('/ping', methods=['POST'])
def ping():
    if 'user_id' in session:
        session['last_activity'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')
        session.modified = True
    return '', 204

# ----------------------------------------------------------------------
# App Configuration
# ----------------------------------------------------------------------
app.config.update({
    'SECRET_KEY':                 os.getenv('SECRET_KEY', 'fallback-secret-key'),
    'SQLALCHEMY_DATABASE_URI':    os.getenv('DATABASE_URL', 'postgresql://postgres:root@localhost/collectivercm'),
    'SQLALCHEMY_TRACK_MODIFICATIONS': False,
    'MAIL_SERVER':                os.getenv('MAIL_SERVER'),
    'MAIL_PORT':                  int(os.getenv('MAIL_PORT', 0)),
    'MAIL_USE_TLS':               os.getenv('MAIL_USE_TLS', 'False') == 'True',
    'MAIL_USERNAME':              os.getenv('MAIL_USERNAME'),
    'MAIL_PASSWORD':              os.getenv('MAIL_PASSWORD'),
    'MAIL_DEFAULT_SENDER':        os.getenv('MAIL_DEFAULT_SENDER'),
})

# ─── Session Cookie & Lifetime ────────────────────────────────────────
# Keep the cookie alive for 3 hours and set sane defaults for cross-site
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=3)
app.config['SESSION_COOKIE_SECURE']   = False    # ← set True under HTTPS in production
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'    # avoid silent cookie blocks
# (SESSION_REFRESH_EACH_REQUEST=True by default, so Flask will auto-refresh)

# ----------------------------------------------------------------------
# Initialize extensions and auto-migrate
# ----------------------------------------------------------------------
login_manager = LoginManager()

db.init_app(app)
migrate = Migrate(app, db)
with app.app_context():
    migrate_upgrade()

login_manager.init_app(app)
login_manager.login_view = 'auth_routes.login'
mail.init_app(app)

# ----------------------------------------------------------------------
# MongoDB Setup
# ----------------------------------------------------------------------
try:
    mongo_client, mongo_db = initialize_mongodb()
    setup_collections(mongo_db)
    logging.info("MongoDB initialized successfully.")
except Exception as e:
    logging.critical(f"MongoDB init failed: {e}")
    raise SystemExit(e)

# ----------------------------------------------------------------------
# APScheduler Setup
# ----------------------------------------------------------------------
scheduler.init_app(app)
init_scheduler(scheduler)
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
# Root & utility routes
# ----------------------------------------------------------------------
@app.route('/')
def root():
    return render_template('home.html')

@app.route('/routes')
def list_routes():
    return jsonify([{'endpoint': r.endpoint, 'url': r.rule} for r in app.url_map.iter_rules()])

# ----------------------------------------------------------------------
# Security headers
# ----------------------------------------------------------------------
@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# ----------------------------------------------------------------------
# User session timeout
# ----------------------------------------------------------------------
def check_afk_timeout():
    # skip static files, auth login/logout, regular exam start & submit,
    # and special-exam start & submit endpoints (and our keep-alive ping)
    if request.endpoint in [
        'static',
        'auth_routes.login',
        'auth_routes.logout',
        'exams_routes.start_exam',    # GET  /<exam_id>/start
        'exams_routes.submit_exam',   # POST /<exam_id>/submit
        'special_exams_routes.exam_paper1',    # GET  /special_exams/paper1
        'special_exams_routes.submit_paper1',  # POST /special_exams/paper1_submit
        'special_exams_routes.exam_paper2',    # GET  /special_exams/paper2
        'special_exams_routes.submit_paper2',  # POST /special_exams/paper2_submit
        'ping',
    ]:
        return

    if 'user_id' in session:
        now = datetime.utcnow()
        last_activity = session.get('last_activity')
        if last_activity:
            last_activity = datetime.strptime(last_activity, '%Y-%m-%d %H:%M:%S.%f')
            # extend AFK timeout to 1 hour 15 minutes
            if now - last_activity > timedelta(hours=1, minutes=15):
                session.clear()
                return redirect(url_for('auth_routes.login'))

        # refresh last_activity and mark session modified
        session['last_activity'] = now.strftime('%Y-%m-%d %H:%M:%S.%f')
        session.modified = True

app.before_request(check_afk_timeout)

# ----------------------------------------------------------------------
# User loader
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
# Seed Runner (one-time)
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
            logging.error(f"Seeding failed: {e}")

# ----------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------
if __name__ == '__main__':
    with app.app_context():
        run_seed_once()
    env = os.getenv('FLASK_ENV', 'development')
    debug_mode = True if env == 'development' else False
    app.run(host='0.0.0.0', debug=debug_mode)
