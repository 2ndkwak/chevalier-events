"""
Read-only. For every event where BOTH members of a linked couple have
their own RSVP (the "done correctly" case, as opposed to "Guest of"),
checks whether their two RSVP rows are linked via linked_rsvp_id.

  - linked_rsvp_id SET on both  -> came from the admin "Add RSVP" tool
    (the only code path that sets this field)
  - linked_rsvp_id NOT set      -> each partner RSVP'd independently
    through the portal, on their own, without ever touching the
    "+ Add partner" quick-add shortcut

This is a purely descriptive check -- it doesn't fix or flag anything,
just answers "how did the correctly-linked couples end up correct."
"""
from collections import defaultdict

from backend import create_app
from backend.models import db, Person, RSVP


def main():
    from datetime import datetime, timedelta
    # Anything created in roughly the last day is almost certainly from
    # today's fix_guest_of_partners.py --apply run, not original historical
    # data -- exclude it so this comparison reflects only what genuinely
    # happened before any of today's fixes.
    cutoff = datetime.utcnow() - timedelta(hours=20)

    app = create_app()
    with app.app_context():
        all_linked = Person.query.filter(Person.partner_id.isnot(None)).all()
        seen_pairs = set()
        admin_linked = []
        independently_rsvpd = []
        excluded_recent = 0

        for p in all_linked:
            partner = Person.query.get(p.partner_id)
            if not partner:
                continue
            pair = tuple(sorted([p.id, partner.id]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            p_rsvps = {r.event_id: r for r in RSVP.query.filter_by(person_id=p.id).all()}
            partner_rsvps = {r.event_id: r for r in RSVP.query.filter_by(person_id=partner.id).all()}
            shared_events = set(p_rsvps) & set(partner_rsvps)

            for eid in shared_events:
                r1, r2 = p_rsvps[eid], partner_rsvps[eid]
                title = r1.event.title
                if "test" in title.lower():
                    continue  # exclude test events -- not representative of real usage
                if (r1.created_at and r1.created_at > cutoff) or (r2.created_at and r2.created_at > cutoff):
                    excluded_recent += 1
                    continue  # exclude today's conversions -- not original historical data
                entry = (p.display_name, partner.display_name, title)
                if r1.linked_rsvp_id == r2.id and r2.linked_rsvp_id == r1.id:
                    admin_linked.append(entry)
                else:
                    independently_rsvpd.append(entry)

        print("=" * 78)
        print("LIVE PRODUCTION EVENTS ONLY -- test events AND today's conversions excluded")
        print("=" * 78)
        print(f"\n(Excluded as too-recent / likely from today's fix: {excluded_recent})")
        print(f"\nGenuinely original, correctly-paired instances: {len(admin_linked) + len(independently_rsvpd)}")
        print(f"  Via admin 'Add RSVP' tool (linked_rsvp_id set):        {len(admin_linked)}")
        print(f"  Via two independent portal RSVPs (not linked at all): {len(independently_rsvpd)}")

        if admin_linked:
            print("\n-- Admin-linked --")
            for a, b, title in admin_linked:
                print(f"  {a} & {b}  ({title})")

        if independently_rsvpd:
            print("\n-- Independently self-RSVP'd --")
            for a, b, title in independently_rsvpd:
                print(f"  {a} & {b}  ({title})")


if __name__ == "__main__":
    main()
