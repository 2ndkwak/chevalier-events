from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash)
from flask_login import login_required, current_user
from ..models import db, Event, RSVP, RSVPGuest, Person, DietaryTag
from ..waitlist import promote_from_waitlist
from datetime import date

portal_bp = Blueprint("portal", __name__)

@portal_bp.route("/")
@login_required
def home():
    today  = date.today()
    from datetime import datetime as _dt
    # Show events through their event day; disappear the day after
    today_start = _dt(today.year, today.month, today.day, 0, 0, 0)
    events = (Event.query
              .filter(Event.is_published == True,
                      Event.event_date >= today_start)
              .order_by(Event.event_date.asc())
              .all())
    my_rsvps = {r.event_id: r for r in
                RSVP.query.filter_by(person_id=current_user.id).all()}
    return render_template("portal/home.html", events=events, my_rsvps=my_rsvps,
                           now=_dt.utcnow())

@portal_bp.route("/event/<int:event_id>")
@login_required
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    if not event.is_published:
        flash("This event is not available.", "error")
        return redirect(url_for("portal.home"))
    my_rsvp = RSVP.query.filter_by(event_id=event_id, person_id=current_user.id).first()
    confirmed_rsvps = [r for r in event.rsvps if r.status == "confirmed"]
    waitlist_pos = None
    if my_rsvp and my_rsvp.status == "waitlist":
        from ..waitlist import group_waitlist_into_parties
        wl = [r for r in event.rsvps if r.status == "waitlist"]
        parties = group_waitlist_into_parties(wl)
        for i, (_, party_rows) in enumerate(parties, start=1):
            if my_rsvp in party_rows:
                waitlist_pos = i
                break
    from datetime import datetime as dt
    return render_template("portal/event_detail.html",
                           event=event, my_rsvp=my_rsvp,
                           confirmed_rsvps=confirmed_rsvps,
                           waitlist_pos=waitlist_pos,
                           now=dt.utcnow())

@portal_bp.route("/event/<int:event_id>/rsvp", methods=["GET", "POST"])
@login_required
def rsvp(event_id):
    event   = Event.query.get_or_404(event_id)
    my_rsvp = RSVP.query.filter_by(event_id=event_id, person_id=current_user.id).first()
    partner_rsvp = None
    if current_user.partner_id:
        candidate = RSVP.query.filter_by(
            event_id=event_id, person_id=current_user.partner_id).first()
        # Only counts as "already RSVP'd" for display purposes if it's an
        # active reservation -- a declined/expired row (e.g. from an
        # earlier cascade cancellation) should still show the add/revive
        # button, not hide it behind a false "Already RSVP'd" pill.
        if candidate and candidate.status not in ("declined", "expired"):
            partner_rsvp = candidate

    # Enforce deadline -- block new RSVPs and cancellations after close time
    from datetime import datetime as dt
    if event.rsvp_deadline and dt.utcnow() > event.rsvp_deadline:
        flash("Reservations for this event are now closed.", "error")
        return redirect(url_for("portal.event_detail", event_id=event_id))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "decline":
            if my_rsvp:
                my_rsvp.status = "declined"
                for g in list(my_rsvp.guests):
                    db.session.delete(g)

                from ..email import (send_cancellation_email, send_waitlist_promotion_email,
                                     send_partner_cancelled_email)

                # Linked partner (registered together, e.g. via the admin
                # "Add RSVP" tool or the portal's partner quick-add) --
                # cascade automatically. They registered as a package, so
                # there's no independent choice to preserve here.
                linked_partner_rsvp = my_rsvp.linked_rsvp
                cascaded_partner = None
                if linked_partner_rsvp and linked_partner_rsvp.status not in ("declined", "expired"):
                    linked_partner_rsvp.status = "declined"
                    for g in list(linked_partner_rsvp.guests):
                        db.session.delete(g)
                    cascaded_partner = linked_partner_rsvp.person

                # Partner has their OWN, separately-made RSVP (not linked)
                # -- don't assume their intent. Leave their reservation
                # alone, but give them an easy way to follow suit if they
                # want to, via a one-click emailed link.
                partner_cancel_token = None
                notify_partner = None
                if (not linked_partner_rsvp and partner_rsvp
                        and partner_rsvp.status not in ("declined", "expired")):
                    import secrets
                    from datetime import timedelta
                    from ..util import utcnow
                    partner_cancel_token = secrets.token_urlsafe(32)
                    partner_rsvp.cancel_token = partner_cancel_token
                    partner_rsvp.cancel_token_expires_at = utcnow() + timedelta(days=7)
                    notify_partner = partner_rsvp.person

                db.session.commit()

                send_cancellation_email(current_user, event,
                                        partner_still_confirmed=notify_partner,
                                        partner_cancel_token=partner_cancel_token)
                if cascaded_partner:
                    send_partner_cancelled_email(cascaded_partner, event, current_user)

                promoted = promote_from_waitlist(event, cancelled_by=current_user)
                for promoted_rsvp in promoted:
                    send_waitlist_promotion_email(promoted_rsvp.person, event, promoted_rsvp)

                if not promoted:
                    # Nothing to promote (empty waitlist, or no one
                    # fits the freed seat) -- notify the GS right away
                    # as before. If a promotion DID happen, the GS is
                    # notified once the promoted member confirms, so
                    # they get the whole story in one message.
                    _notify_admin("cancelled their reservation", event, current_user)

                if cascaded_partner:
                    flash(f"Your reservation has been cancelled, along with "
                         f"{cascaded_partner.display_name}'s linked reservation.", "success")
                else:
                    flash("Your reservation has been cancelled.", "success")
            return redirect(url_for("portal.event_detail", event_id=event_id))

        status = "confirmed"
        if event.is_full and (not my_rsvp or my_rsvp.status != "confirmed"):
            status = "waitlist"

        if not my_rsvp:
            my_rsvp = RSVP(event_id=event_id, person_id=current_user.id, status=status)
            db.session.add(my_rsvp)
            db.session.flush()
        else:
            my_rsvp.status = status

        # Partner, added via the "+ Add [partner]" quick-add: give them
        # their own real, linked RSVP (same mechanism the admin "Add RSVP"
        # tool uses) rather than folding them into the guest list below.
        # A guest-table entry has no connection back to their actual
        # Person record at all -- it can't be found by seating rules,
        # locks, or the "Guest of" display logic that a real linked RSVP
        # gets automatically. If they already have their own ACTIVE RSVP
        # for this event (e.g. they RSVP'd separately themselves), leave
        # it untouched -- nothing to do. But if their existing RSVP is
        # declined/expired (e.g. from an earlier cascade cancellation,
        # linked or not), revive it rather than silently leaving them
        # cancelled -- otherwise there'd be no way to bring a
        # cascade-cancelled partner back through the portal at all.
        include_partner = request.form.get("include_partner") == "1"
        if include_partner and current_user.partner_id:
            partner = Person.query.get(current_user.partner_id)
            if partner:
                partner_rsvp_row = RSVP.query.filter_by(
                    event_id=event_id, person_id=partner.id).first()
                if not partner_rsvp_row:
                    partner_rsvp_row = RSVP(event_id=event_id, person_id=partner.id, status=status)
                    db.session.add(partner_rsvp_row)
                    db.session.flush()
                    my_rsvp.linked_rsvp_id = partner_rsvp_row.id
                    partner_rsvp_row.linked_rsvp_id = my_rsvp.id
                elif partner_rsvp_row.status in ("declined", "expired"):
                    partner_rsvp_row.status = status
                    partner_rsvp_row.cancel_token = None
                    partner_rsvp_row.cancel_token_expires_at = None
                    my_rsvp.linked_rsvp_id = partner_rsvp_row.id
                    partner_rsvp_row.linked_rsvp_id = my_rsvp.id

        for g in list(my_rsvp.guests):
            db.session.delete(g)

        idx = 1
        while True:
            fname = request.form.get(f"guest_{idx}_first", "").strip()
            lname = request.form.get(f"guest_{idx}_last", "").strip()
            if not fname and not lname:
                break
            guest = RSVPGuest(
                rsvp_id    = my_rsvp.id,
                title      = request.form.get(f"guest_{idx}_title", "").strip() or None,
                first_name = fname or "--",
                last_name  = lname or "--",
                suffix     = request.form.get(f"guest_{idx}_suffix", "").strip() or None,
                gender     = request.form.get(f"guest_{idx}_gender", "").strip() or None,
            )
            db.session.add(guest)
            allergy_labels = request.form.get(f"guest_{idx}_allergy_tags", "").split(",")
            DietaryTag.set_from_labels(guest, allergy_labels)
            idx += 1

        db.session.commit()

        if status == "confirmed":
            _notify_admin("RSVP'd as attending", event, current_user)
            flash("You're confirmed! We look forward to seeing you.", "success")
        else:
            _notify_admin("joined the waitlist", event, current_user)
            flash("You've been added to the waitlist.", "info")

        return redirect(url_for("portal.event_detail", event_id=event_id))

    return render_template("portal/rsvp_form.html", event=event, my_rsvp=my_rsvp,
                           partner_rsvp=partner_rsvp,
                           all_tags=DietaryTag.query.order_by(DietaryTag.label).all())


@portal_bp.route("/rsvp/confirm/<token>", methods=["GET", "POST"])
def confirm_promotion(token):
    """
    Public (no login required) link from the waitlist-promotion email.
    Lets the promoted member confirm their provisional seat, or
    proactively release it so the next person can be offered it
    right away instead of waiting out the full 24-hour window.
    """
    from datetime import datetime as dt
    from ..email import send_admin_promotion_resolved_notification, send_waitlist_promotion_email

    rsvp = RSVP.query.filter_by(promotion_token=token).first_or_404()
    event = rsvp.event

    already_resolved = rsvp.status != "promoted"
    expired = (not already_resolved) and rsvp.promotion_expires_at and dt.utcnow() > rsvp.promotion_expires_at

    if request.method == "POST" and not already_resolved and not expired:
        action = request.form.get("action")
        linked = rsvp.linked_rsvp   # partner's row, if this was a paired couple promotion

        if action == "confirm":
            rsvp.status = "confirmed"
            if linked and linked.status == "promoted":
                linked.status = "confirmed"
            db.session.commit()
            send_admin_promotion_resolved_notification(event, rsvp.cancelled_by, rsvp.person)
            flash("You're confirmed! We look forward to seeing you.", "success")

        elif action == "release":
            rsvp.status = "declined"
            for g in list(rsvp.guests):
                db.session.delete(g)
            if linked and linked.status == "promoted":
                linked.status = "declined"
                for g in list(linked.guests):
                    db.session.delete(g)
            db.session.commit()
            promoted = promote_from_waitlist(event, cancelled_by=rsvp.cancelled_by)
            for new_rsvp in promoted:
                send_waitlist_promotion_email(new_rsvp.person, event, new_rsvp)
            flash("You've released your seat -- thank you for letting us know.", "info")

        return redirect(url_for("portal.confirm_promotion", token=token))

    return render_template("portal/confirm_promotion.html",
                           rsvp=rsvp, event=event,
                           already_resolved=already_resolved, expired=expired)


@portal_bp.route("/rsvp/cancel-partner/<token>", methods=["GET", "POST"])
def cancel_partner(token):
    """
    Public (no login required) link from a cancellation email, letting
    someone cancel their partner's separately-made (unlinked) RSVP too,
    since the app doesn't assume that's wanted just because they
    cancelled their own. Valid for 7 days.
    """
    from datetime import datetime as dt
    from ..email import send_partner_cancelled_email

    rsvp = RSVP.query.filter_by(cancel_token=token).first_or_404()
    event = rsvp.event

    already_resolved = rsvp.status in ("declined", "expired")
    expired_link = (not already_resolved) and rsvp.cancel_token_expires_at \
                   and dt.utcnow() > rsvp.cancel_token_expires_at

    if request.method == "POST" and not already_resolved and not expired_link:
        cancelling_person = rsvp.person.partner  # the one who cancelled first
        rsvp.status = "declined"
        for g in list(rsvp.guests):
            db.session.delete(g)
        db.session.commit()

        if cancelling_person:
            send_partner_cancelled_email(rsvp.person, event, cancelling_person)

        promoted = promote_from_waitlist(event, cancelled_by=cancelling_person)
        from ..email import send_waitlist_promotion_email
        for new_rsvp in promoted:
            send_waitlist_promotion_email(new_rsvp.person, event, new_rsvp)

        flash("Reservation cancelled.", "success")
        return redirect(url_for("portal.cancel_partner", token=token))

    return render_template("portal/cancel_partner.html", rsvp=rsvp, event=event,
                           already_resolved=already_resolved, expired=expired_link)


@portal_bp.route("/members")
@login_required
def members_list():
    """
    Member directory: one row per member/honoraire/aspirant, showing their
    spouse/partner alongside them. Grouped and sorted: Officers, then
    Members, then Membres Honoraires, then Aspirants; alphabetical by last
    name within each group.
    """
    GROUP_LABELS = [
        ("officer",  "Officers"),
        ("member",   "Members"),
        ("honoraire","Membres Honoraires"),
        ("aspirant", "Aspirants"),
    ]

    primaries = Person.query.filter(
        Person.person_type.in_(["member", "honoraire", "aspirant"])
    ).all()

    grouped = {key: [] for key, _ in GROUP_LABELS}
    for p in primaries:
        group = "officer" if p.is_officer else p.person_type
        grouped.setdefault(group, []).append(p)

    for key in grouped:
        grouped[key].sort(key=lambda p: (p.last_name.lower(), p.first_name.lower()))

    return render_template("portal/members_list.html",
                           group_labels=GROUP_LABELS,
                           grouped=grouped)


@portal_bp.route("/profile/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current  = request.form.get("current_password", "")
        new_pw   = request.form.get("new_password", "")
        confirm  = request.form.get("confirm_password", "")
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "error")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_pw != confirm:
            flash("New passwords do not match.", "error")
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash("Password updated successfully.", "success")
            return redirect(url_for("portal.home"))
    return render_template("portal/change_password.html")



@portal_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Member self-service profile page."""
    person = current_user

    if request.method == "POST":
        person.phone          = request.form.get("phone", "").strip() or None
        person.home_phone_1   = request.form.get("home_phone_1", "").strip() or None
        person.address_line1  = request.form.get("address_line1", "").strip() or None
        person.address_line2  = request.form.get("address_line2", "").strip() or None
        person.city           = request.form.get("city", "").strip() or None
        person.province_state = request.form.get("province_state", "").strip() or None
        person.postal_code    = request.form.get("postal_code", "").strip() or None
        person.country        = request.form.get("country", "").strip() or None

        person.address2_label  = request.form.get("address2_label", "").strip() or None
        person.home_phone_2    = request.form.get("home_phone_2", "").strip() or None
        person.address2_line1  = request.form.get("address2_line1", "").strip() or None
        person.address2_line2  = request.form.get("address2_line2", "").strip() or None
        person.city2           = request.form.get("city2", "").strip() or None
        person.province_state2 = request.form.get("province_state2", "").strip() or None
        person.postal_code2    = request.form.get("postal_code2", "").strip() or None
        person.country2        = request.form.get("country2", "").strip() or None

        allergy_labels = request.form.get("allergy_tags", "").split(",")
        DietaryTag.set_from_labels(person, allergy_labels)
        db.session.commit()
        flash("Your profile has been updated.", "success")
        return redirect(url_for("portal.profile"))

    return render_template("portal/profile.html", person=person,
                           all_tags=DietaryTag.query.order_by(DietaryTag.label).all())


@portal_bp.route("/profile/partner", methods=["POST"])
@login_required
def update_partner_profile():
    """
    Lets a member maintain their partner's contact/dietary info on the
    partner's behalf, for as long as the partner has no portal login of
    their own. The moment the partner is invited and activates their own
    account, this stops applying -- they manage their own data from then
    on, same as any other member. Nothing here creates a new Person record;
    it only edits the existing linked partner row, so if that partner is
    later invited, everything entered here is already sitting on their
    record with nothing to re-enter.
    """
    partner = current_user.partner

    if not partner or partner.can_login:
        flash("There's no editable partner profile for your account.", "error")
        return redirect(url_for("portal.profile"))

    partner.email          = request.form.get("email", "").strip() or None
    partner.phone          = request.form.get("phone", "").strip() or None
    partner.home_phone_1   = request.form.get("home_phone_1", "").strip() or None
    partner.address_line1  = request.form.get("address_line1", "").strip() or None
    partner.address_line2  = request.form.get("address_line2", "").strip() or None
    partner.city           = request.form.get("city", "").strip() or None
    partner.province_state = request.form.get("province_state", "").strip() or None
    partner.postal_code    = request.form.get("postal_code", "").strip() or None
    partner.country        = request.form.get("country", "").strip() or None

    allergy_labels = request.form.get("allergy_tags", "").split(",")
    DietaryTag.set_from_labels(partner, allergy_labels)

    db.session.commit()
    flash(f"{partner.first_name}'s information has been updated.", "success")
    return redirect(url_for("portal.profile"))


def _notify_admin(action, event, person):
    try:
        from ..email import send_admin_rsvp_notification
        send_admin_rsvp_notification(event, person, action)
    except Exception:
        pass
