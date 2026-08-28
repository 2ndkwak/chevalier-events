"""
seed_screenshots.py -- populate a throwaway SQLite DB with realistic fake
data, for generating documentation screenshots of the real app.

USAGE: copy this file to the root of the app (next to run.py), alongside
run_server.py and capture.py. Then:

    1. Back up or move aside instance/chevalier.db if one already exists
       (this script calls db.drop_all() -- never run it against a real
       database with real member data).
    2. Replace instance/config.py with throwaway values (see the sample
       config in this folder's README) -- never point this at real
       Postmark/Anthropic credentials, and never let it send real mail.
    3. python3 seed_screenshots.py
    4. python3 run_server.py   (in the background -- see README)
    5. python3 capture.py

Names deliberately match the ones already used throughout the Grand
Senechal Guide and Technical Reference (Charles & Margaret Whitfield,
Robert & Linh Nguyen, Sophie Laurent, etc.) so future screenshots stay
visually consistent with old ones without needing to invent new people
each time. Extend this list rather than replacing it wholesale, unless
a documentation refresh calls for genuinely new sample data.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from datetime import datetime, timedelta

from backend import create_app
from backend.models import (
    db, Person, Event, RSVP, RSVPGuest, EventPromotionSend, DietaryTag,
)
from backend.util import utcnow

app = create_app()

with app.app_context():
    # Wipe and start clean
    db.drop_all()
    db.create_all()

    now = utcnow()

    # ---- Admin / GS account -------------------------------------------
    trey = Person(person_type="member", title="", first_name="Trey", last_name="Admin",
                   email="trey@example.com", can_login=True, is_admin=True,
                   is_officer=True, officer_role="Grand S\u00e9n\u00e9chal")
    trey.set_password("changeme")
    db.session.add(trey)

    # ---- Members (matching names already used throughout the guide) ---
    charles = Person(person_type="member", title="Dr.", first_name="Charles", last_name="Whitfield",
                      email="charles.whitfield@example.com", phone="216-555-0114",
                      can_login=True, is_officer=True, officer_role="Grand S\u00e9n\u00e9chal",
                      member_since=datetime(2011, 5, 1).date())
    margaret = Person(person_type="partner_non_member_chevalier", title="", first_name="Margaret",
                       last_name="Whitfield", email="margaret.whitfield@example.com",
                       phone="216-555-0115", can_login=True, gender="F")

    robert = Person(person_type="member", first_name="Robert", last_name="Nguyen",
                     email="robert.nguyen@example.com", can_login=False)
    linh = Person(person_type="partner", first_name="Linh", last_name="Nguyen", gender="F")

    anne_b = Person(person_type="member", title="Countess", first_name="Anne",
                     last_name="de Bellecourt", email="anne.debellecourt@example.com",
                     can_login=True)

    james = Person(person_type="honoraire", first_name="James", last_name="Whitcomb",
                    email="james.whitcomb@example.com", phone="216-555-0177", can_login=False)

    priya = Person(person_type="aspirant", first_name="Priya", last_name="Sharma",
                    email="priya.sharma@example.com", can_login=True)

    # -- Extra members to demonstrate all 5 login/bounce/open badge states --
    marcus = Person(person_type="member", first_name="Marcus", last_name="Bellweather",
                     email="marcus.bellweather@example.com", can_login=False,
                     invite_sent_at=now - timedelta(days=7))  # sent, not opened

    lucille = Person(person_type="member", first_name="Lucille", last_name="Marchetti",
                      email="lucille.marchetti@example.com", can_login=False,
                      invite_sent_at=now - timedelta(days=9),
                      invite_opened_at=now - timedelta(days=8))  # opened, not activated

    grant = Person(person_type="member", first_name="Grant", last_name="Okafor",
                    email="grant.okafor@example.com", can_login=False,
                    invite_sent_at=now - timedelta(days=10),
                    email_bounced_at=now - timedelta(days=10),
                    email_bounce_type="HardBounce")  # bounced

    isabelle = Person(person_type="member", first_name="Isabelle", last_name="Fontaine",
                       email="isabelle.fontaine@example.com", can_login=False)  # no login, no invite

    sophie = Person(person_type="member", first_name="Sophie", last_name="Laurent",
                     email="sophie.laurent@example.com", can_login=True)

    thomas = Person(person_type="member", first_name="Thomas", last_name="Reyes",
                     email="thomas.reyes@example.com", can_login=True)
    claire = Person(person_type="partner", first_name="Claire", last_name="Reyes", gender="F")

    elena = Person(person_type="member", first_name="Elena", last_name="Kowalski",
                    email="elena.kowalski@example.com", can_login=True)

    anne_d = Person(person_type="aspirant", first_name="Anne", last_name="Delacroix",
                     email="anne.delacroix@example.com", can_login=True)

    people = [trey, charles, margaret, robert, linh, anne_b, james, priya,
              marcus, lucille, grant, isabelle, sophie, thomas, claire, elena, anne_d]
    for p in people:
        db.session.add(p)
    db.session.flush()

    # Link partners in both directions now that everyone has an id
    charles.partner_id = margaret.id
    margaret.partner_id = charles.id
    robert.partner_id = linh.id
    linh.partner_id = robert.id
    thomas.partner_id = claire.id
    claire.partner_id = thomas.id
    db.session.flush()

    gf = DietaryTag.get_or_create("Gluten-free")
    shellfish = DietaryTag.get_or_create("Shellfish allergy")
    veg = DietaryTag.get_or_create("Vegetarian")
    charles.dietary_tags = [gf]
    robert.dietary_tags = [shellfish]

    # ---- Events ----------------------------------------------------------
    chablis = Event(
        title="Chablis Dinner",
        event_date=datetime(2026, 9, 20, 18, 30),
        venue_name="Union Club",
        venue_address="1211 Euclid Ave, Cleveland, OH",
        capacity=40,
        price_per_person=145.00,
        paypal_price_per_person=150.00,
        rsvp_deadline=datetime(2026, 9, 17, 17, 0),
        is_published=True,
        chef_name="Jean-Marc Delacroix",
        hosts="Charles & Margaret Whitfield",
        promotion_sent_at=now - timedelta(days=5),
        menu_updated_at=now - timedelta(days=6),
        wine_list_updated_at=now - timedelta(days=6),
    )
    db.session.add(chablis)
    db.session.flush()

    # RSVPs for Chablis Dinner
    def add_rsvp(person, status="confirmed", guests=None, payment_status="unpaid",
                  amount_paid=None):
        r = RSVP(event_id=chablis.id, person_id=person.id, status=status,
                 payment_status=payment_status, amount_paid=amount_paid)
        db.session.add(r)
        db.session.flush()
        for g in (guests or []):
            db.session.add(RSVPGuest(rsvp_id=r.id, first_name=g[0], last_name=g[1]))
        return r

    add_rsvp(charles, payment_status="paid", amount_paid=145.00)
    add_rsvp(anne_b, payment_status="waived")
    add_rsvp(robert, payment_status="partial", amount_paid=75.00)
    add_rsvp(sophie, payment_status="paid", amount_paid=145.00, guests=[("Marc", "Laurent")])
    add_rsvp(margaret, payment_status="unpaid")

    # Promotion send log: some opened, one bounced, some sent-not-opened
    sends = [
        (sophie, now - timedelta(days=5), now - timedelta(days=4)),
        (robert, now - timedelta(days=5), now - timedelta(days=3)),
        (marcus, now - timedelta(days=5), None),
        (lucille, now - timedelta(days=5), now - timedelta(days=2)),  # opened, no RSVP yet
        (grant, now - timedelta(days=5), None),  # bounced -- shown via person.email_bounced_at
        (isabelle, now - timedelta(days=5), None),
    ]
    for person, sent_at, opened_at in sends:
        db.session.add(EventPromotionSend(event_id=chablis.id, person_id=person.id,
                                           sent_at=sent_at, opened_at=opened_at))

    vendanges = Event(
        title="C\u00e9l\u00e9bration des Vendanges",
        event_date=datetime(2026, 10, 5, 18, 0),
        venue_name="The Grove Hotel",
        capacity=60,
        rsvp_deadline=datetime(2026, 10, 2, 17, 0),
        is_published=True,
        promotion_send_started_at=now,  # mid-send, for the "Sending..." screenshot
    )
    db.session.add(vendanges)

    db.session.commit()
    print("Seed complete.")
    print("Admin login: trey@example.com / changeme")
    print(f"Chablis Dinner event id: {chablis.id}")
    print(f"C\u00e9l\u00e9bration des Vendanges event id: {vendanges.id}")
