"""
Wine glass tag generator -- RippedSheets 2.75" die-cut wine tags, 6 per sheet.

Calibrated directly against the customer's physical die-cut sheets (measured
from WINE_TAG_TEMPLATE_WITH_CHEVALIER_LOGO.pdf, confirmed against a real
printed+cut sheet). DO NOT change PAGE_W/PAGE_H or the tag center
coordinates without re-measuring a physical sheet -- the whole point of this
file is that it lines up with real die-cut stock.

IMPORTANT: like every other print job in this app, this must be printed at
"Actual size / 100%", never "Fit to page" -- confirmed by hand against a
real sheet. A "Fit to page" print will silently shift every tag off its
die-cut position.
"""
import math
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

IN = 72.0  # points per inch

# Exact page size from the calibrated template (NOT true 11in tall --
# verified against physical die-cut sheets, do not "fix" this to 11in)
PAGE_W = 8.5 * IN
PAGE_H = 10.987 * IN

# Tag centers (inches from bottom-left, PDF coordinate convention)
TAG_CENTERS_IN = [
    (2.369, PAGE_H/IN - 2.240),   # row1 col1
    (6.133, PAGE_H/IN - 2.240),   # row1 col2
    (2.369, PAGE_H/IN - 5.503),   # row2 col1
    (6.133, PAGE_H/IN - 5.503),   # row2 col2
    (2.369, PAGE_H/IN - 8.750),   # row3 col1
    (6.133, PAGE_H/IN - 8.750),   # row3 col2
]
TAGS_PER_SHEET = len(TAG_CENTERS_IN)

R_OUTER_IN = 1.366
R_HOLE_IN  = 0.447

BURGUNDY = (0x6B/255, 0x1A/255, 0x2A/255)


def _wrap_to_width(c, text, font, size, max_width_pt):
    """Greedy word-wrap text to fit within max_width_pt for the given font/size."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if c.stringWidth(trial, font, size) <= max_width_pt:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_tag_text(c, cx_in, cy_in, vintage, domain, appellation, position_num, course_num):
    cx, cy = cx_in * IN, cy_in * IN
    max_w = 1.85 * IN  # usable width close to the hole edge
    font = "Helvetica-Bold"

    # ---- Above the hole: vintage on its own line, domain below it ----
    vintage_size = 10
    vintage = vintage or ""
    while c.stringWidth(vintage, font, vintage_size) > max_w and vintage_size > 6:
        vintage_size -= 0.5

    domain_size = 9
    domain_lines = _wrap_to_width(c, domain, font, domain_size, max_w)
    while (len(domain_lines) > 2 or
           any(c.stringWidth(w, font, domain_size) > max_w for w in domain_lines)) \
          and domain_size > 6:
        domain_size -= 0.5
        domain_lines = _wrap_to_width(c, domain, font, domain_size, max_w)

    c.setFillColorRGB(*BURGUNDY)
    block_lines = ([(vintage, vintage_size)] if vintage else []) + \
                  [(ln, domain_size) for ln in domain_lines]
    y = cy + R_HOLE_IN*IN + 10
    for text, size in reversed(block_lines):
        c.setFont(font, size)
        c.drawCentredString(cx, y, text)
        y += size + 3

    # ---- Below the hole: appellation / vineyard / classification ----
    size2 = 8
    lines = _wrap_to_width(c, appellation, font, size2, max_w)
    while len(lines) > 3 and size2 > 6:
        size2 -= 0.5
        lines = _wrap_to_width(c, appellation, font, size2, max_w)
    c.setFont(font, size2)
    c.setFillColorRGB(0.15, 0.12, 0.08)
    line_h = size2 + 2
    top_y = cy - R_HOLE_IN*IN - 18
    for i, ln in enumerate(lines):
        c.drawCentredString(cx, top_y - i*line_h, ln)

    # ---- Right of hole: course-relative label (e.g. "2/1" = 2nd wine of course 1) ----
    # position_num is already the wine's index within its course, so the label is
    # simply "position/course" -- no cross-referencing against other wines needed.
    # Nudged up from dead-center so the die-cut slit (which runs through the hole's
    # vertical centerline, for the glass stem) doesn't bisect the digits.
    label = f"{position_num}/{course_num}"
    label_size = 20 if len(label) <= 3 else (16 if len(label) <= 5 else 13)
    c.setFont("Helvetica-Bold", label_size)
    c.setFillColorRGB(*BURGUNDY)
    num_x = cx + R_HOLE_IN*IN + (R_OUTER_IN*IN - R_HOLE_IN*IN)/2 + 4
    NUM_Y_NUDGE_IN = 0.12  # lift clear of the slit; adjust here if it needs more/less
    c.drawCentredString(num_x, cy - label_size*0.35 + NUM_Y_NUDGE_IN*IN, label)


def generate(wines, guest_count, background_jpg, out_path):
    """
    wines: list of dicts, each {"position": int, "course": int, "vintage": str,
           "domain": str, "appellation": str}. position is the wine's index
           within its own course (1st, 2nd... of that course) and resets to 1
           at the start of each new course -- it is NOT unique across the
           whole event on its own; (course, position) together are.
    guest_count: number of guests (each gets one tag per wine).

    Printed to match exactly how a server works the table: stand at one
    place setting, apply every wine's tag in serving order (1/1, 2/1, 1/2,
    2/2, 1/3, 2/3, ...) left glass to right, then move to the next place
    setting and repeat. So the full print sequence is guest 1's complete
    wine list, then guest 2's complete wine list, and so on -- flowing
    continuously across sheet boundaries (a guest's set doesn't need to
    start on a fresh sheet). Cut all the sheets and stack them in printed
    order, and the whole pile reads top to bottom in exactly that order.
    """
    # Serving order = course order, then position within that course.
    wines_sorted = sorted(wines, key=lambda w: (w["course"], w["position"]))

    c = canvas.Canvas(out_path, pagesize=(PAGE_W, PAGE_H))
    bg = ImageReader(background_jpg)
    state = {"first_page": True}

    def new_page():
        if not state["first_page"]:
            c.showPage()
        state["first_page"] = False
        c.drawImage(bg, 0, 0, width=PAGE_W, height=PAGE_H)

    def draw_wine_at_slot(wine, slot_idx):
        cx_in, cy_in = TAG_CENTERS_IN[slot_idx]
        _draw_tag_text(c, cx_in, cy_in, wine.get("vintage", ""), wine["domain"],
                       wine["appellation"], wine["position"], wine["course"])

    if not wines_sorted or guest_count <= 0:
        c.save()
        return

    # Full print sequence: guest 1's entire wine list, then guest 2's, etc.
    sequence = [wine for _guest in range(guest_count) for wine in wines_sorted]

    for i in range(0, len(sequence), TAGS_PER_SHEET):
        chunk = sequence[i:i + TAGS_PER_SHEET]
        new_page()
        for slot, wine in enumerate(chunk):
            draw_wine_at_slot(wine, slot)

    c.save()
