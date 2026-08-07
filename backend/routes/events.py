from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, jsonify)
from flask_login import login_required, current_user
from ..models import db, Event, RSVP, RSVPGuest, Person, DietaryTag, EventAllergyOff
from ..routes.admin import admin_required
from datetime import datetime

events_bp = Blueprint("events", __name__)


# --- LIST ---------------------------------------------------------------------

@events_bp.route("/")
@login_required
@admin_required
def list_events():
    show = request.args.get("show", "upcoming")  # upcoming | past | all
    now = datetime.utcnow()
    if show == "upcoming":
        events = (Event.query
                  .filter(Event.event_date >= now)
                  .order_by(Event.event_date.asc())
                  .all())
    elif show == "past":
        # Archived -- most recent first so last event is at the top
        events = (Event.query
                  .filter(Event.event_date < now)
                  .order_by(Event.event_date.desc())
                  .all())
    else:
        events = Event.query.order_by(Event.event_date.asc()).all()

    from ..routes.admin import _next_action_for_event
    next_actions = {e.id: _next_action_for_event(e, now.date())
                    for e in events if e.event_date >= now}

    # Milestone dots, same as the Dashboard's upcoming-events table. Only
    # computed for upcoming/current events -- an archived event's dots are
    # frozen history that nobody needs to see again, and computing them for
    # every past event this Sous Commanderie has ever run (which the "All"
    # and "Archived" filters can surface) would mean an unbounded, ever-
    # growing set of extra per-event queries with no real payoff.
    milestone_dots = {e.id: e.milestone_dots()
                       for e in events if e.event_date >= now}

    return render_template("admin/events/list.html", events=events, show=show,
                           next_actions=next_actions, milestone_dots=milestone_dots)


# --- CREATE -------------------------------------------------------------------

@events_bp.route("/create", methods=["GET", "POST"])
@login_required
@admin_required
def create_event():
    if request.method == "POST":
        event = Event()
        _event_from_form(event, request.form)
        db.session.add(event)
        db.session.commit()
        flash(f"'{event.title}' created.", "success")
        return redirect(url_for("events.edit_event", event_id=event.id))
    return render_template("admin/events/form.html", event=None)


# --- EDIT ---------------------------------------------------------------------

@events_bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    if request.method == "POST":
        _event_from_form(event, request.form)
        db.session.commit()
        flash("Event updated.", "success")
        return redirect(url_for("events.edit_event", event_id=event.id))
    return render_template("admin/events/form.html", event=event)


# --- PUBLISH / UNPUBLISH ------------------------------------------------------

@events_bp.route("/<int:event_id>/publish", methods=["POST"])
@login_required
@admin_required
def publish_event(event_id):
    event = Event.query.get_or_404(event_id)
    event.is_published = True
    db.session.commit()
    flash(f"'{event.title}' is now published to the member portal.", "success")
    return redirect(url_for("events.edit_event", event_id=event.id))


@events_bp.route("/<int:event_id>/unpublish", methods=["POST"])
@login_required
@admin_required
def unpublish_event(event_id):
    event = Event.query.get_or_404(event_id)
    event.is_published = False
    db.session.commit()
    flash(f"'{event.title}' has been unpublished.", "success")
    return redirect(url_for("events.edit_event", event_id=event.id))


# --- DELETE -------------------------------------------------------------------

@events_bp.route("/<int:event_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    title = event.title
    # Two RSVP rows that reference each other via linked_rsvp_id (couples,
    # confirmed or waitlisted together) form a circular reference that
    # SQLAlchemy can't figure out a delete order for on its own -- clear
    # the links first so each row deletes independently.
    for rsvp in event.rsvps:
        rsvp.linked_rsvp_id = None
    db.session.flush()
    db.session.delete(event)
    db.session.commit()
    flash(f"'{title}' has been deleted.", "success")
    return redirect(url_for("events.list_events"))


# --- RSVP LIST (admin view) ---------------------------------------------------

@events_bp.route("/<int:event_id>/rsvps")
@login_required
@admin_required
def rsvp_list(event_id):
    event     = Event.query.get_or_404(event_id)
    confirmed = [r for r in event.rsvps if r.status == "confirmed"]
    promoted  = [r for r in event.rsvps if r.status == "promoted"]
    waitlist  = [r for r in event.rsvps if r.status == "waitlist"]
    declined  = [r for r in event.rsvps if r.status in ("declined", "expired")]

    sort = request.args.get("sort", "last_name")
    key_func = _rsvp_sort_key(sort)
    confirmed.sort(key=key_func)
    promoted.sort(key=key_func)
    waitlist.sort(key=key_func)
    declined.sort(key=key_func)

    from ..models import Person as _P
    existing_person_ids = [r.person_id for r in event.rsvps]
    all_persons = (_P.query
                   .filter(_P.person_type.in_(["member", "honoraire", "aspirant"]))
                   .filter(~_P.id.in_(existing_person_ids))
                   .all())
    # Chevaliers first, Honoraires/Aspirants at the bottom; alphabetical within each group.
    all_persons.sort(key=lambda p: (
        0 if p.person_type == "member" else 1,
        (p.last_name or "").lower(),
        (p.first_name or "").lower(),
    ))
    total_collected = sum(float(r.amount_paid) for r in confirmed if r.amount_paid)
    return render_template("admin/events/rsvps.html",
                           event=event,
                           confirmed=confirmed,
                           promoted=promoted,
                           waitlist=waitlist,
                           declined=declined,
                           all_persons=all_persons,
                           total_collected=total_collected,
                           sort=sort)


# --- ADMIN: MANUALLY ADD RSVP ------------------------------------------------

@events_bp.route("/<int:event_id>/rsvps/add", methods=["POST"])
@login_required
@admin_required
def admin_add_rsvp(event_id):
    event     = Event.query.get_or_404(event_id)
    person_id = request.form.get("person_id", type=int)
    person    = Person.query.get_or_404(person_id)

    added = []   # list of (display_name, status) tuples

    def confirmed_count_fresh():
        """event.confirmed_count, forced to re-read from the database.
        Rows below are created with event_id= (a raw foreign key), not
        event= (the relationship object), so SQLAlchemy's back_populates
        sync never touches the in-memory event.rsvps collection -- without
        expiring it here, this would keep reading the pre-flush count and
        let a party go fully confirmed straight through capacity."""
        db.session.expire(event, ["rsvps"])
        return event.confirmed_count

    existing = RSVP.query.filter_by(event_id=event_id, person_id=person_id).first()

    include_partner = request.form.get("include_partner") == "1"
    partner = None
    partner_existing = None
    if include_partner and person.partner_id:
        partner = Person.query.get(person.partner_id)
        if partner:
            partner_existing = RSVP.query.filter_by(
                event_id=event_id, person_id=partner.id).first()

    both_new = (not existing) and partner and not partner_existing

    if both_new:
        # A brand-new couple, added together: one atomic 2-seat party.
        # Either both fit or both wait -- never split across the last seat.
        if event.capacity is None:
            status = "confirmed"
        else:
            seats_left = event.capacity - confirmed_count_fresh()
            status = "confirmed" if seats_left >= 2 else "waitlist"

        rsvp1 = RSVP(event_id=event_id, person_id=person.id, status=status)
        rsvp2 = RSVP(event_id=event_id, person_id=partner.id, status=status)
        db.session.add(rsvp1)
        db.session.add(rsvp2)
        db.session.flush()   # both need ids before they can link to each other
        rsvp1.linked_rsvp_id = rsvp2.id
        rsvp2.linked_rsvp_id = rsvp1.id
        added = [(person.display_name, status), (partner.display_name, status)]

    else:
        def next_status():
            """Confirmed if a seat is open right now, waitlist otherwise --
            recomputed live so a second person added in the same request
            correctly lands on the waitlist if the first just filled the
            last seat."""
            if event.capacity is None:
                return "confirmed"
            return "waitlist" if confirmed_count_fresh() >= event.capacity else "confirmed"

        if not existing:
            status = next_status()
            rsvp = RSVP(event_id=event_id, person_id=person_id, status=status)
            db.session.add(rsvp)
            db.session.flush()   # so confirmed_count reflects this row before the partner check
            added.append((person.display_name, status))

        if partner and not partner_existing:
            status = next_status()
            partner_rsvp = RSVP(event_id=event_id,
                                person_id=partner.id, status=status)
            db.session.add(partner_rsvp)
            added.append((partner.display_name, status))

    db.session.commit()

    if added:
        names = " & ".join(name for name, _ in added)
        if any(status == "waitlist" for _, status in added):
            flash(f"Added: {names}. (Event is full -- added to the waitlist.)", "warning")
        else:
            flash(f"Added: {names}.", "success")
    else:
        flash(f"{person.display_name} already has an RSVP for this event.", "error")

    return redirect(url_for("events.rsvp_list", event_id=event_id))


# --- ADMIN: EDIT RSVP GUESTS -------------------------------------------------

@events_bp.route("/<int:event_id>/rsvps/<int:rsvp_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_rsvp(event_id, rsvp_id):
    event = Event.query.get_or_404(event_id)
    rsvp  = RSVP.query.get_or_404(rsvp_id)

    if request.method == "POST":
        # Replace all guests
        for g in list(rsvp.guests):
            db.session.delete(g)

        idx = 1
        while True:
            fname = request.form.get(f"guest_{idx}_first", "").strip()
            lname = request.form.get(f"guest_{idx}_last", "").strip()
            if not fname and not lname:
                break
            guest = RSVPGuest(
                rsvp_id    = rsvp.id,
                title      = request.form.get(f"guest_{idx}_title", "").strip() or None,
                first_name = fname or "--",
                last_name  = lname or "--",
                suffix     = request.form.get(f"guest_{idx}_suffix", "").strip() or None,
                gender     = request.form.get(f"guest_{idx}_gender", "").strip() or None,
                is_officer = bool(request.form.get(f"guest_{idx}_is_officer")),
                officer_title = request.form.get(f"guest_{idx}_officer_title", "").strip() or None,
            )
            db.session.add(guest)
            allergy_labels = request.form.get(f"guest_{idx}_allergy_tags", "").split(",")
            DietaryTag.set_from_labels(guest, allergy_labels)
            idx += 1

        rsvp.status = request.form.get("status", rsvp.status)
        db.session.commit()
        flash(f"{rsvp.person.display_name}'s RSVP updated.", "success")
        return redirect(url_for("events.rsvp_list", event_id=event_id))

    return render_template("admin/events/edit_rsvp.html", event=event, rsvp=rsvp,
                           all_tags=DietaryTag.query.order_by(DietaryTag.label).all())


# --- ADMIN: PROMOTE FROM WAITLIST --------------------------------------------

@events_bp.route("/<int:event_id>/rsvps/<int:rsvp_id>/promote", methods=["POST"])
@login_required
@admin_required
def promote_waitlist(event_id, rsvp_id):
    rsvp = RSVP.query.get_or_404(rsvp_id)
    rsvp.status = "confirmed"
    names = [rsvp.person.display_name]
    if rsvp.linked_rsvp and rsvp.linked_rsvp.status in ("waitlist", "promoted"):
        rsvp.linked_rsvp.status = "confirmed"
        names.append(rsvp.linked_rsvp.person.display_name)
    db.session.commit()
    flash(f"{' & '.join(names)} moved from waitlist to confirmed.", "success")
    _notify_admin_rsvp_change(rsvp.event, rsvp.person, "promoted from waitlist")
    return redirect(url_for("events.rsvp_list", event_id=event_id))


# --- ADMIN: REMOVE RSVP ------------------------------------------------------

@events_bp.route("/<int:event_id>/rsvps/<int:rsvp_id>/remove", methods=["POST"])
@login_required
@admin_required
def remove_rsvp(event_id, rsvp_id):
    rsvp = RSVP.query.get_or_404(rsvp_id)
    event = rsvp.event
    name = rsvp.person.display_name
    freed_seat = rsvp.status in ("confirmed", "promoted")
    cancelled_by = rsvp.cancelled_by if rsvp.status == "promoted" else rsvp.person

    # If this was one half of a still-pending promoted couple, don't
    # strand the other partner in limbo -- put them back on the
    # waitlist (at their original position) rather than deleting them.
    if rsvp.status == "promoted" and rsvp.linked_rsvp and rsvp.linked_rsvp.status == "promoted":
        partner = rsvp.linked_rsvp
        partner.status = "waitlist"
        partner.linked_rsvp_id = None
        partner.promotion_token = None
        partner.promoted_at = None
        partner.promotion_expires_at = None
        name += f" (their partner, {partner.person.display_name}, has been returned to the waitlist)"

    db.session.delete(rsvp)
    db.session.commit()
    flash(f"{name}'s RSVP removed.", "success")

    if freed_seat:
        from ..waitlist import promote_from_waitlist
        from ..email import send_waitlist_promotion_email
        promoted = promote_from_waitlist(event, cancelled_by=cancelled_by)
        for new_rsvp in promoted:
            send_waitlist_promotion_email(new_rsvp.person, event, new_rsvp)
            flash(f"{new_rsvp.person.display_name} has been offered the open seat.", "info")

    return redirect(url_for("events.rsvp_list", event_id=event_id))


# --- ALLERGIES -----------------------------------------------------------------

@events_bp.route("/<int:event_id>/allergies")
@login_required
@admin_required
def allergies(event_id):
    event = Event.query.get_or_404(event_id)
    tag_rows = event.attending_dietary_tags()
    active_ids = {t["tag"].id for t in tag_rows if t["active"]}

    # Guest list scoped to this page: one row per attendee, with their tags
    # marked active/inactive against this event's current toggles.
    guests = []
    for rsvp in event.rsvps:
        if rsvp.status != "confirmed":
            continue
        people = [rsvp.person] + list(rsvp.guests)
        for p in people:
            if not p.dietary_tags:
                continue
            guests.append({
                "name": p.display_name,
                "tags": [{"tag": t, "active": t.id in active_ids}
                         for t in p.dietary_tags],
            })
    guests.sort(key=lambda g: g["name"].split()[-1].lower())

    return render_template("admin/events/allergies.html",
                           event=event, tag_rows=tag_rows, guests=guests)


@events_bp.route("/<int:event_id>/allergies/mark_reviewed", methods=["POST"])
@login_required
@admin_required
def mark_allergies_reviewed(event_id):
    """The one deliberately non-automatic step for this milestone --
    confirms the GS has looked over the current allergy list and
    considers it correct for this event. Deliberately never resets on its
    own; see Event.allergies_reviewed."""
    event = Event.query.get_or_404(event_id)
    event.allergies_reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Allergy list marked correct for event.", "success")
    return redirect(url_for("events.allergies", event_id=event_id))


@events_bp.route("/<int:event_id>/allergies/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_allergy(event_id):
    event = Event.query.get_or_404(event_id)
    tag_id = request.form.get("tag_id", type=int)
    tag = DietaryTag.query.get_or_404(tag_id)

    existing = EventAllergyOff.query.filter_by(event_id=event_id, tag_id=tag_id).first()
    if existing:
        db.session.delete(existing)          # was off -> turn back on
    else:
        db.session.add(EventAllergyOff(event_id=event_id, tag_id=tag_id))  # was on -> turn off
    db.session.commit()

    if request.form.get("ajax"):
        return jsonify({"ok": True, "active": existing is not None})
    return redirect(url_for("events.allergies", event_id=event_id))


# --- PROMOTE EMAIL BLAST ------------------------------------------------------

@events_bp.route("/<int:event_id>/promote", methods=["POST"])
@login_required
@admin_required
def send_promotion(event_id):
    event = Event.query.get_or_404(event_id)
    # Every person with an email address gets the promotion, regardless of
    # person_type or whether they've ever activated a portal account --
    # this is meant to reach the whole membership (Chevaliers, Honoraires,
    # Aspirants, Partners of any kind), not just those who happen to have
    # logged in before.
    recipients = Person.query.filter(
        Person.email.isnot(None)
    ).all()

    from ..email import send_event_promotion
    sent, failed = 0, 0
    for person in recipients:
        try:
            send_event_promotion(event, person)
            sent += 1
        except Exception:
            failed += 1

    event.promotion_sent_at = datetime.utcnow()
    db.session.commit()

    msg = f"Promotion sent to {sent} member{'' if sent==1 else 's'}."
    if failed:
        msg += f" {failed} failed (check email settings)."
    flash(msg, "success" if not failed else "error")
    return redirect(url_for("events.edit_event", event_id=event_id))


# --- HELPERS ------------------------------------------------------------------

def _rsvp_sort_key(sort):
    """Return a key function for sorting a list of RSVP objects."""
    def last_name(r):
        if r.person is None:
            return (1, "", "")
        return (0, (r.person.last_name or "").lower(), (r.person.first_name or "").lower())

    def first_name(r):
        return (0, (r.person.first_name or "").lower(), (r.person.last_name or "").lower())

    def officer(r):
        return (0 if r.person.is_officer else 1,
                (r.person.officer_role or "").lower(),
                (r.person.last_name or "").lower(),
                (r.person.first_name or "").lower())

    def dietary(r):
        has_dietary = bool(r.person.dietary_tags) or any(g.dietary_tags for g in r.guests)
        return (0 if has_dietary else 1,
                (r.person.last_name or "").lower(),
                (r.person.first_name or "").lower())

    return {
        "last_name":  last_name,
        "first_name": first_name,
        "officer":    officer,
        "dietary":    dietary,
    }.get(sort, last_name)


def _event_from_form(event, form):
    event.title       = form.get("title", "").strip()
    event.venue_name  = form.get("venue_name", "").strip() or None
    event.venue_address = form.get("venue_address", "").strip() or None
    event.teaser       = form.get("teaser", "").strip() or None
    event.description = form.get("description", "").strip() or None
    event.dress_code  = form.get("dress_code", "").strip() or None
    event.hosts       = form.get("hosts", "").strip() or None
    event.chef_name   = form.get("chef_name", "").strip() or None
    paypal_link = form.get("paypal_link", "").strip()
    if paypal_link and not paypal_link.lower().startswith(("http://", "https://")):
        paypal_link = "https://" + paypal_link
    event.paypal_link = paypal_link or None
    # menu_finalized no longer read/written here -- Event.menu_uploaded
    # (whether any MenuItem rows exist) replaced it as the dashboard's
    # signal, so there's nothing left for this checkbox to control.
    paypal_price_str = form.get("paypal_price_per_person", "").strip()
    if paypal_price_str:
        try:
            from decimal import Decimal
            event.paypal_price_per_person = Decimal(paypal_price_str)
        except Exception:
            pass
    else:
        event.paypal_price_per_person = None
    event.capacity         = form.get("capacity", type=int) or None
    price_str = form.get("price_per_person", "").strip()
    if price_str:
        try:
            from decimal import Decimal
            event.price_per_person = Decimal(price_str)
        except Exception:
            pass
    else:
        event.price_per_person = None

    date_str = form.get("event_date", "").strip()
    time_str = form.get("event_time", "18:30").strip()
    if date_str:
        try:
            event.event_date = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    deadline_date = form.get("rsvp_deadline_date", "").strip()
    deadline_time = form.get("rsvp_deadline_time", "17:00").strip()
    if deadline_date:
        try:
            event.rsvp_deadline = datetime.strptime(
                f"{deadline_date} {deadline_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    elif date_str and not event.id:
        # Auto-default: 3 days before event at 17:00
        from datetime import timedelta
        try:
            event_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            event.rsvp_deadline = (event_dt - timedelta(days=3)).replace(
                hour=17, minute=0, second=0, microsecond=0)
        except ValueError:
            pass

    return event


def _notify_admin_rsvp_change(event, person, action):
    """Fire-and-forget admin notification -- errors are swallowed gracefully."""
    try:
        from ..email import send_admin_rsvp_notification
        send_admin_rsvp_notification(event, person, action)
    except Exception:
        pass


@events_bp.route("/<int:event_id>/rsvp/<int:rsvp_id>/payment", methods=["POST"])
@login_required
@admin_required
def update_payment(event_id, rsvp_id):
    """Update payment status for an RSVP."""
    from ..models import RSVP
    rsvp = RSVP.query.get_or_404(rsvp_id)
    rsvp.payment_status = request.form.get("payment_status", "unpaid")
    amt = request.form.get("amount_paid", "").strip()
    rsvp.amount_paid  = float(amt) if amt else None
    rsvp.payment_note = request.form.get("payment_note", "").strip() or None
    db.session.commit()
    flash(f"Payment updated for {rsvp.person.display_name}.", "success")
    return redirect(url_for("events.rsvp_list", event_id=event_id))
