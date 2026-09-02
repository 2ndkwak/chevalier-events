"""Read-only: prints event_date for every event referenced in this
session's findings, so we can check for a timing pattern separating
"Guest of" (broken) events from correctly-paired ones."""
from backend import create_app
from backend.models import Event

BROKEN_EVENTS = ["Joint Paulee with Commanderie de Bordeaux **NOTE CHANGE OF LOCATION TO SHAKER HEIGHTS CC**",
                  "Celebration des Vendages"]
CORRECT_EVENTS = ["2027 Grand Conseil, Beaune, France", "Bastille Day", "Salon de Chavelier",
                   "Celebration des Vendages"]

app = create_app()
with app.app_context():
    print("BROKEN (had Guest-of instances):")
    for title in BROKEN_EVENTS:
        e = Event.query.filter_by(title=title).first()
        if e:
            print(f"  {e.event_date}  {title}")

    print("\nCORRECT (independently self-RSVP'd instances found here):")
    for title in CORRECT_EVENTS:
        e = Event.query.filter_by(title=title).first()
        if e:
            print(f"  {e.event_date}  {title}")
