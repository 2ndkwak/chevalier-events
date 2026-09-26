"""
Shared sizing for joint events, where our logo and a partner organization's
logo print side by side (menu booklet cover, table name cards, guest name
badges).

The goal is that neither logo reads as the "bigger" organization. Matching
height or width doesn't achieve that, since the logos have different shapes
(ours is taller than wide, the Commanderie de Bordeaux's is nearly square).
Matching the area of the image FILES doesn't either, because the files carry
different amounts of blank border -- ours is only about two-thirds artwork,
theirs almost entirely artwork. So sizes here are computed to give equal
VISIBLE artwork area, measured from each file's non-white content.
"""
from PIL import Image, ImageOps


def content_fraction(path, threshold=25):
    """Fraction of the image's area inside the bounding box of its
    non-white content (1.0 = no blank border at all)."""
    im = Image.open(path).convert("L")
    mask = ImageOps.invert(im).point(lambda v: 255 if v > threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return 1.0
    return ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / float(im.size[0] * im.size[1])


def aspect(path):
    w, h = Image.open(path).size
    return w / float(h)


def partner_size_for(our_path, our_w, our_h, partner_path):
    """(width, height) to draw the partner's logo file at, so its visible
    artwork has the same area as ours drawn at our_w x our_h. Any unit --
    inches, points -- as long as it's consistent."""
    target_content = our_w * our_h * content_fraction(our_path)
    drawn_area = target_content / content_fraction(partner_path)
    a = aspect(partner_path)
    h = (drawn_area / a) ** 0.5
    return h * a, h


def combined_logo_png(our_path, our_w, partner_path, gap, dpi=600):
    """Our logo and the partner's side by side as ONE image, with an exact
    gap between them, bottoms aligned. Sizes in inches; the partner's is
    computed with partner_size_for and capped at our logo's height.

    Used for the Word-based table name cards: two separate inline pictures
    in a Word paragraph get spaced however the rendering program decides
    (LibreOffice, for one, visibly adds its own gap), whereas a single
    pre-composed image prints identically everywhere.

    Returns (png_bytes_io, total_width_in, height_in)."""
    import io
    our_h = our_w / aspect(our_path)
    pw, ph = partner_size_for(our_path, our_w, our_h, partner_path)
    if ph > our_h:
        pw, ph = pw * our_h / ph, our_h
    total_w = our_w + gap + pw
    px = lambda inches: max(1, int(round(inches * dpi)))
    canvas = Image.new("RGB", (px(total_w), px(our_h)), "white")
    ours = Image.open(our_path).convert("RGB").resize((px(our_w), px(our_h)), Image.LANCZOS)
    theirs = Image.open(partner_path).convert("RGB").resize((px(pw), px(ph)), Image.LANCZOS)
    canvas.paste(ours, (0, 0))
    canvas.paste(theirs, (px(our_w + gap), px(our_h) - px(ph)))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", dpi=(dpi, dpi))
    buf.seek(0)
    return buf, total_w, our_h
