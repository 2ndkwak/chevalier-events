"""
Sep 2026 -- shared helpers for the Photos & Documents event-upload gallery.
Used by portal.py (upload/download), events.py (cleanup on event delete),
and the admin delete-single-upload route.

Files live under instance/uploads/, NOT under static/ or any other
web-served path -- the only way to reach one is through portal.py's
login-gated download route, which checks the current user is authenticated
before calling send_file. There is no public URL to a raw uploaded file.
"""
import os
import uuid
from flask import current_app

# Magic-byte signatures for the allowed types -- a lightweight check that
# the file's actual content matches its extension, not just its claimed
# name. Catches the "renamed .exe to .jpg" class of attack without pulling
# in a full MIME-sniffing library (python-magic needs a system libmagic
# package this server doesn't have installed, and isn't worth the extra
# deployment step for what's fundamentally a basic sanity check here).
SIGNATURES = {
    "jpg":  [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "png":  [b"\x89PNG\r\n\x1a\n"],
    "heic": [b"ftyp"],           # appears a few bytes in, checked specially below
    "pdf":  [b"%PDF-"],
    "docx": [b"PK\x03\x04"],     # docx/pages are both zip containers
    "pages": [b"PK\x03\x04"],
}


def upload_dir():
    """instance/uploads/, created on first use. Kept inside Flask's own
    instance_path (same parent as the sqlite db) rather than a fixed
    absolute path, so it moves correctly if the app is ever relocated."""
    d = os.path.join(current_app.instance_path, "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def allowed_extension(filename):
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower()
    from .models import ALLOWED_UPLOAD_EXTENSIONS
    return ext if ext in ALLOWED_UPLOAD_EXTENSIONS else None


def signature_matches(ext, header_bytes):
    """header_bytes: the first 32 bytes or so of the uploaded file."""
    if ext == "heic":
        # HEIC's signature sits at byte offset 4, not the very start.
        return b"ftyp" in header_bytes[:16]
    sigs = SIGNATURES.get(ext, [])
    return any(header_bytes.startswith(s) for s in sigs)


def save_upload_file(file_storage, ext):
    """Writes the uploaded file to disk under a random, unguessable name.
    Returns (stored_filename, file_size). Caller is responsible for the
    EventUpload database row -- this only handles the bytes on disk."""
    stored_filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(upload_dir(), stored_filename)
    file_storage.save(path)
    file_size = os.path.getsize(path)
    return stored_filename, file_size


def delete_upload_file(stored_filename):
    """Best-effort delete of the file on disk. Swallows a missing file
    (already gone, or never wrote successfully) rather than raising --
    the database row is the source of truth for whether the upload
    'exists' from the app's perspective, and a delete action should
    still succeed in cleaning up the row even if the disk file is
    already missing for some other reason."""
    path = os.path.join(upload_dir(), stored_filename)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
