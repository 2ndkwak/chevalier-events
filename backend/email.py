from flask import current_app, render_template_string
from flask_mail import Message
import re
from html.parser import HTMLParser


def greeting(person):
    """French salutation matching the person's recorded gender ('M' -> Cher,
    'F' -> Chère). Falls back to the plain 'Dear' when gender is blank,
    unrecognized, or 'Other' -- guessing wrong reads worse than not guessing."""
    g = (person.gender or "").strip().upper()
    if g == "M":
        return f"Cher {person.first_name}"
    if g == "F":
        return f"Chère {person.first_name}"
    return f"Dear {person.first_name}"


class _PlainTextExtractor(HTMLParser):
    """Minimal HTML-to-plain-text walker for the small set of tags Quill's
    "snow" editor (the only source of event.description HTML) actually
    produces: p, br, ul/ol/li, a, and inline formatting tags (strong/em/u/
    span) that plain text just can't represent and are safely dropped."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self._link_href = None
        self._link_text = []
        self._list_stack = []
        self._ol_counters = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._link_href = dict(attrs).get("href")
            self._link_text = []
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_counters[-1] += 1
                self.parts.append(f"\n  {self._ol_counters[-1]}. ")
            else:
                self.parts.append("\n  - ")
        elif tag == "ul":
            self._list_stack.append("ul")
        elif tag == "ol":
            self._list_stack.append("ol")
            self._ol_counters.append(0)

    def handle_endtag(self, tag):
        if tag == "a":
            text = "".join(self._link_text).strip()
            href = self._link_href
            if href and text and href != text:
                self.parts.append(f"{text} ({href})")
            elif href:
                self.parts.append(href)
            else:
                self.parts.append(text)
            self._link_href = None
            self._link_text = []
        elif tag == "p":
            self.parts.append("\n\n")
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            if tag == "ol" and self._ol_counters:
                self._ol_counters.pop()
            self.parts.append("\n")

    def handle_data(self, data):
        if self._link_href is not None:
            self._link_text.append(data)
        else:
            self.parts.append(data)


def html_to_plain_text(html_str):
    """Best-effort conversion of Quill-produced event-description HTML into
    readable plain text, for the non-HTML fallback part of the promotion
    email. A link becomes 'link text (https://...)' so the URL is still
    visible and clickable-as-plain-text in mail clients that auto-link
    bare URLs, even for recipients whose client shows the plain-text part
    instead of the HTML one."""
    if not html_str:
        return ""
    parser = _PlainTextExtractor()
    parser.feed(html_str)
    text = "".join(parser.parts)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _send(msg, sender_key, connection=None):
    """Shared dispatch for every send_* function below: attaches the
    right sender address and reply-to (Aug 2026 Postmark migration --
    see instance/config.py's MAIL_SENDER_EVENTS/MAIL_SENDER_ADMIN/
    MAIL_REPLY_TO, kept as config rather than hardcoded here so each
    Sous Commanderie running their own instance sets their own without
    touching code), then sends via the given connection if one was
    provided (a Broadcast-stream connection, for the two batch workers
    that need one) or the app's default (Transactional) mail extension
    otherwise."""
    msg.sender = current_app.config[sender_key]
    msg.reply_to = current_app.config.get("MAIL_REPLY_TO")
    if connection is not None:
        connection.send(msg)
    else:
        mail = current_app.extensions["mail"]
        mail.send(msg)


def send_admin_rsvp_notification(event, person, action):
    """Notify the admin when a member RSVPs, cancels, or changes guests."""
    admin_email = current_app.config.get("ADMIN_EMAIL")
    if not admin_email:
        return

    subject = f"[Chevalier Events] {person.display_name} {action} -- {event.title}"
    body = f"""
Chevalier Events -- RSVP Notification
======================================

Event:   {event.title}
Date:    {event.event_date.strftime("%A, %B %d, %Y")}
Member:  {person.display_name}
Action:  {action.capitalize()}

Confirmed attendees: {event.confirmed_count}{f' / {event.capacity}' if event.capacity else ''}
Waitlist: {sum(1 for r in event.rsvps if r.status == 'waitlist')}

-- Chevalier Events
""".strip()

    msg = Message(subject=subject, recipients=[admin_email], body=body)
    _send(msg, sender_key="MAIL_SENDER_EVENTS")


def send_event_promotion(event, person, connection=None, extra_headers=None):
    """Send an event announcement email to a single member or partner.
    Sends both an HTML version (so a PayPal/payment link or any other
    link, bold/italic, or list entered in the event's description editor
    actually renders as such -- see the Aug 2026 link-button addition to
    that editor) and a plain-text fallback for clients that don't render
    HTML mail, with links rendered as 'text (url)' so the URL itself is
    still visible and usable there too.

    `connection` is an optional flask_mail Connection (from mail.connect()
    or backend/postmark.py's broadcast_connection()) for reusing one SMTP
    connection across a whole batch instead of opening a new one per
    recipient -- see the Aug 2026 background promotion-send worker in
    routes/events.py, which is where this matters: reconnecting per-email
    was the main reason the original blast took long enough to time out
    in the first place. `extra_headers` lets that same worker attach the
    X-PM-Message-Stream header Postmark needs to route a batch send
    through the Broadcast stream rather than Transactional."""
    if not person.email:
        return

    from flask import url_for, render_template
    event_url = url_for("portal.event_detail", event_id=event.id, _external=True)
    logo_url = url_for("static", filename="img/Chevalier_Logo.jpg", _external=True)

    subject = f"[Chevalier Events] {event.title} -- {event.event_date.strftime('%B %d, %Y')}"

    body = f"""
Dear {person.display_name},

You are cordially invited to:

  {event.title}
  {event.event_date.strftime("%A, %B %d, %Y")}{"  at  " + event.event_date.strftime("%I:%M %p") if event.event_date.strftime("%H:%M") != "00:00" else ""}
  {event.venue_name or ""}
  {event.venue_address or ""}

{html_to_plain_text(event.description)}

{"Dress code: " + event.dress_code if event.dress_code else ""}
{"RSVP by: " + event.rsvp_deadline.strftime("%B %d, %Y") if event.rsvp_deadline else ""}

Please log in to the Chevalier Events portal to RSVP:
{event_url}

Tastevin en main,
Confrerie des Chevaliers du Tastevin
""".strip()

    html_body = render_template("email/event_promotion.html",
                                 person=person,
                                 event=event,
                                 event_url=event_url,
                                 logo_url=logo_url)

    # Metadata Postmark echoes back on every webhook event for this
    # message (bounce, open, etc.), used to identify exactly which
    # (event, person) an incoming webhook is about -- see
    # routes/webhooks.py. Built here, not by each caller, so every path
    # that sends a promotion (single test-copy or the real batch) always
    # carries it; a caller's own extra_headers (e.g. the batch worker's
    # X-PM-Message-Stream) are layered on top, not replaced.
    headers = {
        "X-PM-Metadata-kind": "promotion",
        "X-PM-Metadata-person-id": str(person.id),
        "X-PM-Metadata-event-id": str(event.id),
    }
    if extra_headers:
        headers.update(extra_headers)

    msg = Message(subject=subject, recipients=[person.email], body=body, html=html_body,
                  extra_headers=headers)
    _send(msg, sender_key="MAIL_SENDER_EVENTS", connection=connection)


def send_adhoc_email(subject, body_html, person, adhoc_email_id=None,
                      connection=None, extra_headers=None):
    """Send a free-text broadcast email (Aug 2026 "Send Email" feature)
    to a single member/partner, using the same branded shell as the
    event promotion email. `body_html` is Quill-produced HTML, same
    trust boundary as Event.description -- rendered with |safe in
    email/adhoc.html.

    `adhoc_email_id` is omitted for a "send test copy to myself" send
    (see routes/broadcast.py's send_test()) -- a test send is never
    logged to AdHocEmailSend and carries no adhoc-email-id metadata,
    matching send_promotion_test()'s choice not to touch any real
    send-tracking state. When it IS provided, this also goes out over
    whatever `connection` the caller passed (the Broadcast-stream
    connection, for the real batch send in routes/broadcast.py) rather
    than opening a new one per recipient -- same reasoning as
    send_event_promotion() and send_invite_email() above.

    `extra_headers` lets the batch worker attach the X-PM-Message-Stream
    header Postmark needs to route a batch send through the Broadcast
    stream rather than Transactional."""
    if not person.email:
        return

    from flask import url_for, render_template
    logo_url = url_for("static", filename="img/Chevalier_Logo.jpg", _external=True)

    body = f"""{greeting(person)},

{html_to_plain_text(body_html)}

Tastevin en Main,
Confrerie des Chevaliers du Tastevin
""".strip()

    html_body = render_template("email/adhoc.html",
                                 person=person,
                                 greeting=greeting(person),
                                 subject=subject,
                                 body_html=body_html,
                                 logo_url=logo_url)

    # Metadata Postmark echoes back on every webhook event for this
    # message -- used to identify which (adhoc_email, person) an
    # incoming open webhook is about (see routes/webhooks.py). A test
    # send (adhoc_email_id is None) simply omits the adhoc-email-id key,
    # so an open on a test copy has nothing to match against and is
    # silently ignored by the webhook handler, same as any stray/
    # unrecognized metadata.
    headers = {
        "X-PM-Metadata-kind": "adhoc",
        "X-PM-Metadata-person-id": str(person.id),
    }
    if adhoc_email_id is not None:
        headers["X-PM-Metadata-adhoc-email-id"] = str(adhoc_email_id)
    if extra_headers:
        headers.update(extra_headers)

    msg = Message(subject=subject, recipients=[person.email], body=body, html=html_body,
                  extra_headers=headers)
    _send(msg, sender_key="MAIL_SENDER_ADMIN", connection=connection)


def send_invite_email(person, token, connection=None, extra_headers=None):
    """Send a portal invitation email with a set-password link.

    `connection` is an optional flask_mail Connection (from mail.connect()
    or backend/postmark.py's broadcast_connection()) for reusing one SMTP
    connection across a whole batch instead of opening a new one per
    recipient -- see the Aug 2026 background bulk-invite / resend-
    outstanding-invites worker in routes/members.py, which sends this
    exact way for the same reason the promotion-email worker does:
    reconnecting per-recipient is what made the original promotion blast
    slow enough to time out. `extra_headers` lets that same worker attach
    the X-PM-Message-Stream header Postmark needs to route a batch send
    through the Broadcast stream rather than Transactional."""
    from flask import url_for, render_template

    link = url_for("members.accept_invite", token=token, _external=True)
    logo_url = url_for("static", filename="img/Chevalier_Logo.jpg", _external=True)
    subject = "You're invited to the Chevalier Events portal"

    body = f"""{greeting(person)},

You have been invited to the Chevalier Events member portal, where you can view upcoming events and submit your RSVP online.

To activate your account and set your password, click the link below:

{link}

This link is personal -- please do not share it. If you did not expect this invitation, you may ignore this email.

Tastevin en main,
Confrerie des Chevaliers du Tastevin
""".strip()

    html_body = render_template("email/invite.html",
                                 greeting=greeting(person),
                                 link=link,
                                 logo_url=logo_url)

    # Metadata Postmark echoes back on every webhook event for this
    # message -- used to identify which person an incoming open webhook
    # is about (see routes/webhooks.py). Built here, not by each caller,
    # so every invite path (single send, bulk invite, resend) always
    # carries it; a caller's own extra_headers (e.g. the batch worker's
    # X-PM-Message-Stream) are layered on top, not replaced.
    headers = {
        "X-PM-Metadata-kind": "invite",
        "X-PM-Metadata-person-id": str(person.id),
    }
    if extra_headers:
        headers.update(extra_headers)

    msg = Message(subject=subject, recipients=[person.email], body=body, html=html_body,
                  extra_headers=headers)
    _send(msg, sender_key="MAIL_SENDER_ADMIN", connection=connection)


def send_cancellation_email(person, event, partner_still_confirmed=None, partner_cancel_token=None):
    """Confirm to a member that their reservation has been cancelled.

    If partner_still_confirmed is given (a Person who RSVP'd separately
    and is still confirmed for this event), the email also lets the
    member know and offers a one-click link to cancel the partner's
    reservation too, rather than assuming either way."""
    if not person.email:
        return

    partner_note = ""
    if partner_still_confirmed and partner_cancel_token:
        from flask import url_for
        link = url_for("portal.cancel_partner", token=partner_cancel_token, _external=True)
        partner_note = f"""

{partner_still_confirmed.display_name}'s reservation for this event is still confirmed -- we didn't want to assume you'd want that cancelled too. If you'd like to cancel it as well, click below (valid for 7 days):

{link}"""

    subject = f"Reservation cancelled -- {event.title}"
    body = f"""{greeting(person)},

This confirms your reservation for {event.title} on {event.event_date.strftime('%A, %B %d, %Y')} has been cancelled.{partner_note}

If this was a mistake, or you'd like to be added back to a waitlist, please log in to the Chevalier Events portal.

Tastevin en Main,
Confrerie des Chevaliers du Tastevin
""".strip()

    msg = Message(subject=subject, recipients=[person.email], body=body)
    _send(msg, sender_key="MAIL_SENDER_EVENTS")


def send_partner_cancelled_email(person, event, cancelled_by):
    """Notify a member their own reservation was cancelled -- either
    because their linked partner cancelled (automatic cascade), or
    because their partner used the self-service cancel-partner link
    on their separately-made RSVP.

    If this person has no email on file, there's no one to notify
    directly -- falls back to telling cancelled_by instead, since
    they're the one who can act on it."""
    if not person.email:
        if not cancelled_by.email:
            return  # neither has an email on file -- nothing more we can do
        from flask import current_app
        gs_email = current_app.config.get("ADMIN_EMAIL") or ""
        subject = f"Reservation cancelled -- {event.title}"
        body = f"""{greeting(cancelled_by)},

{person.display_name}'s reservation for {event.title} on {event.event_date.strftime('%A, %B %d, %Y')} has been cancelled. We weren't able to notify {person.display_name} directly since we don't have an email on file for them, so we're letting you know instead.

If you'd only like {person.display_name}'s reservation restored, without your own, please contact the Grand Sen\u00e8chal at {gs_email} and we'll take care of it.

Tastevin en Main,
Confrerie des Chevaliers du Tastevin
""".strip()
        msg = Message(subject=subject, recipients=[cancelled_by.email], body=body)
        _send(msg, sender_key="MAIL_SENDER_EVENTS")
        return

    pronoun = {"M": "his", "F": "her"}.get(cancelled_by.gender, "their")
    subject = f"Reservation cancelled -- {event.title}"
    body = f"""{greeting(person)},

{cancelled_by.display_name} canceled {pronoun} reservation for {event.title} on {event.event_date.strftime('%A, %B %d, %Y')}, and your reservation has been cancelled along with it.

If this was a mistake, or you'd like to be added back to a waitlist, please log in to the Chevalier Events portal.

Tastevin en Main,
Confrerie des Chevaliers du Tastevin
""".strip()

    msg = Message(subject=subject, recipients=[person.email], body=body)
    _send(msg, sender_key="MAIL_SENDER_EVENTS")


def send_waitlist_promotion_email(person, event, rsvp):
    """Offer a promoted waitlist member their provisional seat, with a
    24-hour confirmation link."""
    if not person.email:
        return

    from flask import url_for, render_template
    link = url_for("portal.confirm_promotion", token=rsvp.promotion_token, _external=True)
    logo_url = url_for("static", filename="img/Chevalier_Logo.jpg", _external=True)

    seat_plural = "s" if rsvp.guest_count > 1 else ""
    is_are = "are" if rsvp.guest_count > 1 else "is"

    subject = f"A seat has opened up -- {event.title}"
    body = f"""{greeting(person)},

A seat has opened up for {event.title} on {event.event_date.strftime('%A, %B %d, %Y')}, and you're next on the waitlist. Your seat{seat_plural} {is_are} being held for you.

Please confirm within 24 hours -- if we don't hear from you by then, the seat will be offered to the next person on the list.

Confirm here:
{link}

If you're unable to attend, please let us know as soon as you can using the link above, so the seat can be offered to the next member on the waitlist.

Tastevin en Main,
Confrerie des Chevaliers du Tastevin
""".strip()

    html_body = render_template("email/promotion_offer.html",
                                 greeting=greeting(person),
                                 event_title=event.title,
                                 event_date=event.event_date.strftime("%A, %B %d, %Y"),
                                 seat_plural=seat_plural,
                                 is_are=is_are,
                                 link=link,
                                 logo_url=logo_url)

    msg = Message(subject=subject, recipients=[person.email], body=body, html=html_body)
    _send(msg, sender_key="MAIL_SENDER_EVENTS")


def send_promotion_expired_email(person, event):
    """Let a member know their waitlist offer window lapsed and the
    seat has moved on to the next person in line."""
    if not person.email:
        return

    subject = f"Your waitlist offer has expired -- {event.title}"
    body = f"""{greeting(person)},

Your 24-hour window to confirm the seat that opened up for {event.title} on {event.event_date.strftime('%A, %B %d, %Y')} has passed, so the seat has been offered to the next person on the waitlist.

If you'd still like to attend, please log in to the Chevalier Events portal to rejoin the waitlist.

Tastevin en Main,
Confrerie des Chevaliers du Tastevin
""".strip()

    msg = Message(subject=subject, recipients=[person.email], body=body)
    _send(msg, sender_key="MAIL_SENDER_EVENTS")


def send_admin_promotion_resolved_notification(event, cancelled_by, promoted_person):
    """Notify the GS once a waitlist promotion chain resolves: someone
    cancelled, and the seat has now been confirmed by the person who
    was promoted to fill it."""
    admin_email = current_app.config.get("ADMIN_EMAIL")
    if not admin_email:
        return

    cancelled_line = (f"Cancelled by: {cancelled_by.display_name}\n"
                       if cancelled_by else "")

    subject = f"[Chevalier Events] Waitlist seat filled -- {event.title}"
    body = f"""
Chevalier Events -- Waitlist Notification
==========================================

Event:   {event.title}
Date:    {event.event_date.strftime("%A, %B %d, %Y")}
{cancelled_line}Promoted and confirmed: {promoted_person.display_name}

Confirmed attendees: {event.confirmed_count}{f' / {event.capacity}' if event.capacity else ''}
Waitlist: {sum(1 for r in event.rsvps if r.status == 'waitlist')}

-- Chevalier Events
""".strip()

    msg = Message(subject=subject, recipients=[admin_email], body=body)
    _send(msg, sender_key="MAIL_SENDER_EVENTS")


def send_password_reset_email(person, token):
    """Send a self-service password reset link, valid for 1 hour."""
    if not person.email:
        return

    from flask import url_for, render_template
    link = url_for("auth.reset_password", token=token, _external=True)
    logo_url = url_for("static", filename="img/Chevalier_Logo.jpg", _external=True)

    subject = "Reset your Chevalier Events password"
    body = f"""{greeting(person)},

We received a request to reset the password on your Chevalier Events portal account. Click the link below to choose a new one. This link expires in 1 hour.

{link}

If you didn't request this, you can safely ignore this email -- your password will not be changed.

Tastevin en Main,
Confrerie des Chevaliers du Tastevin
""".strip()

    html_body = render_template("email/reset_password.html",
                                 greeting=greeting(person),
                                 link=link,
                                 logo_url=logo_url)

    msg = Message(subject=subject, recipients=[person.email], body=body, html=html_body)
    _send(msg, sender_key="MAIL_SENDER_ADMIN")
