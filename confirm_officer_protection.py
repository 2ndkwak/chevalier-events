"""
Read-only. For Celebration des Vendages, shows exactly who is seated at
Table 6 and Table 3, and for each officer at Table 6, checks whether they
have a party-mate (linked partner OR anyone they're hosting as an RSVP
guest) also seated at Table 6 -- the exact check the deployed fix uses to
decide whether it's safe to move that officer alone. If both officers
have a protected party-mate there, that's the confirmed reason neither
got moved, rather than an unrelated leftover imbalance.

Mirrors the same _party_mate_seated_at logic now live in
_enforce_rules(), just as a read-only report instead of a mover.
"""
from backend import create_app
from backend.models import db, Event, Person, RSVP, SeatAssignment, RSVPGuest


def party_mate_here(person_id, table_num, all_sa, hosted_by):
    pid_to_table = {sa.person_id: sa.table_num for sa in all_sa if sa.person_id}
    gid_to_table = {sa.guest_id: sa.table_num for sa in all_sa if sa.guest_id}

    person = Person.query.get(person_id)
    mate = None
    if person:
        partner = Person.query.get(person.partner_id) if person.partner_id else None
        if partner is None:
            partner = Person.query.filter_by(partner_id=person_id).first()
        if partner and pid_to_table.get(partner.id) == table_num:
            mate = partner.display_name

    if not mate:
        for gid in hosted_by.get(person_id, ()):
            if gid_to_table.get(gid) == table_num:
                g = RSVPGuest.query.get(gid)
                mate = g.display_name if g else f"guest#{gid}"
                break
    return mate


def main():
    app = create_app()
    with app.app_context():
        event = Event.query.filter_by(title="Celebration des Vendages").first()
        all_sa = SeatAssignment.query.filter_by(event_id=event.id).all()
        tables_cfg = {t["id"]: t["size"] for t in (event.table_config or {}).get("tables", [])}

        hosted_by = {}
        for rsvp in RSVP.query.filter_by(event_id=event.id).all():
            if rsvp.person_id:
                hosted_by[rsvp.person_id] = {g.id for g in rsvp.guests}

        # -- Officer count per table, so we know which table is genuinely 0 --
        by_table = {}
        for sa in all_sa:
            by_table.setdefault(sa.table_num, []).append(sa)

        print("=" * 78)
        print("Officer count per table")
        print("=" * 78)
        officer_free_tables = []
        for tnum in sorted(tables_cfg):
            occ = by_table.get(tnum, [])
            n_officers = sum(1 for sa in occ if sa.person_id and Person.query.get(sa.person_id).is_officer)
            seats_used = len(occ)
            cap = tables_cfg[tnum]
            print(f"  Table {tnum}: {n_officers} officer(s), {seats_used}/{cap} seats used")
            if n_officers == 0:
                officer_free_tables.append(tnum)

        for tnum in (6,) + tuple(officer_free_tables):
            print()
            print("=" * 78)
            print(f"Table {tnum} -- full occupant detail")
            print("=" * 78)
            occupants = by_table.get(tnum, [])
            for sa in occupants:
                if sa.person_id:
                    p = Person.query.get(sa.person_id)
                    tag = " [OFFICER]" if p.is_officer else ""
                    print(f"  Seat {sa.seat_num}: {p.display_name}{tag}")
                    mate = party_mate_here(p.id, tnum, all_sa, hosted_by)
                    if mate:
                        print(f"           -> protected: party-mate {mate} also at this table")
                    elif p.is_officer:
                        print(f"           -> NOT protected")
                elif sa.guest_id:
                    g = RSVPGuest.query.get(sa.guest_id)
                    print(f"  Seat {sa.seat_num}: {g.display_name} (guest)")


if __name__ == "__main__":
    main()
