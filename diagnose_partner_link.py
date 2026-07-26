"""
Read-only diagnostic -- inspects Person records matching a name, to check
directly for duplicate records or partner_id mismatches rather than
guessing. Makes no changes to the database.

Usage:
    cd /var/www/chevalier
    source venv/bin/activate
    python3 diagnose_partner_link.py Kaplan
"""
import sys

sys.path.insert(0, "/var/www/chevalier")

if len(sys.argv) < 2:
    print("Usage: python3 diagnose_partner_link.py <last_name_or_search_term>")
    sys.exit(1)

search = sys.argv[1]

from backend import create_app
app = create_app()

with app.app_context():
    from backend.models import Person, RSVP, Event

    matches = Person.query.filter(Person.last_name.ilike(f"%{search}%")).all()
    print(f"Found {len(matches)} Person record(s) matching '{search}':\n")

    for p in matches:
        partner = Person.query.get(p.partner_id) if p.partner_id else None
        partner_points_back = partner and partner.partner_id == p.id
        print(f"  id={p.id}  {p.first_name} {p.last_name}  "
              f"gender={p.gender!r}  type={p.person_type}  "
              f"partner_id={p.partner_id}  "
              f"-> partner: {partner.display_name if partner else 'None'}"
              f"{'  [MISMATCH: partner does not point back]' if partner and not partner_points_back else ''}")

        # Show every RSVP this exact person record has, across all events
        rsvps = RSVP.query.filter_by(person_id=p.id).all()
        for r in rsvps:
            event = Event.query.get(r.event_id)
            print(f"      RSVP: event #{r.event_id} ({event.title if event else '?'}) -- status={r.status}")
        print()

    # Explicitly flag if the same name appears more than once (possible
    # duplicate records -- would explain a "correct" link living on a
    # DIFFERENT record than the one actually RSVP'd to the event)
    from collections import Counter
    name_counts = Counter(f"{p.first_name} {p.last_name}" for p in matches)
    dupes = {name: count for name, count in name_counts.items() if count > 1}
    if dupes:
        print("⚠ POSSIBLE DUPLICATE RECORDS (same name appears more than once):")
        for name, count in dupes.items():
            print(f"    '{name}' appears {count} times")
