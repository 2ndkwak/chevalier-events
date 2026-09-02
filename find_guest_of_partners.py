"""
Scan for couples whose partner link is broken or misrecorded, in two
independent ways:

  0. ASYMMETRIC PARTNER LINKS (whole Person table, no RSVP history needed)
     Person A points to Person B via partner_id, but B doesn't point back
     to A. Checked directly against every Person record, regardless of
     whether that couple has ever RSVP'd to anything -- so this catches
     a broken link BEFORE it causes a misrecorded guest entry, not just
     after.

  1. LINKED BUT MISRECORDED (needs RSVP history)
     The partner already has a real, linked Person record (partner_id set
     on either side) but keeps showing up in rsvp_guests instead of with
     their own RSVP. These are safe to auto-fix: the target Person record
     already exists, we just need to (a) give them their own RSVP for each
     such event, matching their host's status, and (b) remove the
     now-redundant guest row.

  2. FREQUENT UNLINKED GUEST (needs a human decision, needs RSVP history)
     Someone with no Person record at all who shows up as the same name,
     under the same host, across multiple events -- a strong signal of a
     recurring spouse/partner who was simply never given a member record.
     These need a person created before they can be linked; the script
     only reports candidates; it does not create anything for this group.

Reports 1 and 2 can only find problems that have already shown up in past
RSVP data -- a couple that's never (yet) both attended anything is
invisible to them even if their link is broken. Report 0 has no such
blind spot: it checks every Person record directly, so it will catch a
couple's broken link even if they've never RSVP'd to a single event.

Run this against the live database, read-only. It makes NO changes.
"""
from collections import defaultdict

from backend import create_app
from backend.models import db, Person, RSVP, RSVPGuest, Event


def main():
    app = create_app()
    with app.app_context():
        # --- Report 0 -------------------------------------------------------
        print("=" * 78)
        print("REPORT 0 -- Asymmetric partner links (checked directly, all members)")
        print("=" * 78)

        all_linked = Person.query.filter(Person.partner_id.isnot(None)).all()
        asymmetric = []
        seen_pairs = set()
        for p in all_linked:
            partner = Person.query.get(p.partner_id)
            if not partner:
                asymmetric.append((p, None, "partner_id points to a Person that no longer exists"))
                continue
            pair = tuple(sorted([p.id, partner.id]))
            if pair in seen_pairs:
                continue
            if partner.partner_id != p.id:
                seen_pairs.add(pair)
                asymmetric.append((p, partner, None))

        if not asymmetric:
            print("None found -- every partner_id link is set on both sides.")
        else:
            for p, partner, note in asymmetric:
                if note:
                    print(f"\n{p.display_name} (Person id={p.id}) -- {note}")
                else:
                    print(f"\n{p.display_name} (Person id={p.id})  ->  {partner.display_name} "
                          f"(Person id={partner.id})")
                    print(f"  {p.display_name}'s record points to {partner.display_name}, "
                          f"but {partner.display_name}'s own partner_id is "
                          f"{'blank' if not partner.partner_id else 'pointing elsewhere'}.")

        linked_misrecorded = []   # (event, host, partner_person, guest_row)
        unlinked_by_key = defaultdict(list)  # (host_id, first, last) -> [ (event, guest_row) ]

        rsvps = (RSVP.query
                 .filter(RSVP.status.in_(["confirmed", "waitlist", "promoted"]))
                 .all())

        for rsvp in rsvps:
            host = rsvp.person
            if not host:
                continue
            for g in rsvp.guests:
                # Does host have a linked partner, checked bidirectionally
                # (mirrors the fix already applied in seating.py)?
                partner = None
                if host.partner_id:
                    partner = Person.query.get(host.partner_id)
                if partner is None:
                    partner = Person.query.filter_by(partner_id=host.id).first()

                if partner:
                    name_match = (
                        g.first_name.strip().lower() == partner.first_name.strip().lower()
                        and g.last_name.strip().lower() == partner.last_name.strip().lower()
                    )
                    if name_match:
                        linked_misrecorded.append((rsvp.event, host, partner, g))
                        continue

                # No linked Person at all -- track by (host, name) to spot
                # a recurring pattern across events.
                key = (host.id, g.first_name.strip().lower(), g.last_name.strip().lower())
                unlinked_by_key[key].append((rsvp.event, g))

        # --- Report 1 -----------------------------------------------------
        print("=" * 78)
        print("REPORT 1 -- Already linked, but recorded via guest table")
        print("=" * 78)
        if not linked_misrecorded:
            print("None found.")
        else:
            # De-dupe to one line per (host, partner) pair, listing every
            # event it happened on, rather than one line per event.
            by_pair = defaultdict(list)
            for event, host, partner, g in linked_misrecorded:
                by_pair[(host.id, partner.id)].append((event, host, partner, g))

            for (host_id, partner_id), rows in by_pair.items():
                host = rows[0][1]
                partner = rows[0][2]
                events = ", ".join(sorted({r[0].title for r in rows}))
                print(f"\n{host.display_name}  <->  {partner.display_name}"
                      f"  (host Person id={host.id}, partner Person id={partner.id})")
                print(f"  Partner already exists as a linked Person record.")
                print(f"  Appeared as a guest on: {events}")

        # --- Report 2 -----------------------------------------------------
        print()
        print("=" * 78)
        print("REPORT 2 -- Recurring unlinked guest (no Person record at all)")
        print("=" * 78)
        recurring = {k: v for k, v in unlinked_by_key.items() if len(v) >= 2}
        singles = {k: v for k, v in unlinked_by_key.items() if len(v) == 1}

        if not recurring:
            print("None found (no unlinked guest name repeated across 2+ events).")
        else:
            for (host_id, first, last), rows in sorted(
                    recurring.items(), key=lambda kv: -len(kv[1])):
                host = Person.query.get(host_id)
                events = ", ".join(sorted({r[0].title for r in rows}))
                sample_guest = rows[0][1]
                print(f"\n{sample_guest.display_name}  --  Guest of {host.display_name}"
                      f"  (host Person id={host_id})")
                print(f"  Appeared {len(rows)}x, on: {events}")

        print()
        print(f"({len(singles)} other unlinked guest names appeared exactly once -- "
              f"omitted as likely genuine one-off guests, not partners.)")

        # --- Summary --------------------------------------------------------
        print()
        print("=" * 78)
        print(f"SUMMARY: {len(asymmetric)} asymmetric partner link(s) -- fixable directly, "
              f"no RSVP history needed.")
        print(f"         {len(linked_misrecorded)} misrecorded event-attendance rows "
              f"across {len(by_pair) if linked_misrecorded else 0} couple(s) -- auto-fixable.")
        print(f"         {len(recurring)} recurring unlinked candidate(s) -- need a person "
              f"created before they can be linked.")
        print("=" * 78)


if __name__ == "__main__":
    main()
