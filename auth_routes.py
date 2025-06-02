from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, session
)
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
import logging

from extensions import db, mail
from models import (
    User, Department, Designation, Client, Role,
    log_failed_login_attempt, PasswordResetRequest
)
from models import FailedLogin
import os

auth_routes = Blueprint('auth_routes', __name__)

# Serializer for email-based tokens
s = URLSafeTimedSerializer(os.getenv("SECRET_KEY", "fallback-secret"))

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

MAX_FAILED_ATTEMPTS = 3

@auth_routes.route('/register', methods=['GET', 'POST'])
def register():
    departments = Department.query.all()
    designations = Designation.query.all()
    clients = Client.query.all()

    if request.method == 'POST':
        # 1. Extract form fields
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        employee_email = request.form['employee_email']
        password = request.form['password']
        designation_id = request.form['designation']
        department_ids = request.form.getlist('departments', type=int)
        employee_id = request.form['employee_id']
        join_date = request.form['join_date']
        client_ids = request.form.getlist('clients', type=int)

        # 2. Domain restriction
        if not employee_email.endswith("@collectivercm.com"):
            flash("Only @collectivercm.com emails allowed.", "error")
            return redirect(url_for('auth_routes.register'))

        # 3. Duplication check
        if User.query.filter_by(employee_email=employee_email).first():
            flash("Email already registered.", "error")
            return redirect(url_for('auth_routes.register'))

        # 4. Generate & assign email-verification token
        token = s.dumps(employee_email, salt='email-confirmation')

        # 5. Create user WITHOUT department_id (no such column anymore)
        new_user = User(
            first_name=first_name,
            last_name=last_name,
            employee_email=employee_email,
            employee_id=employee_id,
            join_date=join_date,
            designation_id=designation_id,
            is_verified=False,
            verification_token=token
        )
        new_user.set_password(password)

        db.session.add(new_user)

        # 6. Assign departments (many-to-many)
        if department_ids:
            new_user.departments = Department.query.filter(Department.id.in_(department_ids)).all()

        # 7. Assign clients (many-to-many)
        if client_ids:
            new_user.clients = Client.query.filter(Client.id.in_(client_ids)).all()

        # 8. Default “member” role
        member = Role.query.filter_by(name='member').first()
        if member:
            new_user.roles.append(member)

        # 9. Commit everything
        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"Database error during registration: {e}")
            flash("Registration error. Please try again.", "error")
            return redirect(url_for('auth_routes.register'))

        # 10. Send verification email
        verify_url = url_for('auth_routes.verify_email', token=token, _external=True)
        msg = Message(
            subject="Verify Your Email",
            recipients=[employee_email]
        )
        msg.body = (
            "Please verify your email by clicking the link:\n\n"
            f"{verify_url}"
        )
        msg.html = render_template(
            'emails/verification_email.html',
            user=new_user,
            verify_url=verify_url
        )
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
    try:
        # 1) Decode the token to get back the original email
        email = s.loads(token, salt='email-confirmation', max_age=60*60*24)
    except SignatureExpired:
        flash("That verification link has expired.", "error")
        return redirect(url_for('auth_routes.login'))
    except BadSignature:
        flash("Invalid verification link.", "error")
        return redirect(url_for('auth_routes.login'))

    # 2) Look up the user record by email
    user = User.query.filter_by(employee_email=email).first()
    if not user:
        flash("Invalid verification link.", "error")
        return redirect(url_for('auth_routes.login'))

    # 3) If the user exists, check if they’re already verified
    if user.is_verified:
        flash("Your email is already verified.", "info")
    else:
        user.is_verified = True
        user.verification_token = None
        try:
            db.session.commit()
            flash("Email verified! You can now log in.", "success")
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"Database error during email verification: {e}")
            flash("Could not verify email. Please try again or contact support.", "error")

    return redirect(url_for('auth_routes.login'))

@auth_routes.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('employee_email')
        pwd = request.form.get('password')
        user = User.query.filter_by(employee_email=email).first()

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
            user.failed_login_count = 0
            user.is_locked = False
            user.locked_at = None
            db.session.commit()

            if not user.is_verified:
                flash("Account not verified. Contact support.", "error")
                return redirect(url_for('auth_routes.login'))

            session['user_id'] = user.id
            session['is_super_admin'] = user.is_super_admin
            session['role_id'] = user.roles[0].id if user.roles else None
            session['designation_id'] = user.designation_id

            # generate & send 2FA
            user.generate_2fa_code()
            try:
                db.session.commit()
            except SQLAlchemyError as e:
                db.session.rollback()
                logging.error(f"Database error during 2FA code generation: {e}")
                flash("Server error. Please try again.", "error")
                return redirect(url_for('auth_routes.login'))
            msg = Message(
                subject="Your 2FA Code",
                recipients=[user.employee_email]
            )
            msg.body = f"Your code is {user.two_fa_code}. It expires in 5 minutes."
            msg.html = render_template(
                'emails/two_factor_email.html',
                user=user,
                code=user.two_fa_code
            )
            mail.send(msg)

            flash("2FA code sent. Please verify.", "info")
            return redirect(url_for('auth_routes.verify_2fa'))

        # 3) Invalid credentials
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
                user.is_locked = True
                user.locked_at = datetime.utcnow()
                flash("Too many failed attempts. Your account has been locked.", "error")
            else:
                flash("Invalid email or password.", "error")
            db.session.commit()
            log_failed_login_attempt(user.employee_email)
        else:
            flash("Invalid email or password.", "error")

        logging.warning(f"Failed login for {email} from {request.remote_addr}")
        return redirect(url_for('auth_routes.login'))

    return render_template('login.html')


@auth_routes.route('/verify_2fa', methods=['GET', 'POST'])
def verify_2fa():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_routes.login'))

    user = User.query.get(user_id)
    if request.method == 'POST':
        code = request.form.get('2fa_code')
        if code and user.two_fa_code == code and user.two_fa_expiration > datetime.utcnow():
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
    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        logging.error(f"Database error during 2FA resend: {e}")
        flash("Server error. Please try again.", "error")
        return redirect(url_for('auth_routes.login'))
    msg = Message(
        subject="Your 2FA Code",
        recipients=[user.employee_email]
    )
    msg.body = f"Your code is {user.two_fa_code}. It expires in 5 minutes."
    msg.html = render_template(
        'emails/two_factor_email.html',
        user=user,
        code=user.two_fa_code
    )
    mail.send(msg)
    flash("New 2FA code sent.", "info")
    return redirect(url_for('auth_routes.verify_2fa'))


@auth_routes.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['employee_email']
        user = User.query.filter_by(employee_email=email).first()
        if not user:
            flash("Email not found.", "error")
            return redirect(url_for('auth_routes.login'))

        # 1) generate the token & expiry
        token = s.dumps(email, salt='password-reset-salt')
        expires_at = datetime.utcnow() + timedelta(hours=1)

        # 2) record it in PasswordResetRequest table
        pr = PasswordResetRequest(
            user_id=user.id,
            token=token,
            expires_at=expires_at
        )
        db.session.add(pr)

        # (optional) still keep it on the User for quick lookup
        user.password_reset_token = token
        user.password_reset_expiration = expires_at

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"Database error during forgot password: {e}")
            flash("Server error. Please try again.", "error")
            return redirect(url_for('auth_routes.login'))

        # 3) send the email
        reset_url = url_for('auth_routes.reset_password', token=token, _external=True)
        msg = Message(subject='Password Reset', recipients=[email])
        msg.body = f'Reset your password using this link: {reset_url}'
        msg.html = render_template(
            'emails/password_reset_email.html',
            user=user,
            reset_url=reset_url
        )
        mail.send(msg)

        flash("Check your email for the reset link.", "info")
        return redirect(url_for('auth_routes.login'))

    return render_template('forgot_password.html')


@auth_routes.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # 1) verify signature & max_age
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("That link is invalid or has expired.", "error")
        return redirect(url_for('auth_routes.forgot_password'))

    # 2) load your request record and check expiry
    pr = PasswordResetRequest.query.filter_by(token=token).first()
    if not pr or pr.expires_at < datetime.utcnow():
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for('auth_routes.forgot_password'))

    user = User.query.get(pr.user_id)

    if request.method == 'POST':
        pw1 = request.form['new_password']
        pw2 = request.form['confirm_password']
        if not pw1 or not pw2:
            flash("Password fields cannot be empty.", "error")
        elif pw1 != pw2:
            flash("Passwords do not match.", "error")
        elif len(pw1) < 8:
            flash("Password must be at least 8 characters.", "error")
        else:
            user.set_password(pw1)

            # 3) clean up
            db.session.delete(pr)
            user.password_reset_token = None
            user.password_reset_expiration = None
            try:
                db.session.commit()
                flash("Password has been reset! You can now log in.", "success")
                return redirect(url_for('auth_routes.login'))
            except SQLAlchemyError as e:
                db.session.rollback()
                logging.error(f"Database error during password reset: {e}")
                flash("Could not reset password. Please try again.", "error")

    return render_template('reset_password.html')


@auth_routes.route('/logout')
@login_required
def logout():
    user_id = current_user.get_id()
    logout_user()
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