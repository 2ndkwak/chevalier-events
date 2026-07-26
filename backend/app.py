from flask import Flask, redirect, url_for
from .models import db
from flask_login import LoginManager
from flask_mail import Mail

login_manager = LoginManager()
mail = Mail()

def create_app(config=None):
    app = Flask(__name__,
                template_folder="../frontend/templates",
                static_folder="../frontend/static")

    # -- Default config ------------------------------------------------------
    app.config.update(
        SECRET_KEY="change-this-in-production",
        SQLALCHEMY_DATABASE_URI="sqlite:///../instance/chevalier.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,

        # Mail -- admin fills these in config.py
        MAIL_SERVER="smtp.gmail.com",
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=None,
        MAIL_PASSWORD=None,
        MAIL_DEFAULT_SENDER=None,
        ADMIN_EMAIL=None,           # where RSVP notifications go
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp,          url_prefix="/admin")
    app.register_blueprint(members_bp,        url_prefix="/admin/members")
    app.register_blueprint(events_bp,         url_prefix="/admin/events")
    app.register_blueprint(table_planner_bp,  url_prefix="/admin/tables")
    app.register_blueprint(seating_bp,        url_prefix="/admin/seating")
    app.register_blueprint(import_bp,         url_prefix="/admin/import")
    app.register_blueprint(portal_bp,         url_prefix="/portal")

    # -- Bare-domain redirect -------------------------------------------
    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    # -- Custom Jinja2 filters -------------------------------------------
    app.jinja_env.filters["enumerate"] = enumerate
    import math
    app.jinja_env.filters["cos_deg"] = lambda d: math.cos(math.radians(float(d)))
    app.jinja_env.filters["sin_deg"] = lambda d: math.sin(math.radians(float(d)))

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
        ("wine_tags", "course",        "INTEGER DEFAULT 1"),
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
