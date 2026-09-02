"""
Shared geometry for the rectangular-table perimeter-walk seat numbering
convention (Custom Table Plan dev plan, section 1.2). This is the single
source of truth for "which seat number is where" on a rectangular table --
used by table_planner.py (to compute eliminated_seats when saving a Custom
Table Plan) and, later, by seating.py's Part 1.5 elimination guards. The
Table Layout Management canvas (JS) implements the same math client-side
for rendering; keep both in sync if this ever changes.

Numbering convention: walk the perimeter -- end seat (1), down one long
side, the other end seat, back up the other long side, closing the loop
next to seat 1. This makes seat 1 and seat N physical neighbors (the last
side-B seat sits next to end 1), matching the round-table wraparound
already relied on by the "couples not adjacent" rule.

Critical consequence, easy to get wrong (and the reason this module
exists): the SECOND end seat is NOT seat N. It's encountered right after
the first long side finishes, so its number is `2 + seats_on_side_A` --
somewhere in the middle of the sequence, not the last one. Seat N is an
ordinary side-B seat, physically adjacent to seat 1, not an end seat.
"""


def rectangular_side_split(size):
    """
    Given a rectangular table's total seat count, return (side_a_count,
    side_b_count) -- how the size-2 non-end seats split across the two
    long sides. Split evenly; if size-2 is odd, the extra seat goes to
    side A (walked first), so numbering stays deterministic.
    """
    if size < 2:
        raise ValueError(f"rectangular table size must be >= 2, got {size}")
    k = size - 2
    side_a = (k + 1) // 2   # ceil(k/2) -- extra seat (if any) goes here
    side_b = k // 2
    return side_a, side_b


def rectangular_end_seat_numbers(size):
    """
    Return (end1_seat_num, end2_seat_num) for a rectangular table of the
    given size, per the perimeter-walk convention above. end1 is always
    seat 1. end2 is NOT seat `size` -- it's `2 + side_a_count`.
    """
    side_a, _side_b = rectangular_side_split(size)
    return 1, 2 + side_a
