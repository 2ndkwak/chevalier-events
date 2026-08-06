"""
Menu booklet generator -- a single landscape sheet, folded vertically into
four panels: front cover, back cover (attendee list), and two inside panels
(wines, menu). Printed on both sides of one sheet and folded down the middle.

Pure rendering module: takes a fully-prepared data dict (see build_booklet_data
in seating.py for how that's assembled from the database) and a font path,
and produces a PDF. No database access happens in this file.

Color convention (confirmed against the original hand-built booklets):
  - Title/role text (an officer's role, "Chevalier", "Honoraire", including
    a partner's own independent title) prints in burgundy.
  - Person names (primary and partner) print in black.
  - Connector words (et, Mme./M.) print in black.
  - Section headers and cover lines: first letter of each "major" word is
    burgundy, minor connector words (et, de, des, du, la, le, les) are
    skipped entirely (neither letter nor rest of word colored).
"""

import io
import os
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfgen import canvas as canvaslib
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, Spacer, Frame
from reportlab.lib.styles import ParagraphStyle

BURGUNDY = colors.HexColor("#6B1A2A")
BURGUNDY_HEX = "#6B1A2A"
GOLD = colors.HexColor("#B8912A")
INK = colors.HexColor("#1E1208")
INK_HEX = "#1E1208"
MUTED = colors.HexColor("#7A6650")
RED_WINE_HEX = "#6B1A2A"

PAGE_W, PAGE_H = landscape(letter)  # 11in x 8.5in
MARGIN = 0.45 * inch
PANEL_W = PAGE_W / 2 - MARGIN * 1.5
PANEL_H = PAGE_H - MARGIN * 2

FR_DAYS = {0: "Lundi", 1: "Mardi", 2: "Mercredi", 3: "Jeudi",
           4: "Vendredi", 5: "Samedi", 6: "Dimanche"}
FR_MONTHS = {1: "Janvier", 2: "Fevrier", 3: "Mars", 4: "Avril",
             5: "Mai", 6: "Juin", 7: "Juillet", 8: "Aout",
             9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Decembre"}

MINOR_WORDS = {"et", "de", "des", "du", "la", "le", "les", "l'", "d'"}


def format_french_date(dt):
    day_name = FR_DAYS[dt.weekday()]
    month_name = FR_MONTHS[dt.month]
    return f"{day_name}, {dt.day} {month_name}, {dt.year}"


def register_font(font_path):
    if font_path and os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont("BookletFont", font_path))
            return "BookletFont"
        except Exception:
            pass
    return "Times-Roman"


def header_markup(text):
    """First letter of each major word in burgundy. Minor connector words
    (et, de, des, du, la, le, les...) are skipped -- EXCEPT the very first
    word of the line, which always gets colored even if it's normally a
    minor word (matches the source booklets: "Les Commandeurs et les
    Officiers" colors "Les", but the second "les" mid-line stays black)."""
    words = text.split(" ")
    out = []
    for i, word in enumerate(words):
        bare = word.strip(",")
        if not bare:
            out.append(word)
            continue
        if i > 0 and bare.lower() in MINOR_WORDS:
            out.append(word)
            continue
        first, rest = word[0], word[1:]
        out.append(f'<font color="{BURGUNDY_HEX}">{first}</font>{rest}')
    return " ".join(out)


def attendee_line_markup(primary_title, primary_name,
                         partner_honorific=None, partner_title=None, partner_name=None,
                         primary_honorific=None):
    """Title and honorific (Mme./M.) are always mutually exclusive, for
    both the primary and partner slot -- anyone with a title (an officer
    role, or independent Chevalier/Aspirant standing) is shown with that
    title only; only someone with no title of their own gets Mme./M.
    instead. Callers are expected to already enforce that exclusivity
    (only ever pass one of title/honorific per person, never both) --
    this function just lays out whichever was given."""
    parts = []
    if primary_title:
        parts.append(f'<font color="{BURGUNDY_HEX}">{primary_title}</font>')
    elif primary_honorific:
        parts.append(primary_honorific)
    parts.append(primary_name)
    line = " ".join(parts)

    if partner_name:
        tail = ["et"]
        if partner_title:
            tail.append(f'<font color="{BURGUNDY_HEX}">{partner_title}</font>')
        elif partner_honorific:
            tail.append(partner_honorific)
        tail.append(partner_name)
        line = line + " " + " ".join(tail)

    return line


def _fits_at_scale(build_fn, scale, w, h, font_name):
    scratch = canvaslib.Canvas(io.BytesIO(), pagesize=(2000, 2000))
    flows = build_fn(scale, font_name)
    frame = Frame(0, 0, w, h, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0, showBoundary=0)
    try:
        frame.addFromList(flows, scratch)
    except Exception:
        return False
    return len(flows) == 0


def fit_scale(build_fn, w, h, font_name, min_scale=0.55, max_scale=1.8, steps=26):
    """Finds the LARGEST scale (from max_scale down to min_scale) at which
    content still fits the panel -- so short content grows to fill the
    available space rather than sitting small with blank space below, and
    long content still shrinks as needed."""
    for i in range(steps):
        scale = max_scale - i * (max_scale - min_scale) / (steps - 1)
        if _fits_at_scale(build_fn, scale, w, h, font_name):
            return scale
    return min_scale


def _measure_flows_height(flows, w, h):
    """Places flowables into a scratch, off-page Frame the same way
    _fits_at_scale does, and reports how much vertical space they actually
    consumed -- summing each flowable's own .wrap() height in isolation
    undercounts this, since it misses the spaceBefore/spaceAfter Frame
    adds between items."""
    scratch = canvaslib.Canvas(io.BytesIO(), pagesize=(2000, 2000))
    frame = Frame(0, 0, w, h, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0, showBoundary=0)
    frame.addFromList(list(flows), scratch)
    return (frame._y1 + h) - frame._y


def draw_panel(c, build_fn, x, y, w, h, font_name, center_vertically=False):
    scale = fit_scale(build_fn, w, h, font_name)
    flows = build_fn(scale, font_name)

    top_padding = 0
    if center_vertically:
        # Center the block in the panel instead of leaving it flush
        # against the top -- most noticeable on a short list (a small
        # event), but harmless to apply generally, since a nearly-full
        # panel just gets nudged a few points either way.
        content_h = _measure_flows_height(build_fn(scale, font_name), w, h)
        if content_h < h:
            top_padding = (h - content_h) / 2

    frame = Frame(x, y, w, h, leftPadding=0, rightPadding=0,
                  topPadding=top_padding, bottomPadding=0, showBoundary=0)
    frame.addFromList(flows, c)
    return scale


def draw_synced_panels(c, wine_build_fn, menu_build_fn, wine_x, menu_x, y, w, h, font_name,
                       course0_heights_measurer):
    """
    Both frames start at the SAME top position, so "Les Vins" and
    "Escriteau" print at the same level. Course 0 (Cocktails) can hold
    different amounts of content on each side now -- wines on one, an
    optional list of hors d'oeuvres on the other -- so whichever side's
    course-0 block is shorter gets an internal spacer (inserted between
    its title and its first course) sized to match the difference,
    pushing "Premier Assiette" down to line up on both sides regardless
    of which one had more under Cocktails. Later courses aren't
    individually re-synced after that.
    """
    def joint_fits(scale):
        wine_h, menu_h = course0_heights_measurer(scale, font_name, w)
        wine_offset = max(0, menu_h - wine_h)
        menu_offset = max(0, wine_h - menu_h)
        wine_ok = _fits_at_scale(lambda s, f: wine_build_fn(s, f, wine_offset), scale, w, h, font_name)
        menu_ok = _fits_at_scale(lambda s, f: menu_build_fn(s, f, menu_offset), scale, w, h, font_name)
        return wine_ok and menu_ok

    scale = 0.55
    max_scale, min_scale, steps = 1.8, 0.55, 26
    for i in range(steps):
        s = max_scale - i * (max_scale - min_scale) / (steps - 1)
        if joint_fits(s):
            scale = s
            break

    wine_h, menu_h = course0_heights_measurer(scale, font_name, w)
    wine_offset = max(0, menu_h - wine_h)
    menu_offset = max(0, wine_h - menu_h)

    wine_flows = wine_build_fn(scale, font_name, wine_offset)
    wine_frame = Frame(wine_x, y, w, h, leftPadding=0, rightPadding=0,
                       topPadding=0, bottomPadding=0, showBoundary=0)
    wine_frame.addFromList(wine_flows, c)

    menu_flows = menu_build_fn(scale, font_name, menu_offset)
    menu_frame = Frame(menu_x, y, w, h, leftPadding=0, rightPadding=0,
                       topPadding=0, bottomPadding=0, showBoundary=0)
    menu_frame.addFromList(menu_flows, c)
    return scale


def _header_style(scale, font_name):
    return ParagraphStyle(
        "header", fontName=font_name, fontSize=10.5 * scale,
        leading=13 * scale, textColor=INK, alignment=TA_CENTER,
        spaceBefore=8 * scale, spaceAfter=4 * scale,
    )


def _body_style(scale, font_name):
    return ParagraphStyle(
        "body", fontName=font_name, fontSize=10 * scale,
        leading=13 * scale, textColor=INK, alignment=TA_CENTER,
        spaceAfter=2 * scale,
    )


def build_attendee_flowables(data, scale, font_name):
    hstyle = _header_style(scale, font_name)
    bstyle = _body_style(scale, font_name)
    flows = []

    sections = [
        ("Les Commandeurs et les Officiers", data.get("officers", [])),
        ("Les Chevaliers", data.get("members", [])),
        ("Honoraire", data.get("honoraires", [])),
        ("Nos Convives", data.get("guest_lines", [])),
        ("Aspirants", data.get("aspirants", [])),
    ]
    for label, lines in sections:
        if not lines:
            continue
        flows.append(Paragraph(f"<u>{header_markup(label)}</u>", hstyle))
        for line_markup in lines:
            flows.append(Paragraph(line_markup, bstyle))
    return flows


def build_wine_flowables(data, scale, font_name, include_title=True, pre_course_spacer=0):
    hstyle = ParagraphStyle(
        "wheader", fontName=font_name, fontSize=13 * scale,
        leading=16 * scale, textColor=INK, alignment=TA_CENTER,
        spaceBefore=2 * scale, spaceAfter=10 * scale,
    )
    chstyle = ParagraphStyle(
        "cheader", fontName=font_name, fontSize=10.5 * scale,
        leading=13 * scale, textColor=INK, alignment=TA_CENTER,
        spaceBefore=10 * scale, spaceAfter=4 * scale,
    )
    bstyle = ParagraphStyle(
        "wbody", fontName=font_name, fontSize=9.5 * scale,
        leading=12.5 * scale, textColor=INK, alignment=TA_CENTER,
        spaceAfter=2 * scale,
    )
    flows = []
    if include_title:
        flows.append(Paragraph(f"<u>{header_markup('Les Vins')}</u>", hstyle))

    wine_courses = data.get("wine_courses", [])
    # Identify course 0 (Cocktails) by its actual course NUMBER, not by
    # matching its label text -- the label is freely editable (e.g.
    # renamed to "Transmis Hors d'Oeuvres" to match the menu side), so
    # string-matching "cocktail"/"cocktails" silently breaks the moment
    # someone customizes it, which is exactly the point of course 0
    # allowing a custom label in the first place.
    has_course0 = any(c.get("course") == 0 for c in wine_courses)
    # If there's no course-0 wine section at all, the compensating gap
    # (when the menu side has more course-0 content) goes right after the
    # panel title, same as before. Otherwise it goes right after course
    # 0's own block, so "Les Vins" and its course-0 heading stay level
    # with "Escriteau" and its course-0 counterpart on the other side, and
    # only the space before the next course adjusts.
    if not has_course0 and pre_course_spacer > 0:
        flows.append(Spacer(1, pre_course_spacer))

    for course in wine_courses:
        flows.append(Paragraph(f"<u>{header_markup(course['label'])}</u>", chstyle))
        for w in course["wines"]:
            text = w["text"]
            if w.get("color") == "red":
                text = f'<font color="{RED_WINE_HEX}">{text}</font>'
            flows.append(Paragraph(text, bstyle))
        if course.get("course") == 0 and pre_course_spacer > 0:
            flows.append(Spacer(1, pre_course_spacer))
    return flows


def build_menu_flowables(data, scale, font_name, pre_course_spacer=0, include_title=True):
    hstyle = ParagraphStyle(
        "mheader", fontName=font_name, fontSize=13 * scale,
        leading=16 * scale, textColor=INK, alignment=TA_CENTER,
        spaceBefore=2 * scale, spaceAfter=10 * scale,
    )
    chstyle = ParagraphStyle(
        "mcheader", fontName=font_name, fontSize=10.5 * scale,
        leading=13 * scale, textColor=INK, alignment=TA_CENTER,
        spaceBefore=10 * scale, spaceAfter=3 * scale,
    )
    frstyle = ParagraphStyle(
        "mfr", fontName=font_name, fontSize=9.5 * scale,
        leading=12.5 * scale, textColor=INK, alignment=TA_CENTER,
        spaceAfter=1 * scale,
    )
    enstyle = ParagraphStyle(
        "men", fontName=font_name, fontSize=8.5 * scale,
        leading=11 * scale, textColor=MUTED, alignment=TA_CENTER,
        spaceAfter=6 * scale, italic=1,
    )
    flows = []
    if include_title:
        flows.append(Paragraph(f"<u>{header_markup('Escriteau')}</u>", hstyle))

    # Course 0 (Cocktails) can carry several dishes (e.g. a few hors
    # d'oeuvres); every other course still only ever has exactly one. Each
    # dish gets its own French line, with an optional italic English line
    # beneath it, the same as any other course.
    menu_courses = [c for c in data.get("menu_by_course", []) if c.get("dishes")]
    has_course0 = any(c.get("course") == 0 for c in menu_courses)
    # If course 0 has no real content (the traditional blank-Cocktails
    # case), the compensating gap goes right after the panel title, same
    # as before. Otherwise it goes right after course 0's own content, so
    # "Escriteau" and its course-0 heading stay level with "Les Vins" and
    # Cocktails on the other side, and only the space before Premier
    # Assiette adjusts.
    if not has_course0 and pre_course_spacer > 0:
        flows.append(Spacer(1, pre_course_spacer))

    for course in menu_courses:
        flows.append(Paragraph(f"<u>{header_markup(course['label'])}</u>", chstyle))
        for dish in course["dishes"]:
            if dish.get("dish_french"):
                flows.append(Paragraph(dish["dish_french"], frstyle))
            if dish.get("dish_english"):
                flows.append(Paragraph(f"<i>{dish['dish_english']}</i>", enstyle))
        if course.get("course") == 0 and pre_course_spacer > 0:
            flows.append(Spacer(1, pre_course_spacer))
    return flows


def _measure_course0_heights(data):
    """Returns a function (scale, font_name, w) -> (wine_h, menu_h): how
    tall each side's course-0 (Cocktails) block actually is, including its
    own "Les Vins"/"Escriteau" title. Used to reconcile the two sides so
    both the course-0 heading itself AND "Premier Assiette" line up,
    regardless of which side has more content under Cocktails -- wine
    almost always does, but course 0 can now carry its own hors d'oeuvres
    too, sometimes more, sometimes fewer than the number of cocktail
    wines.

    Measures by actually placing the real flowables (the same functions
    the real render uses) into a scratch, off-page Frame and reading back
    how much space they consumed -- summing each paragraph's own .wrap()
    height in isolation was tried first and reliably undercounts real
    inter-paragraph spacing once there's more than a line or two of
    content, which showed up as a real, measurable misalignment."""
    def _frame_height(flows, w):
        scratch = canvaslib.Canvas(io.BytesIO(), pagesize=(2000, 2000))
        frame = Frame(0, 0, w, 5000, leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0, showBoundary=0)
        frame.addFromList(list(flows), scratch)
        return 5000 - frame._y

    def measurer(scale, font_name, w):
        course0_wine = [c for c in data.get("wine_courses", []) if c.get("course") == 0]
        wine_flows = build_wine_flowables({"wine_courses": course0_wine}, scale, font_name,
                                          include_title=True)
        wine_h = _frame_height(wine_flows, w)

        course0 = next((c for c in data.get("menu_by_course", [])
                        if c.get("course") == 0 and c.get("dishes")), None)
        menu_flows = build_menu_flowables({"menu_by_course": [course0] if course0 else []},
                                          scale, font_name, pre_course_spacer=0, include_title=True)
        menu_h = _frame_height(menu_flows, w)

        return wine_h, menu_h
    return measurer


def draw_cover(c, data, x, y, w, h, font_name):
    """Cover panel: logo vertically centered in the panel, org name block
    above it, event title/details below it, whole thing scaled to fill
    the available height (grows for a sparse event, shrinks for a busy
    one) rather than sitting at a fixed size with leftover blank space."""

    def line_width(text, size):
        return c.stringWidth(text, font_name, size)

    def draw_header_line(cy, size, text):
        words = text.split(" ")
        c.setFont(font_name, size)
        total_w = line_width(text, size)
        cx = x + w / 2 - total_w / 2
        for i, word in enumerate(words):
            bare = word.strip(",")
            skip = (i > 0 and bare.lower() in MINOR_WORDS) or not bare
            if skip:
                c.setFillColor(INK)
                c.drawString(cx, cy, word)
                cx += line_width(word, size)
            else:
                c.setFillColor(BURGUNDY)
                c.drawString(cx, cy, word[0])
                cx += line_width(word[0], size)
                c.setFillColor(INK)
                c.drawString(cx, cy, word[1:])
                cx += line_width(word[1:], size)
            cx += line_width(" ", size)

    top_lines = [(13, "Confrerie des Chevaliers du"), (13, "Tastevin"),
                (13, "Sous-Commanderie de Cleveland")]

    bottom_lines = [(20, data["event_title"])] if data.get("event_title") else []
    if data.get("event_date_str"):
        bottom_lines.append((10.5, data["event_date_str"]))
    if data.get("venue_name"):
        bottom_lines.append((10.5, data["venue_name"]))
    if data.get("chef_name"):
        bottom_lines.append((10, f"Chef de Cuisine {data['chef_name']}"))
    if data.get("hosts"):
        bottom_lines.append((10, data["hosts"]))

    logo_path = data.get("logo_path")
    has_logo = bool(logo_path and os.path.exists(logo_path))

    def block_height(lines, scale, line_gap_factor):
        return sum(size * scale * line_gap_factor for size, _ in lines)

    top_gap_factor, bottom_gap_factor = 1.3, 1.45
    base_logo_h = 1.6 * inch
    gap_above_logo, gap_below_logo = 0.15 * inch, 0.2 * inch

    def total_height_at(scale):
        th = block_height(top_lines, scale, top_gap_factor)
        bh = block_height(bottom_lines, scale, bottom_gap_factor)
        logo_h = base_logo_h * scale if has_logo else 0
        gaps = (gap_above_logo + gap_below_logo) * scale if has_logo else 0
        return 2 * max(th, bh) + logo_h + gaps

    def max_line_width_at(scale):
        # The org-name lines are always short enough to fit, but the event
        # title (and occasionally venue name) can be long -- the height-only
        # search below previously had no idea how wide any line actually
        # was, so a short event (few detail lines) could scale the title up
        # well past the panel's actual width and run off the page.
        all_lines = top_lines + bottom_lines
        if not all_lines:
            return 0
        return max(line_width(text, size * scale) for size, text in all_lines)

    available_w = w * 0.94  # small margin so text doesn't print flush to the panel edge

    scale = 0.6
    for i in range(30):
        s = 1.8 - i * (1.8 - 0.6) / 29
        if total_height_at(s) <= h and max_line_width_at(s) <= available_w:
            scale = s
            break

    logo_h = base_logo_h * scale if has_logo else 0
    top_h = block_height(top_lines, scale, top_gap_factor)
    bottom_h = block_height(bottom_lines, scale, bottom_gap_factor)

    panel_center_y = y + h / 2
    logo_top = panel_center_y + logo_h / 2
    logo_bottom = panel_center_y - logo_h / 2

    cy = logo_top + (gap_above_logo * scale if has_logo else 0) + top_h
    for size, text in top_lines:
        line_h = size * scale * top_gap_factor
        cy -= line_h
        draw_header_line(cy + line_h * 0.25, size * scale, text)

    if has_logo:
        logo_w = logo_h * 0.85
        try:
            c.drawImage(logo_path, x + w / 2 - logo_w / 2, logo_bottom,
                       width=logo_w, height=logo_h,
                       preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    cy = logo_bottom - (gap_below_logo * scale if has_logo else 0)
    for size, text in bottom_lines:
        line_h = size * scale * bottom_gap_factor
        cy -= line_h
        draw_header_line(cy + line_h * 0.25, size * scale, text)


def generate(data, font_path, output_path):
    font_name = register_font(font_path)
    c = canvaslib.Canvas(output_path, pagesize=landscape(letter))

    left_x = MARGIN
    right_x = PAGE_W / 2 + MARGIN * 0.5
    panel_y = MARGIN

    draw_panel(c, lambda s, f: build_attendee_flowables(data, s, f),
              left_x, panel_y, PANEL_W, PANEL_H, font_name, center_vertically=True)
    draw_cover(c, data, right_x, panel_y, PANEL_W, PANEL_H, font_name)

    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.setDash(3, 3)
    c.line(PAGE_W / 2, MARGIN * 0.3, PAGE_W / 2, PAGE_H - MARGIN * 0.3)
    c.setDash()
    c.showPage()

    draw_synced_panels(
        c,
        lambda s, f, off=0: build_wine_flowables(data, s, f, pre_course_spacer=off),
        lambda s, f, off=0: build_menu_flowables(data, s, f, off),
        left_x, right_x, panel_y, PANEL_W, PANEL_H, font_name,
        _measure_course0_heights(data),
    )

    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.setDash(3, 3)
    c.line(PAGE_W / 2, MARGIN * 0.3, PAGE_W / 2, PAGE_H - MARGIN * 0.3)
    c.setDash()
    c.showPage()
    c.save()
