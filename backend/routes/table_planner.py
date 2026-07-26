from flask import Blueprint, render_template, request
from flask_login import login_required
from ..routes.admin import admin_required

table_planner_bp = Blueprint("table_planner", __name__)


def solve_tables(n):
    """
    Given n guests, find all exact-fit combinations of 6, 7, and 8-top tables
    that maximize the number of 8-tops.

    Returns a list of solutions, each a dict:
        {"eights": int, "sevens": int, "sixes": int, "tables": int}
    Sorted by descending number of 8-tops (best first).
    Returns empty list if no exact solution exists.
    """
    solutions = []

    max_eights = n // 8
    for eights in range(max_eights, -1, -1):
        remainder = n - (eights * 8)
        # Try to fill remainder with 7s and 6s exactly
        max_sevens = remainder // 7
        for sevens in range(max_sevens, -1, -1):
            leftover = remainder - (sevens * 7)
            if leftover >= 0 and leftover % 6 == 0:
                sixes = leftover // 6
                solutions.append({
                    "eights": eights,
                    "sevens": sevens,
                    "sixes":  sixes,
                    "tables": eights + sevens + sixes,
                    "seats":  n,
                })

    # Sort: most 8-tops first, then fewest tables
    solutions.sort(key=lambda s: (-s["eights"], s["tables"]))

    # Deduplicate (same counts, different order found)
    seen = set()
    unique = []
    for s in solutions:
        key = (s["eights"], s["sevens"], s["sixes"])
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return unique


@table_planner_bp.route("/", methods=["GET", "POST"])
@login_required
@admin_required
def planner():
    headcount = None
    solutions = []
    no_solution = False
    event_id = request.args.get("event_id", type=int)

    if request.method == "POST":
        headcount = request.form.get("headcount", type=int)
        if headcount and headcount > 0:
            solutions = solve_tables(headcount)
            if not solutions:
                no_solution = True

    # If called from an event, we can save the chosen config back
    event = None
    if event_id:
        from ..models import Event
        event = Event.query.get(event_id)

    return render_template("admin/table_planner.html",
                           headcount=headcount,
                           solutions=solutions,
                           no_solution=no_solution,
                           event=event)


@table_planner_bp.route("/save", methods=["POST"])
@login_required
@admin_required
def save_config():
    """Save a chosen table configuration back to an event record."""
    from ..models import db, Event
    import json

    event_id = request.form.get("event_id", type=int)
    eights   = request.form.get("eights",   type=int, default=0)
    sevens   = request.form.get("sevens",   type=int, default=0)
    sixes    = request.form.get("sixes",    type=int, default=0)

    if not event_id:
        return "No event specified", 400

    event = Event.query.get_or_404(event_id)

    # Build table list: number each table, assign size
    tables = []
    t = 1
    for _ in range(eights):
        tables.append({"id": t, "size": 8, "label": f"Table {t}"}); t += 1
    for _ in range(sevens):
        tables.append({"id": t, "size": 7, "label": f"Table {t}"}); t += 1
    for _ in range(sixes):
        tables.append({"id": t, "size": 6, "label": f"Table {t}"}); t += 1

    event.table_config = {"tables": tables}
    db.session.commit()

    from flask import flash, redirect, url_for
    flash(f"Table configuration saved to '{event.title}': "
          f"{eights} x 8-top, {sevens} x 7-top, {sixes} x 6-top "
          f"({eights+sevens+sixes} tables).", "success")
    return redirect(url_for("events.edit_event", event_id=event_id))
