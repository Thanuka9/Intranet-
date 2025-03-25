from flask import Blueprint, request, render_template, redirect, url_for, flash, send_file, jsonify
from flask_login import login_required, current_user
from models import Task, TaskDocument, User
from extensions import db
from utils.email_utils import send_task_assignment_email, send_task_completion_email
from werkzeug.utils import secure_filename
from sqlalchemy import func
import os
from flask import current_app
import io
from datetime import datetime, timedelta
from flask_apscheduler import APScheduler
import plotly.express as px
from plotly.offline import plot
from plotly.graph_objs import Pie, Bar
import pandas as pd


# Define Blueprint for task routes
task_routes = Blueprint('task_routes', __name__, url_prefix="/tasks")

# Set allowed extensions and maximum file sizes
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'docx', 'xlsx'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB per file
MAX_EMAIL_FILE_SIZE = 25 * 1024 * 1024  # 25MB per file for email attachments
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB total

scheduler = APScheduler()

# Status to progress mapping
STATUS_PROGRESS_MAP = {
    "Getting Things Started...": 10,
    "Setting Up the Path...": 25,
    "Halfway There! Keep Going!": 50,
    "Almost Done! Just a Little More!": 70,
    "Wrapping Things Up...": 85,
    "Final Touches in Progress...": 95,
    "Complete! Ready to Go!": 100
}

def allowed_file(filename):
    """Helper function to check if the file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_task_document(file, task_id):
    """Save uploaded file as a TaskDocument linked to a task."""
    filename = secure_filename(file.filename)
    file_data = file.read()
    if len(file_data) > MAX_FILE_SIZE:
        flash("File size exceeds the limit of 5MB", "danger")
        return False
    task_document = TaskDocument(
        filename=filename,
        filetype=file.content_type,
        data=file_data,
        task_id=task_id
    )
    db.session.add(task_document)
    return True

def delete_completed_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == 'Complete! Ready to Go!':
        db.session.delete(task)
        db.session.commit() 

@task_routes.route('/', strict_slashes=False)
@login_required
def view_tasks():
    # Define roles that can view all internal tasks
    elevated_roles = ['Team Lead', 'Senior Team Lead', 'Assistant Manager', 'Manager', 'Senior Manager', 'Human Resource']

    if current_user.role in elevated_roles:
        # Elevated roles see all tasks for their client
        internal_tasks = Task.query.filter_by(client_id=current_user.client_id).order_by(Task.due_date.asc()).all()
    else:
        # Regular users see only tasks assigned to them within their client
        internal_tasks = Task.query.filter(
            Task.assignees.contains(current_user),
            Task.client_id == current_user.client_id
        ).order_by(Task.due_date.asc()).all()

    # External tasks visible to the current user
    external_tasks = Task.query.filter(
        Task.assignees.contains(current_user)
    ).filter(
        Task.client_id != current_user.client_id
    ).order_by(Task.due_date.asc()).all()

    # Tasks assigned by the current user
    tasks_assigned_by_you = Task.query.filter_by(assigned_by=current_user.id).order_by(Task.due_date.asc()).all()

    # Log the context to debug
    current_app.logger.info(f"Internal Tasks: {internal_tasks}")
    current_app.logger.info(f"External Tasks: {external_tasks}")
    current_app.logger.info(f"Tasks Assigned by You: {tasks_assigned_by_you}")

    return render_template(
        'tasks.html',
        internal_tasks=internal_tasks,
        external_tasks=external_tasks,
        tasks_assigned_by_you=tasks_assigned_by_you,
        back_url=url_for('general_routes.dashboard')
    )

@task_routes.route('/task/assign', methods=['GET', 'POST'])
@login_required
def assign_task():
    """Assign a new task."""
    if request.method == 'POST':
        # Retrieve form data
        title = request.form['title']
        description = request.form['description']
        due_date = request.form['due_date']
        priority = request.form.get('priority', 'Medium')
        task_type = request.form.get('task_type', 'internal')

        # Create a new Task instance
        task = Task(
            title=title,
            description=description,
            due_date=due_date,
            priority=priority,
            assigned_by=current_user.id,
            client_id=current_user.client_id if task_type == 'internal' else None,
            status="Getting Things Started...",
            progress=STATUS_PROGRESS_MAP["Getting Things Started..."]
        )
        db.session.add(task)

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating task: {e}", "danger")
            return redirect(url_for('task_routes.assign_task'))

        # Assign to internal or external users
        if task_type == 'internal':
            assignees = request.form.getlist('assignees')
            for user_id in assignees:
                user = User.query.get(user_id)
                if user:
                    task.assignees.append(user)
                    send_task_assignment_email(user, task)
        elif task_type == 'external':
            external_assignees = request.form.getlist('external_assignees')
            for user_id in external_assignees:
                user = User.query.get(user_id)
                if user:
                    task.assignees.append(user)
                    send_task_assignment_email(user, task)

        # Handle file attachments
        if 'attachments' in request.files:
            for file in request.files.getlist('attachments'):
                if file and allowed_file(file.filename):
                    if not save_task_document(file, task.id):
                        db.session.rollback()
                        flash("File upload failed due to size restrictions.", "danger")
                        return redirect(url_for('task_routes.assign_task'))

        try:
            db.session.commit()
            flash("Task successfully assigned.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving task and attachments: {e}", "danger")

        return redirect(url_for('task_routes.view_tasks'))

    # Fetch team members and external users
    team_members = User.query.filter_by(client_id=current_user.client_id).all()
    external_users = User.query.filter(User.client_id != current_user.client_id).all()

    return render_template(
        'assign_task.html',
        team_members=team_members,
        external_users=external_users,
        back_url=url_for('task_routes.view_tasks')
    )

@task_routes.route('/task/<int:task_id>', methods=['GET', 'POST'])
@login_required
def view_task(task_id):
    """View and update task details."""
    # Fetch the task or return 404 if not found
    task = Task.query.get_or_404(task_id)

    # Ensure the current user has access to view or update the task
    if current_user not in task.assignees and task.assigned_by != current_user.id:
        flash("Access denied. You do not have permission to view this task.", "danger")
        return redirect(url_for('task_routes.view_tasks'))

    if request.method == 'POST' and current_user in task.assignees:
        try:
            # Update the task status and progress
            new_status = request.form.get('status', task.status)
            task.status = new_status
            task.progress = STATUS_PROGRESS_MAP.get(new_status, task.progress)

            # Handle email attachments
            email_attachments = request.files.getlist('email_attachments')
            email_files = []

            for file in email_attachments:
                if file and allowed_file(file.filename):
                    file.seek(0, os.SEEK_END)
                    if file.tell() > MAX_EMAIL_FILE_SIZE:
                        flash("Each email attachment must be under 25MB.", "danger")
                        return redirect(url_for('task_routes.view_task', task_id=task_id))
                    file.seek(0)  # Reset file pointer after size check
                    email_files.append({
                        "filename": secure_filename(file.filename),
                        "filetype": file.content_type,
                        "data": file.read()
                    })

            # Handle task completion logic
            if task.progress == 100:
                task.status = 'Complete! Ready to Go!'
                task.completed_by = current_user.id  # Track who completed the task
                db.session.commit()  # Commit changes before sending the email
                
                # Send email to the user who assigned the task
                send_task_completion_email(task.assigned_by_user, task, new_documents=email_files)
                
                # Schedule task deletion after 1 day
                scheduler.add_job(
                    id=f'delete_task_{task_id}',
                    func=delete_completed_task,
                    trigger='date',
                    run_date=datetime.now() + timedelta(days=1),
                    args=[task_id]
                )
                flash("Task marked as completed and email sent to the assigner.", "success")

            # Commit other changes
            db.session.commit()
            flash("Task updated successfully.", "success")

        except Exception as e:
            db.session.rollback()
            flash(f"Error updating task: {e}", "danger")
            current_app.logger.error(f"Error updating task {task_id}: {e}", exc_info=True)

        return redirect(url_for('task_routes.view_tasks'))

    # Render the task details page
    return render_template(
        'view_task.html',
        task=task,
        attachments=task.documents,
        back_url=url_for('task_routes.view_tasks'),
        STATUS_PROGRESS_MAP=STATUS_PROGRESS_MAP
    )

@task_routes.route('/task/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.assigned_by != current_user.id and not current_user.is_manager():
        flash("Access denied to edit this task", "danger")
        return redirect(url_for('task_routes.view_tasks'))

    if request.method == 'POST':
        task.title = request.form['title']
        task.description = request.form['description']
        task.due_date = request.form['due_date']
        task.priority = request.form.get('priority', 'Medium')

        total_size = sum(len(file.read()) for file in request.files.getlist('attachments'))
        if total_size > MAX_TOTAL_SIZE:
            flash("Total attachment size exceeds the 100MB limit.", "danger")
            return redirect(url_for('task_routes.edit_task', task_id=task.id))

        for file in request.files.getlist('attachments'):
            file.seek(0)
            if file and allowed_file(file.filename):
                if not save_task_document(file, task.id):
                    db.session.rollback()
                    flash("File upload failed due to size restrictions", "danger")
                    return redirect(url_for('task_routes.edit_task', task_id=task.id))

        attachment_ids_to_delete = request.form.getlist('delete_attachments')
        for attachment_id in attachment_ids_to_delete:
            document = TaskDocument.query.filter_by(id=attachment_id, task_id=task.id).first()
            if document:
                db.session.delete(document)

        try:
            db.session.commit()
            flash("Task updated successfully", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating task: {e}", "danger")

        return redirect(url_for('task_routes.view_task', task_id=task.id))

    attachments = task.documents
    return render_template(
        'edit_task.html',
        task=task,
        attachments=attachments,
        back_url=url_for('task_routes.view_task', task_id=task.id)
    )

@task_routes.route('/task/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.assigned_by != current_user.id and not current_user.is_manager():
        flash("Access denied to delete this task", "danger")
        return redirect(url_for('task_routes.view_tasks'))
    
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted successfully", "success")
    
    return redirect(url_for('task_routes.view_tasks'))

@task_routes.route('/task/<int:task_id>/attachment/<int:attachment_id>/download')
@login_required
def download_attachment(task_id, attachment_id):
    document = TaskDocument.query.filter_by(id=attachment_id, task_id=task_id).first()

    if not document:
        flash("Attachment not found", "danger")
        return redirect(url_for('task_routes.view_task', task_id=task_id))

    return send_file(
        io.BytesIO(document.data),
        mimetype=document.filetype,
        as_attachment=True,
        download_name=document.filename
    )

@task_routes.route('/analytics_dashboard')
@login_required
def analytics_dashboard():
    """Render analytics dashboard with charts."""
    client_id = current_user.client_id

    # Query data for analytics
    total_tasks = Task.query.filter_by(client_id=client_id).count()
    completed_tasks = Task.query.filter_by(client_id=client_id, status='Complete! Ready to Go!').count()
    overdue_tasks = Task.query.filter(
        Task.client_id == client_id,
        Task.due_date < datetime.now(),
        Task.status != 'Complete! Ready to Go!'
    ).count()

    # Priority data
    task_by_priority = db.session.query(
        Task.priority, func.count(Task.id)
    ).filter_by(client_id=client_id).group_by(Task.priority).all()

    # Status data
    task_by_status = db.session.query(
        Task.status, func.count(Task.id)
    ).filter_by(client_id=client_id).group_by(Task.status).all()

    # Convert to DataFrame
    priority_df = pd.DataFrame(task_by_priority, columns=["Priority", "Count"]) if task_by_priority else pd.DataFrame(columns=["Priority", "Count"])
    status_df = pd.DataFrame(task_by_status, columns=["Status", "Count"]) if task_by_status else pd.DataFrame(columns=["Status", "Count"])

    # Generate Figures
    try:
        priority_fig = px.pie(priority_df, names="Priority", values="Count", title="Tasks by Priority") if not priority_df.empty else {}
        status_fig = px.bar(status_df, x="Status", y="Count", title="Tasks by Status") if not status_df.empty else {}

    except Exception as e:
        current_app.logger.error(f"Error generating figures: {e}")
        priority_fig, status_fig = {}, {}

    return render_template(
        'analytics.html',
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        overdue_tasks=overdue_tasks,
        priority_fig=priority_fig if priority_fig else None,
        status_fig=status_fig if status_fig else None,
    )
