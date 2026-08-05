from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date

db = SQLAlchemy()

# --- PERSON -------------------------------------------------------------------
# Everyone in the system -- full members, non-member partners, and ad-hoc event
# guests -- is a Person. The person_type field distinguishes them.

class Person(UserMixin, db.Model):
    __tablename__ = "persons"

    id             = db.Column(db.Integer, primary_key=True)
    person_type    = db.Column(db.String(20), nullable=False)
    # Values: "member" | "partner" | "guest"
    # "partner" = non-member spouse/partner linked to a member
    # "guest"   = ad-hoc guest added at RSVP time

    # Core name fields (all types)
    title          = db.Column(db.String(50))          # Dr., Count, Countess, Sir?
    first_name     = db.Column(db.String(100), nullable=False)
    last_name      = db.Column(db.String(100), nullable=False)
    suffix         = db.Column(db.String(20))           # Jr., III, etc.
    gender         = db.Column(db.String(10))            # M | F | Other
    dietary        = db.Column(db.Text)                 # free text (legacy note)
    dietary_tags   = db.relationship("DietaryTag", secondary="dietary_tag_persons",
                                     backref="persons")

    # Member / partner only
    email          = db.Column(db.String(200), unique=True, nullable=True)
    phone          = db.Column(db.String(50))            # shared cell phone
    home_phone_1   = db.Column(db.String(50))            # home phone at address 1
    address_line1  = db.Column(db.String(200))
    address_line2  = db.Column(db.String(200))
    city           = db.Column(db.String(100))
    province_state = db.Column(db.String(100))
    postal_code    = db.Column(db.String(20))
    country        = db.Column(db.String(100))

    # Second address (e.g. seasonal / winter home)
    address2_label = db.Column(db.String(100))            # e.g. "Winter home"
    home_phone_2   = db.Column(db.String(50))             # home phone at address 2
    address2_line1  = db.Column(db.String(200))
    address2_line2  = db.Column(db.String(200))
    city2           = db.Column(db.String(100))
    province_state2 = db.Column(db.String(100))
    postal_code2    = db.Column(db.String(20))
    country2        = db.Column(db.String(100))

    member_since   = db.Column(db.Date)
    notes          = db.Column(db.Text)

    # Officer flag + role label
    is_officer     = db.Column(db.Boolean, default=False, nullable=False)
    officer_role   = db.Column(db.String(100))         # "Grand Senechal", "Chancelier"?

    # Portal login
    password_hash  = db.Column(db.String(256))
    can_login      = db.Column(db.Boolean, default=False, nullable=False)
    is_admin       = db.Column(db.Boolean, default=False, nullable=False)
    invite_token   = db.Column(db.String(64), nullable=True, unique=True)
    invite_sent_at = db.Column(db.DateTime, nullable=True)
    reset_token            = db.Column(db.String(64), nullable=True, unique=True)
    reset_token_expires_at = db.Column(db.DateTime, nullable=True)
    last_login     = db.Column(db.DateTime)

    # Partner link (self-referential, mutual)
    partner_id     = db.Column(db.Integer, db.ForeignKey("persons.id"), nullable=True)
    partner        = db.relationship("Person", foreign_keys=[partner_id],
                                     remote_side="Person.id", uselist=False)

    # Host link (for ad-hoc guests: who brought them)
    host_id        = db.Column(db.Integer, db.ForeignKey("persons.id"), nullable=True)
    host           = db.relationship("Person", foreign_keys=[host_id],
                                     remote_side="Person.id", uselist=False)

    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow,
                               onupdate=datetime.utcnow)

    # Activity-log support: stamped explicitly (not via onupdate) so these
    # only change when the specific thing they track actually changes --
    # unlike updated_at above, which fires on any edit at all.
    person_type_updated_at    = db.Column(db.DateTime, nullable=True)
    dietary_tags_updated_at   = db.Column(db.DateTime, nullable=True)

    # -- helpers --
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_name(self):
        parts = [self.title, self.first_name, self.last_name, self.suffix]
        return " ".join(p for p in parts if p)

    @property
    def formal_name(self):
        """For name cards: title + first + last + suffix"""
        return self.display_name

    def __repr__(self):
        return f"<Person {self.id} {self.display_name} ({self.person_type})>"


# --- DIETARY TAGS ---------------------------------------------------------
# A growing, reusable list of dietary/allergy labels (e.g. "Gluten intolerant").
# The first time a label is used anywhere, it's saved here and becomes
# selectable for every member/guest afterward -- this is what lets the
# per-event allergy toggle work off a shared vocabulary instead of free text.

dietary_tag_persons = db.Table(
    "dietary_tag_persons",
    db.Column("person_id", db.Integer, db.ForeignKey("persons.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("dietary_tags.id"), primary_key=True),
)

dietary_tag_guests = db.Table(
    "dietary_tag_guests",
    db.Column("guest_id", db.Integer, db.ForeignKey("rsvp_guests.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("dietary_tags.id"), primary_key=True),
)


class DietaryTag(db.Model):
    __tablename__ = "dietary_tags"

    id    = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(100), nullable=False, unique=True)

    @staticmethod
    def get_or_create(label):
        label = (label or "").strip()
        if not label:
            return None
        existing = DietaryTag.query.filter(
            db.func.lower(DietaryTag.label) == label.lower()
        ).first()
        if existing:
            return existing
        tag = DietaryTag(label=label)
        db.session.add(tag)
        db.session.flush()
        return tag

    @staticmethod
    def set_from_labels(owner, labels):
        """Replace owner.dietary_tags with tags for the given list of label
        strings, creating any new labels as they're first used."""
        tags = []
        seen = set()
        for raw in labels:
            label = (raw or "").strip()
            if not label or label.lower() in seen:
                continue
            seen.add(label.lower())
            tag = DietaryTag.get_or_create(label)
            if tag:
                tags.append(tag)
        old_tag_ids = {t.id for t in owner.dietary_tags}
        new_tag_ids = {t.id for t in tags}
        owner.dietary_tags = tags
        if hasattr(owner, "dietary_tags_updated_at") and old_tag_ids != new_tag_ids:
            owner.dietary_tags_updated_at = datetime.utcnow()

    def __repr__(self):
        return f"<DietaryTag {self.id} '{self.label}'>"


# --- EVENT --------------------------------------------------------------------

class Event(db.Model):
    __tablename__ = "events"

    id              = db.Column(db.Integer, primary_key=True)
    title           = db.Column(db.String(200), nullable=False)
    event_date      = db.Column(db.DateTime, nullable=False)
    venue_name      = db.Column(db.String(200))
    venue_address   = db.Column(db.Text)
    teaser          = db.Column(db.String(300))   # short hook line for portal home list
    description     = db.Column(db.Text)
    dress_code      = db.Column(db.String(200))
    # "hosts" reuses the old "menu_notes" column -- unused, so repurposed
    # in place instead of adding a new column.
    hosts           = db.Column("menu_notes", db.Text)
    chef_name       = db.Column(db.String(200))
    paypal_link     = db.Column(db.String(500))
    paypal_price_per_person = db.Column(db.Numeric(10, 2))
    menu_finalized  = db.Column(db.Boolean, default=False, nullable=False)
    capacity        = db.Column(db.Integer)             # None = unlimited
    price_per_person = db.Column(db.Numeric(10, 2))    # None = no charge / TBD
    rsvp_deadline   = db.Column(db.DateTime)
    is_published    = db.Column(db.Boolean, default=False, nullable=False)

    # Table configuration (set after Table Planner step)
    table_config    = db.Column(db.JSON)
    # e.g. {"tables": [{"id":1,"size":8},{"id":2,"size":7},{"id":3,"size":6}]}

    # Seating rules for this event (JSON, supplements global rules)
    seating_rules   = db.Column(db.JSON)
    # e.g. {"not_together": [[1,2],[3,4]], "prefer_together": [[5,6]],
    #        "custom": ["Keep the head table near the entrance"]}

    # Explicit "this specific thing changed" markers, set only at the exact
    # moment each one actually happens (wine upload, menu upload, officer
    # ranking saved, booklet generated) -- deliberately separate from the
    # generic updated_at below, which fires on any edit to the event at all
    # and would be useless for telling whether the booklet is stale
    # relative to one specific dependency.
    wine_list_updated_at        = db.Column(db.DateTime, nullable=True)
    menu_updated_at             = db.Column(db.DateTime, nullable=True)
    officer_ranking_updated_at  = db.Column(db.DateTime, nullable=True)
    booklet_generated_at        = db.Column(db.DateTime, nullable=True)
    seating_updated_at          = db.Column(db.DateTime, nullable=True)
    table_cards_generated_at    = db.Column(db.DateTime, nullable=True)
    charts_generated_at         = db.Column(db.DateTime, nullable=True)
    seating_accepted_at         = db.Column(db.DateTime, nullable=True)
    allergies_reviewed_at       = db.Column(db.DateTime, nullable=True)
    wine_tags_generated_at      = db.Column(db.DateTime, nullable=True)
    promotion_sent_at           = db.Column(db.DateTime, nullable=True)
    name_badges_generated_at    = db.Column(db.DateTime, nullable=True)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow,
                                onupdate=datetime.utcnow)

    rsvps           = db.relationship("RSVP", back_populates="event",
                                      cascade="all, delete-orphan")
    wine_tags       = db.relationship("WineTag", back_populates="event",
                                      cascade="all, delete-orphan",
                                      order_by="WineTag.position")
    menu_items      = db.relationship("MenuItem", back_populates="event",
                                      cascade="all, delete-orphan",
                                      order_by="MenuItem.course")
    courses         = db.relationship("EventCourse", back_populates="event",
                                      cascade="all, delete-orphan",
                                      order_by="EventCourse.course")
    seat_assignments = db.relationship("SeatAssignment", back_populates="event",
                                      cascade="all, delete-orphan")
    allergy_offs    = db.relationship("EventAllergyOff", back_populates="event",
                                      cascade="all, delete-orphan")
    materials       = db.relationship("EventMaterial", back_populates="event",
                                      cascade="all, delete-orphan")

    @property
    def is_active(self):
        """Visible on member portal: published and not yet day-after"""
        today = date.today()
        return self.is_published and self.event_date.date() >= today

    @property
    def confirmed_count(self):
        """Seats spoken for: confirmed attendees plus anyone currently
        holding a provisional waitlist-promotion offer (their seat is
        reserved until the offer is confirmed or expires)."""
        return sum(r.guest_count for r in self.rsvps
                   if r.status in ("confirmed", "promoted"))

    @property
    def is_full(self):
        if self.capacity is None:
            return False
        return self.confirmed_count >= self.capacity

    def attending_dietary_tags(self):
        """The live list of allergy tags among currently-confirmed attendees
        for this event, each with its current on/off state. Recomputed fresh
        every call -- nothing about *which tags are relevant* is stored,
        only which ones have been explicitly switched off."""
        off_ids = {r.tag_id for r in EventAllergyOff.query
                   .filter_by(event_id=self.id).all()}

        tags = {}
        for rsvp in self.rsvps:
            if rsvp.status != "confirmed":
                continue
            for tag in rsvp.person.dietary_tags:
                tags[tag.id] = tag
            for guest in rsvp.guests:
                for tag in guest.dietary_tags:
                    tags[tag.id] = tag

        return sorted(
            [{"tag": tag, "active": tag.id not in off_ids}
             for tag in tags.values()],
            key=lambda t: t["tag"].label.lower()
        )

    @property
    def menu_uploaded(self):
        """Whether a menu has been uploaded for this event -- replaces the
        old manual "menu finalized" checkbox, which required a separate,
        easy-to-forget click disconnected from actually uploading the menu
        itself."""
        return MenuItem.query.filter_by(event_id=self.id).first() is not None

    def booklet_is_current(self):
        """Whether the last-generated menu booklet still reflects this
        event's current wine list, menu, and officer ranking. Returns
        (is_current, stale_because) -- stale_because is a list of
        plain-English names of whichever dependencies changed after the
        booklet was last generated, empty if it's current, and None
        specifically if it's never been generated at all (a different
        situation from "generated, now stale")."""
        if not self.booklet_generated_at:
            return False, None
        stale_because = []
        if self.wine_list_updated_at and self.wine_list_updated_at > self.booklet_generated_at:
            stale_because.append("wine list")
        if self.menu_updated_at and self.menu_updated_at > self.booklet_generated_at:
            stale_because.append("menu")
        if self.officer_ranking_updated_at and self.officer_ranking_updated_at > self.booklet_generated_at:
            stale_because.append("officer ranking")
        return (len(stale_because) == 0), stale_because

    def table_cards_is_current(self):
        """Whether the last-generated table name cards still reflect this
        event's current seating chart. Same shape as booklet_is_current(),
        but with a single dependency."""
        if not self.table_cards_generated_at:
            return False, None
        if self.seating_updated_at and self.seating_updated_at > self.table_cards_generated_at:
            return False, ["seating chart"]
        return True, []

    def charts_is_current(self):
        """Whether the charts & lists views were printed after the current
        seating chart was last generated/edited. "Printed" here means the
        print button was clicked while viewing one of the four bundled
        tabs (visual chart, by table, alphabetical, table + allergies) --
        the browser's own print dialog after that click isn't something
        the server can observe, so the click itself is the signal."""
        if not self.charts_generated_at:
            return False, None
        if self.seating_updated_at and self.seating_updated_at > self.charts_generated_at:
            return False, ["seating chart"]
        return True, []

    def seating_is_accepted(self):
        """Whether the seating plan has been explicitly accepted (the one
        deliberate, non-automatic milestone -- generating or editing a
        chart is never enough on its own), and whether that acceptance is
        still current relative to the chart's own last-changed timestamp.
        Regenerating, clearing, or manually re-saving the chart after
        acceptance resets this back to unaccepted -- there's no "stale but
        still counts" state here, unlike the printed materials."""
        if not self.seating_accepted_at:
            return False, None
        if self.seating_updated_at and self.seating_updated_at > self.seating_accepted_at:
            return False, ["seating chart"]
        return True, []

    def name_badges_is_current(self):
        """Whether name badges have ever been generated for this event.
        Deliberately has no staleness trigger at all, unlike Table Cards
        or Wine Tags -- there's no single dependency that reliably means
        "this needs updating"; a late-added guest might or might not
        warrant a second set, entirely the GS's own judgment call. Once
        generated, this stays current until generated again -- it's just
        a plain record of whether it's ever been done."""
        if not self.name_badges_generated_at:
            return False, None
        return True, []

    def wine_tags_is_current(self):
        """Whether the last-generated wine tags still reflect this event's
        current wine list. Same shape as table_cards_is_current(), with a
        single dependency."""
        if not self.wine_tags_generated_at:
            return False, None
        if self.wine_list_updated_at and self.wine_list_updated_at > self.wine_tags_generated_at:
            return False, ["wine list"]
        return True, []

    @property
    def officers_ranked(self):
        """Whether at least one attendee (a member/officer or a guest
        marked as a visiting officer) has actually been given a rank for
        this event's officer section -- not just whether the Officer
        Ranking screen was ever saved, which is a different, weaker
        signal (someone could save it with every rank left blank).
        Without at least one rank set, the booklet's officer section is
        silently omitted entirely and everyone prints as a plain
        Chevalier instead."""
        for r in self.rsvps:
            if r.officer_rank is not None:
                return True
            for g in r.guests:
                if g.officer_rank is not None:
                    return True
        return False

    @property
    def promoted(self):
        """Whether the promotion email has ever been sent for this event.
        Deliberately one-way, same as allergies_reviewed -- stamped the
        moment the send is attempted, regardless of individual delivery
        failures to specific recipients (those are a separate, already
        surfaced concern; the deliberate act of sending is the milestone,
        the same way clicking Print is the signal for Charts & Lists)."""
        return self.promotion_sent_at is not None

    @property
    def allergies_reviewed(self):
        """Whether the GS has explicitly confirmed the allergy list is
        correct for this event. Deliberately one-way -- unlike the printed
        materials, this never auto-resets when RSVPs or tags change; the
        GS is expected to handle those changes by judgment, the same way
        they already handle late seating changes after RSVPs close."""
        return self.allergies_reviewed_at is not None

    def __repr__(self):
        return f"<Event {self.id} '{self.title}' {self.event_date.date()}>"


# --- RSVP ---------------------------------------------------------------------

class RSVP(db.Model):
    __tablename__ = "rsvps"

    id          = db.Column(db.Integer, primary_key=True)
    event_id    = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    person_id   = db.Column(db.Integer, db.ForeignKey("persons.id"), nullable=False)
    status      = db.Column(db.String(20), nullable=False)
    # Values: "confirmed" | "waitlist" | "declined" | "promoted" | "expired"
    # "promoted"  = offered a seat off the waitlist; holds the seat provisionally
    #               pending the member's confirmation within the offer window.
    # "expired"   = a "promoted" offer that was not confirmed in time; the
    #               person was passed over and the seat was offered onward.

    # Payment tracking
    payment_status = db.Column(db.String(20), default="unpaid")
    # Values: "unpaid" | "paid" | "waived" | "partial"
    amount_paid    = db.Column(db.Numeric(10, 2), nullable=True)
    payment_note   = db.Column(db.String(200), nullable=True)

    # Waitlist promotion tracking
    promotion_token      = db.Column(db.String(64), unique=True, nullable=True)
    promoted_at           = db.Column(db.DateTime, nullable=True)
    promotion_expires_at  = db.Column(db.DateTime, nullable=True)
    # The person whose cancellation opened the seat that led (possibly
    # through a chain of expired offers) to this promotion -- carried
    # forward so the GS notification can tell the whole story at once.
    cancelled_by_id = db.Column(db.Integer, db.ForeignKey("persons.id"), nullable=True)
    cancelled_by     = db.relationship("Person", foreign_keys=[cancelled_by_id])
    # Links two separate RSVP rows that represent one couple promoted
    # together as a single 2-seat party (e.g. a couple added via the
    # admin "Add RSVP" tool, where each partner has their own row
    # rather than one being a guest of the other). When set, confirming
    # or releasing either row resolves both together.
    linked_rsvp_id  = db.Column(db.Integer, db.ForeignKey("rsvps.id"), nullable=True)
    linked_rsvp      = db.relationship("RSVP", remote_side="RSVP.id",
                                       foreign_keys=[linked_rsvp_id], uselist=False)

    # Menu booklet officer-list print order for this event only -- the
    # person's officer title itself (Person.officer_role) is permanent,
    # but whether/where they print in a given booklet's officer section
    # varies event to event.
    officer_rank    = db.Column(db.Integer, nullable=True)

    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    event       = db.relationship("Event", back_populates="rsvps")
    person      = db.relationship("Person", foreign_keys=[person_id])
    guests      = db.relationship("RSVPGuest", back_populates="rsvp",
                                  cascade="all, delete-orphan")

    @property
    def guest_count(self):
        """Total seats: the member/partner themselves + their guests"""
        return 1 + len(self.guests)

    def __repr__(self):
        return f"<RSVP event={self.event_id} person={self.person_id} {self.status}>"


class RSVPGuest(db.Model):
    """Ad-hoc guests added at RSVP time by a member or partner."""
    __tablename__ = "rsvp_guests"

    id          = db.Column(db.Integer, primary_key=True)
    rsvp_id     = db.Column(db.Integer, db.ForeignKey("rsvps.id"), nullable=False)
    title       = db.Column(db.String(50))
    first_name  = db.Column(db.String(100), nullable=False)
    last_name   = db.Column(db.String(100), nullable=False)
    suffix      = db.Column(db.String(20))
    dietary     = db.Column(db.Text)
    gender      = db.Column(db.String(10))            # M | F | Other
    dietary_tags = db.relationship("DietaryTag", secondary="dietary_tag_guests",
                                   backref="guests")

    # Visiting/guest officer support for the menu booklet -- e.g. a Grand
    # Officer from another chapter who isn't in this club's own member
    # database. Set per-event, right on the guest's RSVP entry.
    is_officer   = db.Column(db.Boolean, default=False, nullable=False)
    officer_title = db.Column(db.String(200))
    officer_rank  = db.Column(db.Integer, nullable=True)

    rsvp        = db.relationship("RSVP", back_populates="guests")

    @property
    def display_name(self):
        parts = [self.title, self.first_name, self.last_name, self.suffix]
        return " ".join(p for p in parts if p)


# --- SEATING ------------------------------------------------------------------

class SeatAssignment(db.Model):
    """One row per seat at a specific event."""
    __tablename__ = "seat_assignments"

    id          = db.Column(db.Integer, primary_key=True)
    event_id    = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    table_num   = db.Column(db.Integer, nullable=False)
    seat_num    = db.Column(db.Integer, nullable=False)

    # The occupant -- either a Person (member/partner) or an RSVPGuest
    person_id   = db.Column(db.Integer, db.ForeignKey("persons.id"), nullable=True)
    guest_id    = db.Column(db.Integer, db.ForeignKey("rsvp_guests.id"), nullable=True)

    is_locked   = db.Column(db.Boolean, default=False, nullable=False)

    event       = db.relationship("Event", back_populates="seat_assignments")
    person      = db.relationship("Person", foreign_keys=[person_id])
    guest       = db.relationship("RSVPGuest", foreign_keys=[guest_id])

    @property
    def occupant_name(self):
        if self.person:
            return self.person.display_name
        if self.guest:
            return self.guest.display_name
        return None

    __table_args__ = (
        db.UniqueConstraint("event_id", "table_num", "seat_num",
                            name="uq_seat_per_event"),
    )


# --- WINE TAGS ------------------------------------------------------------------

class WineTag(db.Model):
    """One row per wine in the tasting flight for an event -- every guest
    drinks the same wines in the same order, so this is a small, event-wide
    list (not per-guest)."""
    __tablename__ = "wine_tags"

    id          = db.Column(db.Integer, primary_key=True)
    event_id    = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    position    = db.Column(db.Integer, nullable=False)   # index within its course, 1-based (resets each course)
    course      = db.Column(db.Integer, nullable=False, default=1)  # which course this wine is served with

    vintage     = db.Column(db.String(20))
    domain      = db.Column(db.String(200), nullable=False)
    appellation = db.Column(db.String(300), nullable=False)
    color       = db.Column(db.String(10), nullable=True)  # "red" | "white" -- for the menu booklet
    color       = db.Column(db.String(10), nullable=True)  # "red" | "white" -- for the menu booklet

    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    event       = db.relationship("Event", back_populates="wine_tags")

    __table_args__ = (
        db.UniqueConstraint("event_id", "course", "position", name="uq_wine_position_per_course"),
    )

    def __repr__(self):
        return f"<WineTag event={self.event_id} course={self.course} #{self.position} {self.domain}>"


# --- MENU ITEMS -----------------------------------------------------------------
# One row per dish, one dish per course -- reuses the same course numbering as
# WineTag so a dish and its paired wine(s) share one course number, keeping
# the two lists in sync for the menu booklet.

class MenuItem(db.Model):
    __tablename__ = "menu_items"

    id            = db.Column(db.Integer, primary_key=True)
    event_id      = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    course        = db.Column(db.Integer, nullable=False, default=1)

    # Nullable: a course can carry just a label with no dish at all (e.g.
    # "Cocktails", which has wines but nothing on the food menu).
    dish_french   = db.Column(db.Text, nullable=True)
    dish_english  = db.Column(db.Text)

    # Display-order hint, only meaningful for course 0 (Cocktails), the one
    # course allowed to hold more than one row -- see the partial unique
    # index below. Not enforced unique; ties just fall back to insertion
    # order. Every other course still gets exactly one row, same as always.
    position      = db.Column(db.Integer, default=1)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow)

    event         = db.relationship("Event", back_populates="menu_items")

    # Only one row per (event, course) -- EXCEPT course 0, which is allowed
    # to repeat (e.g. several hors d'oeuvres items). This is a partial
    # index rather than a plain UniqueConstraint specifically so course 0
    # is excluded from it at the database level, not just in application
    # code -- see migrate_add_menu_position.py, since SQLite can't alter a
    # constraint in place.
    __table_args__ = (
        db.Index("uq_menu_item_per_course", "event_id", "course",
                 unique=True, sqlite_where=db.text("course != 0")),
    )

    def __repr__(self):
        return f"<MenuItem event={self.event_id} course={self.course}>"


# --- COURSE LABELS ---------------------------------------------------------------
# Free-text heading for each course number in an event's wine/menu (e.g.
# "Cocktail", "Premier Assiette", "Selection de Fromages") -- the course
# structure varies event to event, so this is per-event rather than a fixed
# lookup. Set from the optional "label" column on the wine list CSV only;
# the menu CSV doesn't repeat it, to avoid asking for the same thing twice.

class EventCourse(db.Model):
    __tablename__ = "event_courses"

    id       = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    course   = db.Column(db.Integer, nullable=False)
    label    = db.Column(db.String(100), nullable=False)

    event    = db.relationship("Event", back_populates="courses")

    __table_args__ = (
        db.UniqueConstraint("event_id", "course", name="uq_event_course_label"),
    )


# --- EVENT ALLERGY TOGGLES -----------------------------------------------------
# Presence of a row = that tag is switched OFF for that event (not relevant to
# the menu). Absence = ON (the safe default). The list of tags itself is never
# stored -- it's always recomputed live from currently-attending guests.

class EventAllergyOff(db.Model):
    __tablename__ = "event_allergy_off"

    id       = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    tag_id   = db.Column(db.Integer, db.ForeignKey("dietary_tags.id"), nullable=False)

    event    = db.relationship("Event", back_populates="allergy_offs")
    tag      = db.relationship("DietaryTag")

    __table_args__ = (
        db.UniqueConstraint("event_id", "tag_id", name="uq_event_tag_off"),
    )


# --- EVENT MATERIALS CHECKLIST -------------------------------------------------
# Presence of a row = that pre-event material has been prepared and checked off
# by the Grand Senechal. Purely manual -- nothing here is set automatically by
# generating a document, since "generated" and "reviewed and ready" are
# deliberately different moments in the workflow.

class EventMaterial(db.Model):
    __tablename__ = "event_materials"

    id           = db.Column(db.Integer, primary_key=True)
    event_id     = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    material_key = db.Column(db.String(50), nullable=False)
    # Values: "menu_booklet" | "wine_tags" | "table_name_cards" |
    #         "name_badges"  | "charts_and_lists"
    checked_at   = db.Column(db.DateTime, default=datetime.utcnow)

    event        = db.relationship("Event", back_populates="materials")

    __table_args__ = (
        db.UniqueConstraint("event_id", "material_key", name="uq_event_material"),
    )


# --- GLOBAL SEATING RULES -----------------------------------------------------

class SeatingRule(db.Model):
    """Permanent rules that apply to every event unless overridden."""
    __tablename__ = "seating_rules"

    id          = db.Column(db.Integer, primary_key=True)
    rule_type   = db.Column(db.String(50), nullable=False)
    # Values: "couples_same_table" | "couples_non_adjacent" |
    #         "officer_per_table"  | "custom"
    description = db.Column(db.Text)       # human-readable label
    is_active   = db.Column(db.Boolean, default=True, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
