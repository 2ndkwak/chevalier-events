"""
Sets each member's "Member Since" date from an induction-year CSV, matching
by name. Safe by default -- run with no flags first to see a full report of
what WOULD happen; nothing is written to the database until you add --apply.

Usage:
    cd /var/www/chevalier
    source venv/bin/activate
    python3 set_member_since_from_csv.py Induction_dates.csv          # dry run (preview only)
    python3 set_member_since_from_csv.py Induction_dates.csv --apply  # actually writes changes

CSV format expected: two columns, "Full Name" and "Induction Year".
Names may have an officer title prefix (e.g. "GRAND CONNÉTABLE Robert J.
CONRAD") and/or a suffix (Jr., M.D., IV, Ph.D., Esq., etc.) -- both are
handled automatically. The surname is taken to be the last ALL-CAPS word in
the name, after stripping any recognized suffix.

Matching logic:
  1. Find every Person whose last name matches (case-insensitive). This
     pool commonly includes both a member AND their partner, since partners
     are stored as their own record sharing the last name.
  2. Check EVERY name fragment before the surname (a formal first name, a
     middle name, or an initial) against each candidate's first name, using
     nickname-aware matching (Trenton/Trent, Robert/Bob, Katherine/Kathy,
     etc.), not just an exact string match. This runs even when there's
     only one candidate -- a single same-last-name match is never assumed
     correct without the first name also being plausible.
  3. Exactly one candidate passes that check -> that's the match.
  4. Zero or more than one candidate passes -> reported as AMBIGUOUS with
     every candidate listed, and left untouched. Never guessed.
  5. The last name isn't found in the database at all -> NO MATCH.
  6. No usable year ("N/A", blank) -> SKIPPED.

Every row's outcome is printed, followed by a summary count. Review the
dry-run output carefully -- especially any AMBIGUOUS or NO MATCH rows --
before re-running with --apply.
"""
import sys
import csv
import re
from datetime import date

sys.path.insert(0, "/var/www/chevalier")
sys.path.insert(0, ".")
from nicknames import names_plausibly_match

SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v", "vi",
            "m.d.", "md", "ph.d.", "phd", "esq", "esq.", "dds", "d.d.s."}


def _is_caps_token(token):
    letters = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", token)
    return len(letters) > 0 and letters == letters.upper() and letters != letters.lower()


def parse_name(full_name):
    """Returns (list_of_first_name_candidates, last_name) parsed from a raw name string."""
    tokens = full_name.strip().split()

    while tokens:
        candidate = tokens[-1].strip(",").lower()
        if candidate in SUFFIXES:
            tokens.pop()
        else:
            break

    if not tokens:
        return [], None

    last_idx = None
    for i in range(len(tokens) - 1, -1, -1):
        if _is_caps_token(tokens[i]):
            last_idx = i
            break
    if last_idx is None:
        return [], None

    last_name = re.sub(r"[^\w\-'À-ÖØ-öø-ÿ]", "", tokens[last_idx])
    first_part = tokens[:last_idx]

    # Every token before the surname is a candidate -- formal first name,
    # middle name, or initial. Checking all of them (not just the first
    # plausible-looking one) handles "K. Anne YADLEY" (Anne is the 2nd
    # token) and "A. Chace ANDERSON" (Chace is the 2nd token) correctly.
    candidates = [re.sub(r"[^\w'-]", "", t) for t in first_part]
    candidates = [c for c in candidates if c]

    return candidates, last_name


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 set_member_since_from_csv.py <csv_file> [--apply]")
        sys.exit(1)

    csv_path = sys.argv[1]
    apply_changes = "--apply" in sys.argv

    from backend import create_app
    app = create_app()

    with app.app_context():
        from backend.models import db, Person

        candidates_by_last = {}
        for p in Person.query.filter(
            Person.person_type.in_(["member", "honoraire", "aspirant", "partner"])
        ).all():
            if p.last_name:
                candidates_by_last.setdefault(p.last_name.strip().lower(), []).append(p)

        matched = ambiguous = no_match = skipped = 0

        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                full_name = (row.get("Full Name") or "").strip()
                year_raw = (row.get("Induction Year") or "").strip()
                if not full_name:
                    continue

                if not year_raw or not year_raw.isdigit():
                    print(f"SKIPPED     {full_name:50s} (no usable year: '{year_raw}')")
                    skipped += 1
                    continue

                year = int(year_raw)
                first_candidates, last_name = parse_name(full_name)
                if not last_name:
                    print(f"NO MATCH    {full_name:50s} (couldn't parse a last name)")
                    no_match += 1
                    continue

                pool = candidates_by_last.get(last_name.lower(), [])

                if len(pool) == 0:
                    print(f"NO MATCH    {full_name:50s} (no '{last_name}' found in database)")
                    no_match += 1
                    continue

                # Always verify the first name is plausible -- even against
                # a single-candidate pool. A shared last name alone is never
                # enough on its own.
                plausible = [
                    p for p in pool
                    if any(names_plausibly_match(fc, p.first_name) for fc in first_candidates)
                ]

                if len(plausible) != 1:
                    print(f"AMBIGUOUS   {full_name:50s} -- candidates sharing '{last_name}':")
                    for p in pool:
                        marker = " (plausible)" if p in plausible else ""
                        print(f"              - {p.first_name} {p.last_name}  <{p.email or 'no email'}>  (id={p.id}){marker}")
                    ambiguous += 1
                    continue

                person = plausible[0]
                new_date = date(year, 1, 1)
                old_date = person.member_since
                print(f"MATCH       {full_name:50s} -> {person.first_name} {person.last_name} "
                      f"<{person.email or 'no email'}>  Member Since: {old_date} -> {new_date}")
                matched += 1

                if apply_changes:
                    person.member_since = new_date

        if apply_changes:
            db.session.commit()

        print()
        print(f"Matched: {matched}   Ambiguous: {ambiguous}   No match: {no_match}   Skipped: {skipped}")
        if apply_changes:
            print(f"\n✓ Changes SAVED for {matched} member(s).")
        else:
            print(f"\nThis was a DRY RUN -- nothing was changed. "
                  f"Review the report above, then re-run with --apply to save.")


if __name__ == "__main__":
    main()

