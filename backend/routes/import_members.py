import csv, io
from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash)
from flask_login import login_required
from ..models import db, Person
from ..routes.admin import admin_required
from datetime import date

import_bp = Blueprint("import_members", __name__)


@import_bp.route("/", methods=["GET", "POST"])
@login_required
@admin_required
def upload():
    preview = None
    errors  = []

    if request.method == "POST":
        action = request.form.get("action")
        file   = request.files.get("csv_file")

        # -- Preview ------------------------------------------------------
        if action == "preview" and file:
            content = file.read().decode("utf-8-sig")
            rows, errors = _parse_csv(io.StringIO(content))
            if not errors:
                preview = rows
            return render_template("admin/members/import.html",
                                   preview=preview, errors=errors,
                                   csv_raw=content if preview else "")

        # -- Confirm import -----------------------------------------------
        elif action == "import":
            raw = request.form.get("csv_data", "")
            rows, errors = _parse_csv(io.StringIO(raw))
            if not errors:
                added, skipped = _import_rows(rows)
                flash(f"Import complete -- {added} records added, {skipped} skipped (duplicate email).", "success")
                return redirect(url_for("members.list_members"))

    return render_template("admin/members/import.html",
                           preview=preview, errors=errors)


def _parse_csv(source):
    """Parse CSV from file upload or string. Returns (rows, errors)."""
    errors = []
    rows   = []

    try:
        if hasattr(source, "read"):
            raw = source.read()
            content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        else:
            content = source if isinstance(source, str) else source.read()
        reader = csv.DictReader(io.StringIO(content))
    except Exception as e:
        return [], [f"Could not read file: {e}"]

    required = {"first_name", "last_name"}
    if reader.fieldnames:
        missing = required - {f.strip().lower() for f in reader.fieldnames}
        if missing:
            return [], [f"Missing required columns: {', '.join(missing)}"]

    for i, row in enumerate(reader, start=2):
        # Normalise keys to lowercase stripped
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items()}

        if not row.get("first_name") and not row.get("last_name"):
            continue  # skip blank rows silently

        entry = {
            "person_type":    (row.get("person_type", "member") or "member").lower(),
            "title":          row.get("title", "") or None,
            "first_name":     row.get("first_name", ""),
            "last_name":      row.get("last_name", ""),
            "suffix":         row.get("suffix", "") or None,
            "gender":         row.get("gender", "") or None,
            "email":          row.get("email", "").lower() or None,
            "phone":          row.get("phone", "") or None,
            "is_officer":     row.get("is_officer", "").lower() in ("yes","true","1","y"),
            "officer_role":   row.get("officer_role", "") or None,
            "address_line1":  row.get("address_line1", "") or None,
            "address_line2":  row.get("address_line2", "") or None,
            "city":           row.get("city", "") or None,
            "province_state": row.get("province_state", "") or None,
            "postal_code":    row.get("postal_code", "") or None,
            "country":        row.get("country", "") or None,
            "notes":          row.get("notes", "") or None,
            "partner_email":  row.get("partner_email", "").lower() or None,
            "partner_name":   row.get("partner_name", "").strip() or None,
            "member_since":   None,
            "_row":           i,
        }

        ms = row.get("member_since", "").strip()
        if ms:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y"):
                try:
                    entry["member_since"] = date(*[int(x) for x in
                                             __import__("re").split(r"[-/]", ms)])
                    break
                except Exception:
                    pass
            if not entry["member_since"]:
                # Try year-only
                try:
                    entry["member_since"] = date(int(ms), 1, 1)
                except Exception:
                    errors.append(f"Row {i}: Could not parse member_since '{ms}' -- use YYYY-MM-DD or YYYY")

        rows.append(entry)

    return rows, errors


def _import_rows(rows):
    added = skipped = 0
    email_map = {}   # email      ? Person
    name_map  = {}   # "First Last" ? Person

    for entry in rows:
        email = entry.get("email")
        if email and Person.query.filter_by(email=email).first():
            skipped += 1
            continue

        p = Person(
            person_type    = entry["person_type"],
            title          = entry["title"],
            first_name     = entry["first_name"],
            last_name      = entry["last_name"],
            suffix         = entry["suffix"],
            gender         = entry["gender"],
            email          = entry["email"],
            phone          = entry["phone"],
            is_officer     = entry["is_officer"],
            officer_role   = entry["officer_role"],
            address_line1  = entry["address_line1"],
            address_line2  = entry["address_line2"],
            city           = entry["city"],
            province_state = entry["province_state"],
            postal_code    = entry["postal_code"],
            country        = entry["country"],
            notes          = entry["notes"],
            member_since   = entry["member_since"],
            can_login      = False,
        )
        db.session.add(p)
        db.session.flush()
        added += 1

        if email:
            email_map[email] = p
        full_name = f"{entry['first_name']} {entry['last_name']}".strip().lower()
        name_map[full_name] = p

    db.session.flush()

    # Second pass: link partners by email OR by name
    for entry in rows:
        me = entry.get("email")
        p1 = email_map.get(me) if me else None
        if not p1:
            # Try to find by name (in case they were skipped or have no email)
            my_name = f"{entry['first_name']} {entry['last_name']}".strip().lower()
            p1 = name_map.get(my_name)
        if not p1 or p1.partner_id:
            continue

        # Try partner_email first, then partner_name
        p2 = None
        pe = entry.get("partner_email")
        if pe:
            p2 = email_map.get(pe) or Person.query.filter_by(email=pe).first()
        if not p2:
            pn = (entry.get("partner_name") or "").strip().lower()
            if pn:
                p2 = name_map.get(pn) or Person.query.filter(
                    db.func.lower(Person.first_name + " " + Person.last_name) == pn
                ).first()

        if p2 and not p2.partner_id:
            p1.partner_id = p2.id
            p2.partner_id = p1.id

    db.session.commit()
    return added, skipped


@import_bp.route("/template")
@login_required
@admin_required
def download_template():
    """Download a blank CSV template with all supported columns."""
    import csv, io
    from flask import Response

    columns = [
        "person_type", "title", "first_name", "last_name", "suffix",
        "gender", "email", "phone", "is_officer", "officer_role",
        "member_since", "address_line1", "address_line2", "city",
        "province_state", "postal_code", "country",
        "partner_email", "partner_name", "notes",
    ]

    # One header row + two example rows
    example_rows = [
        {
            "person_type": "member", "title": "Dr.", "first_name": "Jean",
            "last_name": "Dupont", "suffix": "", "gender": "M",
            "email": "jean.dupont@example.com", "phone": "216-555-0100",
            "is_officer": "No", "officer_role": "",
            "member_since": "2015", "address_line1": "123 Rue du Vin",
            "address_line2": "", "city": "Cleveland", "province_state": "OH",
            "postal_code": "44101", "country": "USA",
            "partner_email": "", "partner_name": "Marie Dupont", "notes": "",
        },
        {
            "person_type": "partner", "title": "Mrs.", "first_name": "Marie",
            "last_name": "Dupont", "suffix": "", "gender": "F",
            "email": "", "phone": "",
            "is_officer": "No", "officer_role": "",
            "member_since": "", "address_line1": "", "address_line2": "",
            "city": "", "province_state": "", "postal_code": "", "country": "",
            "partner_email": "", "partner_name": "Jean Dupont", "notes": "",
        },
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(example_rows)
    csv_data = output.getvalue()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=members_template.csv"}
    )
