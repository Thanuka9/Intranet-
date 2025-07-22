# audit.py
from flask import request
from extensions import db
from models import AuditLog

def log_event(event_type, user=None, target=None, **metadata):
    forwarded = request.headers.get('X-Forwarded-For', '')
    ip = forwarded.split(',')[0].strip() if forwarded else request.remote_addr

    entry = AuditLog(
        event_type    = event_type,
        actor_user_id = getattr(user, 'id', None),
        ip_address    = ip,
        target_table  = getattr(target, '__tablename__', None),
        target_id     = getattr(target, 'id', None),
        description   = metadata or None
    )
    db.session.add(entry)
    db.session.commit()
