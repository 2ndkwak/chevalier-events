from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from datetime import date
from ..models import Person, Event, RSVP

admin_bp = Blueprint("admin", __name__)

def admin_required(f):
    """Decorator: must be logged in as admin."""
    from functools import wraps
    from flask import abort
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    today = date.today()
    upcoming = (Event.query
                .filter(Event.is_published == True,
                        Event.event_date >= f"{today}")
                .order_by(Event.event_date.asc())
                .limit(5).all())
    total_members   = Person.query.filter_by(person_type="member").count()
    total_honoraire = Person.query.filter_by(person_type="honoraire").count()
    total_aspirants = Person.query.filter_by(person_type="aspirant").count()
    total_partners  = Person.query.filter(
        Person.person_type == "partner",
        Person.partner_id.in_(
            Person.query.with_entities(Person.id).filter_by(person_type="member")
        )
    ).count()
    return render_template("admin/dashboard.html",
                           upcoming=upcoming,
                           total_members=total_members,
                           total_honoraire=total_honoraire,
                           total_aspirants=total_aspirants,
                           total_partners=total_partners)
