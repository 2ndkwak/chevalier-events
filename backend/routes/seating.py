from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, jsonify, current_app)
from flask_login import login_required, current_user
from ..models import db, Event, RSVP, RSVPGuest, Person, SeatAssignment, SeatingRule, WineTag, EventAllergyOff, EventMaterial, MenuItem
from ..routes.admin import admin_required
import json
import sys

# Under gunicorn, stdout isn't connected to a terminal, so Python block-buffers
# it by default -- print() debug statements below would otherwise sit
# invisible in a buffer until it happens to fill up or the worker process
# exits, making `journalctl` look empty or stale right after something just
# happened. Line-buffering forces each print() to flush immediately.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

seating_bp = Blueprint("seating", __name__)


# --- SEATING HOME FOR AN EVENT ------------------------------------------------

@seating_bp.route("/event/<int:event_id>")
@login_required
@admin_required
def seating_home(event_id):
    event = Event.query.get_or_404(event_id)
    global_rules = SeatingRule.query.order_by(SeatingRule.is_active.desc(), SeatingRule.id).all()
    per_event_rules = event.seating_rules or {"not_together": [], "prefer_together": [], "custom": []}

    # All confirmed attendees for this event
    attendees = _get_attendees(event)

    # Only couples with at least one confirmed RSVP for this event
    couples = _get_event_couples(event)

    # Existing seat assignments
    assignments = SeatAssignment.query.filter_by(event_id=event_id).all()

    # Build a name lookup for rule display: person_id -> "Name & Partner Name"
    couple_name_map = {}
    from ..models import Person as _Person
    for c in couples:
        p = _Person.query.get(c["id"])
        partner = _Person.query.get(c["partner_id"]) if c["partner_id"] else None
        if p:
            label = f"{p.display_name} & {partner.display_name}" if partner else p.display_name
            couple_name_map[c["id"]] = label
            if partner:
                couple_name_map[partner.id] = label

    return render_template("admin/seating/home.html",
                           event=event,
                           global_rules=global_rules,
                           per_event_rules=per_event_rules,
                           attendees=attendees,
                           couples=couples,
                           couple_name_map=couple_name_map,
                           assignments=assignments)


# --- SAVE PER-EVENT RULES -----------------------------------------------------

@seating_bp.route("/event/<int:event_id>/rules/save", methods=["POST"])
@login_required
@admin_required
def save_rules(event_id):
    event = Event.query.get_or_404(event_id)
    rules = event.seating_rules or {"not_together": [], "prefer_together": [], "custom": []}

    action = request.form.get("action")

    if action == "add_not_together":
        pair = [request.form.get("couple1_id", type=int),
                request.form.get("couple2_id", type=int)]
        if pair[0] and pair[1] and pair[0] != pair[1]:
            if pair not in rules["not_together"] and list(reversed(pair)) not in rules["not_together"]:
                rules["not_together"].append(pair)

    elif action == "add_prefer_together":
        pair = [request.form.get("couple1_id", type=int),
                request.form.get("couple2_id", type=int)]
        if pair[0] and pair[1] and pair[0] != pair[1]:
            if pair not in rules["prefer_together"] and list(reversed(pair)) not in rules["prefer_together"]:
                rules["prefer_together"].append(pair)

    elif action == "add_custom":
        text = request.form.get("custom_rule", "").strip()
        if text:
            rules["custom"].append(text)

    elif action == "remove_not_together":
        idx = request.form.get("idx", type=int)
        if idx is not None and 0 <= idx < len(rules["not_together"]):
            rules["not_together"].pop(idx)

    elif action == "remove_prefer_together":
        idx = request.form.get("idx", type=int)
        if idx is not None and 0 <= idx < len(rules["prefer_together"]):
            rules["prefer_together"].pop(idx)

    elif action == "remove_custom":
        idx = request.form.get("idx", type=int)
        if idx is not None and 0 <= idx < len(rules["custom"]):
            rules["custom"].pop(idx)

    # Must assign a new dict -- SQLAlchemy won't detect in-place mutation of JSON
    import copy
    event.seating_rules = copy.deepcopy(rules)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(event, "seating_rules")
    db.session.commit()

    # Friendly confirmation message
    if action == "add_not_together":
        flash("'Do not seat together' rule added.", "success")
    elif action == "add_prefer_together":
        flash("'Prefer same table' rule added.", "success")
    elif action == "add_custom":
        flash("Custom rule added.", "success")
    elif action in ("remove_not_together", "remove_prefer_together", "remove_custom"):
        flash("Rule removed.", "success")

    return redirect(url_for("seating.seating_home", event_id=event_id))


# --- TOGGLE GLOBAL RULE -------------------------------------------------------

@seating_bp.route("/rules/global/<int:rule_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_global_rule(rule_id):
    rule = SeatingRule.query.get_or_404(rule_id)
    rule.is_active = not rule.is_active
    db.session.commit()
    event_id = request.form.get("event_id", type=int)
    flash(f"'{rule.description}' {'enabled' if rule.is_active else 'disabled'}.", "success")
    return redirect(url_for("seating.seating_home", event_id=event_id) + "#permanent-rules")


@seating_bp.route("/rules/global/add", methods=["POST"])
@login_required
@admin_required
def add_global_rule():
    """Add a new permanent rule -- applies to every event from now on."""
    description = request.form.get("description", "").strip()
    event_id = request.form.get("event_id", type=int)
    if description:
        db.session.add(SeatingRule(rule_type="custom", description=description, is_active=True))
        db.session.commit()
        flash("Permanent rule added.", "success")
    return redirect(url_for("seating.seating_home", event_id=event_id) + "#permanent-rules")


@seating_bp.route("/rules/global/<int:rule_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_global_rule(rule_id):
    """Permanently delete a global seating rule."""
    rule = SeatingRule.query.get_or_404(rule_id)
    event_id = request.form.get("event_id", type=int)
    db.session.delete(rule)
    db.session.commit()
    flash("Permanent rule deleted.", "success")
    return redirect(url_for("seating.seating_home", event_id=event_id) + "#permanent-rules")


# --- AI PROPOSAL --------------------------------------------------------------

@seating_bp.route("/event/<int:event_id>/propose", methods=["POST"])
@login_required
@admin_required
def propose_seating(event_id):
    event = Event.query.get_or_404(event_id)

    if not event.table_config:
        flash("Please set a table configuration first using the Table Planner.", "error")
        return redirect(url_for("seating.seating_home", event_id=event_id))

    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        flash("Anthropic API key not configured. Please add ANTHROPIC_API_KEY to your instance/config.py file.", "error")
        return redirect(url_for("seating.seating_home", event_id=event_id))

    attendees  = _get_attendees(event)
    couples    = _get_event_couples(event)
    history    = _get_seating_history(event_id, limit=3)
    locked     = {(a.table_num, a.seat_num): a
                  for a in SeatAssignment.query.filter_by(event_id=event_id, is_locked=True).all()}

    prompt = _build_prompt(event, attendees, couples, history, locked)

    try:
        import requests as req
        import re as _re

        HEADERS = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        SYSTEM = (
            "You are a seating assignment engine for a formal chivalric dinner. "
            "Your job is to assign every attendee to exactly one seat. "
            "CRITICAL RULE -- COUPLES: Partners must be at the same table "
            "but MUST NOT sit in adjacent seats (seat numbers differing by 1). "
            "Spread officers: no table should have more officers than another if avoidable. "
            "Keep your reasoning brief and structured -- a human reviewer wants a quick summary "
            "of your approach, not a full explanation of every individual seat."
        )

        # -- Single pass: brief tagged reasoning, then tagged JSON, in one response --
        r1 = req.post(
            "https://api.anthropic.com/v1/messages",
            headers=HEADERS,
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 10000,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=180,
        )
        d1 = r1.json()
        raw = ""
        for block in d1.get("content", []):
            if block.get("type") == "text":
                raw += block["text"]
        print(f"DEBUG single-pass -- HTTP {r1.status_code}, {len(raw)} chars, "
              f"stop={d1.get('stop_reason')}")

        if not raw:
            err = d1.get("error", {}).get("message", "none")
            flash(f"AI returned an empty response. API error: {err}", "error")
            return redirect(url_for("seating.seating_home", event_id=event_id))

        # -- Extract JSON from the response ---------------------------------
        extracted = None

        # Try <json> tags first (this is what the prompt explicitly asks for)
        tag_match = _re.search(r'<json>(.*?)</json>', raw, _re.DOTALL)
        if tag_match:
            extracted = tag_match.group(1).strip()
            print(f"DEBUG - via <json> tag ({len(extracted)} chars)")
        else:
            # Fallback: strip any accidental markdown fences and find the
            # outermost { ... } in case the model didn't use the tags
            cleaned = raw.strip()
            cleaned = _re.sub(r'^```json\s*', '', cleaned, flags=_re.MULTILINE)
            cleaned = _re.sub(r'^```\s*', '', cleaned, flags=_re.MULTILINE)
            cleaned = _re.sub(r'```\s*$', '', cleaned, flags=_re.MULTILINE)
            cleaned = cleaned.strip()
            brace_start = cleaned.find("{")
            brace_end   = cleaned.rfind("}")
            if brace_start >= 0 and brace_end > brace_start:
                extracted = cleaned[brace_start:brace_end+1]
                print(f"DEBUG - via brace trim ({len(extracted)} chars)")

        if not extracted:
            print("DEBUG - raw response (last 400):", raw[-400:])
            flash("AI did not return valid JSON. Please try again.", "error")
            return redirect(url_for("seating.seating_home", event_id=event_id))

        try:
            proposal = json.loads(extracted)
        except json.JSONDecodeError as e:
            print(f"DEBUG - JSON parse error: {e}")
            print(f"DEBUG - extracted (first 500): {extracted[:500]}")
            print(f"DEBUG - extracted (last 200): {extracted[-200:]}")
            raise
        _apply_proposal(event_id, proposal, locked)
        fixes = _enforce_rules(event_id, couples)
        msg = "Seating proposal generated successfully. Review and adjust as needed."
        details = []
        if fixes["couples_separated"] > 0:
            details.append(f"{fixes['couples_separated']} couple(s) moved apart from adjacent seats")
        if fixes["officers_spread"] > 0:
            details.append(f"{fixes['officers_spread']} officer(s) redistributed across tables")
        if details:
            msg += " (Auto-fixed: " + "; ".join(details) + ".)"
        flash(msg, "success")

    except json.JSONDecodeError as e:
        flash(f"AI returned an unexpected format. Please try again. ({e})", "error")
    except Exception as e:
        flash(f"Could not generate proposal: {e}", "error")

    return redirect(url_for("seating.seating_home", event_id=event_id))


@seating_bp.route("/event/<int:event_id>/propose_fast", methods=["POST"])
@login_required
@admin_required
def propose_seating_fast(event_id):
    """
    PROTOTYPE -- party-based seating proposal. Groups attendees into parties
    (couples, guest+host bundles, singles) before calling the AI, so the AI
    only decides which table each party goes to rather than placing every
    individual into a specific seat. Seat-level placement within a table is
    then filled in deterministically. See the comment block above
    _build_parties() for the full rationale.

    This is intentionally a separate route from propose_seating() (not a
    replacement) so the two can be compared side by side on the same event.
    """
    event = Event.query.get_or_404(event_id)

    if not event.table_config:
        flash("Please set a table configuration first using the Table Planner.", "error")
        return redirect(url_for("seating.seating_home", event_id=event_id))

    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        flash("Anthropic API key not configured. Please add ANTHROPIC_API_KEY to your instance/config.py file.", "error")
        return redirect(url_for("seating.seating_home", event_id=event_id))

    attendees = _get_attendees(event)
    couples   = _get_event_couples(event)
    history   = _get_seating_history(event_id, limit=3)
    locked    = {(a.table_num, a.seat_num): a
                 for a in SeatAssignment.query.filter_by(event_id=event_id, is_locked=True).all()}
    tables    = event.table_config.get("tables", [])
    rules     = event.seating_rules or {}

    parties = _build_parties(attendees, locked)
    parties, not_together_pairs = _apply_party_rules(parties, rules)

    # Sanity check: total party size must fit in total table capacity
    total_seats = sum(t["size"] for t in tables)
    total_people = sum(p["size"] for p in parties)
    if total_people > total_seats:
        flash(f"Not enough seats: {total_people} attendees need seating but tables only "
              f"provide {total_seats} seats.", "error")
        return redirect(url_for("seating.seating_home", event_id=event_id))

    prompt = _build_party_prompt(event, parties, not_together_pairs, history, tables)

    try:
        import requests as req
        import re as _re

        HEADERS = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        SYSTEM = (
            "You are a seating assignment engine for a formal chivalric dinner. "
            "Your job is to assign pre-formed parties (groups that must stay together) "
            "to tables -- not individual people to seats. "
            "Keep your reasoning brief and structured."
        )

        r1 = req.post(
            "https://api.anthropic.com/v1/messages",
            headers=HEADERS,
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 3000,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        d1 = r1.json()
        raw = ""
        for block in d1.get("content", []):
            if block.get("type") == "text":
                raw += block["text"]
        print(f"DEBUG propose_fast -- HTTP {r1.status_code}, {len(raw)} chars, "
              f"stop={d1.get('stop_reason')}")

        if not raw:
            err = d1.get("error", {}).get("message", "none")
            flash(f"AI returned an empty response. API error: {err}", "error")
            return redirect(url_for("seating.seating_home", event_id=event_id))

        extracted = None
        tag_match = _re.search(r'<json>(.*?)</json>', raw, _re.DOTALL)
        if tag_match:
            extracted = tag_match.group(1).strip()
        else:
            cleaned = raw.strip()
            cleaned = _re.sub(r'^```json\s*', '', cleaned, flags=_re.MULTILINE)
            cleaned = _re.sub(r'^```\s*', '', cleaned, flags=_re.MULTILINE)
            cleaned = _re.sub(r'```\s*$', '', cleaned, flags=_re.MULTILINE)
            cleaned = cleaned.strip()
            brace_start = cleaned.find("{")
            brace_end   = cleaned.rfind("}")
            if brace_start >= 0 and brace_end > brace_start:
                extracted = cleaned[brace_start:brace_end+1]

        if not extracted:
            print("DEBUG propose_fast - raw response (last 400):", raw[-400:])
            flash("AI did not return valid JSON. Please try again.", "error")
            return redirect(url_for("seating.seating_home", event_id=event_id))

        result = json.loads(extracted)
        party_table = {a["party_id"]: a["table_num"] for a in result.get("assignments", [])}

        # Make sure every party actually got a table (fall back to first
        # table with room if the AI somehow missed one -- shouldn't happen,
        # but better than silently dropping attendees)
        assigned_party_ids = set(party_table.keys())
        missing = [p for p in parties if p["party_id"] not in assigned_party_ids]
        if missing:
            print(f"DEBUG propose_fast - {len(missing)} part(ies) missing from AI response, "
                  f"placing in first available table")
            for p in missing:
                party_table[p["party_id"]] = tables[0]["id"] if tables else 1

        # Locked parties are non-negotiable -- force them to their locked
        # table regardless of what the AI returned, same guarantee the
        # original single-seat path provides for individual locked seats.
        for p in parties:
            if p["table_lock"] is not None and party_table.get(p["party_id"]) != p["table_lock"]:
                print(f"DEBUG propose_fast - overriding AI table choice for locked party "
                      f"{p['party_id']} ({', '.join(p['names'])}): forcing table {p['table_lock']}")
                party_table[p["party_id"]] = p["table_lock"]

        # Guard against the AI overpacking one table beyond its seat count --
        # the earlier check only confirmed enough TOTAL seats exist across
        # all tables combined, not that any single table's assignment fits.
        # Without this, whoever didn't fit at an overpacked table would
        # previously just vanish from the seating chart with no warning at
        # all -- this rebalances first, and reports anyone still left over.
        #
        # Importantly: even if a few people genuinely can't be fit, we still
        # go ahead and seat everyone who CAN be -- rejecting the whole
        # attempt would leave the database (and the chart on screen)
        # untouched from whatever the PREVIOUS attempt produced, which can
        # make an old, unrelated problem look like it's describing this run.
        party_table, unplaced = _fix_table_overflow(parties, party_table, tables, locked)

        # Some parties can be left unplaced not because there's too little
        # TOTAL room, but because whatever room remains is scattered across
        # several tables in pieces too small for that party alone (e.g. one
        # free seat here, one free seat there -- useless to a couple that
        # needs both seats at the same table). Give those a second chance by
        # trying to consolidate scattered slack onto one table.
        if unplaced:
            party_table, unplaced = _consolidate_for_unplaced(
                parties, party_table, tables, unplaced, locked)

        _assign_seats_from_party_tables(event_id, parties, party_table, locked)
        db.session.commit()

        fixes = _enforce_rules(event_id, couples)
        msg = "Seating proposal generated successfully (prototype/fast path). Review and adjust as needed."
        details = []
        if fixes["couples_separated"] > 0:
            details.append(f"{fixes['couples_separated']} couple(s) moved apart from adjacent seats")
        if fixes["officers_spread"] > 0:
            details.append(f"{fixes['officers_spread']} officer(s) redistributed across tables")
        if details:
            msg += " (Auto-fixed: " + "; ".join(details) + ".)"
        flash(msg, "success")

        if unplaced:
            party_by_id = {p["party_id"]: p for p in parties}
            names = "; ".join(", ".join(party_by_id[pid]["names"]) for pid in unplaced)
            flash(f"Everyone else is seated, but couldn't fit everyone at their assigned "
                  f"table -- please seat these manually: {names}", "warning")

    except json.JSONDecodeError as e:
        flash(f"AI returned an unexpected format. Please try again. ({e})", "error")
    except Exception as e:
        flash(f"Could not generate proposal: {e}", "error")

    return redirect(url_for("seating.seating_home", event_id=event_id))


# --- CLEAR SEATING ------------------------------------------------------------

@seating_bp.route("/event/<int:event_id>/clear", methods=["POST"])
@login_required
@admin_required
def clear_seating(event_id):
    keep_locked = request.form.get("keep_locked") == "1"
    q = SeatAssignment.query.filter_by(event_id=event_id)
    if keep_locked:
        q = q.filter_by(is_locked=False)
    q.delete()
    db.session.commit()
    flash("Seating cleared." + (" Locked seats retained." if keep_locked else ""), "success")
    return redirect(url_for("seating.seating_home", event_id=event_id))



# --- DRAG-AND-DROP SAVE -------------------------------------------------------

@seating_bp.route("/event/<int:event_id>/save_canvas", methods=["POST"])
@login_required
@admin_required
def save_canvas(event_id):
    """Receive the full canvas state as JSON and persist it."""
    event = Event.query.get_or_404(event_id)
    data = request.get_json(force=True)
    if not data or "tables" not in data:
        return jsonify({"ok": False, "error": "Invalid payload"}), 400

    # Delete all unlocked assignments for this event
    SeatAssignment.query.filter_by(event_id=event_id, is_locked=False).delete()
    db.session.flush()

    for table in data["tables"]:
        tnum = int(table["table_num"])
        for seat in table.get("seats", []):
            snum    = int(seat["seat_num"])
            pid     = seat.get("person_id") or None
            gid     = seat.get("guest_id") or None
            locked  = bool(seat.get("is_locked", False))
            if pid or gid:
                sa = SeatAssignment(
                    event_id  = event_id,
                    table_num = tnum,
                    seat_num  = snum,
                    person_id = int(pid) if pid else None,
                    guest_id  = int(gid) if gid else None,
                    is_locked = locked,
                )
                db.session.add(sa)

    db.session.commit()
    return jsonify({"ok": True})


# --- TOGGLE SEAT LOCK ---------------------------------------------------------

@seating_bp.route("/event/<int:event_id>/toggle_lock", methods=["POST"])
@login_required
@admin_required
def toggle_lock(event_id):
    data = request.get_json(force=True)
    tnum = data.get("table_num")
    snum = data.get("seat_num")
    sa = SeatAssignment.query.filter_by(
        event_id=event_id, table_num=tnum, seat_num=snum
    ).first()
    if not sa:
        return jsonify({"ok": False, "error": "Seat not found"}), 404
    sa.is_locked = not sa.is_locked
    db.session.commit()
    return jsonify({"ok": True, "is_locked": sa.is_locked})


# --- PRINT PAGE ---------------------------------------------------------------

@seating_bp.route("/event/<int:event_id>/print")
@login_required
@admin_required
def print_seating(event_id):
    """Build all data needed for the five print formats."""
    event = Event.query.get_or_404(event_id)

    if not event.table_config:
        flash("No table configuration set for this event.", "error")
        return redirect(url_for("seating.seating_home", event_id=event_id))

    assignments = SeatAssignment.query.filter_by(event_id=event_id).all()
    if not assignments:
        flash("No seating assignments yet -- generate a proposal first.", "error")
        return redirect(url_for("seating.seating_home", event_id=event_id))

    # Active allergy tags for this event (per the Allergies toggle screen) --
    # only these show up as flags on the seating chart, never the free-text note.
    off_ids = {r.tag_id for r in EventAllergyOff.query.filter_by(event_id=event_id).all()}

    # Pre-event materials checklist -- which items the GS has already
    # checked off as prepared for this event.
    checked_materials = {m.material_key for m in EventMaterial.query.filter_by(event_id=event_id).all()}

    # Build enriched seat list
    seats = []
    for sa in assignments:
        if sa.person:
            name    = sa.person.display_name
            gender  = sa.person.gender or ""
            active_tags = [t.label for t in sa.person.dietary_tags if t.id not in off_ids]
        elif sa.guest:
            name    = sa.guest.display_name
            gender  = sa.guest.gender or ""
            active_tags = [t.label for t in sa.guest.dietary_tags if t.id not in off_ids]
        else:
            continue

        # Table label
        tbl = next((t for t in event.table_config["tables"]
                    if t["id"] == sa.table_num), None)
        label = tbl["label"] if tbl else f"Table {sa.table_num}"

        seats.append({
            "table_num":   sa.table_num,
            "table_label": label,
            "table_size":  tbl["size"] if tbl else 8,
            "seat_num":    sa.seat_num,
            "name":        name,
            "dietary":     ", ".join(active_tags),
            "gender":      gender,
            "is_guest":    sa.guest is not None,
        })

    # Sort helpers
    by_table = {}
    for s in seats:
        by_table.setdefault(s["table_num"], []).append(s)
    for seats_list in by_table.values():
        seats_list.sort(key=lambda x: x["seat_num"])

    alpha = sorted(seats, key=lambda x: x["name"].split()[-1].lower())

    tables_meta = sorted(event.table_config["tables"], key=lambda t: t["id"])
    guest_count = sum(1 for s in seats if s["is_guest"])

    from datetime import datetime
    return render_template(
        "admin/seating/print.html",
        event       = event,
        seats       = seats,
        by_table    = by_table,
        alpha       = alpha,
        tables_meta = tables_meta,
        guest_count = guest_count,
        now         = datetime.now().strftime("%B %d, %Y"),
        checked_materials = checked_materials,
    )


# --- MATERIALS CHECKLIST TOGGLE -----------------------------------------------

@seating_bp.route("/event/<int:event_id>/materials/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_material(event_id):
    """Check/uncheck one pre-event material as prepared. Manual only --
    never set automatically by generating a document, since "generated"
    and "reviewed and ready" are deliberately different moments."""
    data = request.get_json(force=True)
    key = data.get("material_key")
    valid_keys = {"menu_booklet", "wine_tags", "table_name_cards",
                  "name_badges", "charts_and_lists"}
    if key not in valid_keys:
        return jsonify({"ok": False, "error": "Invalid material key"}), 400

    existing = EventMaterial.query.filter_by(event_id=event_id, material_key=key).first()
    if existing:
        db.session.delete(existing)
        checked = False
    else:
        db.session.add(EventMaterial(event_id=event_id, material_key=key))
        checked = True
    db.session.commit()
    return jsonify({"ok": True, "checked": checked})


# --- EXPORT NAME CARDS (Avery 5011 .docx) ------------------------------------

@seating_bp.route("/event/<int:event_id>/export_namecards")
@login_required
@admin_required
def export_namecards(event_id):
    """Generate an Avery 5011-compatible .docx of name cards -- pure Python, no Node."""
    import tempfile, os, sys as _sys
    from flask import send_file

    event = Event.query.get_or_404(event_id)
    assignments = SeatAssignment.query.filter_by(event_id=event_id).all()
    if not assignments:
        flash("No seating assignments to export.", "error")
        return redirect(url_for("seating.print_seating", event_id=event_id))

    off_ids = {r.tag_id for r in EventAllergyOff.query.filter_by(event_id=event_id).all()}

    seats = []
    for sa in assignments:
        if sa.person:
            name = sa.person.display_name
            has_allergy = any(t.id not in off_ids for t in sa.person.dietary_tags)
        elif sa.guest:
            name = sa.guest.display_name
            has_allergy = any(t.id not in off_ids for t in sa.guest.dietary_tags)
        else:
            continue
        tbl = next((t for t in (event.table_config or {}).get("tables", [])
                    if t["id"] == sa.table_num), None)
        label = tbl["label"] if tbl else f"Table {sa.table_num}"
        seats.append({"name": name, "table_label": label, "seat_num": sa.seat_num,
                      "table_num": sa.table_num, "has_allergy": has_allergy})

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    logo = os.path.join(project_root, "frontend", "static", "img", "Chevalier_Logo.jpg")

    out = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    out.close()

    try:
        if project_root not in _sys.path:
            _sys.path.insert(0, project_root)
        # Force reimport so Flask doesn't serve a cached old version
        import importlib
        import gen_namecards as _gnc
        importlib.reload(_gnc)
        _gnc.generate(seats, logo, out.name)
    except Exception as e:
        flash(f"Export failed: {e}", "error")
        return redirect(url_for("seating.print_seating", event_id=event_id))

    safe_title = "".join(c for c in event.title if c.isalnum() or c in " -_")
    filename = f"NameCards_{safe_title}.docx"
    return send_file(out.name, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# --- EXPORT NAME BADGES (Avery 74461 .pdf) -----------------------------------

@seating_bp.route("/event/<int:event_id>/export_namebadges")
@login_required
@admin_required
def export_namebadges(event_id):
    """Generate an Avery 74461-compatible .pdf of guest name badges."""
    import tempfile, os, sys as _sys
    from flask import send_file

    event = Event.query.get_or_404(event_id)
    assignments = SeatAssignment.query.filter_by(event_id=event_id).all()
    if not assignments:
        flash("No seating assignments to export.", "error")
        return redirect(url_for("seating.print_seating", event_id=event_id))

    # Only the ad-hoc guests a member brings (RSVPGuest rows) get a badge --
    # not the member themselves, and not their partner either, since a
    # partner is their own Person record seated via sa.person, same as the
    # member. sa.guest is specifically the "extra guest" bucket.
    guests = []
    for sa in assignments:
        if sa.guest:
            guests.append({"first_name": sa.guest.first_name, "last_name": sa.guest.last_name})

    if not guests:
        flash("No guests (plus-ones) found for this event -- only members/partners are seated.", "error")
        return redirect(url_for("seating.print_seating", event_id=event_id))

    # Alphabetical by last name, matching the other guest-facing print forms
    guests.sort(key=lambda g: (g["last_name"] or "").lower())

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    logo = os.path.join(project_root, "frontend", "static", "img", "Chevalier_Logo.jpg")

    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    out.close()

    try:
        if project_root not in _sys.path:
            _sys.path.insert(0, project_root)
        import importlib
        import gen_namebadges as _gnb
        importlib.reload(_gnb)
        _gnb.generate(guests, logo, out.name)
    except Exception as e:
        flash(f"Export failed: {e}", "error")
        return redirect(url_for("seating.print_seating", event_id=event_id))

    safe_title = "".join(c for c in event.title if c.isalnum() or c in " -_")
    filename = f"NameBadges_{safe_title}.pdf"
    return send_file(out.name, as_attachment=True, download_name=filename,
                     mimetype="application/pdf")


# --- WINE TAGS -----------------------------------------------------------------

@seating_bp.route("/event/<int:event_id>/winetags", methods=["GET", "POST"])
@login_required
@admin_required
def wine_tags(event_id):
    """Upload/replace the wine list for an event (CSV) and view the current list."""
    import csv, io

    event = Event.query.get_or_404(event_id)
    errors = []

    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or not file.filename:
            errors.append("Please choose a CSV file to upload.")
        else:
            try:
                raw = file.read()
                content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
                reader = csv.DictReader(io.StringIO(content))
            except Exception as e:
                errors.append(f"Could not read file: {e}")
                reader = None

            if reader is not None:
                required = {"position", "course", "domain", "appellation"}
                fieldnames = {f.strip().lower() for f in (reader.fieldnames or [])}
                missing = required - fieldnames
                if missing:
                    errors.append(f"Missing required columns: {', '.join(sorted(missing))}")
                else:
                    new_wines = []
                    for i, row in enumerate(reader, start=2):
                        row = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
                        if not row.get("position"):
                            continue  # skip blank rows silently
                        try:
                            position = int(row["position"])
                        except ValueError:
                            errors.append(f"Row {i}: position must be a number, got '{row['position']}'")
                            continue
                        try:
                            course = int(row["course"])
                        except ValueError:
                            errors.append(f"Row {i}: course must be a number, got '{row['course']}'")
                            continue
                        if not row.get("domain") or not row.get("appellation"):
                            errors.append(f"Row {i}: domain and appellation are required")
                            continue
                        new_wines.append({
                            "position": position,
                            "course": course,
                            "vintage": row.get("vintage", "") or None,
                            "domain": row["domain"],
                            "appellation": row["appellation"],
                        })

                    if not errors:
                        if not new_wines:
                            errors.append("No wine rows found in the file.")
                        else:
                            # Position is the wine's index within its own course (1st, 2nd...
                            # of that course) and resets back to 1 at the start of each new
                            # course -- so uniqueness is per (course, position), not global.
                            # Catch collisions here with a clear message instead of letting
                            # the database's unique constraint surface as a 500.
                            seen = {}
                            dupes = set()
                            for w in new_wines:
                                key = (w["course"], w["position"])
                                if key in seen:
                                    dupes.add(key)
                                else:
                                    seen[key] = True
                            if dupes:
                                for course, pos in sorted(dupes):
                                    errors.append(
                                        f"Course {course}, position {pos} is used more than once. "
                                        f"Position restarts at 1 for each new course (1st wine of course 1, "
                                        f"2nd wine of course 1, 1st wine of course 2, ...)."
                                    )

                    if not errors and new_wines:
                        # Replace the whole list for this event
                        WineTag.query.filter_by(event_id=event_id).delete()
                        for w in new_wines:
                            db.session.add(WineTag(event_id=event_id, **w))
                        db.session.commit()
                        flash(f"Wine list saved -- {len(new_wines)} wines.", "success")
                        return redirect(url_for("seating.wine_tags", event_id=event_id))

    wines = WineTag.query.filter_by(event_id=event_id).order_by(WineTag.course, WineTag.position).all()
    return render_template("admin/seating/winetags.html",
                           event=event, wines=wines, errors=errors)


@seating_bp.route("/event/<int:event_id>/winetags/template")
@login_required
@admin_required
def wine_tags_template(event_id):
    """Download a blank CSV template for the wine list."""
    import csv, io
    from flask import Response

    columns = ["position", "course", "vintage", "domain", "appellation"]
    example_rows = [
        {"position": "1", "course": "1", "vintage": "2019", "domain": "Domaine Leflaive",
         "appellation": "Puligny-Montrachet, Vieilles Vignes, Premier Cru"},
        {"position": "2", "course": "1", "vintage": "2018", "domain": "Domaine de la Romanee-Conti",
         "appellation": "Echezeaux Grand Cru, Vieilles Vignes"},
        {"position": "1", "course": "2", "vintage": "2020", "domain": "Chateau Margaux",
         "appellation": "Margaux Grand Cru Classe"},
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(example_rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=wine_list_template.csv"}
    )


@seating_bp.route("/event/<int:event_id>/winetags/print")
@login_required
@admin_required
def print_wine_tags(event_id):
    """Generate the full wine-tag PDF -- one wine's copies grouped together."""
    import tempfile, os, sys as _sys
    from flask import send_file

    event = Event.query.get_or_404(event_id)
    wines = WineTag.query.filter_by(event_id=event_id).order_by(WineTag.course, WineTag.position).all()
    if not wines:
        flash("No wine list uploaded for this event yet.", "error")
        return redirect(url_for("seating.wine_tags", event_id=event_id))

    guest_count = event.confirmed_count
    if not guest_count:
        flash("No confirmed RSVPs yet -- nothing to print tags for.", "error")
        return redirect(url_for("seating.wine_tags", event_id=event_id))

    wine_dicts = [{"position": w.position, "course": w.course, "vintage": w.vintage,
                   "domain": w.domain, "appellation": w.appellation} for w in wines]

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    bg = os.path.join(project_root, "frontend", "static", "img", "wine_tag_bg.jpg")

    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    out.close()

    try:
        if project_root not in _sys.path:
            _sys.path.insert(0, project_root)
        import importlib
        import gen_winetags as _gwt
        importlib.reload(_gwt)   # avoid serving a stale cached version
        _gwt.generate(wine_dicts, guest_count, bg, out.name)
    except Exception as e:
        flash(f"Wine tag generation failed: {e}", "error")
        return redirect(url_for("seating.wine_tags", event_id=event_id))

    safe_title = "".join(c for c in event.title if c.isalnum() or c in " -_")
    filename = f"WineTags_{safe_title}.pdf"
    return send_file(out.name, as_attachment=True, download_name=filename,
                     mimetype="application/pdf")


# --- MENU ITEMS -----------------------------------------------------------------

@seating_bp.route("/event/<int:event_id>/menu", methods=["GET", "POST"])
@login_required
@admin_required
def menu_items(event_id):
    """Upload/replace the menu for an event (CSV) and view the current list.
    One dish per course, sharing the same course numbers as the wine list."""
    import csv, io

    event = Event.query.get_or_404(event_id)
    errors = []

    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or not file.filename:
            errors.append("Please choose a CSV file to upload.")
        else:
            try:
                raw = file.read()
                content_str = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
                reader = csv.DictReader(io.StringIO(content_str))
            except Exception as e:
                errors.append(f"Could not read file: {e}")
                reader = None

            if reader is not None:
                required = {"course", "dish_french"}
                fieldnames = {f.strip().lower() for f in (reader.fieldnames or [])}
                missing = required - fieldnames
                if missing:
                    errors.append(f"Missing required columns: {', '.join(sorted(missing))}")
                else:
                    new_items = []
                    for i, row in enumerate(reader, start=2):
                        row = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
                        if not row.get("course"):
                            continue  # skip blank rows silently
                        try:
                            course = int(row["course"])
                        except ValueError:
                            errors.append(f"Row {i}: course must be a number, got '{row['course']}'")
                            continue
                        if not row.get("dish_french"):
                            errors.append(f"Row {i}: dish_french is required")
                            continue
                        new_items.append({
                            "course": course,
                            "dish_french": row["dish_french"],
                            "dish_english": row.get("dish_english", "") or None,
                        })

                    if not errors:
                        if not new_items:
                            errors.append("No menu rows found in the file.")
                        else:
                            seen = set()
                            dupes = set()
                            for m in new_items:
                                if m["course"] in seen:
                                    dupes.add(m["course"])
                                else:
                                    seen.add(m["course"])
                            if dupes:
                                for course in sorted(dupes):
                                    errors.append(
                                        f"Course {course} appears more than once -- only one dish "
                                        f"per course is supported."
                                    )

                    if not errors and new_items:
                        MenuItem.query.filter_by(event_id=event_id).delete()
                        for m in new_items:
                            db.session.add(MenuItem(event_id=event_id, **m))
                        db.session.commit()
                        flash(f"Menu saved -- {len(new_items)} course(s).", "success")
                        return redirect(url_for("seating.menu_items", event_id=event_id))

    items = MenuItem.query.filter_by(event_id=event_id).order_by(MenuItem.course).all()
    return render_template("admin/seating/menu.html",
                           event=event, items=items, errors=errors)


@seating_bp.route("/event/<int:event_id>/menu/template")
@login_required
@admin_required
def menu_items_template(event_id):
    """Download a blank CSV template for the menu."""
    import csv, io
    from flask import Response

    columns = ["course", "dish_french", "dish_english"]
    example_rows = [
        {"course": "1", "dish_french": "Seriole, citron Meyer, fenouil, emulsion d'olives Castelvetrano, capres",
         "dish_english": "Yellowtail, Meyer lemon, fennel, Castelvetrano olive emulsion, capers"},
        {"course": "2", "dish_french": "Canard, champignons sauvages, fregola sarda, asperges, peche, glace au jus de canard",
         "dish_english": "Duck, wild mushrooms, fregola sarda, asparagus, peach, duck glace"},
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(example_rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=menu_template.csv"}
    )


# --- HELPERS ------------------------------------------------------------------

def _get_attendees(event):
    """
    Return a flat list of everyone confirmed for this event.
    Linked partners are included via their own RSVP only -- not duplicated as guests.
    """
    attendees = []
    confirmed_person_ids = set()

    # First pass: collect all confirmed person IDs
    for rsvp in event.rsvps:
        if rsvp.status == "confirmed":
            confirmed_person_ids.add(rsvp.person_id)

    for rsvp in event.rsvps:
        if rsvp.status != "confirmed":
            continue
        p = rsvp.person
        attendees.append({
            "person_id":    p.id,
            "guest_id":     None,
            "name":         p.display_name,
            "type":         "member" if p.person_type in ("member","honoraire","aspirant") else p.person_type,
            "gender":       p.gender or "",
            "is_officer":   p.is_officer,
            "officer_role": p.officer_role,
            "member_since": p.member_since.isoformat() if p.member_since else None,
            "partner_id":   p.partner_id,
        })
        for g in rsvp.guests:
            # Skip if this guest is actually a linked partner already in the confirmed list
            # Match by name to catch cases where partner was added manually as a guest
            is_linked_partner = False
            if p.partner_id:
                partner = Person.query.get(p.partner_id)
                if partner and partner.id in confirmed_person_ids:
                    # Partner has their own RSVP -- skip if guest name matches partner
                    if (g.first_name.lower() == partner.first_name.lower() and
                        g.last_name.lower() == partner.last_name.lower()):
                        is_linked_partner = True

            if not is_linked_partner:
                attendees.append({
                    "person_id":    None,
                    "guest_id":     g.id,
                    "name":         g.display_name,
                    "type":         "guest",
                    "gender":       g.gender or "",
                    "is_officer":   False,
                    "officer_role": None,
                    "member_since": None,
                    "partner_id":   None,
                    "host_name":    p.display_name,
                    "host_id":      p.id,
                })
    return attendees


def _get_event_couples(event):
    """
    Return selectable entries for the seating-rule dropdowns: couples where
    at least one person has a confirmed RSVP for this event, PLUS singles
    (confirmed attendees with no linked partner in the system).
    """
    # Get all confirmed person IDs for this event
    confirmed_ids = set()
    for rsvp in event.rsvps:
        if rsvp.status == "confirmed":
            confirmed_ids.add(rsvp.person_id)

    couples = []
    seen = set()
    persons = Person.query.filter(
        Person.partner_id.isnot(None),
        Person.person_type.in_(["member", "partner"])
    ).all()

    for p in persons:
        pair = tuple(sorted([p.id, p.partner_id]))
        if pair not in seen:
            # Only include if at least one of the couple has RSVPd
            if p.id in confirmed_ids or p.partner_id in confirmed_ids:
                seen.add(pair)
                partner = Person.query.get(p.partner_id)
                if partner:
                    couples.append({
                        "id":           p.id,
                        "name":         p.display_name,
                        "partner_id":   partner.id,
                        "partner_name": partner.display_name,
                        "couple_label": f"{p.last_name} / {partner.last_name}"
                                        if p.last_name != partner.last_name
                                        else f"{p.display_name} & {partner.display_name}",
                    })

    # Singles: confirmed attendees with no linked partner at all. Without
    # this, members/honoraires/aspirants who aren't part of a recorded
    # couple never appear in the "sit together" / "don't sit together"
    # dropdowns.
    paired_ids = {pid for c in couples for pid in (c["id"], c["partner_id"])}
    for pid in confirmed_ids:
        if pid in paired_ids:
            continue
        p = Person.query.get(pid)
        if p and p.person_type in ("member", "honoraire", "aspirant") and not p.partner_id:
            couples.append({
                "id":           p.id,
                "name":         p.display_name,
                "partner_id":   None,
                "partner_name": None,
                "couple_label": p.display_name,
            })
            paired_ids.add(pid)

    return sorted(couples, key=lambda c: c["couple_label"])


def _get_seating_history(current_event_id, limit=3):
    """
    Return table co-assignments from the last `limit` events (excluding current).
    Result: list of sets of person_ids who sat at the same table.
    """
    past_events = (Event.query
                   .filter(Event.id != current_event_id)
                   .order_by(Event.event_date.desc())
                   .limit(limit).all())
    history = []
    for ev in past_events:
        tables = {}
        for sa in SeatAssignment.query.filter_by(event_id=ev.id).all():
            if sa.person_id:
                tables.setdefault(sa.table_num, set()).add(sa.person_id)
        for table_group in tables.values():
            if len(table_group) > 1:
                history.append(list(table_group))
    return history


def _build_prompt(event, attendees, couples, history, locked):
    """Build the natural-language prompt sent to the AI."""
    tables = event.table_config.get("tables", [])
    rules  = event.seating_rules or {}
    global_rules = SeatingRule.query.filter_by(is_active=True).all()

    # Format locked seats
    locked_desc = ""
    if locked:
        lines = []
        for (tnum, snum), sa in locked.items():
            name = sa.occupant_name or "Unknown"
            lines.append(f"  Table {tnum}, Seat {snum}: {name} (LOCKED -- do not move)")
        locked_desc = "\nLOCKED SEATS (must remain exactly as assigned):\n" + "\n".join(lines)

    # Format history
    history_desc = ""
    if history:
        history_desc = "\nRECENT SEATING HISTORY (avoid repeating these table groupings):\n"
        for i, group in enumerate(history[:10], 1):
            names = []
            for pid in group:
                p = Person.query.get(pid)
                if p:
                    names.append(p.display_name)
            if names:
                history_desc += f"  Past table: {', '.join(names)}\n"

    # Format couple rules
    def couple_name(pid):
        p = Person.query.get(pid)
        return p.display_name if p else f"Person {pid}"

    def couple_names(pid):
        """Return 'Member Name & Partner Name' for a person ID."""
        p = Person.query.get(pid)
        if not p:
            return f"Person {pid}"
        if p.partner_id:
            partner = Person.query.get(p.partner_id)
            if partner:
                return f"{p.display_name} & {partner.display_name}"
        return p.display_name

    not_together = ""
    if rules.get("not_together"):
        lines = [f"  - {couple_names(pair[0])} must NOT be at the same table as {couple_names(pair[1])} (this means ALL members of both couples must be at different tables)"
                 for pair in rules["not_together"]]
        not_together = "\n".join(lines)

    prefer_together = ""
    if rules.get("prefer_together"):
        lines = [f"  - REQUIRED: {couple_names(pair[0])} MUST be at the same table as {couple_names(pair[1])} -- place all four people at the same table"
                 for pair in rules["prefer_together"]]
        prefer_together = "\n".join(lines)

    custom_rules = ""
    if rules.get("custom"):
        custom_rules = "\n".join(f"  - {r}" for r in rules["custom"])

    # Attendee list with key attributes
    attendee_lines = []
    for a in attendees:
        line = f"  ID:{a['person_id'] or 'G'+str(a['guest_id'])} | {a['name']} | {a['type']}"
        if a["is_officer"]:
            line += f" | OFFICER: {a['officer_role'] or 'Officer'}"
        if a["member_since"]:
            line += f" | Member since: {a['member_since'][:4]}"
        if a.get("host_name"):
            line += f" | Guest of: {a['host_name']}"
        if a["partner_id"]:
            partner = Person.query.get(a["partner_id"])
            if partner:
                line += f" | Partner: {partner.display_name} (ID:{a['partner_id']})"
        attendee_lines.append(line)

    prompt = f"""
You are seating {len(attendees)} guests at {len(tables)} tables for '{event.title}'.

TABLE CONFIGURATION:
{chr(10).join(f"  Table {t['id']}: {t['size']} seats (label: {t.get('label','Table '+str(t['id']))})" for t in tables)}

ATTENDEES:
{chr(10).join(attendee_lines)}

PERMANENT RULES -- these are ABSOLUTE and must NEVER be violated under any circumstances:
{chr(10).join(f"  - {r.description}" for r in global_rules)}
  - COUPLES SEATING (CRITICAL -- highest priority rule): Every couple (partner pairs) MUST be at the same table, but MUST NOT occupy adjacent seats. Two seats are adjacent if their seat numbers differ by exactly 1 (e.g. seats 3 and 4 are adjacent; seats 3 and 5 are NOT adjacent). Before finalizing your assignment, verify every couple: same table ?, seat numbers not consecutive ?. This rule overrides optimization goals.
  - Every couple's partner sits with them (same table) -- required without exception.
  - Guests (non-members) must be seated at the same table as their host.

HARD CONSTRAINTS -- treat these as ABSOLUTE REQUIREMENTS:
{not_together or "  None"}
{locked_desc}

STRONG PREFERENCES -- treat these as REQUIRED unless it makes seating mathematically impossible:
{prefer_together or "  None"}
{custom_rules or ""}

OPTIMIZATION PRIORITIES (apply after satisfying all rules above):
  1. Spread officers as evenly as possible across tables (ideally one per table)
  2. Avoid placing couples together who have shared a table at recent events
  3. Mix newer members (joined recently) with longer-standing members
  4. Ensure every guest sits with someone they can converse with
{history_desc}

SEATING CONSTRAINTS:
  - Every attendee must be assigned exactly once
  - Do not exceed each table's seat count

Assign all {len(attendees)} attendees to the tables now. Respond in exactly two parts, in this order:

1. Inside <json></json> tags: the complete final seating as a single JSON object, no markdown
   fences, no other text. Required structure:
   {{"tables": [{{"table_num": 1, "seats": [{{"seat_num": 1, "person_id": 123, "guest_id": null}}]}}, ...]}}
   person_id: integer for members/partners, null for ad-hoc guests.
   guest_id: integer for ad-hoc guests, null otherwise.
   Get straight to this -- it's the part that matters most, and it needs to be complete.

2. Only after the JSON is fully written, inside <reasoning></reasoning> tags: a BRIEF summary of
   your approach -- a few short bullet points covering how you handled couples, any hard
   constraints, and your main optimization choices. This is a quick note for a human reviewer,
   not a full explanation of every seat -- keep it under 100 words. If you're running low on
   room, it's fine to skip this section entirely rather than shorten the JSON.
""".strip()

    return prompt


# --- PARTY-BASED PROTOTYPE (faster proposal path) -----------------------------
# Instead of asking the AI to place every individual into a specific seat, this
# groups attendees into "parties" first (a couple, a guest bundled with their
# host, or a lone person) -- units that must always sit together anyway. The
# AI then only decides which TABLE each party goes to (a much smaller
# reasoning task and a much smaller JSON output than full per-seat
# assignment), and a deterministic pass fills in the actual seat numbers
# afterward. The existing _enforce_rules() safety net still runs at the end,
# same as the original path.

def _build_parties(attendees, locked):
    """
    Group attendees into parties -- units that must be seated together.
    Returns a list of dicts:
      {party_id, member_ids, guest_ids, names, size, has_officer, table_lock}
    """
    by_person_id = {a["person_id"]: a for a in attendees if a["person_id"] is not None}
    guests_by_host = {}
    for a in attendees:
        if a["type"] == "guest":
            guests_by_host.setdefault(a["host_id"], []).append(a)

    # Bidirectional partner lookup -- some real member records only have
    # the partner link set on one side (e.g. an older import, or a manual
    # edit that didn't set both directions). Relying only on "my own
    # partner_id field" would silently split that couple into two
    # separate single-person parties, since whichever partner has the
    # blank field gets processed as a standalone single, and by the time
    # the other partner's own (correct) link is checked, this person is
    # already marked handled.
    partner_of = {}
    for a in attendees:
        pid = a["person_id"]
        if pid is None or not a["partner_id"]:
            continue
        partner_of[pid] = a["partner_id"]
        partner_of.setdefault(a["partner_id"], pid)

    locked_person_table = {}
    locked_guest_table = {}
    for (tnum, _snum), sa in locked.items():
        if sa.person_id:
            locked_person_table[sa.person_id] = tnum
        if sa.guest_id:
            locked_guest_table[sa.guest_id] = tnum

    assigned_persons = set()
    assigned_guests = set()
    parties = []
    next_id = [1]

    for a in attendees:
        pid = a["person_id"]
        if pid is None or pid in assigned_persons:
            continue

        party = {"party_id": next_id[0], "member_ids": [pid], "guest_ids": [],
                 "names": [a["name"]], "size": 1,
                 "has_officer": bool(a["is_officer"]), "table_lock": None}
        next_id[0] += 1
        assigned_persons.add(pid)

        partner_id = partner_of.get(pid)
        if partner_id and partner_id in by_person_id and partner_id not in assigned_persons:
            pa = by_person_id[partner_id]
            party["member_ids"].append(partner_id)
            party["names"].append(pa["name"])
            party["size"] += 1
            party["has_officer"] = party["has_officer"] or bool(pa["is_officer"])
            assigned_persons.add(partner_id)

        for hid in [pid] + ([partner_id] if partner_id else []):
            for g in guests_by_host.get(hid, []):
                if g["guest_id"] not in assigned_guests:
                    party["guest_ids"].append(g["guest_id"])
                    party["names"].append(g["name"])
                    party["size"] += 1
                    assigned_guests.add(g["guest_id"])

        for mid in party["member_ids"]:
            if mid in locked_person_table:
                party["table_lock"] = locked_person_table[mid]
        for gid in party["guest_ids"]:
            if gid in locked_guest_table:
                party["table_lock"] = locked_guest_table[gid]

        parties.append(party)

    return parties


def _apply_party_rules(parties, rules):
    """
    Applies event.seating_rules to the party list:
      - prefer_together pairs get merged into a single combined party
      - not_together pairs are returned separately as hard constraints
        (party_id, party_id) for the AI to respect
    """
    def find_party(pid):
        for p in parties:
            if pid in p["member_ids"]:
                return p
        return None

    for a_id, b_id in (rules.get("prefer_together") or []):
        pa, pb = find_party(a_id), find_party(b_id)
        if pa and pb and pa is not pb:
            pa["member_ids"] += pb["member_ids"]
            pa["guest_ids"]  += pb["guest_ids"]
            pa["names"]      += pb["names"]
            pa["size"]       += pb["size"]
            pa["has_officer"] = pa["has_officer"] or pb["has_officer"]
            if pb["table_lock"] and not pa["table_lock"]:
                pa["table_lock"] = pb["table_lock"]
            parties.remove(pb)

    not_together_pairs = []
    for a_id, b_id in (rules.get("not_together") or []):
        pa, pb = find_party(a_id), find_party(b_id)
        if pa and pb and pa is not pb:
            not_together_pairs.append((pa["party_id"], pb["party_id"]))

    return parties, not_together_pairs


def _build_party_prompt(event, parties, not_together_pairs, history, tables):
    """Build the (much smaller) prompt for the party-level table assignment."""
    rules = event.seating_rules or {}
    global_rules = SeatingRule.query.filter_by(is_active=True).all()

    party_lines = []
    for p in parties:
        tags = []
        if p["has_officer"]:
            tags.append("HAS OFFICER")
        if p["table_lock"]:
            tags.append(f"LOCKED TO TABLE {p['table_lock']}")
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        party_lines.append(f"  Party {p['party_id']}: {', '.join(p['names'])} "
                           f"({p['size']} seat(s)){tag_str}")

    not_together_desc = ""
    if not_together_pairs:
        lines = [f"  - Party {a} must NOT be at the same table as Party {b}"
                 for a, b in not_together_pairs]
        not_together_desc = "\nHARD CONSTRAINT -- these parties must be at different tables:\n" + "\n".join(lines)

    custom_rules = ""
    if rules.get("custom"):
        custom_rules = "\nCUSTOM RULES:\n" + "\n".join(f"  - {r}" for r in rules["custom"])

    history_desc = ""
    if history:
        names_seen = []
        for group in history[:10]:
            names = [Person.query.get(pid).display_name for pid in group if Person.query.get(pid)]
            if names:
                names_seen.append(", ".join(names))
        if names_seen:
            history_desc = "\nRECENT SEATING HISTORY (avoid repeating these groupings):\n" + \
                           "\n".join(f"  - {n}" for n in names_seen)

    prompt = f"""
You are assigning {len(parties)} parties (groups that must sit together) to {len(tables)} tables
for '{event.title}'. A party is already a fixed unit -- do NOT split a party across tables or
assign individual seats. Only decide which TABLE each party goes to.

TABLE CONFIGURATION:
{chr(10).join(f"  Table {t['id']}: {t['size']} seats (label: {t.get('label','Table '+str(t['id']))})" for t in tables)}

PARTIES:
{chr(10).join(party_lines)}

PERMANENT RULES:
{chr(10).join(f"  - {r.description}" for r in global_rules)}
{not_together_desc}

OPTIMIZATION PRIORITIES:
  1. Every locked party MUST stay at its locked table
  2. No table may exceed its seat count (sum of party sizes at that table)
  3. Spread officers as evenly as possible across tables
  4. Avoid recreating recent table groupings where possible
{history_desc}
{custom_rules}

Respond in exactly two parts, in this order:

1. Inside <json></json> tags: the table assignment as a single JSON object, no markdown fences,
   no other text. Required structure:
   {{"assignments": [{{"party_id": 1, "table_num": 1}}, ...]}}
   Every party must appear exactly once. Get straight to this first.

2. Only after the JSON is fully written, inside <reasoning></reasoning> tags: a BRIEF summary of
   your approach. Under 80 words, or skip it entirely if you're running low on room.
""".strip()

    return prompt


def _lookup_gender(kind, ident):
    """Small helper for gender-aware seat placement -- looks up a Person's
    or RSVPGuest's gender by id. A handful of extra tiny queries per
    proposal is negligible next to the AI call itself."""
    if kind == "person":
        p = Person.query.get(ident)
        return (p.gender or "") if p else ""
    else:
        g = RSVPGuest.query.get(ident)
        return (g.gender or "") if g else ""


def _fix_table_overflow(parties, party_table, tables, locked):
    """
    Ensures no table's assigned parties exceed its seat count. The AI is
    only asked to respect capacity as an instruction, not guaranteed to --
    if it (or the earlier missing-party fallback) overpacks one table, this
    moves the overflow to whichever other table has room, smallest party
    first (fewer people disrupted per move), never touching a locked party.

    Effective capacity per table accounts for seats already taken by locked
    assignments -- a table with 8 seats but 2 already locked really only
    has 6 seats of room for everyone else, not 8.

    Runs iteratively rather than in a single pass, since moving overflow
    off of one table can push another table over capacity in turn --
    a single pass would only ever check each table once and could miss a
    cascade like that, incorrectly treating it as resolved.

    Returns (party_table, unplaced) where unplaced is a list of party_ids
    that genuinely don't fit anywhere even after rebalancing settles. If a
    table is still over capacity at that point, only the actual excess is
    reported (keeping as many parties there as still fit, largest first) --
    never the whole table's worth of parties, most of which are typically
    still fine.
    """
    party_table = dict(party_table)
    locked_seats_per_table = {}
    for (tnum, _snum) in locked.keys():
        locked_seats_per_table[tnum] = locked_seats_per_table.get(tnum, 0) + 1
    table_capacity = {t["id"]: t["size"] - locked_seats_per_table.get(t["id"], 0) for t in tables}
    party_by_id = {p["party_id"]: p for p in parties}

    def _load():
        load = {}
        for pid, tnum in party_table.items():
            load[tnum] = load.get(tnum, 0) + party_by_id[pid]["size"]
        return load

    # Iterate until stable (no table over capacity) or no further move makes
    # progress -- bounded generously since cascades settle within a handful
    # of passes at most.
    for _pass in range(len(table_capacity) + 2):
        load = _load()
        overflowing = [t for t, total in load.items() if total > table_capacity.get(t, 0)]
        if not overflowing:
            break

        moved_any = False
        for tnum in overflowing:
            capacity = table_capacity.get(tnum, 0)
            if load[tnum] <= capacity:
                continue
            parties_here = [pid for pid, t in party_table.items() if t == tnum]
            movable = [pid for pid in parties_here if party_by_id[pid]["table_lock"] is None]
            movable.sort(key=lambda pid: party_by_id[pid]["size"])

            for pid in movable:
                if load[tnum] <= capacity:
                    break
                size = party_by_id[pid]["size"]
                target = next((oid for oid, ocap in table_capacity.items()
                              if oid != tnum and load.get(oid, 0) + size <= ocap), None)
                if target is not None:
                    party_table[pid] = target
                    load[tnum] -= size
                    load[target] = load.get(target, 0) + size
                    moved_any = True

        if not moved_any:
            break  # no further progress possible

    # Final pass: for any table still over capacity after rebalancing has
    # settled, keep as many parties there as will fit (largest first, to
    # minimize wasted seats) and report only the genuine leftover -- not
    # everyone at that table, most of whom are still seated just fine.
    final_load = _load()
    unplaced = []
    for tnum, total in list(final_load.items()):
        capacity = table_capacity.get(tnum, 0)
        if total <= capacity:
            continue
        parties_here = [pid for pid, t in party_table.items() if t == tnum]
        locked_here = [pid for pid in parties_here if party_by_id[pid]["table_lock"] is not None]
        movable_here = [pid for pid in parties_here if party_by_id[pid]["table_lock"] is None]
        used = sum(party_by_id[pid]["size"] for pid in locked_here)
        movable_here.sort(key=lambda pid: -party_by_id[pid]["size"])
        for pid in movable_here:
            size = party_by_id[pid]["size"]
            if used + size <= capacity:
                used += size
            else:
                unplaced.append(pid)

    # Critical: actually remove unplaced parties from party_table, rather
    # than just reporting them while leaving them still assigned to their
    # (overloaded) table. Without this, the seat-filler would still try to
    # seat them there, run out of real seats partway through, and whoever
    # happens to be processed last ends up skipped -- which is not
    # necessarily the same party this function identified, making the
    # reported "unplaced" list description not match what actually
    # happened. Removing them here guarantees the two always agree.
    for pid in unplaced:
        party_table.pop(pid, None)

    return party_table, unplaced


def _consolidate_for_unplaced(parties, party_table, tables, unplaced, locked):
    """
    Some parties end up unplaced not because there's too little TOTAL
    room, but because whatever room remains is scattered across several
    tables in pieces too small to use -- one free seat at table 2, one
    free seat at table 5, useless on their own to a couple that needs
    both seats at the same table, even though 2 free seats genuinely
    exist somewhere in the room.

    For each unplaced party, tries a single relocation: is there some
    OTHER already-seated party (never a locked one) that could be moved
    entirely from one under-full table to a different under-full table,
    merging both scraps of slack onto one table so the unplaced party
    fits there? This only rearranges which TABLE parties are at -- the
    existing seat-level placement (couple spacing, gender preference)
    still runs normally afterward on whatever the result is here.

    Returns (party_table, still_unplaced) -- party_table is mutated in
    place for any successful consolidation, and still_unplaced is
    whatever's left after this pass (hopefully smaller, possibly empty).
    """
    party_by_id = {p["party_id"]: p for p in parties}
    locked_seats_per_table = {}
    for (tnum, _snum) in locked.keys():
        locked_seats_per_table[tnum] = locked_seats_per_table.get(tnum, 0) + 1
    table_capacity = {t["id"]: t["size"] - locked_seats_per_table.get(t["id"], 0) for t in tables}

    def _load():
        load = {}
        for pid, tnum in party_table.items():
            load[tnum] = load.get(tnum, 0) + party_by_id[pid]["size"]
        return load

    still_unplaced = list(unplaced)
    for pid in list(still_unplaced):
        party = party_by_id[pid]
        need = party["size"]
        load = _load()
        slack = {tnum: table_capacity.get(tnum, 0) - load.get(tnum, 0)
                 for tnum in table_capacity
                 if table_capacity.get(tnum, 0) - load.get(tnum, 0) > 0}

        solved = False
        for tnum_a in list(slack.keys()):
            if solved:
                break
            if slack[tnum_a] >= need:
                # Shouldn't happen (would already be placed), but no harm
                # in just using it if it's somehow available.
                party_table[pid] = tnum_a
                solved = True
                break
            for tnum_b in list(slack.keys()):
                if tnum_a == tnum_b or solved:
                    continue
                parties_at_a = [p2 for p2, t in party_table.items() if t == tnum_a]
                for mover_pid in parties_at_a:
                    mover = party_by_id[mover_pid]
                    if mover["table_lock"] is not None:
                        continue  # never move a locked party
                    if mover["size"] > slack[tnum_b]:
                        continue  # doesn't fit at the other table
                    if slack[tnum_a] + mover["size"] >= need:
                        # Moving `mover` out of A to B frees enough at A.
                        party_table[mover_pid] = tnum_b
                        party_table[pid] = tnum_a
                        solved = True
                        break
                if solved:
                    break
        if solved:
            still_unplaced.remove(pid)

    return party_table, still_unplaced


def _assign_seats_from_party_tables(event_id, parties, party_table, locked):
    """
    Deterministic seat-level placement: given which table each party is on
    (party_table: {party_id: table_num}), fill in actual seat numbers,
    keeping locked seats fixed and spreading each party's own members across
    non-adjacent seats where possible (final adjacency cleanup still runs
    via _enforce_rules afterward, same as the original path).
    """
    # Defense in depth: a locked party's table is never negotiable, no
    # matter what the caller passed in for it.
    party_table = dict(party_table)
    for p in parties:
        if p["table_lock"] is not None:
            party_table[p["party_id"]] = p["table_lock"]

    event = Event.query.get(event_id)
    tables_cfg = {t["id"]: t["size"] for t in (event.table_config or {}).get("tables", [])}

    SeatAssignment.query.filter_by(event_id=event_id, is_locked=False).delete()
    db.session.flush()

    # Seats already taken by locked assignments, per table -- and the set of
    # people/guests already locked into a seat somewhere, who must NOT be
    # given a second, duplicate seat. Also record their genders up front so
    # a newly-placed person correctly avoids sitting next to a same-gender
    # locked occupant too, not just next to other new placements.
    occupied_by_table = {}
    already_locked_persons = set()
    already_locked_guests = set()
    locked_gender_by_table_seat = {}
    for (tnum, snum), sa in locked.items():
        occupied_by_table.setdefault(tnum, set()).add(snum)
        if sa.person_id:
            already_locked_persons.add(sa.person_id)
            locked_gender_by_table_seat[(tnum, snum)] = _lookup_gender("person", sa.person_id)
        if sa.guest_id:
            already_locked_guests.add(sa.guest_id)
            locked_gender_by_table_seat[(tnum, snum)] = _lookup_gender("guest", sa.guest_id)

    # Group parties by their assigned table
    parties_by_table = {}
    for p in parties:
        tnum = party_table.get(p["party_id"])
        if tnum is not None:
            parties_by_table.setdefault(tnum, []).append(p)

    for tnum, table_parties in parties_by_table.items():
        size = tables_cfg.get(tnum, 8)
        taken = occupied_by_table.get(tnum, set())
        free_seats = [s for s in range(1, size + 1) if s not in taken]

        # Interleave: place the "first" occupant of every party before the
        # "second" occupant of any party, which naturally spreads couples
        # apart when there's more than one party at the table. Any residual
        # adjacency is cleaned up by _enforce_rules() afterward. Anyone
        # already seated via a locked seat is excluded here -- they don't
        # need (and must not get) a second seat.
        occupant_rounds = []
        for p in table_parties:
            occupants = [("person", mid) for mid in p["member_ids"] if mid not in already_locked_persons] + \
                       [("guest", gid) for gid in p["guest_ids"] if gid not in already_locked_guests]
            if occupants:
                occupant_rounds.append(occupants)

        ordered = []
        idx = 0
        while any(idx < len(o) for o in occupant_rounds):
            for o in occupant_rounds:
                if idx < len(o):
                    ordered.append(o[idx])
            idx += 1

        # If only one party is seated at this table (nothing else to
        # interleave with) and there's room to spare, space its members
        # across non-consecutive seats rather than packing them side by
        # side -- e.g. a lone couple with 4 empty extra seats shouldn't
        # end up in seats 1 and 2 just because those happen to be first.
        if len(occupant_rounds) == 1 and len(free_seats) > len(occupant_rounds[0]):
            free_seats = free_seats[0::2] + free_seats[1::2]

        # For the partner-adjacency check below: who is each occupant's
        # own party-mate(s) (so gender preference never seats someone next
        # to their own partner even when that's the only gender-clash-free
        # seat available -- e.g. a couple plus several same-gender singles,
        # where alternation is mathematically impossible for the singles
        # but the couple must still end up apart).
        occupant_partymates = {}
        for p in table_parties:
            all_occ = [("person", mid) for mid in p["member_ids"]] + \
                     [("guest", gid) for gid in p["guest_ids"]]
            for occ in all_occ:
                occupant_partymates[occ] = [o for o in all_occ if o != occ]

        # Seed with already-seated occupants (locked seats at this table),
        # so a not-yet-placed person correctly avoids sitting next to a
        # locked party-mate too, not just one placed earlier in this loop.
        placed_seat = {}
        for (tn, snum), sa_locked in locked.items():
            if tn != tnum:
                continue
            if sa_locked.person_id:
                placed_seat[("person", sa_locked.person_id)] = snum
            if sa_locked.guest_id:
                placed_seat[("guest", sa_locked.guest_id)] = snum

        # Place each occupant in order. Couple/party non-adjacency is a hard
        # rule -- never seat someone directly next to their own already-
        # placed party-mate. Gender alternation is applied only as a
        # secondary preference among whatever seats remain after that,
        # since it isn't always mathematically possible (e.g. 3 singles of
        # the same gender at one table) and must never override the
        # couple-spacing guarantee when the two would conflict.
        remaining_seats = list(free_seats)
        seat_gender = {snum: g for (tn, snum), g in locked_gender_by_table_seat.items() if tn == tnum}
        for occ_idx, (kind, ident) in enumerate(ordered):
            occ = (kind, ident)
            gender = _lookup_gender(kind, ident)

            partner_seats = {placed_seat[po] for po in occupant_partymates.get(occ, [])
                             if po in placed_seat}
            candidates = [s for s in remaining_seats
                         if not any(abs(s - ps) == 1 for ps in partner_seats)]
            if not candidates:
                # Every remaining seat is adjacent to a party-mate -- can't
                # happen if there was enough room to spread out, but fall
                # back to any free seat rather than skip this person.
                candidates = list(remaining_seats)

            chosen = None
            if gender:
                for seat in candidates:
                    if seat_gender.get(seat - 1) == gender or seat_gender.get(seat + 1) == gender:
                        continue
                    chosen = seat
                    break
            if chosen is None and candidates:
                chosen = candidates[0]
            if chosen is None:
                print(f"WARNING: could not find any seat for {kind} {ident} at table {tnum} "
                      f"-- this should not happen after capacity rebalancing; investigate.")
                continue
            remaining_seats.remove(chosen)
            seat_gender[chosen] = gender
            placed_seat[occ] = chosen

            sa = SeatAssignment(
                event_id=event_id, table_num=tnum, seat_num=chosen,
                person_id=ident if kind == "person" else None,
                guest_id=ident if kind == "guest" else None,
            )
            db.session.add(sa)

    db.session.flush()


def _apply_proposal(event_id, proposal, locked):
    """Write AI proposal to SeatAssignment table, preserving locked seats."""
    # Delete all unlocked assignments for this event
    SeatAssignment.query.filter_by(event_id=event_id, is_locked=False).delete()
    db.session.flush()

    for table in proposal.get("tables", []):
        tnum = table["table_num"]
        for seat in table.get("seats", []):
            snum    = seat["seat_num"]
            pid     = seat.get("person_id")
            gid_raw = seat.get("guest_id")

            # guest_id may come back as "G1", "G2" etc -- extract the integer
            gid = None
            if gid_raw is not None:
                if isinstance(gid_raw, int):
                    gid = gid_raw
                elif isinstance(gid_raw, str):
                    digits = ''.join(filter(str.isdigit, gid_raw))
                    gid = int(digits) if digits else None

            # Skip if this seat is locked
            if (tnum, snum) in locked:
                continue

            if pid or gid:
                sa = SeatAssignment(
                    event_id  = event_id,
                    table_num = tnum,
                    seat_num  = snum,
                    person_id = pid,
                    guest_id  = gid,
                    is_locked = False,
                )
                db.session.add(sa)

    db.session.commit()


def _enforce_rules(event_id, couples_data):
    """
    Post-generation enforcement pass.
    Runs after the AI proposal is saved and fixes:
      1. Couples seated in adjacent seats (seat numbers differing by 1)
      2. Officers bunched at one table (more than 1 officer per table
         when spreading is possible)
    Returns a dict with counts of fixes applied.
    """
    fixes = {"couples_separated": 0, "officers_spread": 0}

    # -- Load current assignments ----------------------------------------------
    all_sa = SeatAssignment.query.filter_by(event_id=event_id).all()
    if not all_sa:
        return fixes

    # Build table ? list of SeatAssignment
    tables: dict[int, list] = {}
    for sa in all_sa:
        tables.setdefault(sa.table_num, []).append(sa)

    # pid ? SeatAssignment for quick lookup
    pid_to_sa: dict[int, object] = {sa.person_id: sa for sa in all_sa if sa.person_id}

    # -- 1. Fix adjacent couples -----------------------------------------------
    for couple in couples_data:
        pid1 = couple["id"]
        pid2 = couple["partner_id"]
        sa1 = pid_to_sa.get(pid1)
        sa2 = pid_to_sa.get(pid2)
        if not sa1 or not sa2:
            continue
        # Must be same table and adjacent seat numbers
        if sa1.table_num != sa2.table_num:
            continue  # different table is fine (shouldn't happen, but skip)
        if abs(sa1.seat_num - sa2.seat_num) != 1:
            continue  # not adjacent -- already OK

        # They're adjacent. Find another non-locked seat on the same table
        # that is not adjacent to the partner's seat, and swap sa1 into it.
        table_sas = tables[sa1.table_num]
        partner_seat = sa2.seat_num  # keep sa2 fixed, move sa1

        # Candidate seats: other non-locked assignments on the same table
        # (we swap sa1 with another person on the same table)
        swapped = False
        for candidate in table_sas:
            if candidate.person_id == pid1:
                continue  # that's sa1 itself
            if candidate.person_id == pid2:
                continue  # that's sa1's own partner -- swapping with them fixes nothing
            if candidate.is_locked:
                continue
            cand_seat = candidate.seat_num
            # Check: after swap, sa1 would be at cand_seat -- not adjacent to partner_seat
            if abs(cand_seat - partner_seat) == 1:
                continue  # still adjacent after swap
            # Also check: candidate's new seat (sa1.seat_num) not adjacent to their own partner
            cand_partner_sa = None
            if candidate.person_id:
                cand_person = Person.query.get(candidate.person_id)
                if cand_person and cand_person.partner_id:
                    cand_partner_sa = pid_to_sa.get(cand_person.partner_id)
            new_cand_seat = sa1.seat_num
            if cand_partner_sa and cand_partner_sa.table_num == sa1.table_num:
                if abs(new_cand_seat - cand_partner_sa.seat_num) == 1:
                    continue  # would create adjacency for the candidate's couple

            # Perform the swap via a temporary placeholder seat number, with
            # a flush after EACH individual change -- not just a Python
            # tuple-swap, which only changes both attributes in memory
            # before ever touching the database, then leaves SQLAlchemy to
            # decide what order to send the two UPDATEs in. That order
            # isn't guaranteed, and if it sends "move sa1 into candidate's
            # seat" before "move candidate out of it", the two rows briefly
            # collide on the same seat and the uniqueness constraint
            # correctly rejects it. Flushing after each individual step
            # removes any ambiguity about ordering.
            original_sa1_seat = sa1.seat_num
            sa1.seat_num = -1  # placeholder, guaranteed not to collide
            db.session.flush()
            candidate.seat_num = original_sa1_seat  # move candidate into sa1's old (now free) seat
            db.session.flush()
            sa1.seat_num = cand_seat  # move sa1 into candidate's old (now free) seat
            db.session.flush()
            fixes["couples_separated"] += 1
            swapped = True
            break

        if not swapped:
            # No swap partner found on same table -- try bumping sa1 by 2 seats
            # if that seat is empty (no assignment there yet)
            occupied_seats = {sa.seat_num for sa in table_sas}
            event = Event.query.get(event_id)
            table_cfg = next((t for t in (event.table_config or {}).get("tables", [])
                              if t["id"] == sa1.table_num), None)
            table_size = table_cfg["size"] if table_cfg else 12
            for delta in [2, -2, 3, -3]:
                new_seat = partner_seat + delta
                if 1 <= new_seat <= table_size and new_seat not in occupied_seats:
                    sa1.seat_num = new_seat
                    occupied_seats.add(new_seat)
                    fixes["couples_separated"] += 1
                    break

    db.session.flush()

    # -- 2. Spread officers ----------------------------------------------------
    # Reload after couple fixes
    all_sa = SeatAssignment.query.filter_by(event_id=event_id).all()
    pid_to_sa = {sa.person_id: sa for sa in all_sa if sa.person_id}
    tables = {}
    for sa in all_sa:
        tables.setdefault(sa.table_num, []).append(sa)

    num_tables = len(tables)
    if num_tables < 2:
        db.session.commit()
        return fixes

    def officer_count(tnum):
        return sum(1 for sa in tables.get(tnum, [])
                   if sa.person_id and Person.query.get(sa.person_id) and
                   Person.query.get(sa.person_id).is_officer)

    # Find tables with 2+ officers and tables with 0 officers
    max_passes = 10
    for _ in range(max_passes):
        overcrowded = [t for t in tables if officer_count(t) > 1]
        empty_tables = [t for t in tables if officer_count(t) == 0]
        if not overcrowded or not empty_tables:
            break

        tnum_from = overcrowded[0]
        tnum_to   = empty_tables[0]

        # Pick an unlocked officer from tnum_from to move to tnum_to --
        # but never one whose partner is ALSO seated at this same table.
        # This logic only knows about balancing officer counts across
        # tables; it has no awareness of the "couples stay together" rule
        # on its own, and moving an officer without checking that first
        # would silently split them from their partner, who'd be left
        # behind at the old table.
        def _has_partner_here(sa):
            person = Person.query.get(sa.person_id)
            if not person or not person.partner_id:
                return False
            partner_sa = pid_to_sa.get(person.partner_id)
            return partner_sa is not None and partner_sa.table_num == tnum_from

        officer_sa = next(
            (sa for sa in tables[tnum_from]
             if not sa.is_locked and sa.person_id and
             Person.query.get(sa.person_id) and
             Person.query.get(sa.person_id).is_officer and
             not _has_partner_here(sa)),
            None
        )
        if not officer_sa:
            break

        # Find a non-officer, non-locked person on tnum_to to swap with --
        # same protection as officer_sa above: never pick someone whose own
        # partner is also seated at tnum_to, or swapping them out would
        # split THAT couple instead.
        def _target_has_partner_here(sa):
            person = Person.query.get(sa.person_id)
            if not person or not person.partner_id:
                return False
            partner_sa = pid_to_sa.get(person.partner_id)
            return partner_sa is not None and partner_sa.table_num == tnum_to

        swap_target = next(
            (sa for sa in tables[tnum_to]
             if not sa.is_locked and sa.person_id and
             not Person.query.get(sa.person_id).is_officer and
             not _target_has_partner_here(sa)),
            None
        )
        if not swap_target:
            # No swap partner -- just reassign table number
            old_tnum = officer_sa.table_num
            # Find a free seat on tnum_to
            occupied_to = {sa.seat_num for sa in tables[tnum_to]}
            event = Event.query.get(event_id)
            table_cfg = next((t for t in (event.table_config or {}).get("tables", [])
                              if t["id"] == tnum_to), None)
            table_size = table_cfg["size"] if table_cfg else 12
            for snum in range(1, table_size + 1):
                if snum not in occupied_to:
                    officer_sa.table_num = tnum_to
                    officer_sa.seat_num = snum
                    fixes["officers_spread"] += 1
                    break
        else:
            # Swap table assignments via a temporary placeholder -- a bare
            # Python tuple-swap here would issue sequential SQL UPDATEs that
            # can transiently collide with the (event_id, table_num,
            # seat_num) uniqueness constraint, the same issue already fixed
            # for the couples-separation swap earlier in this function.
            # Same fix here: flush after EACH individual change rather than
            # changing both rows in memory and letting SQLAlchemy pick the
            # order it sends the two UPDATEs in.
            orig_table, orig_seat = officer_sa.table_num, officer_sa.seat_num
            target_table, target_seat = swap_target.table_num, swap_target.seat_num
            officer_sa.table_num, officer_sa.seat_num = -1, -1  # placeholder, guaranteed not to collide
            db.session.flush()
            swap_target.table_num, swap_target.seat_num = orig_table, orig_seat  # into officer_sa's old (now free) seat
            db.session.flush()
            officer_sa.table_num, officer_sa.seat_num = target_table, target_seat  # into swap_target's old (now free) seat
            db.session.flush()
            fixes["officers_spread"] += 1

        # Refresh table map
        db.session.flush()
        all_sa = SeatAssignment.query.filter_by(event_id=event_id).all()
        tables = {}
        for sa in all_sa:
            tables.setdefault(sa.table_num, []).append(sa)

    db.session.commit()
    return fixes
