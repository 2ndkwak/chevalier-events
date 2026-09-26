"""
Sep 2026 -- GS AI Support feature.

Two halves:
  1. Manual management (this file's HTML routes) -- admin uploads a PDF or
     Word doc, sees the extracted text in an editable preview, and confirms
     before it's saved. Full replace, same pattern as the Menu/Wine List
     CSV uploads elsewhere in the app -- see GSManual.current() in models.py.
  2. The support widget itself (the two JSON routes) -- a GS asks a
     question, gets an answer grounded in whatever manual text is
     currently saved, and can escalate to a human if it didn't help.

Deliberately NOT using retrieval/chunking: the manual runs well under
20K tokens even in full, so the whole thing is simply included in the
Anthropic API call's context on every question. Simpler to build, and
much easier to reason about correctness than a search step would be.
"""
from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, jsonify, current_app)
from flask_login import login_required, current_user
from ..models import db, GSManual
from .admin import admin_required

gs_support_bp = Blueprint("gs_support", __name__)


# --- MANUAL MANAGEMENT (admin HTML pages) -----------------------------------

@gs_support_bp.route("/manual", methods=["GET"])
@login_required
@admin_required
def manual_home():
    return render_template("admin/gs_support/manual.html",
                           manual=GSManual.current(), extracted_text=None,
                           source_filename=None)


@gs_support_bp.route("/manual/extract", methods=["POST"])
@login_required
@admin_required
def manual_extract():
    """Step 1 of the upload flow: pull text out of whatever file was
    uploaded and show it back for review -- nothing is saved yet. Keeps
    extraction (which can go wrong on a messy PDF) and saving (which
    shouldn't happen until a human's looked at the result) as two
    distinct steps, per the Sep 2026 design decision to preview/edit
    before committing."""
    file = request.files.get("manual_file")
    if not file or not file.filename:
        flash("Please choose a file to upload.", "error")
        return redirect(url_for("gs_support.manual_home"))

    filename = file.filename
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(file)
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext in ("docx",):
            import docx
            document = docx.Document(file)
            text = "\n\n".join(p.text for p in document.paragraphs)
        else:
            flash("Please upload a PDF or Word (.docx) file.", "error")
            return redirect(url_for("gs_support.manual_home"))
    except Exception as e:
        flash(f"Couldn't read that file: {e}", "error")
        return redirect(url_for("gs_support.manual_home"))

    if not text.strip():
        flash("No text could be extracted from that file -- it may be a "
              "scanned/image-only PDF, which this tool can't read.", "error")
        return redirect(url_for("gs_support.manual_home"))

    return render_template("admin/gs_support/manual.html",
                           manual=GSManual.current(), extracted_text=text,
                           source_filename=filename)


@gs_support_bp.route("/manual/save", methods=["POST"])
@login_required
@admin_required
def manual_save():
    """Step 2: save whatever's currently in the (possibly hand-edited)
    preview textarea as the new active manual. Full replace -- always a
    brand new row rather than editing the existing one in place, so
    uploaded_at/uploaded_by always reflect this specific save."""
    text = request.form.get("manual_text", "").strip()
    if not text:
        flash("Manual text can't be empty.", "error")
        return redirect(url_for("gs_support.manual_home"))

    entry = GSManual(
        text_content=text,
        source_filename=request.form.get("source_filename") or None,
        uploaded_by_id=current_user.id,
    )
    db.session.add(entry)
    db.session.commit()
    flash("GS Manual updated. The support widget will use this version "
          "immediately.", "success")
    return redirect(url_for("gs_support.manual_home"))


# --- SUPPORT WIDGET (JSON, called from the floating widget in base.html) ---

SYSTEM_PROMPT_TEMPLATE = """You are a support assistant for Grand Sénéchals \
(GSs) using the Chevalier Events admin app. Answer ONLY using the GS Manual \
text provided below -- do not use outside knowledge about this app, since \
the manual is the single source of truth and outside guesses could be wrong \
for this specific installation.

If the manual doesn't cover the question, or you're not confident the \
manual actually answers it, say so plainly and suggest the GS use the \
Escalate button rather than guessing. Keep answers concise and practical -- \
a GS mid-task wants the actual steps or fact, not a restatement of the \
question.

--- GS MANUAL ---
{manual_text}
--- END GS MANUAL ---
"""


@gs_support_bp.route("/ask", methods=["POST"])
@login_required
@admin_required
def ask():
    manual = GSManual.current()
    if not manual:
        return jsonify({"error": "No GS Manual has been uploaded yet. "
                        "An admin needs to upload one from Manage GS Manual "
                        "before this can answer questions."}), 400

    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured. "
                        "Please add ANTHROPIC_API_KEY to your "
                        "instance/config.py file."}), 400

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    # Prior turns in this widget conversation, so follow-up questions
    # ("what about X instead?") work -- sent by the widget's own JS from
    # its in-browser (sessionStorage) history, capped client-side.
    history = data.get("history") or []
    if not question:
        return jsonify({"error": "Please type a question."}), 400

    messages = []
    for turn in history:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    try:
        import requests as req
        r = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "system": SYSTEM_PROMPT_TEMPLATE.format(manual_text=manual.text_content),
                "messages": messages,
            },
            timeout=60,
        )
        d = r.json()
        answer = "".join(block.get("text", "") for block in d.get("content", [])
                         if block.get("type") == "text")
        if not answer:
            err = d.get("error", {}).get("message", "The AI didn't return an answer.")
            return jsonify({"error": err}), 502
    except Exception as e:
        return jsonify({"error": f"Couldn't reach the AI support service: {e}"}), 502

    return jsonify({"answer": answer})


@gs_support_bp.route("/escalate", methods=["POST"])
@login_required
@admin_required
def escalate():
    data = request.get_json(silent=True) or {}
    question  = (data.get("question") or "").strip()
    ai_answer = (data.get("ai_answer") or "").strip()
    gs_note   = (data.get("note") or "").strip() or None

    if not question:
        return jsonify({"error": "Nothing to escalate -- ask a question first."}), 400

    from ..email import send_gs_support_escalation
    try:
        send_gs_support_escalation(current_user, question, ai_answer, gs_note)
    except Exception as e:
        return jsonify({"error": f"Couldn't send the escalation email: {e}"}), 502

    return jsonify({"ok": True})
