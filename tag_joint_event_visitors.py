"""
One-time helper for a joint event: sets the Affiliation field on every
Honoraire with an RSVP to the given event (any status except declined),
plus their linked spouses, so they're treated as visitors -- printed in
their own group in the booklet, and kept out of our directory, counts,
officer ranking and emails.

Built for the Oct 2, 2026 joint Paulee with the Commanderie de Bordeaux,
whose attendees were entered as Honoraires. Only safe when NONE of our own
Honoraires are attending that event -- it lists everyone first so you can
check before anything changes.

Usage (from /var/www/chevalier):
  sudo venv/bin/python tag_joint_event_visitors.py EVENT_ID "Commanderie de Bordeaux"
      -> dry run: shows who would be tagged, changes nothing
  sudo venv/bin/python tag_joint_event_visitors.py EVENT_ID "Commanderie de Bordeaux" --apply
      -> actually tags them
"""
import sys
sys.path.insert(0, ".")
from backend.app import create_app
from backend.models import db, Event, RSVP, Person


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    event_id, org = int(sys.argv[1]), sys.argv[2].strip()
    apply = "--apply" in sys.argv

    app = create_app()
    with app.app_context():
        event = Event.query.get(event_id)
        if not event:
            print(f"No event with id {event_id}.")
            sys.exit(1)
        print(f"Event {event.id}: {event.title} ({event.event_date:%b %d, %Y})")
        if event.partner_org_name and event.partner_org_name != org:
            print(f"  NOTE: this event's partner organization is set to "
                  f"'{event.partner_org_name}', not '{org}'.")

        rsvps = RSVP.query.filter(RSVP.event_id == event_id, RSVP.status != "declined").all()
        targets = []
        for r in rsvps:
            p = r.person
            if p.person_type == "honoraire":
                targets.append(p)
                if p.partner is not None:
                    targets.append(p.partner)
        seen, ordered = set(), []
        for p in targets:
            if p.id not in seen:
                seen.add(p.id)
                ordered.append(p)
        ordered.sort(key=lambda p: ((p.last_name or "").lower(), (p.first_name or "").lower()))

        if not ordered:
            print("No Honoraires found on this event -- nothing to do.")
            return
        print(f"\n{len(ordered)} people would be tagged '{org}':")
        for p in ordered:
            role = f"  [{p.officer_role}]" if p.is_officer and p.officer_role else ""
            current = f"  (currently: {p.affiliation})" if p.affiliation else ""
            print(f"  {p.display_name:35s} {p.person_type:12s}{role}{current}")

        if not apply:
            print("\nDry run only -- nothing changed. If this list is exactly the visiting "
                  "group, run again with --apply.")
            return
        # Written one row at a time, directly: saving both spouses of a
        # linked couple in a single ORM flush fails (each points at the
        # other -- SQLAlchemy CircularDependencyError).
        from sqlalchemy import update
        ids = [p.id for p in ordered]
        db.session.rollback()
        for pid in ids:
            db.session.execute(update(Person).where(Person.id == pid).values(affiliation=org))
        db.session.commit()
        print(f"\nDone -- {len(ordered)} people tagged.")


if __name__ == "__main__":
    main()
