"""
Convert "Guest of" partners (Report 1 from find_guest_of_partners.py) into
their own proper RSVPs, linked to their host's RSVP the same way the admin
"Add RSVP" tool already does it for couples added correctly.

For each event where a partner was recorded as a guest of their linked host:
  1. Create a new RSVP for the partner (status matches the host's RSVP
     status for that event), linked bidirectionally via linked_rsvp_id.
  2. Delete any current seat assignment tied to that guest row -- per
     decision made when this was scoped: don't try to carry the seat
     over, they'll get placed properly the next time a proposal is
     generated for that event.
  3. Delete the now-redundant RSVPGuest row.

Safe to re-run: skips any (event, partner) pair where the partner already
has their own RSVP for that event (either from a prior run of this script,
or because they were a genuine duplicate to begin with).

Defaults to a DRY RUN that only prints what it would do. Pass --apply to
actually write anything.

Usage:
    python3 fix_guest_of_partners.py           # dry run
    python3 fix_guest_of_partners.py --apply   # make the real changes
"""
import sys

from backend import create_app
from backend.models import db, Person, RSVP, RSVPGuest, SeatAssignment, Event


def find_conversions():
    """Same matching logic as find_guest_of_partners.py's Report 1."""
    conversions = []  # (event, host_rsvp, partner, guest_row)
    rsvps = RSVP.query.filter(RSVP.status.in_(["confirmed", "waitlist", "promoted"])).all()
    for rsvp in rsvps:
        host = rsvp.person
        if not host:
            continue
        for g in rsvp.guests:
            partner = None
            if host.partner_id:
                partner = Person.query.get(host.partner_id)
            if partner is None:
                partner = Person.query.filter_by(partner_id=host.id).first()
            if not partner:
                continue
            name_match = (g.first_name.strip().lower() == partner.first_name.strip().lower()
                          and g.last_name.strip().lower() == partner.last_name.strip().lower())
            if not name_match:
                continue
            # Already has their own RSVP for this event? Then this guest row
            # is a stray duplicate, not a genuine "Guest of" case -- the
            # bidirectional dedup fix already deployed in _get_attendees
            # handles that situation at seating-generation time, so leave
            # it alone here.
            already = RSVP.query.filter_by(event_id=rsvp.event_id, person_id=partner.id).first()
            if already:
                continue
            conversions.append((rsvp.event, rsvp, partner, g))
    return conversions


def main():
    apply_changes = "--apply" in sys.argv

    app = create_app()
    with app.app_context():
        conversions = find_conversions()

        if not conversions:
            print("Nothing to convert -- no matching 'Guest of' rows found.")
            return

        print("APPLYING -- writing changes now" if apply_changes
              else "DRY RUN -- add --apply to actually make changes")
        print("=" * 78)

        for event, host_rsvp, partner, g in conversions:
            print(f"\n{event.title}: {g.display_name}  (guest of {host_rsvp.person.display_name})")
            print(f"  -> Create RSVP for {partner.display_name} (Person id={partner.id}), "
                  f"status='{host_rsvp.status}', linked to host RSVP id={host_rsvp.id}")

            existing_seat = SeatAssignment.query.filter_by(event_id=event.id, guest_id=g.id).first()
            if existing_seat:
                print(f"  -> Remove current seat (Table {existing_seat.table_num}, "
                      f"Seat {existing_seat.seat_num}) -- will be re-placed on next proposal")

            print(f"  -> Delete guest row (RSVPGuest id={g.id})")

            if apply_changes:
                new_rsvp = RSVP(
                    event_id=event.id,
                    person_id=partner.id,
                    status=host_rsvp.status,
                    linked_rsvp_id=host_rsvp.id,
                )
                db.session.add(new_rsvp)
                db.session.flush()  # assign new_rsvp.id before linking back
                host_rsvp.linked_rsvp_id = new_rsvp.id

                if existing_seat:
                    db.session.delete(existing_seat)

                db.session.delete(g)

        if apply_changes:
            db.session.commit()
            print(f"\nDone -- {len(conversions)} partner(s) converted.")
        else:
            print(f"\n{len(conversions)} would be converted. Re-run with --apply to make it real.")


if __name__ == "__main__":
    main()
