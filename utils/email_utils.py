from flask_mail import Message
from flask import current_app, render_template, url_for
from extensions import mail
from datetime import datetime, timedelta
from models import Task

def init_scheduler(scheduler):
    """
    Initialize the scheduled tasks for sending notifications.
    Should be called from app.py where `scheduler` is initialized.
    Runs every 60 minutes to check for tasks nearing their deadlines.
    """
    scheduler.add_job(id='deadline_notification', func=check_and_send_deadline_notifications, trigger='interval', minutes=60)

def send_task_assignment_email(user, task):
    """
    Send an email to notify a user about a newly assigned task.
    Attaches files if they are under 10MB.
    """
    subject = f"New Task Assigned: {task.title}"
    recipients = [user.employee_email]
    task_url = url_for('task_routes.view_task', task_id=task.id, _external=True)

    body_html = render_template(
        "emails/task_assignment.html",
        user=user,
        task=task,
        task_url=task_url,
        logo_url=url_for('static', filename='images/logo.png', _external=True)
    )

    msg = Message(subject=subject, recipients=recipients, html=body_html)
    
    # Attach files if under 10MB
    try:
        for document in task.documents:
            if len(document.data) <= 10 * 1024 * 1024:  # File size check
                msg.attach(document.filename, document.filetype, document.data)
            else:
                current_app.logger.warning(f"Skipped attachment {document.filename} due to size limits.")
    except Exception as e:
        current_app.logger.error(f"Failed to attach document to task assignment email: {e}")

    # Send the email
    try:
        mail.send(msg)
    except Exception as e:
        current_app.logger.error(f"Failed to send task assignment email to {user.employee_email}: {e}")

def send_task_completion_email(assigner, task, new_documents=None):
    """
    Notify the task assigner that the assigned user has marked the task as completed.
    Optionally attaches new documents.
    """
    subject = f"Task Completed: {task.title}"
    recipients = [assigner.employee_email]
    task_url = url_for('task_routes.view_task', task_id=task.id, _external=True)

    body_html = render_template(
        "emails/task_completion.html",
        assigner=assigner,
        task=task,
        task_url=task_url,
        logo_url=url_for('static', filename='images/logo.png', _external=True)
    )

    msg = Message(subject=subject, recipients=recipients, html=body_html)
    
    # Attach files if under 10MB
    try:
        documents_to_attach = list(task.documents) + (new_documents or [])
        for document in documents_to_attach:
            filename = document.get("filename") if isinstance(document, dict) else document.filename
            filetype = document.get("filetype") if isinstance(document, dict) else document.filetype
            data = document.get("data") if isinstance(document, dict) else document.data
            
            if len(data) <= 10 * 1024 * 1024:  # File size check
                msg.attach(filename, filetype, data)
            else:
                current_app.logger.warning(f"Skipped attachment {filename} due to size limits.")
    except Exception as e:
        current_app.logger.error(f"Failed to attach document to task completion email: {e}")

    # Send the email
    try:
        mail.send(msg)
    except Exception as e:
        current_app.logger.error(f"Failed to send task completion email to {assigner.employee_email}: {e}")

def send_deadline_reminder(task):
    """
    Send a deadline reminder email to the assignees and the assigner.
    """
    try:
        subject = f"Reminder: Task Deadline Approaching - {task.title}"
        recipients = [user.employee_email for user in task.assignees]
        recipients.append(task.assigned_by_user.employee_email)

        task_url = url_for('task_routes.view_task', task_id=task.id, _external=True)
        body_html = render_template(
            "emails/task_deadline_reminder.html",
            task=task,
            task_url=task_url,
            logo_url=url_for('static', filename='images/logo.png', _external=True)
        )

        msg = Message(subject=subject, recipients=recipients, html=body_html)
        
        # Send the email
        mail.send(msg)
        current_app.logger.info(f"Deadline reminder sent for task {task.id}")
    except Exception as e:
        current_app.logger.error(f"Failed to send deadline reminder for task {task.id}: {e}")

def check_and_send_deadline_notifications():
    """
    Check for tasks nearing their deadlines and send reminders.
    Scheduled to run every hour via init_scheduler.
    """
    tasks = Task.query.filter(
        Task.due_date <= datetime.now() + timedelta(hours=24),
        Task.due_date > datetime.now(),
        Task.status != 'Complete! Ready to Go!'
    ).all()

    for task in tasks:
        send_deadline_reminder(task)
