from flask import Blueprint, request, jsonify, current_app
import secrets
import sys
import traceback

# Under gunicorn, stdout isn't connected to a terminal, so Python
# block-buffers it by default -- print() statements below would
# otherwise sit invisible in a buffer indefinitely. Same fix already
# used in routes/seating.py, routes/events.py, routes/members.py.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

webhooks_bp = Blueprint("webhooks", __name__)


def _webhook_authorized():
    """HTTP Basic Auth check for incoming Postmark webhook calls -- the
    credentials live directly in the webhook URL configured in Postmark's
    dashboard (https://user:pass@yourdomain.com/webhooks/postmark), which
    is Postmark's own documented approach for this. Uses
    secrets.compare_digest for both fields to avoid a timing side-channel,
    and fails closed (rejects) if either side of the credential isn't
    configured, rather than accepting an unauthenticated request just
    because setup was incomplete."""
    expected_user = current_app.config.get("POSTMARK_WEBHOOK_USERNAME")
    expected_pass = current_app.config.get("POSTMARK_WEBHOOK_PASSWORD")
    if not expected_user or not expected_pass:
        return False
    auth = request.authorization
    if not auth:
        return False
    return (secrets.compare_digest(auth.username or "", expected_user) and
            secrets.compare_digest(auth.password or "", expected_pass))


@webhooks_bp.route("/webhooks/postmark", methods=["POST"])
def postmark_webhook():
    """Receives Bounce and Open events from Postmark (Aug 2026) for both
    the promotion blast and invite emails -- see the per-send metadata
    (X-PM-Metadata-kind / -person-id / -event-id) set in email.py, which
    Postmark echoes back on every webhook event for that message so we
    know exactly which (event, person) or person a given event is about,
    without needing to guess from timing or match on email address alone.

    Always returns 200, even when something inside fails -- Postmark's
    own guidance is that a non-200 response triggers a retry, and
    retrying a genuine processing bug just repeats the same failure
    (and spams logs) rather than fixing anything; better to log the
    error here for us to investigate and acknowledge receipt regardless."""
    if not _webhook_authorized():
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    record_type = payload.get("RecordType")
    metadata = payload.get("Metadata") or {}
    kind = metadata.get("kind")
    person_id = metadata.get("person-id")
    event_id = metadata.get("event-id")
    adhoc_email_id = metadata.get("adhoc-email-id")

    try:
        from ..models import db, Person, EventPromotionSend, AdHocEmailSend
        from ..util import utcnow

        if record_type == "Bounce":
            person = None
            if person_id:
                person = Person.query.get(int(person_id))
            if not person:
                email = payload.get("Email")
                if email:
                    person = Person.query.filter_by(email=email).first()
            if person:
                person.email_bounced_at = utcnow()
                person.email_bounce_type = payload.get("Type")  # e.g. "HardBounce", "SoftBounce"
                db.session.commit()
                print(f"[postmark webhook] bounce recorded for person {person.id} "
                      f"({person.email}): {payload.get('Type')}", flush=True)
            else:
                print(f"[postmark webhook] bounce event, but no matching person "
                      f"(person-id={person_id!r}, email={payload.get('Email')!r})", flush=True)

        elif record_type == "Open":
            if kind == "promotion" and person_id and event_id:
                row = EventPromotionSend.query.filter_by(
                    event_id=int(event_id), person_id=int(person_id)
                ).first()
                if row and not row.opened_at:
                    # First-open-wins: "when did they first see this" is
                    # the useful question, not a jittery timestamp that
                    # moves every time their mail client re-fetches the
                    # tracking pixel on a repeat view.
                    row.opened_at = utcnow()
                    db.session.commit()
                    print(f"[postmark webhook] promotion open recorded: "
                          f"event {event_id}, person {person_id}", flush=True)
            elif kind == "invite" and person_id:
                person = Person.query.get(int(person_id))
                if person and not person.invite_opened_at:
                    person.invite_opened_at = utcnow()
                    db.session.commit()
                    print(f"[postmark webhook] invite open recorded: person {person_id}", flush=True)
            elif kind == "adhoc" and person_id and adhoc_email_id:
                row = AdHocEmailSend.query.filter_by(
                    adhoc_email_id=int(adhoc_email_id), person_id=int(person_id)
                ).first()
                if row and not row.opened_at:
                    row.opened_at = utcnow()
                    db.session.commit()
                    print(f"[postmark webhook] adhoc email open recorded: "
                          f"email {adhoc_email_id}, person {person_id}", flush=True)
            else:
                print(f"[postmark webhook] open event with unrecognized/missing "
                      f"metadata: {metadata!r}", flush=True)
    except Exception:
        print("[postmark webhook] error processing event:", flush=True)
        traceback.print_exc()

    return jsonify({"ok": True}), 200
