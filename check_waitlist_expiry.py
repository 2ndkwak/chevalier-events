"""
Waitlist offer expiry sweep.

Finds any "promoted" RSVPs whose 24-hour confirmation window has
passed, marks them expired, lets that person know, and offers the
seat onward to the next person in line -- carrying forward the
original cancellation reference so the eventual GS notification still
tells the whole story.

Intended to run on a schedule (e.g. every 15 minutes via systemd
timer), independent of anyone browsing the site, so it keeps working
right up to the event even after normal RSVPs have closed.

Run manually with:
    python3 check_waitlist_expiry.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from backend.app import create_app
from backend.models import db, RSVP
from backend.waitlist import promote_from_waitlist
from backend.email import send_promotion_expired_email, send_waitlist_promotion_email
from backend.util import utcnow

# Explicit absolute DB path (same computation used by the migration
# scripts) so this resolves correctly no matter what working directory
# the systemd timer launches it from.
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "chevalier.db")


def main():
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{_DB_PATH}"})
    with app.app_context():
        now = utcnow()
        lapsed = RSVP.query.filter(
            RSVP.status == "promoted",
            RSVP.promotion_expires_at < now,
        ).all()

        if not lapsed:
            print("No lapsed waitlist offers.")
            return

        processed_ids = set()
        expired_count = 0

        for rsvp in lapsed:
            if rsvp.id in processed_ids:
                continue   # already handled as the linked partner of an earlier row

            event = rsvp.event
            person = rsvp.person
            carried_cancelled_by = rsvp.cancelled_by
            linked = rsvp.linked_rsvp   # partner's row, if this was a paired couple

            rsvp.status = "expired"
            processed_ids.add(rsvp.id)
            expired_count += 1
            print(f"Expired offer: {person.display_name} -- {event.title}")
            send_promotion_expired_email(person, event)

            if linked and linked.status == "promoted":
                linked.status = "expired"
                processed_ids.add(linked.id)
                expired_count += 1
                print(f"Expired offer: {linked.person.display_name} -- {event.title} (linked partner)")
                send_promotion_expired_email(linked.person, event)

            db.session.commit()

            promoted = promote_from_waitlist(event, cancelled_by=carried_cancelled_by)
            for new_rsvp in promoted:
                send_waitlist_promotion_email(new_rsvp.person, event, new_rsvp)
                print(f"  -> promoted {new_rsvp.person.display_name}")

        print(f"Processed {expired_count} lapsed offer(s).")


if __name__ == "__main__":
    main()
