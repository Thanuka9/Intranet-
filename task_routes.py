from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, send_file, abort, current_app
)
from utils.email_utils import send_task_assignment_email, send_task_completion_email
from flask_login import login_required, current_user
from extensions import db, scheduler
from models import User, Designation, Event, Client, UserScore, Task, TaskDocument
from mongodb_operations import get_profile_picture, save_profile_picture, delete_profile_picture
from werkzeug.utils import secure_filename
from io import BytesIO
import imghdr
import logging
import os
import io
from datetime import datetime, timedelta, timezone
import pandas as pd 
from sqlalchemy import func

# Define Blueprint for task routes
task_routes = Blueprint('task_routes', __name__, url_prefix="/tasks")

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'docx', 'xlsx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 5MB per file
MAX_EMAIL_FILE_SIZE = 25 * 1024 * 1024  # 25MB per file for email attachments
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB total


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
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_task_document(file, task_id):
    filename = secure_filename(file.filename)
    current_app.logger.info(f"Attempting to save document: {filename} for task_id: {task_id}")

    file_data = file.read() # Read file data to check size
    file_size = len(file_data)

    if file_size > MAX_FILE_SIZE:
        current_app.logger.warning(f"File {filename} for task {task_id} rejected: size {file_size} bytes exceeds limit {MAX_FILE_SIZE} bytes.")
        flash("File size exceeds the limit of 5MB", "danger")
        return False

    task_document = TaskDocument(
        filename=filename,
        filetype=file.content_type,
        data=file_data, # Use the already read data
        task_id=task_id
    )
    try:
        db.session.add(task_document)
        current_app.logger.info(f"Successfully added TaskDocument record for {filename} (task {task_id}) to session.")
        return True
    except Exception as e:
        current_app.logger.error(f"Error adding TaskDocument {filename} for task {task_id} to DB session: {e}", exc_info=True)
        # flash("Error saving document to database.", "danger") # Optional: flash message, but could be too noisy if other flashes exist
        return False

from extensions import scheduler, db
from models import Task
# import logging # logging is already imported at the top level of the file

def delete_completed_task(task_id):
    # APScheduler jobs run outside of any request, so we need an app context
    app = scheduler.app  # flask_apscheduler stores your app here
    with app.app_context():
        task = Task.query.get(task_id)
        if not task:
            logging.info(f"[delete_completed_task] Task {task_id} not found.")
            return
        if task.status != 'Complete! Ready to Go!':
            logging.info(f"[delete_completed_task] Task {task_id} status is '{task.status}', skipping delete.")
            return

        db.session.delete(task)
        db.session.commit()
        logging.info(f"[delete_completed_task] Deleted completed task {task_id}.")

def auto_delete_completed_tasks(client_id):
    """
    Automatically delete tasks for the given client that are complete.
    If client_id is None, this function does nothing.
    """
    if client_id is None:
        return
    tasks_to_delete = Task.query.filter_by(client_id=client_id, status='Complete! Ready to Go!').all()
    for task in tasks_to_delete:
        db.session.delete(task)
    db.session.commit()

@task_routes.route('/', strict_slashes=False)
@login_required
def view_tasks():
    """
    View tasks for the current user.
    If the user has any clients, we auto-delete completed tasks for each of them.
    Internal tasks are those whose task.client_id is in the user's clients.
    External tasks are those with a NULL client_id.
    """
    # collect all of current_user's client IDs
    client_ids = [c.id for c in current_user.clients]

    # auto‐delete completed tasks for each client
    for cid in client_ids:
        auto_delete_completed_tasks(cid)

    q = request.args.get('q', '').strip()

    elevated_roles = [
        'Team Lead', 'Senior Team Lead', 'Assistant Manager', 'Manager',
        'Senior Manager', 'Human Resource'
    ]

    # build base query for internal tasks
    if current_user.role in elevated_roles:
        # elevated: see all tasks for these clients
        if client_ids:
            base = Task.query.filter(Task.client_id.in_(client_ids))
        else:
            base = Task.query.filter_by(client_id=None)  # no clients → none internal
    else:
        # non-elevated: only tasks assigned to me and for my clients
        if client_ids:
            base = Task.query.filter(
                Task.assignees.contains(current_user),
                Task.client_id.in_(client_ids)
            )
        else:
            base = Task.query.filter(
                Task.assignees.contains(current_user),
                Task.client_id.is_(None)
            )

    # apply search
    if q:
        base = base.filter(Task.title.ilike(f'%{q}%'))

    internal_tasks = base.order_by(Task.due_date.asc()).all()

    # external tasks: those assigned to me and client_id is NULL
    ext = Task.query.filter(
        Task.assignees.contains(current_user),
        Task.client_id.is_(None)
    )
    if q:
        ext = ext.filter(Task.title.ilike(f'%{q}%'))
    external_tasks = ext.order_by(Task.due_date.asc()).all()

    # tasks I assigned
    by_you = Task.query.filter_by(assigned_by=current_user.id)
    if q:
        by_you = by_you.filter(Task.title.ilike(f'%{q}%'))
    tasks_assigned_by_you = by_you.order_by(Task.due_date.asc()).all()

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
    """
    Assign a new task to internal or external users.
    Internal tasks pick up the first client in current_user.clients.
    External tasks have client_id=NULL.
    """
    if request.method == 'POST':
        # 1) Gather form data
        title       = request.form['title']
        description = request.form['description']
        due_date    = request.form['due_date']
        priority    = request.form.get('priority', 'Medium')
        task_type   = request.form.get('task_type', 'internal')
        client_id   = current_user.clients[0].id if current_user.clients else None

        # 2) Create & add the Task
        task = Task(
            title       = title,
            description = description,
            due_date    = due_date,
            priority    = priority,
            assigned_by = current_user.id,
            client_id   = (client_id if task_type == 'internal' else None),
            status      = "Getting Things Started...",
            progress    = STATUS_PROGRESS_MAP["Getting Things Started..."]
        )
        db.session.add(task)

        # 3) Flush so task.id is populated
        try:
            db.session.flush()
        except Exception as e:
            db.session.rollback()
            flash(f"Error allocating Task ID: {e}", "danger")
            return redirect(url_for('task_routes.assign_task'))

        # 4) Save attachments (5MB max each)
        for f in request.files.getlist('attachments'):
            if f and allowed_file(f.filename):
                current_app.logger.info(f"Processing attachment {f.filename} for new task (task_id: {task.id}, title: {title}) in assign_task.")
                saved_successfully = save_task_document(f, task.id)
                if not saved_successfully:
                    current_app.logger.warning(f"Failed to save attachment {f.filename} for task {task.id} in assign_task. Triggering rollback.")
                    db.session.rollback()
                    # Flash message is already handled by save_task_document or here if specific
                    flash("One of your attachments could not be saved (e.g., size limit exceeded or DB error). Task not created.", "danger")
                    return redirect(url_for('task_routes.assign_task'))
                current_app.logger.info(f"Successfully processed attachment {f.filename} for task {task.id} in assign_task.")

        # 5) Link assignees
        key = 'assignees' if task_type == 'internal' else 'external_assignees'
        for uid in request.form.getlist(key):
            user = User.query.get(uid)
            if user:
                task.assignees.append(user)

        # 6) Commit everything in one go
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving task and attachments: {e}", "danger")
            return redirect(url_for('task_routes.assign_task'))

        # 7) Send assignment emails (attachments now exist in DB!)
        for user in task.assignees:
            send_task_assignment_email(user, task)

        flash("Task assigned and notifications sent.", "success")
        return redirect(url_for('task_routes.view_tasks'))

    # GET: build team / external lists
    client_ids = [c.id for c in current_user.clients]
    if client_ids:
        team_members   = User.query.join(User.clients).filter(Client.id.in_(client_ids)).all()
        external_users = User.query.filter(~User.clients.any(Client.id.in_(client_ids))).all()
    else:
        team_members   = []
        external_users = User.query.all()

    return render_template(
        'assign_task.html',
        team_members=team_members,
        external_users=external_users,
        back_url=url_for('task_routes.view_tasks')
    )


@task_routes.route('/task/<int:task_id>', methods=['GET', 'POST'])
@login_required
def view_task(task_id):
    """
    View or update a single task.
    If an assignee marks it complete, we send a completion email
    and schedule deletion 24 h later.
    """
    task = Task.query.get_or_404(task_id)

    # Permission check
    if current_user not in task.assignees and task.assigned_by != current_user.id:
        flash("Access denied.", "danger")
        return redirect(url_for('task_routes.view_tasks'))

    if request.method == 'POST' and current_user in task.assignees:
        try:
            # Get potential new status from the form
            new_status_from_form = request.form.get('status')

            process_email_attachments = True
            # Check if email_attachments are present and if the status is NOT completion
            if new_status_from_form and new_status_from_form != 'Complete! Ready to Go!' and \
               request.files.getlist('email_attachments'):
                flash("Files attached here are only sent with the completion email. To add permanent documents to this task, please use the 'Edit Task' page. No files were saved with the task this time.", "warning")
                process_email_attachments = False

            # 1) Update status & progress using the status from form if available, else existing task status
            current_status_for_update = new_status_from_form if new_status_from_form else task.status
            task.status     = current_status_for_update
            task.progress   = STATUS_PROGRESS_MAP.get(current_status_for_update, task.progress)

            # 2) Collect any 2nd-step attachments for the completion email
            email_files = []
            if process_email_attachments:
                for f in request.files.getlist('email_attachments'):
                    if f and allowed_file(f.filename):
                        f.seek(0, os.SEEK_END)
                        if f.tell() > MAX_EMAIL_FILE_SIZE:
                            flash("Each email attachment must be under 25 MB.", "danger")
                            return redirect(url_for('task_routes.view_task', task_id=task_id))
                        f.seek(0)
                        email_files.append({
                            "filename": secure_filename(f.filename),
                            "filetype": f.content_type,
                            "data": f.read()
                        })

            # 3) If marking complete...
            # Check progress, which is now updated based on new_status_from_form
            if task.progress == 100:
                task.status       = 'Complete! Ready to Go!'
                task.completed_by = current_user.id
                db.session.commit()

                # 3a) Notify the assigner
                send_task_completion_email(
                    task.assigned_by_user,
                    task,
                    new_documents=email_files
                )

                # 3b) Schedule deletion 24 h from now (UTC)
                run_at = datetime.now(timezone.utc) + timedelta(days=1)
                scheduler.add_job(
                    id=f'delete_task_{task.id}',
                    func=delete_completed_task,
                    trigger='date',
                    run_date=run_at,
                    args=[task.id],
                    replace_existing=True
                )

                flash("Task marked complete; your assigner has been notified.", "success")
            else:
                # 4) Intermediate status update
                db.session.commit()
                flash("Task status updated.", "success")

        except Exception as e:
            db.session.rollback()
            flash(f"Error updating task: {e}", "danger")
            current_app.logger.error(f"view_task POST error: {e}", exc_info=True)

        return redirect(url_for('task_routes.view_tasks'))

    # GET: render the task page
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
    """
    Edit an existing task (title, description, due date, priority, attachments).
    """
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
            file.seek(0) # Reset pointer in case it was read by total_size calculation
            if file and allowed_file(file.filename):
                current_app.logger.info(f"Processing new attachment {file.filename} for task_id: {task_id} in edit_task.")
                # Need to re-read the file for save_task_document as total_size check consumed it.
                # save_task_document expects a FileStorage object, so we pass `file` directly.
                # It will re-read it.
                saved_successfully = save_task_document(file, task.id)
                if not saved_successfully:
                    current_app.logger.warning(f"Failed to save new attachment {file.filename} for task {task_id} in edit_task. Triggering rollback.")
                    db.session.rollback()
                    # Flash message handled by save_task_document or here
                    flash("File upload failed (e.g., size restrictions or DB error). Changes not saved.", "danger")
                    return redirect(url_for('task_routes.edit_task', task_id=task.id))
                current_app.logger.info(f"Successfully processed new attachment {file.filename} for task {task_id} in edit_task.")

        attachment_ids_to_delete = request.form.getlist('delete_attachments')
        if attachment_ids_to_delete:
            current_app.logger.info(f"Attachments marked for deletion for task {task_id}: {attachment_ids_to_delete}")
        for attachment_id in attachment_ids_to_delete:
            current_app.logger.info(f"Attempting to delete TaskDocument with id: {attachment_id} for task_id: {task_id}.")
            document = TaskDocument.query.filter_by(id=attachment_id, task_id=task.id).first()
            if document:
                try:
                    db.session.delete(document)
                    current_app.logger.info(f"Successfully deleted TaskDocument {attachment_id} for task {task_id} from session.")
                except Exception as e:
                    current_app.logger.error(f"Error deleting TaskDocument {attachment_id} for task {task_id} from DB session: {e}", exc_info=True)
                    # Potentially flash a message or decide if this error should halt the entire commit
            else:
                current_app.logger.warning(f"TaskDocument with id: {attachment_id} not found for task_id: {task_id} during deletion attempt.")

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
    """
    Delete a task if the current user is authorized (assigned_by or manager).
    """
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
    """
    Download an attachment for a given task.
    """
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
    """
    Display analytics for tasks. Aggregates all tasks where the user is either
    an assignee or the assigner, then builds:
      - a Plotly donut chart of Completed vs Incomplete
      - a status_data list for Chart.js
    """
    # Base query: any task involving this user
    tasks = Task.query.filter(
        (Task.assignees.contains(current_user)) |
        (Task.assigned_by == current_user.id)
    )

    total_tasks     = tasks.count()
    completed_tasks = tasks.filter_by(status='Complete! Ready to Go!').count()
    overdue_tasks   = tasks.filter(
        Task.due_date < datetime.now(),
        Task.status != 'Complete! Ready to Go!'
    ).count()

    # --- 1) Build a Plotly donut for completion rate ---
    incomplete_tasks = total_tasks - completed_tasks
    active_tasks = incomplete_tasks # active_tasks is the same as incomplete_tasks

    import plotly.express as px
    from plotly.offline import plot

    completion_df = {
        "Status": ["Completed", "Incomplete"],
        "Count":  [completed_tasks, incomplete_tasks]
    }
    completion_fig = px.pie(
        completion_df,
        names="Status",
        values="Count",
        hole=0.4,
        title="Completion Rate"
    )
    completion_chart = plot(
        completion_fig,
        output_type="div",
        include_plotlyjs='cdn'
    )

    # --- 2) Prepare status_data for Chart.js ---
    task_by_status = db.session.query(
        Task.status, func.count(Task.id)
    ).filter(
        (Task.assignees.contains(current_user)) |
        (Task.assigned_by == current_user.id)
    ).group_by(Task.status).all()

    status_data = [
        {"status": status, "count": count}
        for status, count in task_by_status
    ]

    return render_template(
        'analytics.html',
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        active_tasks=active_tasks, # Added active_tasks
        overdue_tasks=overdue_tasks,
        completion_chart=completion_chart,
        status_data=status_data,
    )
