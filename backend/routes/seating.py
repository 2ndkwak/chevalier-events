from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, jsonify, current_app)
from flask_login import login_required, current_user
from ..models import db, Event, RSVP, RSVPGuest, Person, SeatAssignment, SeatingRule, WineTag, EventAllergyOff, MenuItem, EventCourse
from ..routes.admin import admin_required
from datetime import datetime
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

    seating_accepted, seating_accepted_stale_because = event.seating_is_accepted()

    return render_template("admin/seating/home.html",
                           event=event,
                           global_rules=global_rules,
                           per_event_rules=per_event_rules,
                           attendees=attendees,
                           couples=couples,
                           couple_name_map=couple_name_map,
                           assignments=assignments,
                           seating_accepted=seating_accepted,
                           seating_accepted_stale_because=seating_accepted_stale_because)


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
    event_id = request.form.get("event_id", type=int)

    # These two are fused into a single seating "party" before the AI ever
    # runs, not checked afterward like the other three -- there's no
    # meaningful way to turn them off, so reject a toggle attempt here too,
    # not just hide the button in the template.
    if rule.rule_type in ("couples_same_table", "guests_with_host"):
        flash(f"'{rule.description}' is structural and can't be disabled -- "
              f"it's fused into how seating is built before anything else runs.", "warning")
        return redirect(url_for("seating.seating_home", event_id=event_id) + "#permanent-rules")

    rule.is_active = not rule.is_active
    db.session.commit()
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

        was_accepted, _ = event.seating_is_accepted()
        _assign_seats_from_party_tables(event_id, parties, party_table, locked)
        event.seating_updated_at = datetime.utcnow()
        db.session.commit()
        if was_accepted:
            flash("The seating plan was previously accepted -- Table Name Cards and "
                  "Charts & Lists may now be out of date. Check the Print screen "
                  "before reprinting anything you don't need to redo yet.", "warning")

        fixes = _enforce_rules(event_id, couples)
        msg = "Seating proposal generated successfully. Review and adjust as needed."
        details = []
        if fixes["couples_separated"] > 0:
            details.append(f"{fixes['couples_separated']} couple(s) moved apart from adjacent seats")
        if fixes["officers_spread"] > 0:
            details.append(f"{fixes['officers_spread']} officer(s) redistributed across tables")
        if fixes.get("not_together_fixed", 0) > 0:
            details.append(f"{fixes['not_together_fixed']} \"do not seat together\" conflict(s) resolved")
        if fixes.get("gender_improved", 0) > 0:
            details.append(f"{fixes['gender_improved']} seat(s) rearranged to alternate gender")
        if details:
            msg += " (Auto-fixed: " + "; ".join(details) + ".)"
        flash(msg, "success")

        if fixes.get("not_together_unresolved", 0) > 0:
            flash(f"{fixes['not_together_unresolved']} \"do not seat together\" pair(s) could not be "
                  f"separated automatically -- please check and adjust manually.", "warning")

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
    event = Event.query.get_or_404(event_id)
    was_accepted, _ = event.seating_is_accepted()
    keep_locked = request.form.get("keep_locked") == "1"
    q = SeatAssignment.query.filter_by(event_id=event_id)
    if keep_locked:
        q = q.filter_by(is_locked=False)
    q.delete()
    event.seating_updated_at = datetime.utcnow()
    db.session.commit()
    flash("Seating cleared." + (" Locked seats retained." if keep_locked else ""), "success")
    if was_accepted:
        flash("The seating plan was previously accepted -- Table Name Cards and "
              "Charts & Lists may now be out of date. Check the Print screen "
              "before reprinting anything you don't need to redo yet.", "warning")
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

    was_accepted, _ = event.seating_is_accepted()

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

    event.seating_updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "seating_was_accepted": was_accepted})


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


@seating_bp.route("/event/<int:event_id>/accept_seating", methods=["POST"])
@login_required
@admin_required
def accept_seating(event_id):
    """The one deliberately non-automatic milestone -- confirms the GS has
    reviewed the current seating chart and is satisfied with it. Resets
    automatically (via Event.seating_is_accepted()) the moment the chart
    is regenerated, cleared, or manually re-saved afterward."""
    event = Event.query.get_or_404(event_id)
    event.seating_accepted_at = datetime.utcnow()
    db.session.commit()
    flash("Final seating plan accepted.", "success")
    return redirect(url_for("seating.seating_home", event_id=event_id))


@seating_bp.route("/event/<int:event_id>/mark_charts_printed", methods=["POST"])
@login_required
@admin_required
def mark_charts_printed(event_id):
    """Pinged by the print button's own click handler, only when one of the
    four bundled charts/lists tabs (visual, by table, alphabetical,
    table+allergies) is the one being printed -- the browser's own print
    dialog after that click is invisible to the server, so the click
    itself is the signal used here.

    Expects a JSON body {"which": "visual" | "by-table" | "alpha" |
    "by-table-allergy"} identifying which of the four tabs triggered the
    print. Sets that item's own printed_at, and also refreshes the
    original aggregate charts_generated_at so the existing Dashboard/
    Events-page "Charts & Lists" dot keeps working unchanged for now.
    A missing/unrecognized "which" is tolerated (aggregate-only update)
    rather than erroring, so an old cached page or an unexpected client
    doesn't break the print button."""
    event = Event.query.get_or_404(event_id)
    now = datetime.utcnow()

    which_field = {
        "visual":           "charts_visual_printed_at",
        "by-table":         "charts_by_table_printed_at",
        "alpha":            "charts_alpha_printed_at",
        "by-table-allergy": "charts_by_table_allergy_printed_at",
    }
    which = (request.get_json(silent=True) or {}).get("which")
    field_name = which_field.get(which)
    if field_name:
        setattr(event, field_name, now)

    event.charts_generated_at = now
    db.session.commit()
    return jsonify({"ok": True})


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

    booklet_current, booklet_stale_because = event.booklet_is_current()
    table_cards_current, table_cards_stale_because = event.table_cards_is_current()
    charts_current, charts_stale_because = event.charts_is_current()
    wine_tags_current, wine_tags_stale_because = event.wine_tags_is_current()
    name_badges_current, name_badges_stale_because = event.name_badges_is_current()
    charts_visual_current, charts_visual_stale_because = event.charts_visual_is_current()
    charts_by_table_current, charts_by_table_stale_because = event.charts_by_table_is_current()
    charts_alpha_current, charts_alpha_stale_because = event.charts_alpha_is_current()
    charts_by_table_allergy_current, charts_by_table_allergy_stale_because = event.charts_by_table_allergy_is_current()

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
        booklet_current = booklet_current,
        booklet_stale_because = booklet_stale_because,
        table_cards_current = table_cards_current,
        table_cards_stale_because = table_cards_stale_because,
        charts_current = charts_current,
        charts_stale_because = charts_stale_because,
        wine_tags_current = wine_tags_current,
        wine_tags_stale_because = wine_tags_stale_because,
        name_badges_current = name_badges_current,
        name_badges_stale_because = name_badges_stale_because,
        charts_visual_current = charts_visual_current,
        charts_visual_stale_because = charts_visual_stale_because,
        charts_by_table_current = charts_by_table_current,
        charts_by_table_stale_because = charts_by_table_stale_because,
        charts_alpha_current = charts_alpha_current,
        charts_alpha_stale_because = charts_alpha_stale_because,
        charts_by_table_allergy_current = charts_by_table_allergy_current,
        charts_by_table_allergy_stale_because = charts_by_table_allergy_stale_because,
    )


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

    event.table_cards_generated_at = datetime.utcnow()
    db.session.commit()

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

    event.name_badges_generated_at = datetime.utcnow()
    db.session.commit()

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
                if isinstance(raw, bytes):
                    try:
                        content = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        content = raw.decode("cp1252")
                else:
                    content = raw
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
                        # Only "red" ever gets special treatment in the booklet (burgundy
                        # text); everything else -- blank, "white", "rose", a typo, whatever --
                        # is rendered identically as the default. So there's nothing to
                        # validate here beyond normalizing it; rejecting anything that isn't
                        # exactly "red"/"white" would only block legitimate rare colors
                        # (rose, sparkling, etc.) for no functional benefit.
                        color = row.get("color", "").strip().lower() or None
                        new_wines.append({
                            "position": position,
                            "course": course,
                            "vintage": row.get("vintage", "") or None,
                            "domain": row["domain"],
                            "appellation": row["appellation"],
                            "color": color,
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
                        event.wine_list_updated_at = datetime.utcnow()
                        db.session.commit()
                        flash(f"Wine list saved -- {len(new_wines)} wines.", "success")
                        return redirect(url_for("seating.wine_tags", event_id=event_id))

    wines = WineTag.query.filter_by(event_id=event_id).order_by(WineTag.course, WineTag.position).all()
    mismatches = _course_mismatch_warnings(event_id)
    return render_template("admin/seating/winetags.html",
                           event=event, wines=wines, errors=errors, mismatches=mismatches)


@seating_bp.route("/event/<int:event_id>/winetags/template")
@login_required
@admin_required
def wine_tags_template(event_id):
    """Download a blank CSV template for the wine list."""
    import csv, io
    from flask import Response

    columns = ["position", "course", "vintage", "domain", "appellation", "color"]
    example_rows = [
        {"position": "1", "course": "0", "vintage": "2019", "domain": "Domaine Leflaive",
         "appellation": "Puligny-Montrachet, Vieilles Vignes, Premier Cru", "color": "white"},
        {"position": "2", "course": "0", "vintage": "2018", "domain": "Domaine de la Romanee-Conti",
         "appellation": "Echezeaux Grand Cru, Vieilles Vignes", "color": "red"},
        {"position": "1", "course": "1", "vintage": "2020", "domain": "Chateau Margaux",
         "appellation": "Margaux Grand Cru Classe", "color": "red"},
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
    all_wines = WineTag.query.filter_by(event_id=event_id).order_by(WineTag.course, WineTag.position).all()
    if not all_wines:
        flash("No wine list uploaded for this event yet.", "error")
        return redirect(url_for("seating.wine_tags", event_id=event_id))

    # Course 0 = Cocktails, by convention -- no physical tag is printed for
    # those wines (they're served before guests are seated, so a die-cut
    # tag for them is wasted stock). They still appear normally in the
    # wine list and the menu booklet's wine panel; this exclusion is
    # scoped to the printed tags only.
    wines = [w for w in all_wines if w.course != 0]
    if not wines:
        flash("Only Cocktails-course wines are on the list for this event -- "
              "no tags are printed for those. Add wines for a real course to print tags.", "error")
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

    event.wine_tags_generated_at = datetime.utcnow()
    db.session.commit()

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
                if isinstance(raw, bytes):
                    try:
                        content_str = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        content_str = raw.decode("cp1252")
                else:
                    content_str = raw
                reader = csv.DictReader(io.StringIO(content_str))
            except Exception as e:
                errors.append(f"Could not read file: {e}")
                reader = None

            if reader is not None:
                required = {"course"}
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

                        # Optional -- only meaningful for course 0 (Cocktails), the
                        # one course that can hold more than one row (e.g. several
                        # hors d'oeuvres). Defaults to 1; not required to be unique,
                        # it's just a display-order hint. Every other course still
                        # gets exactly one row, same as always.
                        position = 1
                        if row.get("position"):
                            try:
                                position = int(row["position"])
                            except ValueError:
                                errors.append(f"Row {i}: position must be a number, got '{row['position']}'")
                                continue

                        # dish_french is optional -- a row can carry just a course
                        # number and a label (e.g. course 0 = "Cocktails", which
                        # has wines but no matching dish) with no dish text at
                        # all. Course numbering convention: 0 = Cocktails,
                        # 1 = Premier Assiette, 2 = Deuxieme, 3 = Troisieme,
                        # 4 = Fromages -- matches the French ordinals exactly,
                        # so course number and "which course" never drift
                        # apart. The CSV template pre-fills "Leave Blank" as a
                        # hint for the Cocktails row -- treat it the same as a
                        # genuinely empty cell in case it's left in place
                        # rather than deleted.
                        def _blank_or(value):
                            v = (value or "").strip()
                            return None if not v or v.lower() == "leave blank" else v

                        new_items.append({
                            "course": course,
                            "position": position,
                            "dish_french": _blank_or(row.get("dish_french")),
                            "dish_english": _blank_or(row.get("dish_english")),
                            "label": row.get("label", "").strip() or None,
                        })

                    if not errors:
                        if not new_items:
                            errors.append("No menu rows found in the file.")
                        else:
                            # Course 0 (Cocktails) is the one exception allowed to
                            # repeat -- everything else still needs exactly one row.
                            seen = set()
                            dupes = set()
                            for m in new_items:
                                if m["course"] == 0:
                                    continue
                                if m["course"] in seen:
                                    dupes.add(m["course"])
                                else:
                                    seen.add(m["course"])
                            if dupes:
                                for course in sorted(dupes):
                                    errors.append(
                                        f"Course {course} appears more than once -- only one dish "
                                        f"per course is supported (course 0 is the only exception)."
                                    )

                    if not errors and new_items:
                        MenuItem.query.filter_by(event_id=event_id).delete()
                        course_labels = {}
                        for m in new_items:
                            label = m.pop("label", None)
                            if label:
                                course_labels[m["course"]] = label
                            db.session.add(MenuItem(event_id=event_id, **m))

                        # Upsert course labels -- only for courses that actually had a
                        # non-blank label in this upload, so a re-upload without the
                        # label column never wipes out labels set previously.
                        for course_num, label in course_labels.items():
                            existing_label = EventCourse.query.filter_by(
                                event_id=event_id, course=course_num).first()
                            if existing_label:
                                existing_label.label = label
                            else:
                                db.session.add(EventCourse(event_id=event_id,
                                                           course=course_num, label=label))

                        event.menu_updated_at = datetime.utcnow()
                        db.session.commit()
                        flash(f"Menu saved -- {len(new_items)} course(s).", "success")
                        return redirect(url_for("seating.menu_items", event_id=event_id))

    items = MenuItem.query.filter_by(event_id=event_id).order_by(MenuItem.course, MenuItem.position, MenuItem.id).all()
    mismatches = _course_mismatch_warnings(event_id)
    return render_template("admin/seating/menu.html",
                           event=event, items=items, errors=errors, mismatches=mismatches)


@seating_bp.route("/event/<int:event_id>/menu/template")
@login_required
@admin_required
def menu_items_template(event_id):
    """Download a blank CSV template for the menu."""
    import csv, io
    from flask import Response

    columns = ["course", "position", "dish_french", "dish_english", "label"]
    example_rows = [
        # Course 0 (Cocktails) is the one course that can have more than one
        # row -- position tells the app what order to print them in. Every
        # other course still gets exactly one row; position can just stay 1
        # (or be left blank) for those.
        {"course": "0", "position": "1", "dish_french": "Gougeres au fromage",
         "dish_english": "Cheese gougeres", "label": "Transmis Hors d'Oeuvres"},
        {"course": "0", "position": "2", "dish_french": "Tartare de saumon fume",
         "dish_english": "Smoked salmon tartare", "label": ""},
        {"course": "1", "position": "1", "dish_french": "Seriole, citron Meyer, fenouil, emulsion d'olives Castelvetrano, capres",
         "dish_english": "Yellowtail, Meyer lemon, fennel, Castelvetrano olive emulsion, capers", "label": "Premier Assiette"},
        {"course": "2", "position": "1", "dish_french": "Canard, champignons sauvages, fregola sarda, asperges, peche, glace au jus de canard",
         "dish_english": "Duck, wild mushrooms, fregola sarda, asparagus, peach, duck glace", "label": "Deuxieme Assiette"},
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


# --- OFFICER RANKING (for the menu booklet) -----------------------------------

@seating_bp.route("/event/<int:event_id>/officers", methods=["GET", "POST"])
@login_required
@admin_required
def officer_ranking(event_id):
    """Assign print order to this event's officer list -- combines confirmed
    member officers (title comes from their permanent Person.officer_role)
    with any guest marked as a visiting officer on their RSVP entry."""
    event = Event.query.get_or_404(event_id)

    if request.method == "POST":
        for key, val in request.form.items():
            val = val.strip()
            rank = int(val) if val else None
            if key.startswith("rank_rsvp_"):
                rid = int(key[len("rank_rsvp_"):])
                r = RSVP.query.get(rid)
                if r:
                    r.officer_rank = rank
            elif key.startswith("rank_guest_"):
                gid = int(key[len("rank_guest_"):])
                g = RSVPGuest.query.get(gid)
                if g:
                    g.officer_rank = rank
        event.officer_ranking_updated_at = datetime.utcnow()
        db.session.commit()
        flash("Officer ranking saved.", "success")
        return redirect(url_for("seating.officer_ranking", event_id=event_id))

    member_officers = (RSVP.query.join(Person, RSVP.person_id == Person.id)
                        .filter(RSVP.event_id == event_id,
                                RSVP.status == "confirmed",
                                Person.is_officer == True)
                        .all())
    guest_officers = (RSVPGuest.query.join(RSVP, RSVPGuest.rsvp_id == RSVP.id)
                       .filter(RSVP.event_id == event_id,
                               RSVP.status == "confirmed",
                               RSVPGuest.is_officer == True)
                       .all())

    combined = []
    for r in member_officers:
        combined.append({"kind": "rsvp", "id": r.id, "name": r.person.display_name,
                         "title": r.person.officer_role or "Officer", "rank": r.officer_rank})
    for g in guest_officers:
        combined.append({"kind": "guest", "id": g.id, "name": g.display_name,
                         "title": g.officer_title or "Officer", "rank": g.officer_rank})
    combined.sort(key=lambda x: (x["rank"] if x["rank"] is not None else 9999, x["name"]))

    return render_template("admin/seating/officers.html", event=event, officers=combined)


# --- MENU BOOKLET --------------------------------------------------------------

def _booklet_title_for(person):
    """The title text (if any) that prints in front of this person's name
    on the booklet -- red, per the confirmed color convention. Single
    source of truth used both for a person's own line and for how they
    print when shown as someone else's partner, so the two never disagree.
    Both partner-Chevalier types print as plain "Chevalier" -- the booklet
    doesn't distinguish member vs. non-member Chevaliers."""
    title_map = {
        "member": "Chevalier",
        "partner_member_chevalier": "Chevalier",
        "partner_non_member_chevalier": "Chevalier",
        "honoraire": None,
        "aspirant": "Aspirant",
        "partner": None,
    }
    title = title_map.get(person.person_type)
    # Honoraires use their personal title (e.g. "Chef") instead of the
    # word "Honoraire" itself, same whether shown on their own line or as
    # someone else's partner.
    if person.person_type == "honoraire" and person.title:
        title = person.title
    return title


def _is_independent_member(person):
    """Whether this person holds membership standing in their own right --
    Chevalier (any of the three member/partner-Chevalier types) or
    Aspirant -- as opposed to a plain non-member partner or an Honoraire
    (Honoraire is deliberately excluded here: their personal title, if
    any, is a courtesy title, not membership standing, and doesn't
    participate in the couple-ordering cascade below)."""
    return person.person_type in ("member", "partner_member_chevalier",
                                  "partner_non_member_chevalier", "aspirant")


def _format_person(person, officer_role=None):
    """Returns (title, honorific) for this person, always mutually
    exclusive: an officer role or independent title (Chevalier/Aspirant/
    Honoraire's personal title) always wins and suppresses Mme./M.
    entirely; only someone with no title of their own gets an honorific,
    by gender."""
    title = officer_role or _booklet_title_for(person)
    honorific = None
    if not title:
        if person.gender == "F":
            honorific = "Mme."
        elif person.gender == "M":
            honorific = "M."
    return title, honorific


def _choose_primary(p1, p1_officer_role, p2, p2_officer_role):
    """Which of a confirmed couple prints first, per the confirmed
    ordering cascade: whichever holds an officer rank for this event >
    whichever is independently a member (Chevalier or Aspirant) >
    whichever is male. Returns (primary, primary_role, secondary,
    secondary_role)."""
    p1_is_officer = p1_officer_role is not None
    p2_is_officer = p2_officer_role is not None
    if p1_is_officer != p2_is_officer:
        return (p1, p1_officer_role, p2, p2_officer_role) if p1_is_officer \
            else (p2, p2_officer_role, p1, p1_officer_role)

    p1_indep = _is_independent_member(p1)
    p2_indep = _is_independent_member(p2)
    if p1_indep != p2_indep:
        return (p1, p1_officer_role, p2, p2_officer_role) if p1_indep \
            else (p2, p2_officer_role, p1, p1_officer_role)

    if p1.gender == "M" and p2.gender != "M":
        return (p1, p1_officer_role, p2, p2_officer_role)
    if p2.gender == "M" and p1.gender != "M":
        return (p2, p2_officer_role, p1, p1_officer_role)
    return (p1, p1_officer_role, p2, p2_officer_role)


def _person_section_lines(people, confirmed_person_ids, paired_ids):
    """Builds attendee lines for one section (Chevaliers/Honoraire/Aspirants),
    pairing with a confirmed partner where applicable and skipping anyone
    already shown as someone else's partner. By the time this runs, the
    officer loop has already claimed every officer and their partner (see
    build_booklet_data), so nobody reaching this function is an officer --
    only the independent-member and male-first tiers of the cascade can
    ever apply here."""
    import os as _os, sys as _sys
    project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))
    if project_root not in _sys.path:
        _sys.path.insert(0, project_root)
    import gen_menu_booklet as _gmb

    lines = []
    for p in sorted(people, key=lambda x: ((x.last_name or "").lower(), (x.first_name or "").lower())):
        if p.id in paired_ids:
            continue
        partner = Person.query.get(p.partner_id) if p.partner_id else None
        if partner and partner.id in confirmed_person_ids:
            primary, _, secondary, _ = _choose_primary(p, None, partner, None)
            primary_title, primary_honorific = _format_person(primary)
            secondary_title, secondary_honorific = _format_person(secondary)
            lines.append(_gmb.attendee_line_markup(
                primary_title, primary.display_name,
                secondary_honorific, secondary_title, secondary.display_name,
                primary_honorific=primary_honorific))
            paired_ids.add(p.id)
            paired_ids.add(partner.id)
        else:
            title, honorific = _format_person(p)
            lines.append(_gmb.attendee_line_markup(title, p.display_name,
                                                    primary_honorific=honorific))
            paired_ids.add(p.id)
    return lines


def build_booklet_data(event):
    """Assembles the full data dict gen_menu_booklet.generate() needs,
    pulling from confirmed RSVPs, wine tags, menu items, course labels,
    and this event's officer ranking."""
    import os as _os, sys as _sys
    project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))
    if project_root not in _sys.path:
        _sys.path.insert(0, project_root)
    import gen_menu_booklet as _gmb

    confirmed_rsvps = RSVP.query.filter_by(event_id=event.id, status="confirmed").all()
    confirmed_person_ids = {r.person_id for r in confirmed_rsvps}
    paired_ids = set()

    # Every confirmed person's own officer role for THIS event, if any --
    # looked up once here so the officer-pairing loop below can check
    # whether a partner is independently an officer too (rare, but two
    # officers can be married to each other), not just whichever side
    # happened to drive the loop.
    officer_role_by_person = {r.person_id: (r.person.officer_role or "Officier")
                              for r in confirmed_rsvps if r.officer_rank is not None}

    # -- Officers: member officers (ranked) + guest officers (ranked) --
    officer_entries = []
    for r in confirmed_rsvps:
        p = r.person
        if p.is_officer and r.officer_rank is not None:
            officer_entries.append((r.officer_rank, "rsvp", r))
    for r in confirmed_rsvps:
        for g in r.guests:
            if g.is_officer and g.officer_rank is not None:
                officer_entries.append((g.officer_rank, "guest", g))
    officer_entries.sort(key=lambda e: e[0])

    officers = []
    for rank, kind, obj in officer_entries:
        if kind == "rsvp":
            p = obj.person
            if p.id in paired_ids:
                continue
            partner = Person.query.get(p.partner_id) if p.partner_id else None
            if partner and partner.id in confirmed_person_ids:
                p_role = officer_role_by_person.get(p.id)
                partner_role = officer_role_by_person.get(partner.id)
                primary, primary_role, secondary, secondary_role = _choose_primary(
                    p, p_role, partner, partner_role)
                primary_title, primary_honorific = _format_person(primary, primary_role)
                secondary_title, secondary_honorific = _format_person(secondary, secondary_role)
                officers.append(_gmb.attendee_line_markup(
                    primary_title, primary.display_name,
                    secondary_honorific, secondary_title, secondary.display_name,
                    primary_honorific=primary_honorific))
                paired_ids.add(partner.id)
            else:
                title, honorific = _format_person(p, officer_role_by_person.get(p.id))
                officers.append(_gmb.attendee_line_markup(title, p.display_name,
                                                           primary_honorific=honorific))
            paired_ids.add(p.id)
        else:
            officers.append(_gmb.attendee_line_markup(obj.officer_title or "Officier", obj.display_name))

    # -- Members / Honoraires / Aspirants (non-officers only) --
    # Les Chevaliers pool: our own members, plus any partner-variant type
    # (plain partner, partner_member_chevalier, partner_non_member_chevalier)
    # -- this is what lets a partner who attends WITHOUT their Cleveland-
    # member spouse still show up with the correct title (or no title, for
    # a plain partner), rather than only appearing when paired.
    chevalier_pool_types = ("member", "partner", "partner_member_chevalier", "partner_non_member_chevalier")
    members_people = [r.person for r in confirmed_rsvps
                      if r.person.person_type in chevalier_pool_types and not r.person.is_officer]
    honoraire_people = [r.person for r in confirmed_rsvps
                        if r.person.person_type == "honoraire" and not r.person.is_officer]
    aspirant_people = [r.person for r in confirmed_rsvps
                       if r.person.person_type == "aspirant" and not r.person.is_officer]

    members = _person_section_lines(members_people, confirmed_person_ids, paired_ids)
    honoraires = _person_section_lines(honoraire_people, confirmed_person_ids, paired_ids)
    aspirants = _person_section_lines(aspirant_people, confirmed_person_ids, paired_ids)

    # -- Guests, grouped by host, excluding those marked as officers (shown above instead) --
    guests_by_host = {}
    for r in confirmed_rsvps:
        for g in r.guests:
            if g.is_officer:
                continue
            guests_by_host.setdefault(r.person.display_name, []).append(g)

    guest_lines = []
    for host_name, guests in guests_by_host.items():
        names = []
        for g in guests:
            if g.gender == "F":
                names.append(f"Mme. {g.display_name}")
            elif g.gender == "M":
                names.append(f"M. {g.display_name}")
            else:
                names.append(g.display_name)
        label = "Guest of" if len(guests) == 1 else "Guests of"
        guest_lines.append(f"{label} {host_name}: {' et '.join(names)}")

    # -- Wines and menu, grouped by course, with course labels --
    course_labels = {c.course: c.label for c in event.courses}
    wines = WineTag.query.filter_by(event_id=event.id).order_by(WineTag.course, WineTag.position).all()
    wine_by_course = {}
    for w in wines:
        wine_by_course.setdefault(w.course, []).append(w)
    wine_courses = []
    for course_num in sorted(wine_by_course.keys()):
        label = course_labels.get(course_num, f"Course {course_num}")
        wine_list = []
        for w in wine_by_course[course_num]:
            text_parts = [p for p in [w.vintage, w.domain, f'"{w.appellation}"' if w.appellation else None] if p]
            wine_list.append({"text": " ".join(text_parts), "color": w.color})
        wine_courses.append({"course": course_num, "label": label, "wines": wine_list})

    menu_item_rows = MenuItem.query.filter_by(event_id=event.id) \
        .order_by(MenuItem.course, MenuItem.position, MenuItem.id).all()
    menu_by_course = []
    _course_entries = {}
    for m in menu_item_rows:
        entry = _course_entries.get(m.course)
        if entry is None:
            label = course_labels.get(m.course, f"Course {m.course}")
            entry = {"course": m.course, "label": label, "dishes": []}
            _course_entries[m.course] = entry
            menu_by_course.append(entry)
        if m.dish_french or m.dish_english:
            entry["dishes"].append({"dish_french": m.dish_french, "dish_english": m.dish_english})

    logo_path = _os.path.join(project_root, "frontend", "static", "img", "Chevalier_Logo.jpg")

    def _short_text(value, max_len=80):
        """Safety net for the cover page's unwrapped single-line text --
        strips embedded line breaks and caps length, so stale/oversized
        data in any of these fields can never blow out the layout the way
        a stray legacy value did here."""
        if not value:
            return value
        cleaned = " ".join(value.split())
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len - 1].rstrip() + "..."
        return cleaned

    return {
        "event_title": _short_text(event.title, 100),
        "event_date_str": _gmb.format_french_date(event.event_date),
        "venue_name": _short_text(event.venue_name),
        "chef_name": _short_text(event.chef_name),
        "hosts": _short_text(event.hosts),
        "logo_path": logo_path,
        "officers": officers,
        "members": members,
        "honoraires": honoraires,
        "aspirants": aspirants,
        "guest_lines": guest_lines,
        "wine_courses": wine_courses,
        "menu_by_course": menu_by_course,
    }


@seating_bp.route("/event/<int:event_id>/booklet")
@login_required
@admin_required
def generate_booklet(event_id):
    """Generate the printable menu booklet PDF for this event."""
    import tempfile, os as _os, sys as _sys
    from flask import send_file

    event = Event.query.get_or_404(event_id)
    font_choice = request.args.get("font", "gregorian")

    # If the wine list and menu's course numbers don't line up, require an
    # explicit confirmation click rather than silently generating a booklet
    # that's likely to have wines and dishes printed under the wrong
    # headings -- but never hard-block, since generating a partial booklet
    # mid-process (before every course's wine is in yet) is a normal,
    # intentional part of the workflow.
    mismatches = _course_mismatch_warnings(event_id)
    if mismatches and request.args.get("confirmed") != "1":
        flash("Course numbers don't line up between the wine list and menu -- "
              "review the warnings below and confirm before generating.", "warning")
        return redirect(url_for("seating.menu_items", event_id=event_id, font=font_choice))

    try:
        data = build_booklet_data(event)
    except Exception as e:
        flash(f"Could not gather booklet data: {e}", "error")
        return redirect(url_for("seating.menu_items", event_id=event_id))

    project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))
    if project_root not in _sys.path:
        _sys.path.insert(0, project_root)

    font_path = None
    if font_choice == "gregorian":
        font_path = _os.path.join(project_root, "frontend", "static", "fonts", "GregorianFLF.ttf")

    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    out.close()

    try:
        import importlib
        import gen_menu_booklet as _gmb
        importlib.reload(_gmb)
        _gmb.generate(data, font_path, out.name)
    except Exception as e:
        flash(f"Booklet generation failed: {e}", "error")
        return redirect(url_for("seating.menu_items", event_id=event_id))

    event.booklet_generated_at = datetime.utcnow()
    db.session.commit()

    safe_title = "".join(c for c in event.title if c.isalnum() or c in " -_")
    filename = f"MenuBooklet_{safe_title}.pdf"
    return send_file(out.name, as_attachment=True, download_name=filename,
                     mimetype="application/pdf")


# --- HELPERS ------------------------------------------------------------------

def _course_mismatch_warnings(event_id):
    """
    Compares the wine list's course numbers against the menu's course
    numbers for an event and returns a list of specific, human-readable
    warnings for anything that doesn't line up -- e.g. a course with
    wine(s) uploaded but no matching dish, or vice versa. This is what
    catches the single most common real-world mistake with the booklet:
    a course-numbering mismatch between the two lists.

    Returns an empty list whenever there's nothing meaningful to compare
    yet (either list is still empty) or everything lines up correctly.
    Never raises -- this is advisory, not a hard gate.
    """
    wines = WineTag.query.filter_by(event_id=event_id).all()
    items = MenuItem.query.filter_by(event_id=event_id).all()
    if not wines or not items:
        return []

    wine_courses = {w.course for w in wines}
    menu_courses = {m.course for m in items}

    warnings = []
    # Wine with no matching dish -- flagged for every course, including 0
    # (Cocktails): wine is usually but not always paired with hors d'oeuvres
    # there, so a GS should still see it and decide for themselves.
    for c in sorted(wine_courses - menu_courses):
        warnings.append(f"Course {c} has wine(s) uploaded but no matching dish in the menu.")
    # Dish with no wine -- no exemption for course 0 here; a dish entered
    # under Cocktails with no wine to go with it is unusual and worth
    # flagging just the same as any other course.
    for c in sorted(menu_courses - wine_courses):
        warnings.append(f"Course {c} has a dish in the menu but no wine uploaded.")
    return warnings


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
                    # Deterministic ordering: always the alphabetically-first
                    # last name leads the label, regardless of which of the
                    # two happened to come first in this unordered query --
                    # otherwise the same couple could sort under either
                    # partner's last name from one page load to the next.
                    first, second = (
                        (p, partner)
                        if (p.last_name or "").lower() <= (partner.last_name or "").lower()
                        else (partner, p)
                    )
                    couples.append({
                        "id":           first.id,
                        "name":         first.display_name,
                        "partner_id":   second.id,
                        "partner_name": second.display_name,
                        "couple_label": f"{first.last_name} / {second.last_name}"
                                        if first.last_name != second.last_name
                                        else f"{first.display_name} & {second.display_name}",
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

    priorities = [
        "Every locked party MUST stay at its locked table",
        "No table may exceed its seat count (sum of party sizes at that table)",
    ]
    if _rule_active("officer_per_table"):
        priorities.append("Spread officers as evenly as possible across tables")
    priorities.append("Avoid recreating recent table groupings where possible")
    priorities_desc = "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(priorities))

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
{priorities_desc}
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


def _rule_active(rule_type, default=True):
    """Looks up whether a permanent seating rule is currently enabled, by
    its stable internal type name (e.g. "officer_per_table") rather than
    its editable description text. Defaults to True (the rule behaves as
    if it's on) if the rule record is somehow missing entirely, since
    that's always been the app's normal state."""
    rule = SeatingRule.query.filter_by(rule_type=rule_type).first()
    return rule.is_active if rule else default


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
    Post-generation enforcement pass. Runs after the AI proposal is saved
    and fixes, in this order:
      1. Officers bunched at one table (more than 1 officer per table
         when spreading is possible)
      2. "Do not seat together" pairs who ended up at the same table
      3. Couple adjacency and gender alternation, jointly -- run LAST
         since neither step above is aware of seat-level adjacency or
         gender at all, and could otherwise silently undo this step's
         work if it ran earlier.
    Returns a dict with counts of fixes applied.
    """
    fixes = {"officers_spread": 0, "not_together_fixed": 0,
             "not_together_unresolved": 0, "couples_separated": 0,
             "gender_improved": 0}

    # -- Load current assignments ----------------------------------------------
    all_sa = SeatAssignment.query.filter_by(event_id=event_id).all()
    if not all_sa:
        return fixes

    tables: dict[int, list] = {}
    for sa in all_sa:
        tables.setdefault(sa.table_num, []).append(sa)
    pid_to_sa: dict[int, object] = {sa.person_id: sa for sa in all_sa if sa.person_id}

    # -- 1. Spread officers ------------------------------------------------------
    num_tables = len(tables)
    if _rule_active("officer_per_table") and num_tables >= 2:
        def officer_count(tnum):
            return sum(1 for sa in tables.get(tnum, [])
                       if sa.person_id and Person.query.get(sa.person_id) and
                       Person.query.get(sa.person_id).is_officer)

        max_passes = 10
        for _ in range(max_passes):
            overcrowded = [t for t in tables if officer_count(t) > 1]
            empty_tables = [t for t in tables if officer_count(t) == 0]
            if not overcrowded or not empty_tables:
                break

            tnum_from = overcrowded[0]
            tnum_to   = empty_tables[0]

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
                old_tnum = officer_sa.table_num
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
                orig_table, orig_seat = officer_sa.table_num, officer_sa.seat_num
                target_table, target_seat = swap_target.table_num, swap_target.seat_num
                officer_sa.table_num, officer_sa.seat_num = -1, -1
                db.session.flush()
                swap_target.table_num, swap_target.seat_num = orig_table, orig_seat
                db.session.flush()
                officer_sa.table_num, officer_sa.seat_num = target_table, target_seat
                db.session.flush()
                fixes["officers_spread"] += 1

            db.session.flush()
            all_sa = SeatAssignment.query.filter_by(event_id=event_id).all()
            tables = {}
            for sa in all_sa:
                tables.setdefault(sa.table_num, []).append(sa)

    # -- 2. Fix "do not seat together" violations --------------------------------
    all_sa = SeatAssignment.query.filter_by(event_id=event_id).all()
    pid_to_sa = {sa.person_id: sa for sa in all_sa if sa.person_id}
    tables = {}
    for sa in all_sa:
        tables.setdefault(sa.table_num, []).append(sa)

    event = Event.query.get(event_id)
    not_together_pairs = (event.seating_rules or {}).get("not_together") or []
    table_sizes = {t["id"]: t["size"] for t in (event.table_config or {}).get("tables", [])}

    def _seated_with_partner(sa):
        person = Person.query.get(sa.person_id)
        if not person or not person.partner_id:
            return False
        partner_sa = pid_to_sa.get(person.partner_id)
        return partner_sa is not None and partner_sa.table_num == sa.table_num

    for a_id, b_id in not_together_pairs:
        sa_a = pid_to_sa.get(a_id)
        sa_b = pid_to_sa.get(b_id)
        if not sa_a or not sa_b:
            continue
        if sa_a.table_num != sa_b.table_num:
            continue

        mover = None
        if not sa_a.is_locked and not _seated_with_partner(sa_a):
            mover = sa_a
        elif not sa_b.is_locked and not _seated_with_partner(sa_b):
            mover = sa_b

        if mover is None:
            fixes["not_together_unresolved"] += 1
            continue

        current_table = mover.table_num
        moved = False
        for tnum, size in table_sizes.items():
            if tnum == current_table:
                continue
            occupied = {sa.seat_num for sa in tables.get(tnum, [])}
            if len(occupied) >= size:
                continue
            free_seat = next((s for s in range(1, size + 1) if s not in occupied), None)
            if free_seat is None:
                continue
            if mover in tables.get(current_table, []):
                tables[current_table].remove(mover)
            mover.table_num = tnum
            mover.seat_num = free_seat
            tables.setdefault(tnum, []).append(mover)
            fixes["not_together_fixed"] += 1
            moved = True
            break

        if not moved:
            fixes["not_together_unresolved"] += 1

    db.session.flush()

    # -- 3. Jointly optimize couple-adjacency and gender alternation ------------
    # Runs last, after officer-spread and the not-together fix, so it has the
    # final word and can clean up any adjacency/gender fallout either of those
    # leaves behind (neither is aware of seat-level adjacency at all). For
    # each table, repeatedly looks for the single best swap between two
    # non-locked occupants -- automating the same thing done by eye on a
    # finished table: scan it, try a move, see if it helps. Couple-adjacency
    # always takes priority -- a swap is only ever accepted if it doesn't
    # increase the number of adjacent couples, even when it would improve
    # gender alternation. Only ever swaps seats within the same table; never
    # relocates anyone to a different table.
    #
    # IMPORTANT: gender is looked up once, up front, into a plain dict --
    # never inside the trial-swap loop below. Evaluating a trial swap
    # temporarily gives two seat assignments the same (table, seat) in
    # memory before it's reverted; any ORM query during that window (e.g.
    # Person.query.get(), as this used to call via _lookup_gender) triggers
    # SQLAlchemy's autoflush and tries to persist that momentarily-invalid
    # state, which the database's uniqueness constraint correctly rejects.
    # Precomputing genders means the trial loop never touches the database
    # at all, so this can't happen.
    all_sa = SeatAssignment.query.filter_by(event_id=event_id).all()
    tables = {}
    for sa in all_sa:
        tables.setdefault(sa.table_num, []).append(sa)

    gender_lookup = {}
    for sa in all_sa:
        if sa.person_id is not None:
            gender_lookup[("person", sa.person_id)] = _lookup_gender("person", sa.person_id)
        elif sa.guest_id is not None:
            gender_lookup[("guest", sa.guest_id)] = _lookup_gender("guest", sa.guest_id)

    # Both checked once, up front, rather than inside the hot loop below --
    # when a rule is disabled, this pass simply stops treating it as a
    # violation to fix at all, rather than forcing it regardless.
    couples_adjacent_active = _rule_active("couples_non_adjacent")
    gender_active = _rule_active("alternate_genders")

    # Tables are always round (Table Planner only ever builds round tables),
    # so seat 1 and the last seat are physical neighbors too, not just
    # consecutively-numbered seats -- this wraparound pair was previously
    # missed everywhere adjacency was checked in this file.
    def _table_violation_counts(table_sas, table_size):
        def _is_adjacent(s1, s2):
            if abs(s1 - s2) == 1:
                return True
            return {s1, s2} == {1, table_size}

        couple_viol = 0
        if couples_adjacent_active:
            seat_of_pid = {sa.person_id: sa.seat_num for sa in table_sas if sa.person_id}
            for c in couples_data:
                s1 = seat_of_pid.get(c["id"])
                s2 = seat_of_pid.get(c["partner_id"])
                if s1 is not None and s2 is not None and _is_adjacent(s1, s2):
                    couple_viol += 1

        gender_viol = 0
        if gender_active:
            gender_by_seat = {}
            for sa in table_sas:
                key = ("person", sa.person_id) if sa.person_id is not None else ("guest", sa.guest_id)
                g = gender_lookup.get(key, "")
                if g:
                    gender_by_seat[sa.seat_num] = g
            for seat in range(1, table_size + 1):
                neighbor = 1 if seat == table_size else seat + 1
                g1 = gender_by_seat.get(seat)
                g2 = gender_by_seat.get(neighbor)
                if g1 and g2 and g1 == g2:
                    gender_viol += 1
        return couple_viol, gender_viol

    before_couple_total = before_gender_total = 0
    for tnum, table_sas in tables.items():
        c, g = _table_violation_counts(table_sas, table_sizes.get(tnum, 8))
        before_couple_total += c
        before_gender_total += g

    for tnum, table_sas in tables.items():
        size = table_sizes.get(tnum, 8)
        for _ in range(20):
            current_couple, current_gender = _table_violation_counts(table_sas, size)
            if current_couple == 0 and current_gender == 0:
                break

            movable = [sa for sa in table_sas if not sa.is_locked]
            best = None
            for i in range(len(movable)):
                for j in range(i + 1, len(movable)):
                    sa_x, sa_y = movable[i], movable[j]
                    sa_x.seat_num, sa_y.seat_num = sa_y.seat_num, sa_x.seat_num
                    trial_couple, trial_gender = _table_violation_counts(table_sas, size)
                    sa_x.seat_num, sa_y.seat_num = sa_y.seat_num, sa_x.seat_num  # revert (in-memory only, no DB access above)
                    if trial_couple > current_couple:
                        continue  # never accept a swap that increases couple adjacency
                    if best is None or (trial_couple, trial_gender) < (best[0], best[1]):
                        best = (trial_couple, trial_gender, sa_x, sa_y)

            if best is None or (best[0], best[1]) >= (current_couple, current_gender):
                break  # no improving swap found for this table

            _, _, sa_x, sa_y = best
            orig_x, orig_y = sa_x.seat_num, sa_y.seat_num
            with db.session.no_autoflush:
                sa_x.seat_num = -1  # placeholder, guaranteed not to collide
                db.session.flush()
                sa_y.seat_num = orig_x
                db.session.flush()
                sa_x.seat_num = orig_y
                db.session.flush()

    after_couple_total = after_gender_total = 0
    for tnum, table_sas in tables.items():
        c, g = _table_violation_counts(table_sas, table_sizes.get(tnum, 8))
        after_couple_total += c
        after_gender_total += g

    fixes["couples_separated"] = max(0, before_couple_total - after_couple_total)
    fixes["gender_improved"] = max(0, before_gender_total - after_gender_total)

    db.session.commit()
    return fixes
