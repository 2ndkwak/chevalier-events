from flask_mail import _Mail as _MailState, Connection


def broadcast_connection(app):
    """A flask_mail Connection configured for Postmark's Broadcast Message
    Stream, independent of the app's default mail config (which points at
    Postmark's Transactional stream -- see instance/config.py). Use as a
    context manager: `with broadcast_connection(app) as connection: ...`.

    Postmark requires Broadcast sends to use a different SMTP host
    (smtp-broadcasts.postmarkapp.com) than Transactional sends
    (smtp.postmarkapp.com), plus an X-PM-Message-Stream header on each
    message -- see the callers of this function, which set that header.
    Flask-Mail's own Mail extension only supports one server config per
    app (app.extensions["mail"] is a single slot), so this builds a
    second, independent one directly rather than fighting that
    assumption, reusing the same Server API Token as both username and
    password (Postmark's own recommended approach -- no separate
    per-stream SMTP credentials required).

    Relies on flask_mail's internal `_Mail` state class (underscore-
    prefixed, not part of the public API) purely as a plain settings
    container -- it has no behavior of its own beyond holding these
    fields, so this is a low-risk dependency, but worth knowing if a
    future flask_mail upgrade ever restructures it."""
    state = _MailState(
        server=app.config["POSTMARK_BROADCAST_SMTP_SERVER"],
        username=app.config["POSTMARK_SERVER_TOKEN"],
        password=app.config["POSTMARK_SERVER_TOKEN"],
        port=app.config.get("MAIL_PORT", 587),
        use_tls=app.config.get("MAIL_USE_TLS", True),
        use_ssl=app.config.get("MAIL_USE_SSL", False),
        default_sender=app.config.get("MAIL_DEFAULT_SENDER"),
        debug=0,
        max_emails=None,
        suppress=app.config.get("MAIL_SUPPRESS_SEND", False),
        ascii_attachments=False,
    )
    return Connection(state)
