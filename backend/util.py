from datetime import datetime, timezone


def utcnow():
    """Current UTC time as a naive datetime (no tzinfo attached) -- a
    drop-in replacement for the deprecated datetime.utcnow(), same value
    and same naive semantics, just computed via the non-deprecated API.

    Deliberately kept naive, NOT genuinely timezone-aware, even though
    that looks backwards at first glance. SQLite/SQLAlchemy's DateTime
    column silently strips timezone info on every round-trip regardless
    of how a value was written (confirmed by direct testing before this
    helper was written -- even a value written as tz-aware comes back
    from a fresh read as naive). Since this app's comparisons constantly
    mix a freshly-computed "now" against a value just read from the
    database (staleness checks, invite cutoffs, etc.), making "now"
    genuinely tz-aware while every DB-read value stays naive would raise
    "can't compare offset-naive and offset-aware datetimes" throughout
    the app. Keeping this naive-but-correctly-UTC avoids that entirely.

    If a display feature ever needs to show a timestamp in the viewer's
    local time (e.g. converting to their browser's timezone), that's a
    separate, deliberate step at serialization time -- explicitly
    treating the naive value as UTC there (e.g. appending "Z" to an ISO
    string) -- not something this helper should try to solve by itself."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
