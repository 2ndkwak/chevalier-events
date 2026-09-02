"""
Table Layout Management -- a new, isolated module for positioning and
rotating tables on a print-friendly canvas. Self-contained: no knowledge of
Person, RSVPGuest, or SeatAssignment, only Event. See TableArrangement in
models.py and Part 2 of the Custom Table Plan dev plan.

Routes (Part 2.3):
    GET  /admin/events/<id>/table-layout        -- editor
    POST /admin/events/<id>/table-layout/save   -- persist x/y/rotation
    GET  /admin/events/<id>/table-layout/print  -- print-friendly render

sync_tables_for_event() is called directly as a Python function from
table_planner.py's save_custom_config() (same process, same app) rather than
over HTTP -- per 1.6, the preferred one-directional export path. No separate
/sync HTTP route exists; there's nothing else that should be able to trigger
a sync, so an internal-only function keeps that one-directional data flow
enforced by the code structure itself, not just convention.

Part 2.1-2.3 note: this file lands the model, blueprint, and sync logic per
the dev plan's suggested build sequence, boot-tested in isolation. The full
drag/zoom/rotate canvas (Part 2.4) and cross-navigation banners (Part 2.5)
are a separate, later step -- the editor/print routes below are minimal
placeholders for now, real enough to exercise the data layer end-to-end.
"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required
from ..routes.admin import admin_required

table_layout_bp = Blueprint("table_layout", __name__)

# Fixed staging-area origin for newly-synced tables with no prior position
# (2.2: "Lands in a fixed staging area at a set corner of the canvas").
# Successive new tables in the same sync are offset diagonally so they don't
# stack exactly on top of each other.
STAGING_ORIGIN_X = 40
STAGING_ORIGIN_Y = 40
STAGING_STEP = 30


def sync_tables_for_event(event_id, tables):
    """
    One-directional, idempotent sync from a saved Custom Table Plan into
    this module's own storage. Called directly (function call, not HTTP)
    from table_planner.py's save_custom_config().

    `tables` is the list saved into Event.table_config["tables"]:
        [{"id": int, "label": str, "size": int, "shape": str,
          "eliminated_seats": [..] (optional)}, ...]

    Merge behavior (2.2):
      - Existing table (same table_num): position/rotation preserved.
        If label/shape/seats/eliminated_seats changed, update those fields
        and set changed_since_sync = True.
      - New table (table_num not seen before): no prior position -- lands
        in the staging area, changed_since_sync stays False (it's already
        visibly new by virtue of being in the staging corner).
      - Table removed (existing table_num not present in `tables`): row
        deleted. The space it occupied is left empty; no reshuffle.
    """
    from ..models import db, TableArrangement

    incoming_by_num = {t["id"]: t for t in tables}
    existing = {ta.table_num: ta for ta in
                TableArrangement.query.filter_by(event_id=event_id).all()}

    staging_offset = 0

    for table_num, t in incoming_by_num.items():
        eliminated = t.get("eliminated_seats", [])
        row = existing.get(table_num)

        if row is None:
            # New table -- staging area, no prior position to inherit.
            row = TableArrangement(
                event_id=event_id,
                table_num=table_num,
                label=t["label"],
                shape=t["shape"],
                seats=t["size"],
                eliminated_seats=eliminated,
                x=STAGING_ORIGIN_X + staging_offset,
                y=STAGING_ORIGIN_Y + staging_offset,
                rotation=0,
                changed_since_sync=False,
            )
            db.session.add(row)
            staging_offset += STAGING_STEP
            continue

        # Existing table -- preserve x/y/rotation; only flag as changed if
        # something about its shape actually changed.
        changed = (row.label != t["label"] or
                   row.shape != t["shape"] or
                   row.seats != t["size"] or
                   (row.eliminated_seats or []) != eliminated)

        row.label = t["label"]
        row.shape = t["shape"]
        row.seats = t["size"]
        row.eliminated_seats = eliminated
        if changed:
            row.changed_since_sync = True

    # Tables removed from the Custom Table Plan -- delete their arrangement
    # rows. The space they occupied stays empty; GS decides whether to
    # reshuffle the remaining tables.
    for table_num, row in existing.items():
        if table_num not in incoming_by_num:
            db.session.delete(row)

    db.session.commit()


@table_layout_bp.route("/", methods=["GET"])
@login_required
@admin_required
def editor(event_id):
    """Full-screen layout editor. Placeholder pending Part 2.4's canvas --
    lists tables and their current x/y/rotation so the data layer can be
    exercised and verified before the drag/zoom/rotate UI is built."""
    from ..models import Event, TableArrangement

    event = Event.query.get_or_404(event_id)
    arrangements = (TableArrangement.query
                     .filter_by(event_id=event_id)
                     .order_by(TableArrangement.table_num)
                     .all())

    return render_template("admin/table_layout_editor.html",
                           event=event,
                           arrangements=arrangements)


@table_layout_bp.route("/save", methods=["POST"])
@login_required
@admin_required
def save(event_id):
    """Persist x/y/rotation for one or more tables from the canvas.
    Accepts JSON: {"positions": [{"table_num": int, "x": float,
    "y": float, "rotation": float}, ...]}"""
    from ..models import db, TableArrangement

    payload = request.get_json(silent=True) or {}
    positions = payload.get("positions", [])

    if not positions:
        return jsonify({"error": "no positions provided"}), 400

    arrangements = {
        ta.table_num: ta for ta in
        TableArrangement.query.filter_by(event_id=event_id).all()
    }

    updated = 0
    for p in positions:
        row = arrangements.get(p.get("table_num"))
        if row is None:
            continue
        if "x" in p:
            row.x = p["x"]
        if "y" in p:
            row.y = p["y"]
        if "rotation" in p:
            row.rotation = p["rotation"]
        updated += 1

    db.session.commit()
    return jsonify({"updated": updated})


@table_layout_bp.route("/print", methods=["GET"])
@login_required
@admin_required
def print_layout(event_id):
    """Print-friendly render: same layout data, static, no
    changed_since_sync markers, no names -- shapes/numbers/labels only."""
    from ..models import Event, TableArrangement

    event = Event.query.get_or_404(event_id)
    arrangements = (TableArrangement.query
                     .filter_by(event_id=event_id)
                     .order_by(TableArrangement.table_num)
                     .all())

    return render_template("admin/table_layout_print.html",
                           event=event,
                           arrangements=arrangements)
