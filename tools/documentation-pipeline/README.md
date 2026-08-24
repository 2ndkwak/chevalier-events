# Documentation Screenshot Pipeline

Built Aug 15, 2026, during the Postmark-migration documentation refresh
(GS Guide / Technical Reference Rev 3). Lets Claude (or anyone) produce
*real* screenshots of the Chevalier Events app for documentation, instead
of a hand-drawn mockup or an outdated image.

Two complementary techniques live here. Use whichever fits:

## Technique A — reuse what's already real

Before generating anything new, check whether the screen you need already
has an accurate screenshot sitting in the **previous** edition of whatever
document you're updating. PDFs embed their images as raster data you can
pull out directly, full resolution, no regeneration needed:

```bash
pdfimages -list some_old_guide.pdf      # lists every embedded image + which page it's on
pdfimages -png some_old_guide.pdf img   # extracts them all as img-000.png, img-001.png, ...
```

Cross-reference the `-list` output's page numbers against the doc's own
text/captions (`pdftotext -layout some_old_guide.pdf -`) to figure out
which `img-NNN.png` is which screen. Only regenerate (Technique B) the
screens that actually changed since that edition — reusing the rest saves
a lot of time and guarantees pixel-perfect fidelity for anything that
didn't change.

## Technique B — generate fresh ones from the real app

For screens that **did** change, boot the actual app locally and
screenshot the genuine rendered HTML — not a redraw, not a mockup.

**Files:**
- `seed_screenshots.py` — wipes a throwaway SQLite DB and populates it with
  realistic fake data (members, events, RSVPs) matching the names already
  used throughout the existing docs, for visual continuity across
  documentation revisions.
- `run_server.py` — boots the real Flask app (`create_app()`) on
  `127.0.0.1:5050` against that throwaway DB.
- `capture.py` — logs in with Playwright and screenshots whichever real
  admin routes you need, in whatever UI state you need (expanding a panel,
  etc.) before each screenshot.

**Steps, in order:**

1. Copy these three files to the root of the app (next to `run.py`),
   in whatever sandbox/workspace you're already using to read the code.
2. **Never point this at a real database or real credentials.** Write a
   throwaway `instance/config.py`:

   ```python
   SECRET_KEY = "sandbox-screenshot-key-not-real"
   ANTHROPIC_API_KEY = None
   MAIL_SUPPRESS_SEND = True
   MAIL_SERVER = "localhost"
   MAIL_PORT = 25
   MAIL_USE_TLS = False
   MAIL_USERNAME = "test"
   MAIL_PASSWORD = "test"
   MAIL_DEFAULT_SENDER = "test@example.com"
   MAIL_SENDER_EVENTS = "events@example.com"
   MAIL_SENDER_ADMIN = "admin@example.com"
   MAIL_REPLY_TO = "test@example.com"
   POSTMARK_SERVER_TOKEN = "test-token"
   POSTMARK_WEBHOOK_USERNAME = "test"
   POSTMARK_WEBHOOK_PASSWORD = "test"
   ADMIN_EMAIL = "admin@example.com"
   ```

   (Field names will drift as the real app's config does — check
   `instance/config.py.sample` in the actual repo for the current list
   and copy its shape instead of trusting this list blindly.)

3. Make sure there's no `instance/chevalier.db` left over from a real
   deploy sitting in your working copy — `seed_screenshots.py` calls
   `db.drop_all()`.
4. Install exact dependency versions from the app's own `requirements.txt`
   (`pip install --break-system-packages -q Flask==X Flask-Login==X ...`
   — match versions, don't just `pip install Flask`).
5. `python3 seed_screenshots.py`
6. Start the server **in a way that survives the sandbox's per-tool-call
   process boundary** — a plain `&` gets killed when that bash call ends:

   ```bash
   setsid nohup python3 run_server.py > server.log 2>&1 < /dev/null &
   ```

   Then, on the *next* tool call (not the same one), confirm it's still
   up before trying to use it:

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5050/login
   ```

7. `python3 capture.py` — edit its screenshot list for whatever screens
   you actually need this time.
8. `pkill -f run_server.py` when done.
9. Crop/resize as needed (PIL) to match the surrounding document's style
   before embedding — see the crop step used for the event-edit status bar
   in the Aug 15 2026 session, which took a full-page capture down to just
   the relevant UI strip to match the original doc's tighter crops.

## Gotchas hit while building this

- **`wkhtmltoimage`** (the tool used in an even earlier session's version
  of this pipeline) uses an old QtWebKit engine with real CSS Grid / async
  JS / modern-DOM-method gaps. **Playwright + real Chromium** (used here)
  doesn't have those problems and is the better default now that it's
  confirmed available in the sandbox — no need to fight CSS Grid rewrites
  or JS polyfills.
- External CDN scripts (e.g. the Quill rich-text editor loaded from
  `cdnjs.cloudflare.com` on the event edit form) may not be reachable from
  a sandboxed environment's network allowlist. This doesn't break most
  screens, but a screenshot of a screen that specifically needs Quill to
  render (the rich-text description box mid-edit) may show it unstyled or
  broken. Check the sandbox's allowed-domains list before relying on any
  external asset loading correctly.
- Partner links (`Person.partner`) are **not** automatically mutual at the
  ORM/relationship level, even though the real app's UI makes them mutual
  when you link two people through a form. Seed scripts must set
  `a.partner_id = b.id` AND `b.partner_id = a.id` explicitly, or dashboard
  stat cards that count by `partner_id` (e.g. "Partners") will silently
  undercount.
- Event dates in seed data need to be **after** whatever the current
  in-conversation date is, or they won't show up in "Upcoming Events" —
  easy to miss since the seed script runs without error either way.

## Extending this later

If the app's models/routes change enough that this stops working
(new required fields, renamed routes, a login flow change), don't
resurrect this file's exact contents from memory — re-derive the seed
data and routes from the actual current `backend/models.py` and
`backend/routes/*.py`, the same way this version was built. The technique
(boot real app → seed fake data → Playwright the real routes) is the
durable part; the specific field names and routes are not.
