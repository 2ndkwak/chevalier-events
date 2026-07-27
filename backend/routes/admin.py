from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from datetime import date, datetime
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
    now = datetime.utcnow()
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

    # --- Pending & Recent Changes card ---

    # Pending waitlist offers: promoted, not yet resolved. Soonest-to-expire first.
    pending_offers = (RSVP.query
                       .filter(RSVP.status == "promoted")
                       .order_by(RSVP.promotion_expires_at.asc())
                       .all())
    pending_offer_items = []
    for r in pending_offers:
        hours_left = None
        if r.promotion_expires_at:
            delta = r.promotion_expires_at - now
            hours_left = max(0, int(delta.total_seconds() // 3600))
        pending_offer_items.append({"rsvp": r, "hours_left": hours_left})

    # Outstanding invites: sent but not yet accepted. Longest-outstanding first.
    outstanding_invites_query = (Person.query
                                  .filter(Person.invite_sent_at.isnot(None),
                                          Person.can_login == False)
                                  .order_by(Person.invite_sent_at.asc()))
    outstanding_invites_total = outstanding_invites_query.count()
    outstanding_invites = outstanding_invites_query.limit(5).all()
    outstanding_invites_more = max(0, outstanding_invites_total - len(outstanding_invites))

    # Recent roster changes: new additions + person_type changes, newest first.
    roster_additions = (Person.query
                         .filter(Person.person_type.in_(["member", "honoraire", "aspirant"]))
                         .order_by(Person.created_at.desc())
                         .limit(8).all())
    roster_promotions = (Person.query
                          .filter(Person.person_type_updated_at.isnot(None))
                          .order_by(Person.person_type_updated_at.desc())
                          .limit(8).all())
    roster_changes = []
    for p in roster_additions:
        roster_changes.append({"person": p, "when": p.created_at, "kind": "added"})
    for p in roster_promotions:
        roster_changes.append({"person": p, "when": p.person_type_updated_at, "kind": "type_changed"})
    roster_changes.sort(key=lambda r: r["when"], reverse=True)
    roster_changes = roster_changes[:6]

    # Dietary tag edits, but only for people attending an upcoming event.
    upcoming_person_ids = {
        r.person_id for r in
        RSVP.query.join(Event)
        .filter(Event.event_date >= f"{today}",
                RSVP.status.in_(["confirmed", "waitlist", "promoted"]))
        .all()
    }
    dietary_edits = []
    if upcoming_person_ids:
        dietary_edits = (Person.query
                          .filter(Person.dietary_tags_updated_at.isnot(None),
                                  Person.id.in_(upcoming_person_ids))
                          .order_by(Person.dietary_tags_updated_at.desc())
                          .limit(6).all())

    next_actions = {e.id: _next_action_for_event(e, today) for e in upcoming}

    return render_template("admin/dashboard.html",
                           upcoming=upcoming,
                           total_members=total_members,
                           total_honoraire=total_honoraire,
                           total_aspirants=total_aspirants,
                           total_partners=total_partners,
                           pending_offer_items=pending_offer_items,
                           outstanding_invites=outstanding_invites,
                           outstanding_invites_more=outstanding_invites_more,
                           roster_changes=roster_changes,
                           dietary_edits=dietary_edits,
                           next_actions=next_actions)


def _next_action_for_event(event, today):
    """One-line status for the dashboard's upcoming events table, reflecting
    where this event currently sits in the Grand Senechal's workflow."""
    if not event.menu_finalized:
        return "Waiting on chef's menu proposal"
    if not event.price_per_person:
        return "Set pricing & PayPal link"
    if not event.wine_tags:
        return "Send menu to Cellarer for wine selection"

    days_out = (event.event_date.date() - today).days
    if days_out > 2:
        waitlist_count = sum(1 for r in event.rsvps if r.status == "waitlist")
        return f"Promoting -- {event.confirmed_count} confirmed, {waitlist_count} waitlist"

    materials_total = 5
    materials_done = len(event.materials)
    if materials_done < materials_total:
        return f"Materials: {materials_done} of {materials_total} done"

    return "Ready for event day"
