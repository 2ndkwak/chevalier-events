from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, current_app)
from flask_login import login_required, current_user
from ..models import db, Person, AdHocEmail, AdHocEmailSend
from ..routes.admin import admin_required
from ..util import utcnow
import threading
import sys
import traceback

# Under gunicorn, stdout isn't connected to a terminal, so Python
# block-buffers it by default -- print() statements from a background
# thread would otherwise sit invisible in a buffer indefinitely. Same
# fix already used in routes/events.py, routes/members.py,
# routes/seating.py, routes/webhooks.py.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

broadcast_bp = Blueprint("broadcast", __name__)


# Recipient filter groups for the compose screen (Aug 2026 "Send Email"
# feature -- per the design conversation with Trey). Deliberately NOT
# the same 3-heading/6-type grouping the Members list itself uses:
# there, Partner Member Chevalier is grouped under "Chevaliers" because
# she's a full Chevalier in her own right and prints that way in the
# menu booklet. Here, all three partner sub-types are pooled into one
# "Partners" checkbox instead, so every one of these four buckets is a
# clean, non-overlapping group -- nobody is eligible under two
# different checkboxes at once, which matters for a "select some
# combination of groups plus a few individuals" UI in a way it doesn't
# for a name-ordered print list.
ADHOC_FILTER_GROUPS = [
    ("chevaliers", "Chevaliers",  ["member"]),
    ("honoraires", "Honoraires",  ["honoraire"]),
    ("aspirants",  "Aspirants",   ["aspirant"]),
    ("partners",   "Partners",    ["partner", "partner_member_chevalier",
                                    "partner_non_member_chevalier"]),
]
ADHOC_ELIGIBLE_TYPES = [t for _, _, types in ADHOC_FILTER_GROUPS for t in types]


@broadcast_bp.route("/compose")
@login_required
@admin_required
def compose():
    """The 'Send Email' screen: filter checkboxes + individual search
    both feed the same live roster, entirely client-side -- the full
    eligible list (everyone in one of the four groups with an email on
    file) is small enough to embed directly rather than round-tripping
    to the server on every keystroke, same reasoning as the seating
    screen embedding its guest roster for drag-and-drop."""
    type_to_group = {t: key for key, _, types in ADHOC_FILTER_GROUPS for t in types}

    persons = (Person.query
               .filter(Person.person_type.in_(ADHOC_ELIGIBLE_TYPES),
                       Person.email.isnot(None))
               .order_by(Person.last_name, Person.first_name)
               .all())

    roster = [{"id": p.id, "name": p.display_name, "email": p.email,
               "group": type_to_group.get(p.person_type)} for p in persons]

    group_counts = []
    for key, label, types in ADHOC_FILTER_GROUPS:
        count = sum(1 for p in persons if p.person_type in types)
        group_counts.append({"key": key, "label": label, "count": count})

    return render_template("admin/broadcast/compose.html",
                           roster=roster, group_counts=group_counts)


def _send_adhoc_batch(app, adhoc_email_id, person_ids):
    """The actual ad-hoc-email work, run on a background thread so the
    request that kicks it off returns immediately -- same reasoning,
    and the same shape, as _send_promotion_batch (routes/events.py) and
    _send_invite_batch (routes/members.py): a single shared Broadcast
    connection for the whole run, and each individual success logged
    and committed right away (not batched at the end) so an interrupted
    run leaves accurate partial progress and can simply be re-run,
    skipping anyone already logged in AdHocEmailSend, rather than
    risking a duplicate or a silent gap."""
    base_url = app.config.get("SITE_BASE_URL", "http://localhost:5000")
    with app.app_context(), app.test_request_context(base_url=base_url):
        from ..email import send_adhoc_email
        try:
            adhoc_email = AdHocEmail.query.get(adhoc_email_id)
            if not adhoc_email:
                return

            already_sent_ids = {row.person_id for row in AdHocEmailSend.query
                                 .filter_by(adhoc_email_id=adhoc_email_id).all()}
            remaining_ids = [pid for pid in person_ids if pid not in already_sent_ids]
            print(f"[adhoc email] email {adhoc_email_id}: {len(remaining_ids)} "
                  f"of {len(person_ids)} recipient(s) remaining", flush=True)

            from ..postmark import broadcast_connection
            broadcast_headers = {"X-PM-Message-Stream": app.config["POSTMARK_BROADCAST_STREAM_ID"]}
            sent_count = 0
            with broadcast_connection(app) as connection:
                for person_id in remaining_ids:
                    person = Person.query.get(person_id)
                    if not person or not person.email:
                        continue
                    try:
                        send_adhoc_email(adhoc_email.subject, adhoc_email.body_html, person,
                                          adhoc_email_id=adhoc_email.id, connection=connection,
                                          extra_headers=broadcast_headers)
                        db.session.add(AdHocEmailSend(adhoc_email_id=adhoc_email.id,
                                                       person_id=person.id, sent_at=utcnow()))
                        db.session.commit()
                        sent_count += 1
                    except Exception:
                        db.session.rollback()
                        # One bad address shouldn't stop the rest of the
                        # batch -- but it must stay visible in the logs,
                        # not silently swallowed (see the Aug 11 2026
                        # promotion-send incident this pattern was
                        # already fixed for).
                        print(f"[adhoc email] FAILED for person {person_id} "
                              f"({person.email}):", flush=True)
                        traceback.print_exc()
                        continue

            print(f"[adhoc email] email {adhoc_email_id}: batch complete, "
                  f"{sent_count} sent this run", flush=True)
        except Exception:
            print(f"[adhoc email] email {adhoc_email_id}: batch-level failure "
                  f"(e.g. mail.connect() itself failed):", flush=True)
            traceback.print_exc()
        finally:
            db.session.remove()


@broadcast_bp.route("/send", methods=["POST"])
@login_required
@admin_required
def send():
    subject = (request.form.get("subject") or "").strip()
    body_html = request.form.get("body") or ""
    person_ids = [int(pid) for pid in request.form.getlist("person_ids") if pid.isdigit()]

    if not subject:
        flash("Subject is required.", "error")
        return redirect(url_for("broadcast.compose"))
    if not person_ids:
        flash("Select at least one recipient before sending.", "error")
        return redirect(url_for("broadcast.compose"))

    adhoc_email = AdHocEmail(subject=subject, body_html=body_html,
                             sender_id=current_user.id,
                             recipient_count=len(person_ids),
                             created_at=utcnow())
    db.session.add(adhoc_email)
    db.session.commit()

    app = current_app._get_current_object()
    thread = threading.Thread(target=_send_adhoc_batch,
                              args=(app, adhoc_email.id, person_ids), daemon=True)
    thread.start()

    flash(f"Sending to {len(person_ids)} recipient(s) in the background -- "
          f"see Email History for delivery status as it comes in.", "success")
    return redirect(url_for("broadcast.history"))


@broadcast_bp.route("/send-test", methods=["POST"])
@login_required
@admin_required
def send_test():
    """Send the exact same email to the logged-in admin only, so
    formatting/links can be checked in a real inbox before the real
    send goes out. Deliberately does NOT create or touch any
    AdHocEmail/AdHocEmailSend row -- a test send is not part of the
    Email History, matching send_promotion_test()'s same choice for
    event promotions."""
    subject = (request.form.get("subject") or "").strip()
    body_html = request.form.get("body") or ""

    if not current_user.email:
        flash("Your own account has no email address on file, so a test send has nowhere to go.", "error")
        return redirect(url_for("broadcast.compose"))
    if not subject:
        flash("Enter a subject before sending a test.", "error")
        return redirect(url_for("broadcast.compose"))

    from ..email import send_adhoc_email
    try:
        send_adhoc_email(subject, body_html, current_user)
        flash(f"Test email sent to {current_user.email}.", "success")
    except Exception:
        flash("Test send failed (check email settings).", "error")
    return redirect(url_for("broadcast.compose"))


@broadcast_bp.route("/history")
@login_required
@admin_required
def history():
    emails = AdHocEmail.query.order_by(AdHocEmail.created_at.desc()).all()
    return render_template("admin/broadcast/history.html", emails=emails)
