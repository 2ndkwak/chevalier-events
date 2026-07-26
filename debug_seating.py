import requests, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend import create_app

app = create_app()
with app.app_context():
    from backend.models import Event
    from backend.routes.seating import _get_attendees, _get_event_couples, _get_seating_history, _build_prompt

    key = app.config.get("ANTHROPIC_API_KEY")

    event = Event.query.filter(Event.table_config.isnot(None)).first()
    if not event:
        print("No event with table config found.")
        exit()

    # Force fresh load
    from backend.models import db
    db.session.expire(event)
    db.session.refresh(event)

    print(f"Testing with event: {event.title}")
    print(f"Attendees: {len(_get_attendees(event))}")
    print(f"Seating rules: {event.seating_rules}")

    attendees = _get_attendees(event)
    couples   = _get_event_couples(event)
    history   = _get_seating_history(event.id, limit=3)
    locked    = {}

    prompt = _build_prompt(event, attendees, couples, history, locked)
    print(f"\nPrompt length: {len(prompt)} characters")
    print("\n--- FULL PROMPT ---")
    print(prompt)

    print("\n--- SENDING TO API ---")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 4000,
            "system": (
                "You are a seating assignment engine for a formal chivalric dinner. "
                "When given a seating task, first reason through the constraints, "
                "then provide your final answer as a JSON object wrapped in <json></json> tags.\n\n"
                "Required JSON structure:\n"
                "{\"tables\": [{\"table_num\": 1, \"seats\": ["
                "{\"seat_num\": 1, \"person_id\": 123, \"guest_id\": null}"
                "]}, ...]}\n\n"
                "- person_id: integer for members/partners, null for ad-hoc guests\n"
                "- guest_id: integer for ad-hoc guests, null for members/partners\n"
                "- Every attendee must appear exactly once\n"
                "- Always end your response with the complete <json>...</json> block"
            ),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60
    )

    print(f"Status: {r.status_code}")
    data = r.json()
    raw = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            raw += block["text"]

    print(f"Raw response length: {len(raw)}")
    print("\n--- LAST 2000 CHARS OF RESPONSE (where json tag should be) ---")
    print(raw[-2000:])

    import re as _re
    tag_match = _re.search(r'<json>(.*?)</json>', raw, _re.DOTALL)
    if tag_match:
        extracted = tag_match.group(1).strip()
        print(f"\n✓ JSON tag found! Extracted {len(extracted)} chars")
        try:
            parsed = json.loads(extracted)
            total_seats = sum(len(t['seats']) for t in parsed['tables'])
            print(f"✓ Valid JSON — {len(parsed['tables'])} tables, {total_seats} seats assigned")
        except Exception as e:
            print(f"✗ JSON parse error: {e}")
    else:
        print("\n✗ No <json> tag found in response")
        last_brace = raw.rfind('{"tables"')
        if last_brace >= 0:
            print(f"  Found '{{\"tables\"' at position {last_brace} — fallback will be used")
