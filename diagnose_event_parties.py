"""
Read-only diagnostic -- runs the actual party-building code directly
against a real event, so we can see exactly what it computes instead of
guessing. Makes no changes to the database.

Usage:
    cd /var/www/chevalier
    source venv/bin/activate
    python3 diagnose_event_parties.py <event_id> [search_term]

Example:
    python3 diagnose_event_parties.py 6 Kaplan
"""
import sys

sys.path.insert(0, "/var/www/chevalier")

if len(sys.argv) < 2:
    print("Usage: python3 diagnose_event_parties.py <event_id> [search_term]")
    sys.exit(1)

event_id = int(sys.argv[1])
search = sys.argv[2] if len(sys.argv) > 2 else None

from backend import create_app
app = create_app()

with app.app_context():
    from backend.models import Event, Person, RSVP, RSVPGuest, SeatAssignment
    from backend.routes.seating import _get_attendees, _build_parties

    event = Event.query.get_or_404(event_id)
    print(f"Event #{event_id}: {event.title}\n")

    # Show every RSVP for this event and their guests, so we can see the
    # raw data exactly as the app sees it -- including any leftover /
    # duplicate guest entries that might not be obvious otherwise.
    print("=== Raw RSVPs and guests for this event ===")
    for r in RSVP.query.filter_by(event_id=event_id).all():
        p = Person.query.get(r.person_id)
        tag = ""
        if search and p and search.lower() in (p.last_name or "").lower():
            tag = "  <=== MATCH"
        print(f"  RSVP #{r.id}: {p.display_name if p else '?'} "
              f"(person_id={r.person_id}, type={p.person_type if p else '?'}, "
              f"status={r.status}){tag}")
        for g in r.guests:
            gtag = "  <=== MATCH" if search and search.lower() in (g.last_name or "").lower() else ""
            print(f"      guest: {g.display_name} (guest_id={g.id}){gtag}")

    # Now run the ACTUAL production code and show what it computes.
    locked = {(sa.table_num, sa.seat_num): sa
              for sa in SeatAssignment.query.filter_by(event_id=event_id, is_locked=True).all()}

    attendees = _get_attendees(event)
    print(f"\n=== _get_attendees() result: {len(attendees)} attendees ===")
    for a in attendees:
        tag = "  <=== MATCH" if search and search.lower() in a["name"].lower() else ""
        print(f"  {a['name']}  (person_id={a['person_id']}, guest_id={a['guest_id']}, "
              f"type={a['type']}, partner_id={a['partner_id']}){tag}")

    parties = _build_parties(attendees, locked)
    print(f"\n=== _build_parties() result: {len(parties)} parties ===")
    for p in parties:
        tag = "  <=== MATCH" if search and any(search.lower() in n.lower() for n in p["names"]) else ""
        print(f"  Party {p['party_id']}: {p['names']} (size={p['size']}){tag}")
