from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from extensions import db, mail
from models import User, Department, Designation, Client, Role, log_failed_login_attempt
from flask_login import login_user, logout_user, login_required
from flask_mail import Message
import logging
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from datetime import datetime, timedelta

auth_routes = Blueprint('auth_routes', __name__)

# Secret key serializer for token generation
s = URLSafeTimedSerializer("YourSecretKey")

@auth_routes.route('/register', methods=['GET', 'POST'])
def register():
    # Query the DB for dynamic form data on GET
    departments  = Department.query.all()
    designations = Designation.query.all()
    clients      = Client.query.all()

    if request.method == 'POST':
        # Extract form data
        first_name     = request.form['first_name']
        last_name      = request.form['last_name']
        employee_email = request.form['employee_email']
        password       = request.form['password']
        designation_id = request.form['designation']
        department_id  = request.form['department']
        employee_id    = request.form['employee_id']
        join_date      = request.form['join_date']

        # For multi-select, pull ints
        client_ids = request.form.getlist('clients', type=int)

        # Validate allowed email domain
        allowed_domain = "@collectivercm.com"
        if not employee_email.endswith(allowed_domain):
            flash("Only @collectivercm.com domain emails are allowed.", "error")
            return redirect(url_for('auth_routes.register'))

        # Check if email is already registered
        if User.query.filter_by(employee_email=employee_email).first():
            flash("Email is already registered.", "error")
            return redirect(url_for('auth_routes.register'))

        # Create a new User
        new_user = User(
            first_name     = first_name,
            last_name      = last_name,
            employee_email = employee_email,
            employee_id    = employee_id,
            join_date      = join_date,
            department_id  = department_id,
            designation_id = designation_id,
            is_verified    = False
        )
        new_user.set_password(password)

        # Assign selected clients in one go
        new_user.clients = Client.query.filter(Client.id.in_(client_ids)).all()

        # Assign default role "member"
        member_role = Role.query.filter_by(name='member').first()
        if member_role:
            new_user.roles.append(member_role)

        db.session.add(new_user)
        db.session.commit()

        send_verification_email(new_user)
        flash("Registration successful! Please verify your email to log in.", "success")
        return redirect(url_for('auth_routes.login'))

    # GET: render the registration form
    return render_template(
        'register.html',
        departments=departments,
        designations=designations,
        clients=clients
    )


def send_verification_email(user):
    token = user.verification_token
    verification_url = url_for('auth_routes.verify_email', token=token, _external=True)
    msg = Message('Verify Your Email', sender='your-email@example.com', recipients=[user.employee_email])
    msg.body = f'Click the link to verify your email: {verification_url}'
    mail.send(msg)


@auth_routes.route('/verify/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first_or_404()
    if user.is_verified:
        flash("Your account is already verified.", "info")
    else:
        user.is_verified = True
        user.verification_token = None
        db.session.commit()
        flash("Your account has been verified! You can now log in.", "success")
    return redirect(url_for('auth_routes.login'))


@auth_routes.route('/login', methods=['GET', 'POST'])
def login():
    """
    Handle user login and enforce 2FA for all users.
    """
    if request.method == 'POST':
        employee_email = request.form.get('employee_email')
        password = request.form.get('password')

        # Fetch user from the database
        user = User.query.filter_by(employee_email=employee_email).first()

        if user:
            # Check if the password matches
            if user.check_password(password):
                # Check if the user is verified
                if user.is_verified:
                    # Store session variables for user access
                    session['user_id'] = user.id
                    session['is_super_admin'] = user.is_super_admin  # Check super admin
                    session['designation_id'] = user.designation_id  # Store designation

                    # Generate and send 2FA code
                    user.generate_2fa_code()
                    send_2fa_code(user)

                    flash("A 2FA code has been sent to your email. Please verify to complete login.", "info")
                    return redirect(url_for('auth_routes.verify_2fa'))

                else:
                    flash("Your account is not verified. Please contact support.", "error")
            else:
                flash("Invalid email or password.", "error")
        else:
            flash("Invalid email or password.", "error")

        # Log the failed login attempt
        log_failed_login_attempt(employee_email)
        return redirect(url_for('auth_routes.login'))

    # Render the login page for GET requests
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
            return redirect(url_for('general_routes.dashboard'))  # Redirect to dashboard after 2FA
        else:
            flash("Invalid or expired 2FA code. Please try again.", "error")
            return redirect(url_for('auth_routes.verify_2fa'))

    return render_template('verify_2fa.html')

@auth_routes.route('/resend_2fa')
def resend_2fa():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_routes.login'))
    
    user = User.query.get(user_id)
    user.generate_2fa_code()
    send_2fa_code(user)
    flash("A new 2FA code has been sent to your email.", "info")
    return redirect(url_for('auth_routes.verify_2fa'))

def send_2fa_code(user):
    msg = Message("Your 2FA Code", sender="your-email@example.com", recipients=[user.employee_email])
    msg.body = f"Your 2FA code is: {user.two_fa_code}. It will expire in 5 minutes."
    mail.send(msg)

# Forgot Password route
@auth_routes.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        employee_email = request.form['employee_email']
        user = User.query.filter_by(employee_email=employee_email).first()
        
        if user:
            # Generate a reset token
            reset_token = s.dumps(user.employee_email, salt='password-reset-salt')
            reset_url = url_for('auth_routes.reset_password', token=reset_token, _external=True)
            
            # Send reset email
            msg = Message('Password Reset Request', sender='your-email@gmail.com', recipients=[employee_email])
            msg.body = f'Click the link to reset your password: {reset_url}'
            mail.send(msg)
            
            flash("Password reset link has been sent to your email.", "info")
        else:
            flash("Email not found.", "error")
            
        return redirect(url_for('auth_routes.login'))
    
    return render_template('forgot_password.html')

# Reset Password route
@auth_routes.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600)  # Token valid for 1 hour
    except:
        flash("The reset link is invalid or has expired.", "error")
        return redirect(url_for('auth_routes.forgot_password'))
    
    user = User.query.filter_by(employee_email=email).first_or_404()

    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        # Validate passwords match
        if new_password == confirm_password:
            user.set_password(new_password)  # Update the user's password
            db.session.commit()
            flash("Your password has been reset. Please log in.", "success")
            return redirect(url_for('auth_routes.login'))
        else:
            flash("Passwords do not match. Please try again.", "error")

    return render_template('reset_password.html')

@auth_routes.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for('auth_routes.login'))