from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from ..models import Person

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard") if current_user.is_admin
                        else url_for("portal.home"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        person   = Person.query.filter_by(email=email, can_login=True).first()

        if person and person.check_password(password):
            login_user(person)
            next_page = request.args.get("next")
            if person.is_admin:
                return redirect(next_page or url_for("admin.dashboard"))
            return redirect(next_page or url_for("portal.home"))

        flash("Invalid email or password.", "error")

    return render_template("auth/login.html")

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        import secrets
        from datetime import datetime, timedelta
        from ..models import db
        from ..email import send_password_reset_email

        email  = request.form.get("email", "").strip().lower()
        person = Person.query.filter_by(email=email, can_login=True).first()

        if person:
            token = secrets.token_urlsafe(32)
            person.reset_token = token
            person.reset_token_expires_at = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            try:
                send_password_reset_email(person, token)
            except Exception:
                pass

        # Same message either way -- don't reveal whether the email
        # is registered.
        flash("If an account exists with that email, we've sent a password reset link. "
             "It's valid for 1 hour.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    from datetime import datetime
    from ..models import db

    person = Person.query.filter_by(reset_token=token).first_or_404()
    expired = (not person.reset_token_expires_at) or datetime.utcnow() > person.reset_token_expires_at

    if request.method == "POST" and not expired:
        pw  = request.form.get("password", "").strip()
        pw2 = request.form.get("password2", "").strip()
        if len(pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif pw != pw2:
            flash("Passwords do not match.", "error")
        else:
            person.set_password(pw)
            person.reset_token = None
            person.reset_token_expires_at = None
            db.session.commit()
            flash("Password updated -- you can now sign in.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", person=person, expired=expired)
