from flask import Flask, redirect, url_for
from .models import db
from flask_login import LoginManager
from flask_mail import Mail

login_manager = LoginManager()
mail = Mail()

# Friendly labels for the person_type column, used anywhere a raw value
# would otherwise be shown to a user (e.g. the dashboard's recent-changes list).
PERSON_TYPE_LABELS = {
    "member":                       "Chevalier",
    "honoraire":                    "Membre Honoraire",
    "aspirant":                     "Aspirant",
    "partner":                      "Partner",
    "partner_member_chevalier":     "Partner Member Chevalier",
    "partner_non_member_chevalier": "Partner Non-Member Chevalier",
}

def create_app(config=None):
    app = Flask(__name__,
                template_folder="../frontend/templates",
                static_folder="../frontend/static")

    # -- Default config ------------------------------------------------------
    app.config.update(
        SECRET_KEY="change-this-in-production",
        SQLALCHEMY_DATABASE_URI="sqlite:///../instance/chevalier.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # SQLite's default is to fail an operation immediately ("database
        # is locked") if it can't get a write lock, rather than waiting.
        # The Aug 2026 background-thread promotion send (see routes/
        # events.py) does many small commits over a stretch of time while
        # the app may also be handling other requests, which is exactly
        # the situation that makes a collision more likely than it's been
        # so far -- this gives any writer a few seconds to retry instead
        # of erroring outright. Applies app-wide, not just to that one
        # feature.
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 15}},

        # Base URL used to build absolute links (portal event pages, the
        # logo image) in emails sent from outside a real HTTP request --
        # currently just the Aug 2026 background promotion-send worker
        # (routes/events.py). Deliberately NOT the same thing as Flask's
        # own SERVER_NAME setting, which also affects Host-header
        # validation on every real incoming request and is riskier to
        # set globally without being certain of the deployment's exact
        # hostname/proxy setup. Override in instance/config.py if this
        # Sous Commanderie's domain is ever different.
        SITE_BASE_URL="https://clevelandchevaliers.com",

        # Mail -- admin fills these in config.py
        MAIL_SERVER="smtp.gmail.com",
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=None,
        MAIL_PASSWORD=None,
        MAIL_DEFAULT_SENDER=None,
        ADMIN_EMAIL=None,           # where RSVP notifications go

        # Broadcast Message Stream sends (the real promotion blast, and
        # the bulk-invite/resend-outstanding-invites worker) need to
        # connect to a different SMTP host than everything else -- see
        # backend/postmark.py. This hostname is universal Postmark
        # infrastructure, not org-specific, so unlike MAIL_SERVER etc.
        # it doesn't need to live in instance/config.py.
        POSTMARK_BROADCAST_SMTP_SERVER="smtp-broadcasts.postmarkapp.com",
        # The X-PM-Message-Stream value that actually routes a message to
        # the account's Broadcast stream. "broadcast" (singular) is the
        # ID Postmark auto-assigns to every account's default Broadcast
        # stream -- confirmed directly on the stream's own settings page,
        # NOT "broadcasts" (plural), which is what generic example text
        # in Postmark's own docs uses and is easy to copy verbatim by
        # mistake (exactly what happened here the first time: the header
        # silently didn't match any real stream, so Postmark accepted the
        # SMTP transaction with no error but then dropped the message
        # entirely -- no bounce, no delivery, no trace in either stream's
        # activity log).
        POSTMARK_BROADCAST_STREAM_ID="broadcast",

        # HTTP Basic Auth credentials protecting the incoming Postmark
        # webhook (routes/webhooks.py) from random internet traffic --
        # Postmark supports embedding these directly in the webhook URL
        # you configure in their dashboard (https://user:pass@host/...).
        # Real values live in instance/config.py, same pattern as
        # MAIL_USERNAME/PASSWORD -- these None defaults mean the webhook
        # route refuses all requests until real credentials are set.
        POSTMARK_WEBHOOK_USERNAME=None,
        POSTMARK_WEBHOOK_PASSWORD=None,
        ANTHROPIC_API_KEY=None,     # required for AI seating proposals
    )

    if config:
        app.config.update(config)

    # Try loading local overrides (email credentials, secret key, etc.)
    try:
        app.config.from_pyfile("../instance/config.py")
    except FileNotFoundError:
        pass

    # -- Extensions ----------------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to continue."
    mail.init_app(app)

    # -- User loader ---------------------------------------------------------
    from .models import Person

    @login_manager.user_loader
    def load_user(user_id):
        return Person.query.get(int(user_id))

    # -- Blueprints ----------------------------------------------------------
    from .routes.auth          import auth_bp
    from .routes.admin         import admin_bp
    from .routes.members       import members_bp
    from .routes.events        import events_bp
    from .routes.table_planner import table_planner_bp
    from .routes.seating       import seating_bp
    from .routes.import_members import import_bp
    from .routes.portal        import portal_bp
    from .routes.webhooks      import webhooks_bp
    from .routes.broadcast     import broadcast_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp,          url_prefix="/admin")
    app.register_blueprint(members_bp,        url_prefix="/admin/members")
    app.register_blueprint(events_bp,         url_prefix="/admin/events")
    app.register_blueprint(table_planner_bp,  url_prefix="/admin/tables")
    app.register_blueprint(seating_bp,        url_prefix="/admin/seating")
    app.register_blueprint(import_bp,         url_prefix="/admin/import")
    app.register_blueprint(portal_bp,         url_prefix="/portal")
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(broadcast_bp,      url_prefix="/admin/broadcast")

    # -- Bare-domain redirect -------------------------------------------
    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    # -- Custom Jinja2 filters -------------------------------------------
    app.jinja_env.filters["enumerate"] = enumerate
    import math
    app.jinja_env.filters["cos_deg"] = lambda d: math.cos(math.radians(float(d)))
    app.jinja_env.filters["sin_deg"] = lambda d: math.sin(math.radians(float(d)))
    app.jinja_env.filters["person_type_label"] = lambda v: PERSON_TYPE_LABELS.get(v, v)

    # -- Create tables on first run ------------------------------------------
    with app.app_context():
        db.create_all()
        _auto_migrate()
        _seed_default_rules()
        _ensure_admin()

    return app


def _auto_migrate():
    """Safely add any new columns that don't exist yet in the database."""
    from .models import db
    migrations = [
        ("events",  "price_per_person", "NUMERIC(10,2)"),
        ("persons", "gender",           "VARCHAR(10)"),
        ("rsvp_guests", "gender",       "VARCHAR(10)"),
        ("rsvps", "payment_status",   "VARCHAR(20) DEFAULT 'unpaid'"),
        ("rsvps", "amount_paid",      "NUMERIC(10,2)"),
        ("rsvps", "payment_note",     "VARCHAR(200)"),
        ("persons", "invite_token",    "VARCHAR(64)"),
        ("persons", "invite_sent_at",  "DATETIME"),
        ("persons", "invite_opened_at",  "DATETIME"),
        ("persons", "email_bounced_at",  "DATETIME"),
        ("persons", "email_bounce_type", "VARCHAR(50)"),
        ("event_promotion_sends", "opened_at", "DATETIME"),
        ("wine_tags", "course",        "INTEGER DEFAULT 1"),
        ("persons", "person_type_updated_at",  "DATETIME"),
        ("persons", "dietary_tags_updated_at", "DATETIME"),
        ("events", "chef_name",       "VARCHAR(200)"),
        ("events", "paypal_link",     "VARCHAR(500)"),
        ("events", "menu_finalized",  "BOOLEAN DEFAULT 0"),
        ("events", "paypal_price_per_person", "NUMERIC(10,2)"),
        ("rsvps", "officer_rank",       "INTEGER"),
        ("rsvp_guests", "is_officer",    "BOOLEAN DEFAULT 0"),
        ("rsvp_guests", "officer_title", "VARCHAR(200)"),
        ("rsvp_guests", "officer_rank",  "INTEGER"),
        ("wine_tags", "color", "VARCHAR(10)"),
        ("events", "wine_list_updated_at",       "DATETIME"),
        ("events", "menu_updated_at",             "DATETIME"),
        ("events", "officer_ranking_updated_at",  "DATETIME"),
        ("events", "booklet_generated_at",        "DATETIME"),
        ("events", "seating_updated_at",           "DATETIME"),
        ("events", "table_cards_generated_at",      "DATETIME"),
        ("events", "charts_generated_at",           "DATETIME"),
        ("events", "charts_visual_printed_at",           "DATETIME"),
        ("events", "charts_by_table_printed_at",         "DATETIME"),
        ("events", "charts_alpha_printed_at",            "DATETIME"),
        ("events", "charts_by_table_allergy_printed_at", "DATETIME"),
        ("events", "seating_accepted_at",           "DATETIME"),
        ("events", "allergies_reviewed_at",         "DATETIME"),
        ("events", "wine_tags_generated_at",        "DATETIME"),
        ("events", "promotion_sent_at",              "DATETIME"),
        ("events", "promotion_send_started_at",       "DATETIME"),
        ("events", "name_badges_generated_at",      "DATETIME"),
    ]
    with db.engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                print(f"Migration: added {table}.{column}")
            except Exception:
                pass  # Column already exists -- fine


def _seed_default_rules():
    """Insert the three permanent seating rules if they don't exist yet."""
    from .models import SeatingRule
    defaults = [
        ("couples_same_table",    "Couples seated at the same table"),
        ("couples_non_adjacent",  "Couples not seated next to each other"),
        ("officer_per_table",     "At least one officer (with partner) per table"),
        ("alternate_genders",     "Alternate genders around each table"),
        ("guests_with_host",      "Guests seated at the same table as their host member"),
    ]
    for rule_type, description in defaults:
        if not SeatingRule.query.filter_by(rule_type=rule_type).first():
            db.session.add(SeatingRule(rule_type=rule_type,
                                       description=description,
                                       is_active=True))
    db.session.commit()


def _ensure_admin():
    """Create a default admin account if none exists."""
    from .models import Person
    if not Person.query.filter_by(is_admin=True).first():
        admin = Person(
            person_type="member",
            first_name="Admin",
            last_name="User",
            email="admin@chevalier.local",
            can_login=True,
            is_admin=True,
        )
        admin.set_password("changeme")
        db.session.add(admin)
        db.session.commit()
        print("Default admin created: admin@chevalier.local / changeme")
        print("IMPORTANT: Change this password immediately after first login.")
