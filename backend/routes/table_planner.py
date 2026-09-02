from flask import Blueprint, render_template, request, flash, redirect, url_for
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

    event.table_config = {"mode": "standard", "tables": tables}
    db.session.commit()

    flash(f"Table configuration saved to '{event.title}': "
          f"{eights} x 8-top, {sevens} x 7-top, {sixes} x 6-top "
          f"({eights+sevens+sixes} tables).", "success")
    return redirect(url_for("events.edit_event", event_id=event_id))


@table_planner_bp.route("/custom", methods=["GET"])
@login_required
@admin_required
def custom_planner():
    """Custom Table Plan editor: label / shape / size / eliminated ends
    per table, no 6-8 constraint. Separate save path from solve_tables()
    and the Standard planner above -- does not touch either."""
    from ..models import Event

    event_id = request.args.get("event_id", type=int)
    if not event_id:
        return "No event specified", 400

    event = Event.query.get_or_404(event_id)

    existing = event.table_config or {}
    existing_mode = existing.get("mode")
    if existing_mode is None and existing.get("tables"):
        # Pre-existing config saved before the mode field was introduced --
        # only the Standard planner ever wrote table_config until now.
        existing_mode = "standard"

    tables = existing.get("tables", []) if existing_mode == "custom" else []

    return render_template("admin/custom_table_planner.html",
                           event=event,
                           tables=tables,
                           existing_mode=existing_mode)


@table_planner_bp.route("/custom/save", methods=["POST"])
@login_required
@admin_required
def save_custom_config():
    """Save a Custom Table Plan: label/shape/size/eliminated-ends per table.
    One-directional export sync to Table Layout Management is fired here
    once that module exists (Part 2) -- no-op today if it doesn't."""
    from ..models import db, Event

    event_id = request.form.get("event_id", type=int)
    if not event_id:
        return "No event specified", 400

    event = Event.query.get_or_404(event_id)

    labels = request.form.getlist("table_label[]")
    shapes = request.form.getlist("table_shape[]")
    sizes  = request.form.getlist("table_size[]")
    ends   = request.form.getlist("table_eliminated[]")  # "none" | "one" | "both"

    tables = []
    table_num = 1
    for label, shape, size_raw, end in zip(labels, shapes, sizes, ends):
        try:
            size = int(size_raw)
        except (TypeError, ValueError):
            continue
        if size < 1:
            continue

        shape = shape if shape in ("round", "rectangular") else "round"
        label = (label or f"Table {table_num}").strip() or f"Table {table_num}"

        entry = {"id": table_num, "label": label, "size": size, "shape": shape}
        if shape == "rectangular":
            if end == "one":
                entry["eliminated_seats"] = [1]
            elif end == "both":
                entry["eliminated_seats"] = [1, size]

        tables.append(entry)
        table_num += 1

    if not tables:
        flash("No valid tables submitted -- nothing saved.", "error")
        return redirect(url_for("table_planner.custom_planner", event_id=event_id))

    event.table_config = {"mode": "custom", "tables": tables}
    db.session.commit()

    flash(f"Custom table plan saved to '{event.title}': "
          f"{len(tables)} table{'s' if len(tables) != 1 else ''}, "
          f"{sum(t['size'] for t in tables)} seats configured.", "success")

    try:
        from . import table_layout
        table_layout.sync_tables_for_event(event_id, tables)
    except ImportError:
        pass  # Table Layout Management module not yet built (Part 2)

    return redirect(url_for("events.edit_event", event_id=event_id))
