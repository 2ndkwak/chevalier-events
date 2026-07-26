"""
Guest name badge generator -- Avery 74461 (and identical siblings 5383,
5390, 74549): 3.5" W x 2.25" H, 8 per sheet, 2 columns x 4 rows on Letter.

CALIBRATION NOTE: Avery does not publish an official margin/pitch table for
this product on their public site -- only the 3.5x2.25" label size and
"8 per sheet" are documented. The layout below (0.75" side margins, 1.0"
top/bottom margins, zero gap between labels) is derived geometrically: it's
the only combination that divides the 8.5x11" sheet exactly for a 2x4 grid
of 3.5x2.25" labels, and it matches Avery's own description of these sheets
as micro-perforated stock that "tears cleanly" (i.e. no printed gap is
needed between cells -- the perforation is the only separation).

This is a strong derivation, not a copied spec sheet. Print ONE test sheet
on actual Avery 74461 stock and check alignment before running a real batch
-- same rule as every other physical print job in this app. If it's off,
adjust TOP_MARGIN_IN / LEFT_MARGIN_IN below (small, consistent offsets in
one direction usually mean the whole grid needs to shift, not that the
label size itself is wrong).

IMPORTANT: like every other print job in this app, print at
"Actual size / 100%", never "Fit to page".
"""
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

IN = 72.0  # points per inch

PAGE_W_IN = 8.5
PAGE_H_IN = 11.0
PAGE_W = PAGE_W_IN * IN
PAGE_H = PAGE_H_IN * IN

BADGE_W_IN = 3.5
BADGE_H_IN = 2.25
COLS, ROWS = 2, 4
BADGES_PER_SHEET = COLS * ROWS

TOP_MARGIN_IN  = 1.0
LEFT_MARGIN_IN = 0.75

BURGUNDY = (0x6B/255, 0x1A/255, 0x2A/255)
INK      = (0x1E/255, 0x12/255, 0x08/255)


def _badge_origin_in(slot_idx):
    """Bottom-left corner of the given badge slot, in inches from the
    page's bottom-left (PDF coordinate convention)."""
    col = slot_idx % COLS
    row = slot_idx // COLS
    x0 = LEFT_MARGIN_IN + col * BADGE_W_IN
    y0 = PAGE_H_IN - TOP_MARGIN_IN - (row + 1) * BADGE_H_IN
    return x0, y0


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


def _fit_text_block(c, text, font, start_size, min_size, max_lines, max_width_pt):
    """Largest font size (down to min_size) at which text fits within
    max_lines of max_width_pt, wrapping if needed. Long compound names
    (double-barrelled, hyphenated) wrap to a 2nd line rather than either
    overflowing the badge edge or shrinking to the point of being hard to
    read from a few feet away."""
    size = start_size
    lines = [text]
    while size > min_size:
        lines = _wrap_to_width(c, text, font, size, max_width_pt)
        fits = (len(lines) <= max_lines and
                all(c.stringWidth(ln, font, size) <= max_width_pt for ln in lines))
        if fits:
            break
        size -= 0.5
    # Even at min_size, cap at max_lines -- an absurdly long single word
    # would otherwise keep spilling past the width forever.
    return lines[:max_lines], size


def _draw_badge(c, x0_in, y0_in, logo, first_name, last_name):
    x0, y0 = x0_in * IN, y0_in * IN
    cx = x0 + (BADGE_W_IN * IN) / 2
    max_w = (BADGE_W_IN - 0.3) * IN   # usable width, small margin each side

    # Faint cut/registration outline -- helps confirm alignment on a test
    # sheet; effectively invisible once real badge stock (with its own
    # printed border) is used, but harmless either way.
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(0.5)
    c.rect(x0, y0, BADGE_W_IN * IN, BADGE_H_IN * IN)

    # ---- Logo, upper-left corner ----
    inset = 0.14 * IN
    logo_h = 0.55 * IN
    logo_w = logo_h * (logo.getSize()[0] / logo.getSize()[1])
    c.drawImage(logo, x0 + inset, y0 + BADGE_H_IN * IN - inset - logo_h,
               width=logo_w, height=logo_h, mask='auto')

    # ---- Name block: first name large & bold above, last name below ----
    # Both wrap to up to 2 lines and shrink if needed, so a long compound
    # name degrades gracefully instead of overflowing the badge edge.
    fn_lines, fn_size = _fit_text_block(c, first_name, "Helvetica-Bold", 34, 18, 2, max_w)
    ln_lines, ln_size = _fit_text_block(c, last_name, "Helvetica-Bold", 28, 14, 2, max_w)

    # Available vertical space is everything below the logo down to a
    # small bottom margin.
    top_y    = y0 + BADGE_H_IN * IN - inset - logo_h - 4
    bottom_y = y0 + 8

    fn_line_h = fn_size * 1.15
    ln_line_h = ln_size * 1.15
    gap = 6
    block_h = len(fn_lines) * fn_line_h + gap + len(ln_lines) * ln_line_h

    # Center the whole name block in the space below the logo; if it's
    # shorter than the space available, this adds even padding above/below
    # rather than always hugging the top.
    start_y = bottom_y + (top_y - bottom_y - block_h) / 2 + block_h

    y = start_y
    c.setFillColorRGB(*BURGUNDY)
    c.setFont("Helvetica-Bold", fn_size)
    for ln in fn_lines:
        y -= fn_line_h
        c.drawCentredString(cx, y + (fn_line_h - fn_size) * 0.3, ln)
    y -= gap

    c.setFillColorRGB(*INK)
    c.setFont("Helvetica-Bold", ln_size)
    for ln in ln_lines:
        y -= ln_line_h
        c.drawCentredString(cx, y + (ln_line_h - ln_size) * 0.3, ln)


def generate(guests, logo_path, out_path):
    """
    guests: list of dicts, each {"first_name": str, "last_name": str}.
    One badge per guest, one guest per slot, 8 slots per sheet.
    """
    c = canvas.Canvas(out_path, pagesize=(PAGE_W, PAGE_H))
    logo = ImageReader(logo_path)

    if not guests:
        c.save()
        return

    for i, guest in enumerate(guests):
        slot = i % BADGES_PER_SHEET
        if slot == 0 and i > 0:
            c.showPage()
        x0_in, y0_in = _badge_origin_in(slot)
        _draw_badge(c, x0_in, y0_in, logo,
                   guest.get("first_name", ""), guest.get("last_name", ""))

    c.save()
