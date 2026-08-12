"""
Waitlist promotion logic.

When capacity frees up on an event (someone cancels, or a provisional
offer expires without being confirmed), this walks the waitlist in
strict first-in-line order and offers the freed seat(s) to whoever
fits -- stopping the moment the next person in line doesn't fit,
rather than skipping ahead to someone further down who would. Any
seat that can't be filled this way is simply left open for the GS to
handle manually.

A promotion is provisional: it holds the seat (counts against
capacity) for a 24-hour window while the promoted member confirms via
an emailed link. This keeps working right up to the event itself,
even after the normal RSVP deadline has closed.
"""
import secrets
from datetime import datetime, timedelta
from .util import utcnow

from .models import db, RSVP

PROMOTION_WINDOW = timedelta(hours=24)


def group_waitlist_into_parties(waitlist):
    """
    Group a list of waitlisted RSVP rows into parties, pairing up linked
    couples where both partners separately hold their own waitlisted RSVP
    for the same event (e.g. added via the admin "Add RSVP" tool). A
    party is normally just one RSVP row -- guests attached directly to a
    row (e.g. a portal RSVP where a partner was added as a guest on the
    same row) don't need pairing, since they're already one row.

    Returns a list of (party_time, [rsvp, ...]) tuples, sorted by
    party_time (the earliest created_at among the party's rows) -- i.e.
    FIFO order by party, not by individual row. Shared by promotion (so
    a couple is promoted or held back together) and by the member-facing
    waitlist position display (so a couple ahead of you counts as one
    step, not two).
    """
    consumed_ids = set()
    parties = []
    for r in sorted(waitlist, key=lambda r: r.created_at):
        if r.id in consumed_ids:
            continue
        party_rows = [r]
        consumed_ids.add(r.id)

        partner_id = r.person.partner_id
        if partner_id:
            partner_rsvp = next(
                (w for w in waitlist
                 if w.person_id == partner_id and w.id not in consumed_ids),
                None
            )
            if partner_rsvp:
                party_rows.append(partner_rsvp)
                consumed_ids.add(partner_rsvp.id)

        party_time = min(pr.created_at for pr in party_rows)
        parties.append((party_time, party_rows))

    parties.sort(key=lambda p: p[0])
    return parties


def promote_from_waitlist(event, cancelled_by=None):
    """
    Fill any open seats on `event` from its waitlist, in FIFO order,
    stopping at the first party who doesn't fit the remaining open
    seats. Returns the list of RSVPs that were newly promoted (each
    now has status="promoted" and a live promotion_token/expiry).

    A "party" is normally a single RSVP row (its guest_count already
    covers any guests attached directly to it -- e.g. a portal RSVP
    where a partner was added as a guest on the same row). But if two
    separate waitlisted RSVP rows belong to a linked couple (each
    partner has their own row -- e.g. added via the admin "Add RSVP"
    tool), they're grouped into one two-seat party so they're promoted
    or held back together, never separated.

    `cancelled_by` is the Person whose cancellation opened the seat
    (or, for a re-promotion after an expired offer, the *original*
    canceller further back in the chain) -- carried onto the newly
    promoted RSVP(s) so the eventual GS notification can tell the
    whole story in one message.
    """
    if event.capacity is None:
        return []   # unlimited capacity -- no such thing as a waitlist to clear

    waitlist = [r for r in event.rsvps if r.status == "waitlist"]
    parties = group_waitlist_into_parties(waitlist)

    promoted = []
    for _, party_rows in parties:
        open_seats = event.capacity - event.confirmed_count
        if open_seats <= 0:
            break
        party_size = sum(pr.guest_count for pr in party_rows)
        if party_size > open_seats:
            # Strict FIFO: don't skip this party to seat someone
            # smaller further down the list. Leave the remaining
            # seat(s) open for the GS to handle by hand.
            break

        for pr in party_rows:
            pr.status = "promoted"
            pr.promoted_at = utcnow()
            pr.promotion_expires_at = utcnow() + PROMOTION_WINDOW
            pr.promotion_token = secrets.token_urlsafe(32)
            pr.cancelled_by_id = cancelled_by.id if cancelled_by else None
            promoted.append(pr)

        if len(party_rows) == 2:
            party_rows[0].linked_rsvp_id = party_rows[1].id
            party_rows[1].linked_rsvp_id = party_rows[0].id

    if promoted:
        db.session.commit()

    return promoted
