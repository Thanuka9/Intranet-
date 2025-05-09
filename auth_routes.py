
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, session
)
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from datetime import datetime, timedelta
import logging

from extensions import db, mail
from models import (
    User, Department, Designation, Client, Role,
    log_failed_login_attempt
)
from datetime import datetime
from models import FailedLogin

auth_routes = Blueprint('auth_routes', __name__)

# Serializer for email‐based tokens
s = URLSafeTimedSerializer("YourSecretKey")

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

MAX_FAILED_ATTEMPTS = 3

@auth_routes.route('/register', methods=['GET', 'POST'])
def register():
    # GET: dynamic dropdowns
    departments  = Department.query.all()
    designations = Designation.query.all()
    clients      = Client.query.all()

    if request.method == 'POST':
        # Extract form fields
        first_name     = request.form['first_name']
        last_name      = request.form['last_name']
        employee_email = request.form['employee_email']
        password       = request.form['password']
        designation_id = request.form['designation']
        department_id  = request.form['department']
        employee_id    = request.form['employee_id']
        join_date      = request.form['join_date']
        client_ids     = request.form.getlist('clients', type=int)

        # Domain restriction
        if not employee_email.endswith("@collectivercm.com"):
            flash("Only @collectivercm.com emails allowed.", "error")
            return redirect(url_for('auth_routes.register'))

        # Duplication check
        if User.query.filter_by(employee_email=employee_email).first():
            flash("Email already registered.", "error")
            return redirect(url_for('auth_routes.register'))

        # Create user
        new_user = User(
            first_name=first_name,
            last_name=last_name,
            employee_email=employee_email,
            employee_id=employee_id,
            join_date=join_date,
            department_id=department_id,
            designation_id=designation_id,
            is_verified=False
        )
        new_user.set_password(password)

        # Assign clients
        new_user.clients = Client.query.filter(Client.id.in_(client_ids)).all()

        # Default “member” role
        member = Role.query.filter_by(name='member').first()
        if member:
            new_user.roles.append(member)

        db.session.add(new_user)
        db.session.commit()

        # Send verification
        token = new_user.verification_token
        verify_url = url_for('auth_routes.verify_email', token=token, _external=True)
        msg = Message('Verify Your Email',
                      sender='no-reply@collectivercm.com',
                      recipients=[new_user.employee_email])
        msg.body = f'Click to verify: {verify_url}'
        mail.send(msg)

        flash("Registration successful! Check your email to verify.", "success")
        return redirect(url_for('auth_routes.login'))

    # GET
    return render_template(
        'register.html',
        departments=departments,
        designations=designations,
        clients=clients
    )


@auth_routes.route('/verify/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first_or_404()
    if user.is_verified:
        flash("Already verified.", "info")
    else:
        user.is_verified = True
        user.verification_token = None
        db.session.commit()
        flash("Email verified! You can now log in.", "success")
    return redirect(url_for('auth_routes.login'))


@auth_routes.route('/login', methods=['GET', 'POST'])
def login():
    """
    GET:  show the login form.
    POST: validate creds, enforce lockout & verification, then send 2FA.
    """
    if request.method == 'POST':
        email = request.form.get('employee_email')
        pwd   = request.form.get('password')
        user  = User.query.filter_by(employee_email=email).first()

        # 1) Locked-out users
        if user and user.is_locked:
            flash(
                "Your account has been locked due to too many failed login attempts. "
                "Please use Forgot Password to reset.",
                "error"
            )
            return redirect(url_for('auth_routes.login'))

        # 2) Correct password path
        if user and user.check_password(pwd):
            # reset failure counter & unlock
            user.failed_login_count = 0
            user.is_locked         = False
            user.locked_at         = None
            db.session.commit()

            # email verification
            if not user.is_verified:
                flash("Account not verified. Contact support.", "error")
                return redirect(url_for('auth_routes.login'))

            # --- STORE session INFO for downstream checks ---
            session['user_id']         = user.id
            session['is_super_admin']  = user.is_super_admin

            # if you have a one-to-many role scheme, you might do:
            # session['role_id'] = user.role_id
            #
            # but since you’re many-to-many:
            session['role_id'] = user.roles[0].id if user.roles else None

            # (optional) keep designation if you still use it elsewhere
            session['designation_id'] = user.designation_id

            # generate & send 2FA
            user.generate_2fa_code()
            msg = Message(
                "Your 2FA Code",
                sender="no-reply@collectivercm.com",
                recipients=[user.employee_email]
            )
            msg.body = f"Your code is {user.two_fa_code}. It expires in 5 minutes."
            mail.send(msg)

            flash("2FA code sent. Please verify.", "info")
            return redirect(url_for('auth_routes.verify_2fa'))

        # 3) Invalid credentials
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
                user.is_locked  = True
                user.locked_at  = datetime.utcnow()
                flash(
                    "Too many failed attempts. Your account has been locked.",
                    "error"
                )
            else:
                flash("Invalid email or password.", "error")
            db.session.commit()
            log_failed_login_attempt(user.employee_email)
        else:
            flash("Invalid email or password.", "error")

        logging.warning(f"Failed login for {email} from {request.remote_addr}")
        return redirect(url_for('auth_routes.login'))

    # GET
    return render_template('login.html')


@auth_routes.route('/verify_2fa', methods=['GET', 'POST'])
def verify_2fa():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_routes.login'))

    user = User.query.get(user_id)
    if request.method == 'POST':
        code = request.form.get('2fa_code')
        if (
            code
            and user.two_fa_code == code
            and user.two_fa_expiration > datetime.utcnow()
        ):
            # clear 2FA and log in
            user.two_fa_code = None
            user.two_fa_expiration = None
            db.session.commit()

            login_user(user)
            logging.info(f"User {user.id} logged in from {request.remote_addr}")
            return redirect(url_for('general_routes.dashboard'))
        flash("Invalid or expired 2FA code.", "error")
        return redirect(url_for('auth_routes.verify_2fa'))

    return render_template('verify_2fa.html')


@auth_routes.route('/resend_2fa')
def resend_2fa():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_routes.login'))

    user = User.query.get(user_id)
    user.generate_2fa_code()
    msg = Message("Your 2FA Code",
                  sender="no-reply@collectivercm.com",
                  recipients=[user.employee_email])
    msg.body = (
        f"Your code is {user.two_fa_code}. "
        "It expires in 5 minutes."
    )
    mail.send(msg)
    flash("New 2FA code sent.", "info")
    return redirect(url_for('auth_routes.verify_2fa'))


@auth_routes.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['employee_email']
        user  = User.query.filter_by(employee_email=email).first()
        if user:
            token = s.dumps(email, salt='password-reset-salt')
            reset_url = url_for('auth_routes.reset_password', token=token, _external=True)
            msg = Message('Password Reset',
                          sender='no-reply@collectivercm.com',
                          recipients=[email])
            msg.body = f'Reset here: {reset_url}'
            mail.send(msg)
            flash("Check your email for reset link.", "info")
        else:
            flash("Email not found.", "error")
        return redirect(url_for('auth_routes.login'))

    return render_template('forgot_password.html')


@auth_routes.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("Link invalid or expired.", "error")
        return redirect(url_for('auth_routes.forgot_password'))

    user = User.query.filter_by(employee_email=email).first_or_404()
    if request.method == 'POST':
        pw1 = request.form['new_password']
        pw2 = request.form['confirm_password']
        if pw1 == pw2:
            user.set_password(pw1)
            db.session.commit()
            flash("Password reset! Please log in.", "success")
            return redirect(url_for('auth_routes.login'))
        flash("Passwords do not match.", "error")

    return render_template('reset_password.html')


@auth_routes.route('/logout')
@login_required
def logout():
    # record who & where
    user_id = current_user.get_id()
    logout_user()

    # clear everything
    session.clear()

    logging.info(f"User {user_id} logged out from {request.remote_addr}")
    flash("You have been logged out successfully.", "success")
    return redirect(url_for('auth_routes.login'))

def log_failed_login_attempt(email):
    ip = request.remote_addr
    ua = request.headers.get('User-Agent')
    fl = FailedLogin(
        email=email,
        ip_address=ip,
        user_agent=ua,
        timestamp=datetime.utcnow()
    )
    db.session.add(fl)
    db.session.commit()