"""
Read-only diagnostic for a specific event's seating. Makes NO changes and
never calls the AI. Two things:

  A. GROUND-TRUTH SPLIT CHECK
     Builds the real parties (same code path propose_seating_fast uses:
     _get_attendees + _build_parties + _apply_party_rules) -- these are
     the units that are SUPPOSED to be at one table together. Then reads
     whatever is currently stored in seat_assignments for this event and
     checks, per party, whether its members ended up at more than one
     table. If so, prints exactly who and which tables -- this is the
     direct, factual answer to "which couple got split and where."

  B. CAPACITY / SLACK PICTURE
     Prints total people vs. total seats, per-table load from the current
     assignment, and runs _fix_table_overflow against the CURRENT
     party->table layout (read-only -- it only computes, nothing is
     written back) to show whether the capacity math itself is tight
     enough to explain a split, and whether any party would be reported
     unplaced by that logic today.

Usage:
    python3 diagnose_split.py "Celebration des Vendages"
"""
import sys
from collections import defaultdict

from backend import create_app
from backend.models import db, Event, SeatAssignment, Person, RSVPGuest
from backend.routes.seating import (
    _get_attendees, _build_parties, _apply_party_rules, _fix_table_overflow
)


def main():
    event_title = sys.argv[1] if len(sys.argv) > 1 else "Celebration des Vendages"

    app = create_app()
    with app.app_context():
        event = Event.query.filter_by(title=event_title).first()
        if not event:
            print(f"No event found titled {event_title!r}")
            return

        tables = (event.table_config or {}).get("tables", [])
        table_cap = {t["id"]: t["size"] for t in tables}
        print(f"Event: {event.title}  (id={event.id})")
        print(f"Tables: {tables}")

        # -- Build ground-truth parties (no AI involved) --------------------
        attendees = _get_attendees(event)
        rules = event.seating_rules or {}
        locked = {(a.table_num, a.seat_num): a
                  for a in SeatAssignment.query.filter_by(event_id=event.id, is_locked=True).all()}
        parties = _build_parties(attendees, locked)
        parties, not_together_pairs = _apply_party_rules(parties, rules)

        total_people = sum(p["size"] for p in parties)
        total_seats = sum(t["size"] for t in tables)
        print(f"\nAttendees: {len(attendees)}   Parties: {len(parties)}   "
              f"Total people (post-rules): {total_people}   Total seats: {total_seats}")

        # -- Read what's CURRENTLY stored for this event ---------------------
        current = SeatAssignment.query.filter_by(event_id=event.id).all()
        person_table = {}
        guest_table = {}
        for sa in current:
            if sa.person_id:
                person_table[sa.person_id] = sa.table_num
            if sa.guest_id:
                guest_table[sa.guest_id] = sa.table_num

        if not current:
            print("\nNo seat assignments currently stored for this event "
                  "(no proposal has been generated/saved yet).")
        else:
            # -- A. Ground-truth split check ---------------------------------
            print("\n" + "=" * 78)
            print("A. SPLIT CHECK -- does any party span more than one table?")
            print("=" * 78)
            any_split = False
            for p in parties:
                tables_used = set()
                for mid in p["member_ids"]:
                    if mid in person_table:
                        tables_used.add(person_table[mid])
                for gid in p["guest_ids"]:
                    if gid in guest_table:
                        tables_used.add(guest_table[gid])
                if len(tables_used) > 1:
                    any_split = True
                    print(f"\nSPLIT: {', '.join(p['names'])}")
                    for mid in p["member_ids"]:
                        if mid in person_table:
                            print(f"  {Person.query.get(mid).display_name} -> Table {person_table[mid]}")
                    for gid in p["guest_ids"]:
                        if gid in guest_table:
                            g = RSVPGuest.query.get(gid)
                            print(f"  {g.display_name if g else gid} -> Table {guest_table[gid]}")
                elif len(tables_used) == 1 and p["size"] > 1:
                    pass  # correctly together -- not printed, keep output focused
            if not any_split:
                print("\nNone -- every party is currently at a single table together.")

            # -- Per-table load from the CURRENT assignment -------------------
            print("\n" + "-" * 78)
            print("Current per-table load:")
            load = defaultdict(int)
            for sa in current:
                load[sa.table_num] += 1
            for tnum in sorted(table_cap):
                used = load.get(tnum, 0)
                cap = table_cap[tnum]
                flag = "  <-- OVER CAPACITY" if used > cap else ""
                print(f"  Table {tnum}: {used}/{cap}{flag}")

        # -- B. Capacity / slack picture, re-derived from current layout -----
        print("\n" + "=" * 78)
        print("B. CAPACITY CHECK -- re-running the overflow/consolidation logic")
        print("   against the party->table layout implied by current assignments")
        print("=" * 78)
        if current:
            # Infer each party's table from whichever seat its first found
            # member/guest currently occupies, so we can re-run the same
            # rebalancing logic that runs during a real proposal.
            party_table = {}
            for p in parties:
                for mid in p["member_ids"]:
                    if mid in person_table:
                        party_table[p["party_id"]] = person_table[mid]
                        break
                else:
                    for gid in p["guest_ids"]:
                        if gid in guest_table:
                            party_table[p["party_id"]] = guest_table[gid]
                            break

            missing = [p["names"] for p in parties if p["party_id"] not in party_table]
            if missing:
                print(f"\n{len(missing)} part(y/ies) have no current seat at all "
                      f"(never assigned): {missing}")

            result_table, unplaced = _fix_table_overflow(parties, dict(party_table), tables, locked)
            if unplaced:
                print(f"\nWith today's data, {len(unplaced)} part(y/ies) cannot be placed "
                      f"even after rebalancing:")
                by_id = {p["party_id"]: p for p in parties}
                for pid in unplaced:
                    print(f"  {', '.join(by_id[pid]['names'])}  (size {by_id[pid]['size']})")
            else:
                print("\nRebalancing logic finds a way to fit everyone -- capacity math "
                      "itself is not the blocker with today's data.")
        else:
            print("(skipped -- no current assignment to analyze)")


if __name__ == "__main__":
    main()
