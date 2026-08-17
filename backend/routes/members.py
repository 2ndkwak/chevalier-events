from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, jsonify, current_app)
from flask_login import login_required
from ..models import db, Person, DietaryTag, RSVP, EventPromotionSend
from ..routes.admin import admin_required
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
from ..util import utcnow
import secrets, string
import threading
import sys
import traceback

# Under gunicorn, stdout isn't connected to a terminal, so Python
# block-buffers it by default -- print() statements from a background
# thread would otherwise sit invisible in a buffer indefinitely. Same
# fix already used in routes/seating.py and routes/events.py.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

members_bp = Blueprint("members", __name__)


# --- LIST ---------------------------------------------------------------------

@members_bp.route("/")
@login_required
@admin_required
def list_members():
    q      = request.args.get("q", "").strip()
    ftype  = request.args.get("type", "all")   # all | member | partner
    sort   = request.args.get("sort", "last_name")

    query = Person.query.filter(Person.person_type.in_(['member', 'honoraire', 'aspirant', 'partner', 'partner_member_chevalier', 'partner_non_member_chevalier']))

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Person.first_name.ilike(like),
                   Person.last_name.ilike(like),
                   Person.email.ilike(like),
                   Person.officer_role.ilike(like))
        )
    if ftype in ('member', 'honoraire', 'aspirant', 'partner', 'partner_member_chevalier', 'partner_non_member_chevalier'):
        query = query.filter_by(person_type=ftype)

    if sort == "first_name":
        query = query.order_by(Person.first_name, Person.last_name)
    elif sort == "officer":
        # Officers first (grouped by their role), then everyone else A-Z
        query = query.order_by(Person.is_officer.desc(), Person.officer_role,
                               Person.last_name, Person.first_name)
    else:
        query = query.order_by(Person.last_name, Person.first_name)

    persons = query.all()
    return render_template("admin/members/list.html",
                           persons=persons, q=q, ftype=ftype, sort=sort)


# --- ADD ----------------------------------------------------------------------

@members_bp.route("/add", methods=["GET", "POST"])
@login_required
@admin_required
def add_member():
    if request.method == "POST":
        person = _person_from_form(Person(), request.form)
        db.session.add(person)
        db.session.commit()
        flash(f"{person.display_name} added successfully.", "success")
        return redirect(url_for("members.edit_member", person_id=person.id))
    return render_template("admin/members/form.html", person=None,
                           all_persons=_linkable_persons(None),
                           all_tags=DietaryTag.query.order_by(DietaryTag.label).all())


# --- EDIT ---------------------------------------------------------------------

@members_bp.route("/<int:person_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_member(person_id):
    person = Person.query.get_or_404(person_id)
    if request.method == "POST":
        old_email = person.email
        _person_from_form(person, request.form)
        if person.email != old_email and person.email_bounced_at:
            # A corrected address deserves a fresh start rather than
            # showing "bounced" forever because of the old, wrong one --
            # see the Aug 2026 bounce-tracking work.
            person.email_bounced_at  = None
            person.email_bounce_type = None
        db.session.commit()
        flash(f"{person.display_name} updated.", "success")
        return redirect(url_for("members.edit_member", person_id=person.id))
    return render_template("admin/members/form.html", person=person,
                           all_persons=_linkable_persons(person),
                           all_tags=DietaryTag.query.order_by(DietaryTag.label).all())


# --- DELETE -------------------------------------------------------------------

@members_bp.route("/<int:person_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_member(person_id):
    person = Person.query.get_or_404(person_id)
    active_rsvps = RSVP.query.filter(
        RSVP.person_id == person_id,
        RSVP.status.in_(["confirmed", "waitlist", "promoted"])
    ).all()
    if active_rsvps:
        event_names = ", ".join(sorted({r.event.title for r in active_rsvps}))
        flash(
            f"Cannot delete {person.display_name} -- they have an active RSVP "
            f"on: {event_names}. Cancel or remove those RSVPs first, then try "
            f"deleting again.",
            "error"
        )
        return redirect(url_for("members.edit_member", person_id=person_id))
    # Break partner link first
    if person.partner:
        person.partner.partner_id = None
    # Clean up promotion-send history so no orphaned EventPromotionSend
    # row is left behind pointing at a person_id that no longer exists
    # (that used to crash the RSVP list page's sort -- fixed Aug 2026).
    EventPromotionSend.query.filter_by(person_id=person_id).delete()
    name = person.display_name
    db.session.delete(person)
    db.session.commit()
    flash(f"{name} removed from the database.", "success")
    return redirect(url_for("members.list_members"))


# --- PARTNER LINK -------------------------------------------------------------

@members_bp.route("/<int:person_id>/link-partner", methods=["POST"])
@login_required
@admin_required
def link_partner(person_id):
    person     = Person.query.get_or_404(person_id)
    partner_id = request.form.get("partner_id", type=int)

    # Clear existing link first
    if person.partner_id:
        old_partner = Person.query.get(person.partner_id)
        if old_partner:
            old_partner.partner_id = None

    if partner_id:
        partner = Person.query.get_or_404(partner_id)
        # Clear partner's existing link
        if partner.partner_id:
            prev = Person.query.get(partner.partner_id)
            if prev:
                prev.partner_id = None
        # Set mutual link
        person.partner_id  = partner.id
        partner.partner_id = person.id
        db.session.commit()
        flash(f"{person.display_name} linked with {partner.display_name}.", "success")
    else:
        person.partner_id = None
        db.session.commit()
        flash("Partner link removed.", "success")

    return redirect(url_for("members.edit_member", person_id=person_id))


@members_bp.route("/<int:person_id>/create-partner", methods=["POST"])
@login_required
@admin_required
def create_partner(person_id):
    """Create a brand-new non-member partner and link them."""
    member  = Person.query.get_or_404(person_id)
    partner = Person(person_type="partner")
    _person_from_form(partner, request.form)

    # Clear member's old link
    if member.partner_id:
        old = Person.query.get(member.partner_id)
        if old:
            old.partner_id = None

    db.session.add(partner)
    db.session.flush()   # get partner.id

    member.partner_id  = partner.id
    partner.partner_id = member.id
    db.session.commit()
    flash(f"{partner.display_name} created and linked to {member.display_name}.", "success")
    return redirect(url_for("members.edit_member", person_id=person_id))


# --- LOGIN MANAGEMENT ---------------------------------------------------------

@members_bp.route("/<int:person_id>/login/enable", methods=["POST"])
@login_required
@admin_required
def enable_login(person_id):
    person   = Person.query.get_or_404(person_id)
    password = request.form.get("password") or _random_password()
    person.set_password(password)
    person.can_login = True
    db.session.commit()
    flash(f"Login enabled for {person.display_name}. "
          f"Temporary password: {password}", "success")
    return redirect(url_for("members.edit_member", person_id=person_id))


@members_bp.route("/<int:person_id>/login/disable", methods=["POST"])
@login_required
@admin_required
def disable_login(person_id):
    person = Person.query.get_or_404(person_id)
    person.can_login = False
    db.session.commit()
    flash(f"Login disabled for {person.display_name}.", "success")
    return redirect(url_for("members.edit_member", person_id=person_id))


@members_bp.route("/<int:person_id>/login/reset", methods=["POST"])
@login_required
@admin_required
def reset_password(person_id):
    person   = Person.query.get_or_404(person_id)
    password = _random_password()
    person.set_password(password)
    db.session.commit()
    flash(f"Password reset for {person.display_name}. "
          f"New temporary password: {password}", "success")
    return redirect(url_for("members.edit_member", person_id=person_id))


# --- DIETARY TAG MANAGEMENT -----------------------------------------------------
# Members and admins can type any free-text label into the "allergy / dietary
# tags" field, so near-duplicates and misspellings ("Scallops" vs "No Scallops")
# can build up over time. This screen lets an admin clean that up in one place:
# rename a tag (fixes a typo everywhere it's used, instantly) or merge one tag
# into another (folds a duplicate into the canonical one and removes the
# duplicate), without needing to touch every member's record individually.

@members_bp.route("/tags")
@login_required
@admin_required
def manage_tags():
    tags = DietaryTag.query.order_by(DietaryTag.label).all()
    # Usage count per tag, for display and to decide whether Delete is safe
    tag_usage = {t.id: len(t.persons) + len(t.guests) for t in tags}
    return render_template("admin/members/tags.html", tags=tags, tag_usage=tag_usage)


@members_bp.route("/tags/<int:tag_id>/rename", methods=["POST"])
@login_required
@admin_required
def rename_tag(tag_id):
    tag = DietaryTag.query.get_or_404(tag_id)
    new_label = request.form.get("label", "").strip()
    if not new_label:
        flash("Tag name can't be blank.", "error")
        return redirect(url_for("members.manage_tags"))

    existing = DietaryTag.query.filter(
        db.func.lower(DietaryTag.label) == new_label.lower(),
        DietaryTag.id != tag_id,
    ).first()
    if existing:
        flash(f"'{new_label}' already exists as a separate tag. "
              f"Use Merge instead if you want to combine them.", "error")
        return redirect(url_for("members.manage_tags"))

    old_label = tag.label
    tag.label = new_label
    db.session.commit()
    flash(f"Renamed '{old_label}' to '{new_label}'. This applies everywhere it was used.", "success")
    return redirect(url_for("members.manage_tags"))


@members_bp.route("/tags/<int:tag_id>/merge", methods=["POST"])
@login_required
@admin_required
def merge_tag(tag_id):
    """Merge this tag into another tag: everyone who had this tag now has the
    target tag instead, and this tag is removed. Used to fold a duplicate or
    misspelled tag into the correct canonical one."""
    source = DietaryTag.query.get_or_404(tag_id)
    target_id = request.form.get("target_id", type=int)
    target = DietaryTag.query.get_or_404(target_id) if target_id else None

    if not target or target.id == source.id:
        flash("Pick a different tag to merge into.", "error")
        return redirect(url_for("members.manage_tags"))

    moved = 0
    for person in list(source.persons):
        if target not in person.dietary_tags:
            person.dietary_tags.append(target)
            moved += 1
        person.dietary_tags.remove(source)
    for guest in list(source.guests):
        if target not in guest.dietary_tags:
            guest.dietary_tags.append(target)
            moved += 1
        guest.dietary_tags.remove(source)

    source_label = source.label
    db.session.delete(source)
    db.session.commit()
    flash(f"Merged '{source_label}' into '{target.label}' ({moved} record(s) updated).", "success")
    return redirect(url_for("members.manage_tags"))


@members_bp.route("/tags/<int:tag_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_tag(tag_id):
    tag = DietaryTag.query.get_or_404(tag_id)
    usage = len(tag.persons) + len(tag.guests)
    if usage > 0:
        flash(f"'{tag.label}' is still used by {usage} record(s) -- merge it into "
              f"another tag instead of deleting it.", "error")
        return redirect(url_for("members.manage_tags"))
    label = tag.label
    db.session.delete(tag)
    db.session.commit()
    flash(f"Deleted unused tag '{label}'.", "success")
    return redirect(url_for("members.manage_tags"))


# --- HELPERS ------------------------------------------------------------------

def _person_from_form(person, form):
    """Populate a Person from a form submission."""
    old_person_type = person.person_type
    person.person_type    = form.get("person_type", person.person_type or "member")
    if old_person_type and old_person_type != person.person_type:
        from datetime import datetime
        person.person_type_updated_at = utcnow()
    person.title          = form.get("title", "").strip() or None
    person.first_name     = form.get("first_name", "").strip()
    person.last_name      = form.get("last_name", "").strip()
    person.suffix         = form.get("suffix", "").strip() or None
    person.gender         = form.get("gender", "").strip() or None
    person.email          = form.get("email", "").strip().lower() or None
    person.phone          = form.get("phone", "").strip() or None
    person.home_phone_1   = form.get("home_phone_1", "").strip() or None
    person.address_line1  = form.get("address_line1", "").strip() or None
    person.address_line2  = form.get("address_line2", "").strip() or None
    person.city           = form.get("city", "").strip() or None
    person.province_state = form.get("province_state", "").strip() or None
    person.postal_code    = form.get("postal_code", "").strip() or None
    person.country        = form.get("country", "").strip() or None

    person.address2_label  = form.get("address2_label", "").strip() or None
    person.home_phone_2    = form.get("home_phone_2", "").strip() or None
    person.address2_line1  = form.get("address2_line1", "").strip() or None
    person.address2_line2  = form.get("address2_line2", "").strip() or None
    person.city2           = form.get("city2", "").strip() or None
    person.province_state2 = form.get("province_state2", "").strip() or None
    person.postal_code2    = form.get("postal_code2", "").strip() or None
    person.country2        = form.get("country2", "").strip() or None

    person.notes          = form.get("notes", "").strip() or None
    person.is_officer     = bool(form.get("is_officer"))
    person.officer_role   = form.get("officer_role", "").strip() or None
    person.is_admin       = bool(form.get("is_admin"))

    allergy_labels = form.get("allergy_tags", "").split(",")
    DietaryTag.set_from_labels(person, allergy_labels)

    member_since = form.get("member_since", "").strip()
    if member_since:
        from datetime import date
        try:
            person.member_since = date.fromisoformat(member_since)
        except ValueError:
            pass

    return person


def _linkable_persons(exclude):
    """All members and partners who could be linked as a partner."""
    q = Person.query.filter(Person.person_type.in_(['member', 'honoraire', 'aspirant', 'partner', 'partner_member_chevalier', 'partner_non_member_chevalier']))
    if exclude and exclude.id:
        q = q.filter(Person.id != exclude.id)
    return q.order_by(Person.last_name, Person.first_name).all()


def _random_password(length=10):
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


# --- MEMBER SELF-REGISTRATION (INVITATION) ------------------------------------

@members_bp.route("/<int:person_id>/invite", methods=["POST"])
@login_required
@admin_required
def send_invite(person_id):
    """Generate an invite token and email a set-password link to the member."""
    import secrets
    from datetime import datetime
    from ..email import send_invite_email

    person = Person.query.get_or_404(person_id)
    if not person.email:
        flash("This member has no email address -- add one before sending an invite.", "error")
        return redirect(url_for("members.edit_member", person_id=person_id))

    token = secrets.token_urlsafe(32)
    person.invite_token   = token
    person.invite_sent_at = utcnow()
    person.can_login      = False   # they'll activate on first login
    db.session.commit()

    try:
        send_invite_email(person, token)
        flash(f"Invitation sent to {person.email}.", "success")
    except Exception as e:
        flash(f"Could not send email: {e}", "error")

    return redirect(url_for("members.edit_member", person_id=person_id))


@members_bp.route("/accept-invite/<token>", methods=["GET", "POST"])
def accept_invite(token):
    """Public page: member clicks email link and sets their password."""
    person = Person.query.filter_by(invite_token=token).first_or_404()

    if request.method == "POST":
        pw  = request.form.get("password", "").strip()
        pw2 = request.form.get("password2", "").strip()
        if len(pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif pw != pw2:
            flash("Passwords do not match.", "error")
        else:
            person.set_password(pw)
            person.can_login      = True
            person.invite_token   = None
            person.invite_sent_at = None
            db.session.commit()
            flash("Password set -- you can now sign in.", "success")
            return redirect(url_for("auth.login"))

    return render_template("portal/accept_invite.html", person=person)


def _send_invite_batch(app, person_ids):
    """Shared background worker for bulk_invite() and
    resend_outstanding_invites() -- they differ only in which person_ids
    they hand this. Runs on a background thread so the request that
    kicks it off returns immediately instead of blocking for however
    long the whole batch takes -- see the Aug 2026 promotion-email
    incident (routes/events.py, _send_promotion_batch) this exact
    pattern was built to fix, applied here before it could cause the
    same problem: this is precisely the kind of "invite the whole
    roster" blast a brand-new Sous Commanderie would run soon after
    getting set up.

    Each person's token is committed to the database IMMEDIATELY, right
    after it's generated and BEFORE that person's email is even
    attempted -- not batched at the end of the loop like the code this
    replaced. Two reasons:
      1. Resumability: if this thread dies partway through (crash,
         worker restart, anything), whoever already has a committed
         token is naturally excluded from a future run's candidate
         query (bulk_invite excludes anyone with a token; resend
         excludes anyone whose invite_sent_at is too recent), so a
         retry only reaches whoever still needs it -- no new tracking
         table required, the existing columns already do this job once
         the commit happens at the right time.
      2. Correctness: the OLD batch-commit-at-the-end version could, if
         interrupted after some emails had already gone out but before
         the single final commit, leave a real person holding a dead
         link -- their token was in the sent email but never actually
         saved to the database. Committing per-person before sending
         means a token that goes out in an email is always already
         valid in the database, even if this thread never gets any
         further.

    Reuses one SMTP connection for the whole batch (mail.connect())
    rather than reconnecting per recipient, matching the promotion
    worker's fix for the same slow-reconnect problem.

    No cross-process "already running" lock -- unlike the promotion
    button, there's no single natural place to hang one (this isn't
    scoped to one event), and this is a rare, deliberate admin action
    rather than something someone impatiently re-clicks. Committing
    each person's token before attempting their send shrinks a genuine
    double-launch's danger window down to just the moment between the
    initial candidate query and that person's own commit, rather than
    the full multi-minute duration of the whole batch."""
    base_url = app.config.get("SITE_BASE_URL", "http://localhost:5000")
    with app.app_context(), app.test_request_context(base_url=base_url):
        from ..email import send_invite_email
        sent, failed = 0, 0
        try:
            from ..postmark import broadcast_connection
            broadcast_headers = {"X-PM-Message-Stream": app.config["POSTMARK_BROADCAST_STREAM_ID"]}
            with broadcast_connection(app) as connection:
                for person_id in person_ids:
                    person = Person.query.get(person_id)
                    if not person or not person.email or person.can_login:
                        continue
                    token = secrets.token_urlsafe(32)
                    person.invite_token   = token
                    person.invite_sent_at = utcnow()
                    db.session.commit()
                    try:
                        send_invite_email(person, token, connection=connection,
                                           extra_headers=broadcast_headers)
                        sent += 1
                    except Exception:
                        failed += 1
                        print(f"[invite send] FAILED for person {person_id} "
                              f"({person.email}):", flush=True)
                        traceback.print_exc()
            print(f"[invite send] batch complete: {sent} sent, {failed} failed",
                  flush=True)
        except Exception:
            print("[invite send] batch-level failure (e.g. mail.connect() "
                  "itself failed):", flush=True)
            traceback.print_exc()


@members_bp.route("/bulk-invite", methods=["POST"])
@login_required
@admin_required
def bulk_invite():
    """Send invitation emails to all members with email but no portal access."""
    candidates = Person.query.filter(
        Person.email.isnot(None),
        Person.can_login == False,
        Person.invite_token.is_(None),
        Person.person_type.in_(["member", "honoraire", "aspirant"]),
    ).all()

    if not candidates:
        flash("No members currently need an invitation.", "success")
        return redirect(url_for("members.list_members"))

    person_ids = [p.id for p in candidates]
    app = current_app._get_current_object()
    thread = threading.Thread(target=_send_invite_batch, args=(app, person_ids), daemon=True)
    thread.start()

    flash(f"Sending invitations to {len(person_ids)} member(s) in the "
          f"background -- refresh in a bit to see updated status.", "success")
    return redirect(url_for("members.list_members"))


@members_bp.route("/resend-outstanding-invites", methods=["POST"])
@login_required
@admin_required
def resend_outstanding_invites():
    """Re-send the invitation email to everyone whose invite has been
    outstanding 5+ days -- reuses the exact same per-person token logic as
    a single-person resend (fresh token, fresh invite_sent_at each time),
    just applied to the whole outstanding batch via the shared background
    worker. A short cutoff (5 days) keeps this from immediately re-nagging
    someone invited yesterday the first time this button gets clicked."""
    cutoff = utcnow() - timedelta(days=5)
    candidates = Person.query.filter(
        Person.invite_sent_at.isnot(None),
        Person.can_login == False,
        Person.invite_sent_at <= cutoff,
    ).all()

    if not candidates:
        flash("No outstanding invites are old enough to resend yet.", "success")
        return redirect(url_for("admin.dashboard"))

    person_ids = [p.id for p in candidates]
    app = current_app._get_current_object()
    thread = threading.Thread(target=_send_invite_batch, args=(app, person_ids), daemon=True)
    thread.start()

    flash(f"Resending invitations to {len(person_ids)} outstanding member(s) "
          f"in the background -- refresh in a bit to see updated status.", "success")
    return redirect(url_for("admin.dashboard"))


# -- MEMBER LIST PDF ----------------------------------------------------------

@members_bp.route("/list.pdf")
@login_required
@admin_required
def members_pdf():
    """Generate a PDF of the member list grouped by category."""
    import io, os
    from flask import send_file
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable)
    from reportlab.platypus import Image as RLImage
    from datetime import datetime

    BURGUNDY = colors.HexColor("#6B1A2A")
    GOLD     = colors.HexColor("#B8912A")
    PARCHMENT= colors.HexColor("#F9F5ED")
    MUTED    = colors.HexColor("#7A6650")
    WHITE    = colors.white

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=.75*inch, rightMargin=.75*inch,
        topMargin=.75*inch, bottomMargin=.75*inch,
        title="Member List",
    )

    story = []

    # Header
    logo_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend", "static", "img", "Chevalier_Logo.jpg"
    )
    if os.path.exists(logo_path):
        logo = RLImage(logo_path, width=.6*inch, height=.75*inch)
        story.append(logo)

    title_style = ParagraphStyle("title",
        fontName="Times-Bold", fontSize=16,
        textColor=BURGUNDY, spaceAfter=2)
    sub_style = ParagraphStyle("sub",
        fontName="Times-Italic", fontSize=10,
        textColor=MUTED, spaceAfter=8)

    from backend import create_app as _ca
    from flask import current_app
    chapter = current_app.config.get("CHAPTER_NAME", "Chevalier Events")
    story.append(Paragraph(chapter, title_style))
    story.append(Paragraph(
        f"Member List -- {datetime.now().strftime('%B %d, %Y')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=BURGUNDY, spaceAfter=12))

    # Groups -- Chevaliers includes partner_member_chevalier, since she's a
    # full member of our commanderie in her own right, not just linked to one.
    groups = [
        ("Chevaliers",         ("member", "partner_member_chevalier")),
        ("Members Honoraire",  ("honoraire",)),
        ("Aspirants",          ("aspirant",)),
    ]

    all_persons = Person.query.filter(
        Person.person_type.in_(['member', 'honoraire', 'aspirant', 'partner', 'partner_member_chevalier', 'partner_non_member_chevalier'])
    ).order_by(Person.last_name, Person.first_name).all()

    heading_style = ParagraphStyle("heading",
        fontName="Helvetica-Bold", fontSize=9,
        textColor=WHITE, spaceAfter=0)
    name_style = ParagraphStyle("name",
        fontName="Times-Roman", fontSize=9, leading=12)
    small_style = ParagraphStyle("small",
        fontName="Helvetica", fontSize=8, textColor=MUTED, leading=11)

    col_widths = [2.0*inch, 1.5*inch, 1.0*inch, 1.5*inch, 1.0*inch]

    for group_label, group_types in groups:
        members = [p for p in all_persons if p.person_type in group_types]
        if not members:
            continue

        # Group heading
        story.append(Spacer(1, 8))
        heading_tbl = Table(
            [[Paragraph(f"{group_label}  ({len(members)})", heading_style)]],
            colWidths=[7.0*inch],
        )
        heading_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), BURGUNDY),
            ("TOPPADDING",  (0,0), (-1,-1), 4),
            ("BOTTOMPADDING",(0,0),(-1,-1), 4),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
        ]))
        story.append(heading_tbl)

        # Column headers
        header_row = [
            Paragraph("<b>Name</b>", small_style),
            Paragraph("<b>Email</b>", small_style),
            Paragraph("<b>Phone</b>", small_style),
            Paragraph("<b>Role</b>", small_style),
            Paragraph("", small_style),
        ]

        rows = [header_row]
        for p in members:
            role = p.officer_role if p.is_officer else ("Admin" if p.is_admin else "")
            rows.append([
                Paragraph(p.display_name, name_style),
                Paragraph(p.email or "--", small_style),
                Paragraph(p.phone or "--", small_style),
                Paragraph(role or "--", small_style),
                Paragraph("" , small_style),
            ])
            # Partner row indented below -- only if the partner doesn't
            # already have their own top-level row somewhere in this listing
            # (member/honoraire/aspirant/partner_member_chevalier all do).
            if p.partner and p.partner.person_type in ("partner", "partner_non_member_chevalier"):
                partner_name_style = ParagraphStyle("pname",
                    fontName="Times-Italic", fontSize=8.5,
                    textColor=MUTED, leading=12, leftIndent=10)
                partner_role = "Chevalier" if p.partner.person_type == "partner_non_member_chevalier" else "partner"
                rows.append([
                    Paragraph("+ " + p.partner.display_name, partner_name_style),
                    Paragraph(p.partner.email or "--", small_style),
                    Paragraph(p.partner.phone or "--", small_style),
                    Paragraph(partner_role, small_style),
                    Paragraph("", small_style),
                ])

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), PARCHMENT),
            ("TEXTCOLOR",     (0,0), (-1,0), BURGUNDY),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,0), 8),
            ("BOTTOMPADDING", (0,0), (-1,0), 4),
            ("TOPPADDING",    (0,0), (-1,0), 4),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, colors.HexColor("#F5F0E8")]),
            ("FONTNAME",      (0,1), (-1,-1), "Times-Roman"),
            ("FONTSIZE",      (0,1), (-1,-1), 9),
            ("TOPPADDING",    (0,1), (-1,-1), 3),
            ("BOTTOMPADDING", (0,1), (-1,-1), 3),
            ("GRID",          (0,0), (-1,-1), 0.25, colors.HexColor("#DDD5C5")),
            ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ]))
        story.append(tbl)

    # Footer count
    total = len(all_persons)
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"Total: {total} record{'s' if total != 1 else ''}",
        ParagraphStyle("footer", fontName="Helvetica", fontSize=8, textColor=MUTED)
    ))

    doc.build(story)
    buf.seek(0)
    return send_file(
        buf, mimetype="application/pdf",
        as_attachment=True,
        download_name=f"members_{datetime.now().strftime('%Y%m%d')}.pdf"
    )
