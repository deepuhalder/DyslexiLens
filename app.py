"""
DyslexiLens — a dyslexia-friendly reading workspace.

This single Flask file contains the whole backend:
  * Authentication (signup / login / logout) with hashed passwords
  * Document upload with PDF text-layer extraction and OCR (with image cleanup)
  * Text normalization (junk removal, ligature/quote fixing)
  * Tough-word detection (long AND rare words) with real syllable breakdowns
  * A pronunciation dictionary for special words (e.g. "DyslexiLens")
  * Reading settings persistence, recent documents, progress tracking,
    and an Excel export of all practice data.

Design rules kept throughout:
  * Every SQL query uses parameters (?) — never string joining.
  * Passwords are hashed with werkzeug, never stored in plain text.
  * Every user-facing page requires login (see login_required).
  * Admin pages check the role server-side (see admin_required).
"""

import os
import io
import re
import secrets
import smtplib
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    g,
    send_file,
    abort
)
from flask_wtf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
import pymupdf as fitz
from PIL import Image, ImageOps
import pytesseract
import pdf2image
from wordfreq import zipf_frequency
import pyphen
import openpyxl
from openpyxl.styles import Font, PatternFill

# Load .env (SECRET_KEY, admin credentials, SMTP) when the file exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; environment variables still work

# Configure Tesseract default Windows path
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(TESSERACT_EXE):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dyslexilens_dev_secret_key_change_me")

# Debug mode OFF unless FLASK_DEBUG=1 is set (never debug on a public server)
DEBUG_MODE = os.environ.get("FLASK_DEBUG", "0") == "1"

# --- Security configuration -------------------------------------------------
CSRFProtect(app)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024   # uploads capped at 10 MB
app.config["SESSION_COOKIE_HTTPONLY"] = True           # JS cannot read the cookie
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"          # CSRF hardening for cookies
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"  # True behind HTTPS
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)  # idle auto-logout

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dyslexilens.db")

# Email (SMTP) settings for password resets — all from environment variables
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "")
MAIL_ENABLED = bool(SMTP_HOST and MAIL_FROM)


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------

def get_db():
    """Opens a new database connection if there is none yet for this request."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        init_db()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    """Closes the database again at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Creates all tables (if missing) and migrates older databases.

    Tables:
      users            — one row per account (hashed password, never plain text)
      login_history    — one row per login (tracking data kept for the export)
      documents        — every uploaded document's extracted text
      reading_sessions — one row per read-aloud session (words spoken, duration)
      word_events      — one row per tough word the user clicked/practised
      user_settings    — one row per user with their reader preferences
    """
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Users table (first_name is the capitalized name shown in the interface)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            first_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration: older databases may miss the first_name column
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migration: role + account status columns (admin, block/unblock)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass

    # One-time password reset tokens (30-minute expiry, single use)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Security/activity log: logins, logouts, failed logins, admin actions…
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            event TEXT NOT NULL,
            ip_address TEXT,
            session_duration TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Login history table (kept for tracking/export; not shown as a raw log)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            logout_time TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Uploaded documents and their extracted text
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            clean_text TEXT NOT NULL,
            char_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Read-aloud sessions: what was read, how much, for how long
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reading_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            document_id INTEGER,
            filename TEXT,
            words_total INTEGER NOT NULL DEFAULT 0,
            words_spoken INTEGER NOT NULL DEFAULT 0,
            seconds_read REAL NOT NULL DEFAULT 0,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Tough words the user clicked / listened to (practice tracking)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS word_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            document_id INTEGER,
            word TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'tough_click',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Per-user reader preferences (persisted between visits)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            font_size INTEGER NOT NULL DEFAULT 24,
            letter_spacing REAL NOT NULL DEFAULT 0.02,
            line_spacing REAL NOT NULL DEFAULT 1.5,
            background TEXT NOT NULL DEFAULT 'peach',
            focus_mode INTEGER NOT NULL DEFAULT 0,
            speech_rate REAL NOT NULL DEFAULT 1.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Migration (November update): page zoom + voice guide preferences
    for column_sql in (
        "ALTER TABLE user_settings ADD COLUMN page_zoom INTEGER NOT NULL DEFAULT 100",
        "ALTER TABLE user_settings ADD COLUMN voice_guide INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            cursor.execute(column_sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Migration (November update): remember the last sentence read per document
    try:
        cursor.execute("ALTER TABLE documents ADD COLUMN last_position INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


# Initialize database when module is loaded
init_db()


# ---------------------------------------------------------------------------
# DISPLAY NAME HELPERS
# Users are addressed by their FIRST NAME, capitalized ("Deepsikha"), never
# by the lowercase username. The database is the source of truth.
# ---------------------------------------------------------------------------

def capitalize_name(value):
    """Capitalizes the first letter of each word of a name ("deepsikha" -> "Deepsikha")."""
    return " ".join(part[:1].upper() + part[1:] for part in str(value or "").split() if part)


def get_display_name(user_row):
    """Best name to show: first_name, else the capitalized username."""
    if user_row is None:
        return ""
    try:
        first = (user_row["first_name"] or "").strip() if "first_name" in user_row.keys() else ""
    except (KeyError, IndexError):
        first = ""
    if first:
        return capitalize_name(first.split()[0])
    return capitalize_name((user_row["username"] or "").split("_")[0].split(".")[0])


@app.template_filter("display_name")
def display_name_filter(user_row):
    """Jinja filter: `user|display_name` renders the capitalized first name."""
    return get_display_name(user_row)


def client_ip():
    """The visitor's IP address (for the activity log)."""
    return request.headers.get("X-Forwarded-For", request.remote_addr or "")


def log_activity(event, user_id=None, username=None, details=None, session_duration=None):
    """Writes one row to activity_log. Best-effort: never breaks a request."""
    try:
        conn = sqlite3.connect(DATABASE)
        conn.execute(
            "INSERT INTO activity_log (user_id, username, event, ip_address, session_duration, details) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, event, client_ip(), session_duration, details)
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------------
# AUTHENTICATION HELPERS
# ---------------------------------------------------------------------------

def login_required(f):
    """Decorator: only logged-in users may reach the wrapped route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "info")
            return redirect(url_for("login"))
        session.permanent = True  # enables the 30-minute inactivity logout
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator: the logged-in user must have the admin role (checked on the
    SERVER for every request — hiding menu links is not enough)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "info")
            return redirect(url_for("login"))
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT is_admin, status FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        conn.close()
        if not row or not row["is_admin"]:
            log_activity("admin_access_denied", user_id=session["user_id"], username=session.get("username"))
            abort(403)
        if row["status"] != "active":
            session.clear()
            flash("Your account is blocked. Contact the administrator.", "error")
            return redirect(url_for("login"))
        session.permanent = True
        return f(*args, **kwargs)
    return decorated_function


@app.route("/")
def index():
    """Public landing page: what the app is, plus Log in / Sign up."""
    if "user_id" in session:
        return redirect(url_for("home"))
    return render_template("landing.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Creates a new account. Passwords are hashed before storage."""
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        first_name = capitalize_name(request.form.get("first_name", "").strip())
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        # Basic validations
        if not first_name or not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("signup.html", username=username, email=email, first_name=first_name)

        if len(username) < 3:
            flash("Username must be at least 3 characters long.", "error")
            return render_template("signup.html", username=username, email=email, first_name=first_name)

        if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            flash("Password must be at least 8 characters and include a letter and a number.", "error")
            return render_template("signup.html", username=username, email=email, first_name=first_name)

        db = get_db()
        cursor = db.cursor()

        # Separate, specific errors (parameterized queries — safe from injection)
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            flash("That username is already taken. Try another.", "error")
            return render_template("signup.html", username=username, email=email, first_name=first_name)
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            flash("An account with this email already exists. Try logging in.", "error")
            return render_template("signup.html", username=username, email=email, first_name=first_name)

        # Hash password securely with werkzeug.security (never plain text)
        hashed_password = generate_password_hash(password)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor.execute(
                "INSERT INTO users (username, email, password, first_name, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, email, hashed_password, first_name, now_str)
            )
            db.commit()
            flash("Account created. You can log in now.", "success")
            return redirect(url_for("login"))
        except sqlite3.Error:
            flash("The account could not be created. Please try again.", "error")
            return render_template("signup.html", username=username, email=email, first_name=first_name)

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Logs the user in. After login they always land on a fresh Scan screen.

    Rate limited: 5 failed attempts lock this username + IP for 15 minutes.
    """
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Please enter both username and password.", "error")
            return render_template("login.html", username=username)

        if login_rate_limited(username):
            log_activity("login_locked", username=username)
            flash("Too many failed attempts. Try again in 15 minutes.", "error")
            return render_template("login.html", username=username)

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()

        if user and check_password_hash(user["password"], password):
            if user["status"] != "active":
                log_activity("login_blocked", user_id=user["id"], username=username)
                flash("This account is blocked. Contact the administrator.", "error")
                return render_template("login.html", username=username)

            # Record login in login_history table (tracking data, kept for export)
            login_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT INTO login_history (user_id, login_time) VALUES (?, ?)",
                (user["id"], login_time)
            )
            db.commit()
            history_id = cursor.lastrowid

            # New session ID after login: clear first, then re-set (blocks
            # session fixation). display_name = capitalized first name.
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["display_name"] = get_display_name(user)
            session["email"] = user["email"]
            session["is_admin"] = bool(user["is_admin"])
            session["login_history_id"] = history_id
            session.permanent = True  # 30-minute inactivity auto-logout

            _clear_login_failures(username)
            log_activity("login", user_id=user["id"], username=username)

            # Logged in with the voice guide on the login page? Then keep
            # speaking page names and messages after login too.
            if request.form.get("voice_mode") == "1":
                prefs = _load_settings(user["id"])
                prefs["voice_guide"] = 1
                _save_settings_row(prefs)
            return redirect(url_for("home"))
        else:
            _record_login_failure(username)
            log_activity("login_failed", username=username)
            flash("Wrong username or password.", "error")
            return render_template("login.html", username=username)

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Ends the session and records the logout time in login_history."""
    history_id = session.get("login_history_id")
    duration_text = None
    if history_id:
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute("SELECT login_time FROM login_history WHERE id = ?", (history_id,))
            row = cursor.fetchone()
            if row and row["login_time"]:
                try:
                    started = datetime.strptime(row["login_time"], "%Y-%m-%d %H:%M:%S")
                    seconds = max(0, int((datetime.now() - started).total_seconds()))
                    duration_text = f"{seconds // 60}m {seconds % 60}s"
                except ValueError:
                    duration_text = None
            logout_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "UPDATE login_history SET logout_time = ? WHERE id = ?",
                (logout_time, history_id)
            )
            db.commit()
        except sqlite3.Error:
            pass

    log_activity(
        "logout",
        user_id=session.get("user_id"),
        username=session.get("username"),
        session_duration=duration_text
    )

    # Clear Flask session
    session.clear()
    flash("You've been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# LOGIN RATE LIMITING
# 5 failed attempts (username + IP) lock login for 15 minutes. Kept in
# memory — restarting the server clears the locks, which is fine here.
# ---------------------------------------------------------------------------

_LOGIN_LOCK_WINDOW = timedelta(minutes=15)
_login_failures = {}   # "username|ip" -> {"count": int, "locked_until": datetime|None}


def _failure_key(username):
    return f"{(username or '').lower()}|{client_ip()}"


def login_rate_limited(username):
    """True while this username + IP is locked out."""
    entry = _login_failures.get(_failure_key(username))
    if not entry:
        return False
    locked_until = entry.get("locked_until")
    return bool(locked_until and datetime.now() < locked_until)


def _record_login_failure(username):
    key = _failure_key(username)
    entry = _login_failures.setdefault(key, {"count": 0, "locked_until": None})
    entry["count"] += 1
    if entry["count"] >= 5:
        entry["locked_until"] = datetime.now() + _LOGIN_LOCK_WINDOW
        entry["count"] = 0
        log_activity("login_lockout", username=username)


def _clear_login_failures(username):
    _login_failures.pop(_failure_key(username), None)


# ---------------------------------------------------------------------------
# PASSWORD RESET (one-time token, 30-minute expiry, SMTP from env vars)
# If email is not configured, the page tells the user to contact the admin,
# who can reset any password from Admin -> User management.
# ---------------------------------------------------------------------------

def _send_reset_email(to_address, reset_url):
    """Sends the reset link via SMTP. Returns False when sending fails."""
    try:
        msg = EmailMessage()
        msg["Subject"] = "DyslexiLens password reset"
        msg["From"] = MAIL_FROM
        msg["To"] = to_address
        msg.set_content(
            "A password reset was requested for your DyslexiLens account.\n\n"
            f"Open this link to choose a new password (valid 30 minutes):\n{reset_url}\n\n"
            "If you did not request this, ignore this email."
        )
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception:
        return False


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Ask for a reset link. Never reveals whether the email exists."""
    if MAIL_ENABLED:
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            db = get_db()
            cursor = db.cursor()
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            if row:
                token = secrets.token_urlsafe(32)
                expires = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute(
                    "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                    (row["id"], token, expires)
                )
                db.commit()
                _send_reset_email(email, url_for("reset_password", token=token, _external=True))
            flash("If that email exists, a reset link has been sent.", "info")
            return redirect(url_for("login"))
        return render_template("forgot_password.html", mail_enabled=True)
    return render_template("forgot_password.html", mail_enabled=False)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """One-time reset link: validates the token, then sets the new password."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT t.id, t.user_id, t.expires_at, t.used FROM password_reset_tokens t
        WHERE t.token = ?
    """, (token,))
    row = cursor.fetchone()
    valid = bool(
        row and not row["used"]
        and row["expires_at"]
        and datetime.now() <= datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    )
    if not valid:
        flash("This reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            flash("Password must be at least 8 characters and include a letter and a number.", "error")
            return render_template("reset_password.html", token=token)
        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)
        cursor.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(password), row["user_id"]))
        cursor.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = ?", (row["id"],))
        db.commit()
        log_activity("password_reset", user_id=row["user_id"])
        flash("Password changed. Log in with the new one.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    """Settings page: defaults for background, font size, spacing, speed."""
    if request.method == "POST":
        data = request.form.to_dict()
        # An unticked checkbox is simply missing from the form, so set it here
        data["voice_guide"] = 1 if "voice_guide" in request.form else 0
        settings, error = _merge_validated_settings(_load_settings(session["user_id"]), data)
        if error:
            flash(error, "error")
        else:
            _save_settings_row(settings)
            flash("Saved.", "success")
        return redirect(url_for("settings_page"))
    settings = _load_settings(session["user_id"])
    return render_template("settings.html", settings=settings)


@app.route("/profile", methods=["GET"])
@login_required
def profile_page():
    """Profile page: edit details, change password, delete account."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, username, email, first_name FROM users WHERE id = ?", (session["user_id"],))
    return render_template("profile.html", user=cursor.fetchone())


@app.route("/profile/update", methods=["POST"])
@login_required
def profile_update():
    """Updates first name, username and email with duplicate checks."""
    first_name = capitalize_name(request.form.get("first_name", "").strip())
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()

    if not first_name or not username or not email:
        flash("All fields are required.", "error")
        return redirect(url_for("profile_page"))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ? AND id != ?", (username, session["user_id"]))
    if cursor.fetchone():
        flash("That username is already taken.", "error")
        return redirect(url_for("profile_page"))
    cursor.execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, session["user_id"]))
    if cursor.fetchone():
        flash("That email is already in use.", "error")
        return redirect(url_for("profile_page"))

    cursor.execute(
        "UPDATE users SET first_name = ?, username = ?, email = ? WHERE id = ?",
        (first_name, username, email, session["user_id"])
    )
    db.commit()
    session["username"] = username
    session["display_name"] = capitalize_name(first_name.split()[0])
    session["email"] = email
    log_activity("profile_updated", user_id=session["user_id"], username=username)
    flash("Saved.", "success")
    return redirect(url_for("profile_page"))


@app.route("/profile/password", methods=["POST"])
@login_required
def profile_password():
    """Change password: verifies the CURRENT password first."""
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT password FROM users WHERE id = ?", (session["user_id"],))
    row = cursor.fetchone()

    if not row or not check_password_hash(row["password"], current):
        flash("Wrong password.", "error")
        return redirect(url_for("profile_page"))
    if len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
        flash("New password must be at least 8 characters and include a letter and a number.", "error")
        return redirect(url_for("profile_page"))
    if new != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("profile_page"))

    cursor.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new), session["user_id"]))
    db.commit()
    log_activity("password_change", user_id=session["user_id"], username=session.get("username"))
    flash("Password changed.", "success")
    return redirect(url_for("profile_page"))


@app.route("/profile/delete", methods=["POST"])
@login_required
def profile_delete():
    """Delete the account: requires the current password; removes the user's
    documents and settings too. Then logs out to a cleared session."""
    password = request.form.get("password", "")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT password FROM users WHERE id = ?", (session["user_id"],))
    row = cursor.fetchone()
    if not row or not check_password_hash(row["password"], password):
        flash("Wrong password. The account was not deleted.", "error")
        return redirect(url_for("profile_page"))

    uid = session["user_id"]
    uname = session.get("username")
    for table in ("documents", "reading_sessions", "word_events", "user_settings", "login_history"):
        cursor.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
    cursor.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    log_activity("account_deleted", user_id=None, username=uname)
    session.clear()
    flash("Your account has been deleted.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# TEXT NORMALIZATION (cleaning extracted OCR / PDF text)
# ---------------------------------------------------------------------------

# Ligature and symbol fixes: OCR often turns "fi" into a single character.
_CHAR_FIXES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...",
    "\u00a0": " ", "\u200b": "", "\ufeff": "",
}
_CHAR_FIX_TABLE = str.maketrans(_CHAR_FIXES)


def normalize_text(raw):
    """Cleans extracted text while KEEPING the original paragraph/line structure.

    Steps: unicode normalization, ligature/quote fixing, control-character
    removal, hyphenated line-wrap repair ("exam-\\nple" -> "example"), and
    collapsing of runs of spaces. Line breaks and paragraph breaks survive.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFC", raw)
    text = text.translate(_CHAR_FIX_TABLE)

    # Remove control characters except newline and tab
    text = "".join(ch for ch in text if ch in ("\n", "\t") or not unicodedata.category(ch).startswith("C"))

    # Collapse runs of spaces / tabs inside each line (keep the line breaks)
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line:
            cleaned_lines.append(line)
        elif cleaned_lines and cleaned_lines[-1] != "":
            # Keep ONE blank line as a paragraph separator, drop extra blanks
            cleaned_lines.append("")

    # Repair words split across lines by hyphenation: "exam-\nple" -> "example"
    text = "\n".join(cleaned_lines)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# IMAGE PRE-PROCESSING (better OCR quality before recognition)
# ---------------------------------------------------------------------------

def preprocess_image_for_ocr(pil_image):
    """Cleans a scanned image so Tesseract reads it more accurately.

    Steps: auto-rotate using EXIF, convert to grayscale, stretch the contrast,
    and upscale small images (Tesseract works best at ~300 DPI scale).
    """
    try:
        img = ImageOps.exif_transpose(pil_image)  # fix rotation/tilt from the camera
    except Exception:
        img = pil_image

    # Flatten transparency onto white so dark-mode screenshots still read well
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "L":
        img = img.convert("RGB")

    # Grayscale + gentle auto-contrast (fixes faint scans and washed photos)
    gray = img.convert("L")
    gray = ImageOps.autocontrast(gray, cutoff=1)

    # Upscale small images: Tesseract needs reasonably large glyphs
    width, height = gray.size
    if min(width, height) < 1000 and min(width, height) > 0:
        scale = min(2.5, 1000 / min(width, height))
        gray = gray.resize(
            (int(width * scale), int(height * scale)),
            Image.LANCZOS
        )
    return gray


# ---------------------------------------------------------------------------
# TOUGH WORD DETECTION
#
# A word is "tough" only when it is BOTH long (7+ letters) AND rare
# (Zipf frequency below 4.0). Requiring both conditions avoids over-flagging
# normal long-but-common words like "understanding" or "different".
# ---------------------------------------------------------------------------

TOUGH_MIN_LENGTH = 7       # letters
TOUGH_MAX_ZIPF = 4.0       # rare when below this Zipf frequency

_LETTERS_RE = re.compile(r"^[^\W\d_]+$")  # unicode word characters, no digits/underscore

# Single shared pyphen dictionary object (thread-safe reads, created once)
_HYPHENATOR = pyphen.Pyphen(lang="en_US")

# ---------------------------------------------------------------------------
# PRONUNCIATION HELPERS
#
# PYPHEN_OVERRIDES fixes the few common words pyphen splits badly (or not at
# all) — real pronounceable syllables only, never single letters.
# SAY_OVERRIDES maps special words to how they should be SPOKEN as a whole
# (e.g. the app name "DyslexiLens" is spoken as "dyslexia lens").
# ---------------------------------------------------------------------------

PYPHEN_OVERRIDES = {
    "vibrate": "vi-brate",
    "dyslexia": "dys-lex-i-a",
    "contrast": "con-trast",
    "visual": "vis-u-al",
    "stress": "stress",
    "dyslexilens": "dys-lex-i-a-lens",
    "comprehension": "com-pre-hen-sion",
    "neurodiversity": "neu-ro-di-ver-si-ty",
    "typography": "ty-pog-ra-phy",
    "readable": "read-a-ble",
    "different": "dif-fer-ent",
    "every": "ev-ery",
    "business": "busi-ness",
    "weather": "weath-er",
    "beautiful": "beau-ti-ful",
}

# How the WHOLE word is spoken after the syllables (lowercase, TTS-safe).
SAY_OVERRIDES = {
    "dyslexilens": "dyslexia lens",
}

# Fallback syllable splitter for words pyphen cannot hyphenate:
# chunks = consonant cluster + vowel group (a rough but pronounceable split).
_VOWEL_CHUNK_RE = re.compile(r"[^aeiouy]*[aeiouy]+(?:[^aeiouy](?=[aeiouy]))?", re.IGNORECASE)


def syllabify_word(word):
    """Returns real syllables for a word, e.g. 'vi-brate'. Never letter-by-letter.

    Order: hand-curated dictionary -> pyphen hyphenation -> vowel-group
    fallback. Returns None when nothing sensible can be produced.
    """
    key = word.lower().strip("'’")
    if not key:
        return None
    if key in PYPHEN_OVERRIDES:
        return PYPHEN_OVERRIDES[key]
    try:
        hyphenated = _HYPHENATOR.inserted(key, hyphen="-")
    except Exception:
        hyphenated = ""
    if hyphenated and hyphenated != key and "-" in hyphenated:
        return hyphenated
    # Fallback split (used rarely): consonant+vowel chunks of 2+ letters
    chunks = [c for c in _VOWEL_CHUNK_RE.findall(key) if len(c) >= 2]
    if len(chunks) >= 2 and "".join(chunks) == key:
        return "-".join(chunks)
    return None


def spoken_form(word):
    """How the whole word should be spoken (after its syllables)."""
    return SAY_OVERRIDES.get(word.lower().strip("'’"), word)


# Word matches: letters (with optional internal apostrophe). Separator matches:
# whitespace runs and any other single non-space character (punctuation).
# Separators are passed through untouched so the frontend can rebuild the text
# with its original spacing, line breaks and punctuation intact.
_TOKEN_RE = re.compile(
    r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*"  # a word
    r"|\s+"                                # whitespace run
    r"|\S"                                 # any punctuation char
)


def analyze_tough_words(text):
    """Tokenizes extracted text and tags each word as tough or normal.

    Runs fresh on ANY text it is given — there is no word list, no memorized
    demo content. Every word is measured by the same rule:
        tough  <=>  7+ letters  AND  Zipf frequency < 4.0

    Returns a JSON-ready list of tokens: `{word, tough, syllables?}` for words,
    plus `{word, tough: False, separator: True}` tokens carrying whitespace and
    punctuation so the original text can be reconstructed exactly.
    Tough words additionally carry their syllable breakdown and a confidence
    number, and are deduplicated via a cache so repeated words are analyzed
    only once per request.
    """
    tokens = []
    cache = {}
    for match in _TOKEN_RE.finditer(text or ""):
        word = match.group(0)

        # Whitespace / punctuation pass-through: never analyzed, never flagged
        if not word[0].isalpha():
            tokens.append({"word": word, "tough": False, "separator": True})
            continue

        key = word.lower()
        if key in cache:
            # Reuse the analysis, but keep THIS word's own spelling and
            # capitals (before, "dyslexia" became "Dyslexia" everywhere).
            cached = dict(cache[key])
            cached["word"] = word
            tokens.append(cached)
            continue

        entry = {"word": word, "tough": False}

        # Rule: LONG (7+ letters) AND RARE (zipf < 4.0). Both must hold.
        if _LETTERS_RE.match(key) and len(key) >= TOUGH_MIN_LENGTH:
            try:
                zipf = zipf_frequency(key, "en")
            except Exception:
                # Frequency database unavailable -> degrade to "normal", never crash
                zipf = TOUGH_MAX_ZIPF
            if zipf < TOUGH_MAX_ZIPF:
                syllables = syllabify_word(key)
                if syllables:
                    entry["tough"] = True
                    entry["syllables"] = syllables
                    entry["say"] = spoken_form(key)
                    entry["zipf"] = round(zipf, 2)

        cache[key] = entry
        tokens.append(dict(entry))

    return tokens


@app.route("/api/analyze-text", methods=["POST"])
@login_required
def analyze_text_api():
    """Breaks extracted text into words, tagging tough (long AND rare) words.

    Body: JSON {"text": "..."}. Returns {"success", "tokens", "tough_count",
    "word_count"}. Tokens are `{word, tough}` plus `syllables` (real syllables,
    e.g. 'com-pre-hen-sion') and `say` (spoken form) for tough words only.
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "No text provided to analyze."}), 400

    tokens = analyze_tough_words(text)
    tough_count = sum(1 for t in tokens if t["tough"])
    word_count = sum(1 for t in tokens if not t.get("separator"))
    return jsonify({
        "success": True,
        "tokens": tokens,
        "word_count": word_count,
        "tough_count": tough_count
    })


# ---------------------------------------------------------------------------
# DOCUMENT EXTRACTION (PDF text layer + OCR with pre-processing)
# ---------------------------------------------------------------------------

def extract_text_from_file(file_storage):
    """Extracts text from PDF, JPG, or PNG uploads.

    PDFs: the real text layer is used directly when present (fast + accurate);
    only scanned pages fall back to OCR. Images are cleaned up first
    (grayscale, contrast, rotation, upscaling) for better OCR quality.
    Returns (True, clean_text) or (False, friendly_error_message).
    """
    filename = file_storage.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    file_bytes = file_storage.read()

    if not file_bytes:
        return False, "That file is empty. Choose a PDF, JPG or PNG and try again."

    if ext not in [".pdf", ".jpg", ".jpeg", ".png"]:
        return False, "That file type isn't supported. Try a PDF, JPG or PNG."

    # REAL file type check (magic bytes), not just the extension. A file
    # named .pdf that is actually an executable or HTML never gets parsed.
    if file_bytes[:4] == b"%PDF":
        pass  # a real PDF
    elif file_bytes[:3] == b"\xff\xd8\xff":
        pass  # a real JPEG
    elif file_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        pass  # a real PNG
    else:
        return False, "That file isn't a real PDF, JPG or PNG. Try exporting it again."

    extracted_text = ""

    if ext == ".pdf":
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")

            # 1. First attempt: Direct digital text extraction with PyMuPDF
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            text = normalize_text("\n\n".join(text_parts))

            # 2. Fall back to OCR only for scanned pages with no text layer
            if len(text.strip()) >= 15:
                extracted_text = text
            else:
                images = []
                try:
                    # Attempt pdf2image first (requires poppler)
                    images = pdf2image.convert_from_bytes(file_bytes)
                except Exception:
                    # Fallback using PyMuPDF pixmap rendering (no Poppler needed)
                    images = []
                    for page in doc:
                        pix = page.get_pixmap(dpi=150)
                        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        images.append(img)

                ocr_parts = []
                for img in images:
                    cleaned = preprocess_image_for_ocr(img)  # grayscale/contrast/scale
                    txt = pytesseract.image_to_string(cleaned)
                    if txt.strip():
                        ocr_parts.append(txt.strip())
                extracted_text = normalize_text("\n\n".join(ocr_parts))

        except Exception:
            return False, "That PDF could not be read. It may be damaged or password-protected."

    elif ext in [".jpg", ".jpeg", ".png"]:
        try:
            img = Image.open(io.BytesIO(file_bytes))
            cleaned = preprocess_image_for_ocr(img)  # grayscale/contrast/scale
            extracted_text = normalize_text(pytesseract.image_to_string(cleaned))
        except Exception:
            return False, "That image could not be read. Try a clearer photo or screenshot."

    if not extracted_text or not extracted_text.strip():
        return False, "No readable text was found. Try a clearer image or a text-based PDF."

    return True, extracted_text.strip()


@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    """Handles a document upload: extract, clean, store, and return the text.

    The text is NOT shown as final — the client puts it into a review box so
    the user can fix OCR mistakes before reading. It is also saved to the
    documents table so it appears under Recent documents.
    """
    if "document" not in request.files:
        message = "No file was selected for upload."
        flash(message, "error")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": message}), 400
        return redirect(url_for("home"))

    file = request.files["document"]
    if file.filename == "":
        message = "Please select a file to upload."
        flash(message, "error")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": message}), 400
        return redirect(url_for("home"))

    success, result = extract_text_from_file(file)

    if not success:
        flash(result, "error")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": result}), 400
        return redirect(url_for("home"))

    # Save the document so it can be reopened from "Recent documents"
    doc_id = None
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO documents (user_id, filename, raw_text, clean_text, char_count) VALUES (?, ?, ?, ?, ?)",
            (session["user_id"], file.filename, result, result, len(result))
        )
        db.commit()
        doc_id = cursor.lastrowid
    except sqlite3.Error:
        pass  # storing history is best-effort; reading still works

    flash(f"{file.filename} scanned. Check the text below.", "success")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "text": result,
            "filename": file.filename,
            "document_id": doc_id
        })

    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# SAMPLE DOCUMENT (for quick demos without any upload)
# ---------------------------------------------------------------------------

SAMPLE_DOCUMENT_TEXT = """Understanding Dyslexia and Visual Stress

Dyslexia is a common learning difference that affects the way the brain processes written language. It has nothing to do with intelligence. Many people with dyslexia experience visual stress: printed letters seem to vibrate, blur, or swim across the page, especially under harsh white contrast.

DyslexiLens was built to reduce that strain. The reader offers a soft peach background, generous letter spacing, and real syllable breakdowns for hard words. You can listen to any passage, adjust the font and line spacing with live sliders, and magnify lines as you read.

Try the controls above. Click a tinted word to see its syllables, press the sound button to hear each syllable slowly and then the whole word, and use Read Aloud to listen to the entire passage while the current word is highlighted."""

SAMPLE_DOCUMENT_NAME = "sample_document.txt"


@app.route("/api/sample-document")
@login_required
def sample_document_api():
    """Returns the built-in sample text for the 'Try a sample document' button."""
    return jsonify({
        "success": True,
        "filename": SAMPLE_DOCUMENT_NAME,
        "text": SAMPLE_DOCUMENT_TEXT
    })


# ---------------------------------------------------------------------------
# USER SETTINGS (persisted reader preferences)
# ---------------------------------------------------------------------------

# focus_mode stays in the database table (old rows) but is no longer used:
# line focus was removed in the November update.
_SETTINGS_KEYS = ("font_size", "letter_spacing", "line_spacing", "background",
                  "speech_rate", "page_zoom", "voice_guide")

# Page zoom limits (percent of normal size)
ZOOM_MIN, ZOOM_MAX = 100, 150

# Allowed reading backgrounds (soft pastels only; peach is the default)
ALLOWED_BACKGROUNDS = ("peach", "cream", "yellow", "turquoise")

DEFAULT_SETTINGS = {
    "font_size": 24,   # 24px = 18pt, the research-backed default
    "letter_spacing": 0.02,
    "line_spacing": 1.5,
    "background": "peach",
    "speech_rate": 1.0,
    "page_zoom": 100,    # 100% = normal size, up to 150%
    "voice_guide": 0,    # 1 = speak page names and messages aloud
}


def _load_settings(user_id):
    """Fetches the user's saved settings, falling back to defaults."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    settings = dict(DEFAULT_SETTINGS)
    if row:
        for key in _SETTINGS_KEYS:
            if row[key] is not None:
                settings[key] = row[key]
    return settings


@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    """Returns the current user's reader settings."""
    return jsonify({"success": True, "settings": _load_settings(session["user_id"])})


def _merge_validated_settings(current, data):
    """Applies incoming settings onto `current` with clamping/validation.
    Returns (settings, error_message). error_message is None when valid."""
    settings = dict(current)
    try:
        if "font_size" in data:
            settings["font_size"] = max(14, min(32, int(data["font_size"])))
        if "letter_spacing" in data:
            settings["letter_spacing"] = max(0.0, min(0.15, float(data["letter_spacing"])))
        if "line_spacing" in data:
            settings["line_spacing"] = max(1.4, min(2.6, float(data["line_spacing"])))
        if "background" in data:
            if data["background"] not in ALLOWED_BACKGROUNDS:
                return current, "Unknown background option."
            settings["background"] = data["background"]
        if "page_zoom" in data:
            zoom = int(round(float(data["page_zoom"]) / 10.0) * 10)
            settings["page_zoom"] = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        if "voice_guide" in data:
            settings["voice_guide"] = 1 if str(data["voice_guide"]).lower() in ("1", "true", "on", "yes") else 0
        if "speech_rate" in data:
            settings["speech_rate"] = max(0.5, min(2.0, float(data["speech_rate"])))
    except (TypeError, ValueError):
        return current, "Invalid setting value."
    return settings, None


def _save_settings_row(settings):
    """Writes one user's settings row (insert or update)."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO user_settings
            (user_id, font_size, letter_spacing, line_spacing, background,
             speech_rate, page_zoom, voice_guide, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            font_size = excluded.font_size,
            letter_spacing = excluded.letter_spacing,
            line_spacing = excluded.line_spacing,
            background = excluded.background,
            speech_rate = excluded.speech_rate,
            page_zoom = excluded.page_zoom,
            voice_guide = excluded.voice_guide,
            updated_at = excluded.updated_at
    """, (
        session["user_id"], settings["font_size"], settings["letter_spacing"],
        settings["line_spacing"], settings["background"], settings["speech_rate"],
        settings["page_zoom"], settings["voice_guide"],
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    db.commit()


@app.route("/api/settings", methods=["POST"])
@login_required
def save_settings():
    """Validates and stores the user's reader settings."""
    data = request.get_json(silent=True) or {}
    settings, error = _merge_validated_settings(_load_settings(session["user_id"]), data)
    if error:
        return jsonify({"success": False, "error": error}), 400
    _save_settings_row(settings)
    return jsonify({"success": True, "settings": settings})


# ---------------------------------------------------------------------------
# RECENT DOCUMENTS
# ---------------------------------------------------------------------------

@app.route("/api/documents", methods=["GET"])
@login_required
def list_documents():
    """Lists the user's recent uploaded documents (newest first, max 15)."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT id, filename, char_count, created_at
        FROM documents
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 15
    """, (session["user_id"],))
    docs = [dict(row) for row in cursor.fetchall()]
    return jsonify({"success": True, "documents": docs})


@app.route("/api/documents/<int:doc_id>", methods=["GET"])
@login_required
def get_document(doc_id):
    """Returns one document's cleaned text — only if it belongs to the user."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, filename, clean_text, last_position FROM documents WHERE id = ? AND user_id = ?",
        (doc_id, session["user_id"])
    )
    row = cursor.fetchone()
    if not row:
        return jsonify({"success": False, "error": "Document not found."}), 404
    return jsonify({
        "success": True,
        "document": {
            "id": row["id"],
            "filename": row["filename"],
            "text": row["clean_text"],
            "last_position": row["last_position"] or 0
        }
    })


@app.route("/api/documents/<int:doc_id>/position", methods=["POST"])
@login_required
def save_document_position(doc_id):
    """Remembers the last sentence the user reached in one-sentence mode,
    so the document opens at the same place next time. Only the owner's
    own documents can be updated."""
    data = request.get_json(silent=True) or {}
    try:
        position = max(0, int(data.get("sentence", 0)))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid position."}), 400
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE documents SET last_position = ? WHERE id = ? AND user_id = ?",
        (position, doc_id, session["user_id"])
    )
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"success": False, "error": "Document not found."}), 404
    return jsonify({"success": True, "last_position": position})


@app.route("/api/documents/<int:doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id):
    """Deletes one document — only if it belongs to the requesting user."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM documents WHERE id = ? AND user_id = ?", (doc_id, session["user_id"]))
    if not cursor.fetchone():
        return jsonify({"success": False, "error": "Document not found."}), 404
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    db.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# SECURITY HEADERS + NO-CACHE ON EVERY RESPONSE
# Protected pages must never come back via the browser Back button after
# logout, so they are stored nowhere: Cache-Control: no-store.
# ---------------------------------------------------------------------------

@app.after_request
def set_security_headers(response):
    """Adds security headers and disables caching on every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "connect-src 'self'"
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# PROGRESS TRACKING (reading sessions + practised words)
# ---------------------------------------------------------------------------

@app.route("/api/progress", methods=["POST"])
@login_required
def record_progress():
    """Records one practice event from the reader.

    Types: 'document_opened' (words_total), 'reading_session' (words_spoken +
    seconds), 'word_practiced' (a tough word was clicked and spoken).
    """
    data = request.get_json(silent=True) or {}
    event_type = data.get("type", "")

    db = get_db()
    cursor = db.cursor()

    if event_type == "document_opened":
        cursor.execute("""
            INSERT INTO reading_sessions (user_id, document_id, filename, words_total, started_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            session["user_id"], data.get("document_id"),
            (data.get("filename") or "")[:120],
            max(0, int(data.get("words", 0))),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        db.commit()
    elif event_type == "reading_session":
        cursor.execute("""
            INSERT INTO reading_sessions (user_id, document_id, filename, words_total, words_spoken, seconds_read, started_at, ended_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session["user_id"], data.get("document_id"),
            (data.get("filename") or "")[:120],
            max(0, int(data.get("words", 0))),
            max(0, int(data.get("words_spoken", 0))),
            max(0.0, float(data.get("seconds", 0))),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        db.commit()
    elif event_type == "word_practiced":
        word = (data.get("word") or "").strip()[:60]
        if not word:
            return jsonify({"success": False, "error": "Missing word."}), 400
        cursor.execute(
            "INSERT INTO word_events (user_id, document_id, word, event_type) VALUES (?, ?, ?, ?)",
            (session["user_id"], data.get("document_id"), word, "word_practiced")
        )
        db.commit()
    else:
        return jsonify({"success": False, "error": "Unknown event type."}), 400

    # Answer with the fresh summary so the page chips update instantly
    return jsonify({"success": True, "progress": _progress_summary(user_id=session["user_id"])})


def _progress_summary(user_id):
    """Small friendly totals: documents read, words spoken, words practised."""
    db = get_db()
    cursor = db.cursor()

    # Distinct documents (rows with a filename are documents; blank filenames
    # are pasted text, counted once as one pseudo-document)
    cursor.execute("""
        SELECT COUNT(DISTINCT COALESCE(NULLIF(filename, ''), '(pasted text)'))
        FROM reading_sessions
        WHERE user_id = ? AND words_total > 0
    """, (user_id,))
    documents_read = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COALESCE(SUM(words_spoken), 0) FROM reading_sessions WHERE user_id = ?",
        (user_id,)
    )
    words_spoken = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM word_events WHERE user_id = ?",
        (user_id,)
    )
    words_practised = cursor.fetchone()[0]

    return {
        "documents_read": documents_read,
        "words_spoken": words_spoken,
        "words_practised": words_practised
    }


@app.route("/api/progress", methods=["GET"])
@login_required
def get_progress():
    """Returns a small friendly summary: documents read and words practised."""
    return jsonify({"success": True, "progress": _progress_summary(session["user_id"])})


@app.route("/api/syllabify", methods=["GET"])
@login_required
def syllabify_api():
    """Debug/demo helper: returns the syllable breakdown and spoken form of a
    single word (?word=vibrate) — useful for checking the pronunciation rules."""
    word = (request.args.get("word") or "").strip()
    if not word or len(word) > 40:
        return jsonify({"success": False, "error": "Provide ?word=<word>"}), 400
    return jsonify({
        "success": True,
        "word": word,
        "syllables": syllabify_word(word),
        "say": spoken_form(word)
    })


# ---------------------------------------------------------------------------
# EXCEL EXPORT (all tracking data, kept out of the UI)
# ---------------------------------------------------------------------------

@app.route("/export/excel")
@login_required
def export_excel():
    """Builds an Excel workbook with the user's full practice history.

    Sheets: Progress (summary), Sessions (read aloud), Words Practised, Documents.
    """
    db = get_db()
    cursor = db.cursor()
    user_id = session["user_id"]

    wb = openpyxl.Workbook()

    # Sheet 1: simple summary
    summary = wb.active
    summary.title = "Progress"
    summary.append(["DyslexiLens Progress Report"])
    summary.append(["Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    summary.append([])
    cursor.execute("""
        SELECT COUNT(DISTINCT COALESCE(NULLIF(filename, ''), '(pasted text)'))
        FROM reading_sessions
        WHERE user_id = ? AND words_total > 0
    """, (user_id,))
    summary.append(["Documents read", cursor.fetchone()[0]])
    cursor.execute(
        "SELECT COALESCE(SUM(words_spoken), 0) FROM reading_sessions WHERE user_id = ?", (user_id,)
    )
    summary.append(["Words spoken aloud", cursor.fetchone()[0]])
    cursor.execute("SELECT COUNT(*) FROM word_events WHERE user_id = ?", (user_id,))
    summary.append(["Tough words practised", cursor.fetchone()[0]])
    summary["A1"].font = Font(bold=True, size=14)

    # Sheet 2: reading sessions
    sessions_sheet = wb.create_sheet("Sessions")
    sessions_sheet.append(["Started", "Ended", "Document", "Words total", "Words spoken", "Seconds read"])
    cursor.execute("""
        SELECT started_at, ended_at, filename, words_total, words_spoken, seconds_read
        FROM reading_sessions WHERE user_id = ? ORDER BY id DESC
    """, (user_id,))
    for row in cursor.fetchall():
        sessions_sheet.append([row[0], row[1], row[2], row[3], row[4], round(row[5], 1)])
    for cell in sessions_sheet[1]:
        cell.font = Font(bold=True)
    sessions_sheet.column_dimensions["C"].width = 30

    # Sheet 3: practised words
    words_sheet = wb.create_sheet("Words Practised")
    words_sheet.append(["Date", "Word"])
    cursor.execute(
        "SELECT created_at, word FROM word_events WHERE user_id = ? ORDER BY id DESC",
        (user_id,)
    )
    for row in cursor.fetchall():
        words_sheet.append([row[0], row[1]])
    for cell in words_sheet[1]:
        cell.font = Font(bold=True)

    # Sheet 4: uploaded documents
    docs_sheet = wb.create_sheet("Documents")
    docs_sheet.append(["Uploaded", "Filename", "Characters"])
    cursor.execute(
        "SELECT created_at, filename, char_count FROM documents WHERE user_id = ? ORDER BY id DESC",
        (user_id,)
    )
    for row in cursor.fetchall():
        docs_sheet.append([row[0], row[1], row[2]])
    for cell in docs_sheet[1]:
        cell.font = Font(bold=True)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"dyslexilens-progress-{timestamp}.xlsx"
    return send_file(
        out,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ---------------------------------------------------------------------------
# WORKSPACE PAGE
# ---------------------------------------------------------------------------

@app.route("/home")
@login_required
def home():
    """The workspace: always a FRESH scan screen — no previous scan auto-loads.

    Previous documents are available only through the "Recent documents" list.
    Tracking stays in the database; only a small friendly progress summary is
    shown on the page (never raw session logs).
    """
    user_id = session["user_id"]
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, username, first_name FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()

    # Small friendly summary (no raw logs shown anywhere)
    cursor.execute(
        "SELECT COUNT(*) FROM reading_sessions WHERE user_id = ? AND words_total > 0",
        (user_id,)
    )
    documents_read = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COALESCE(SUM(words_spoken), 0) FROM reading_sessions WHERE user_id = ?",
        (user_id,)
    )
    words_spoken = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM word_events WHERE user_id = ?", (user_id,))
    words_practised = cursor.fetchone()[0]

    # Persisted reader preferences load with the page (background colour etc.)
    settings = _load_settings(user_id)

    return render_template(
        "home.html",
        user=user,
        documents_read=documents_read,
        words_spoken=words_spoken,
        words_practised=words_practised,
        settings=settings
    )


@app.route("/help")
def help_page():
    """Help: how to use the app, voice commands and keyboard shortcuts.
    Open to everyone, so it also helps before logging in."""
    return render_template("help.html")


@app.route("/write")
@login_required
def write_page():
    """Writing pad: type or dictate, spellcheck, hear it read back, download."""
    return render_template("write.html")


@app.context_processor
def inject_user_prefs():
    """Makes page zoom and the voice guide setting available to every page
    (base.html uses them). Visitors who are not logged in get the defaults;
    their zoom is remembered in the browser instead (see voice.js)."""
    if "user_id" not in session:
        return {"user_prefs": None}
    try:
        prefs = _load_settings(session["user_id"])
    except sqlite3.Error:
        prefs = dict(DEFAULT_SETTINGS)
    return {"user_prefs": {"page_zoom": prefs["page_zoom"], "voice_guide": prefs["voice_guide"]}}


# ---------------------------------------------------------------------------
# ADMIN PAGES (role checked server-side on every request via admin_required)
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    """Admin overview: totals plus the latest activity entries."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM documents")
    total_docs = cursor.fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM activity_log WHERE event = 'login' AND created_at LIKE ?", (today + "%",))
    logins_today = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM activity_log WHERE event = 'login' AND created_at LIKE ?", (today + "%",))
    week_users = cursor.fetchone()[0]
    cursor.execute("SELECT username, event, created_at FROM activity_log ORDER BY id DESC LIMIT 10")
    recent = cursor.fetchall()
    return render_template("admin/dashboard.html", total_users=total_users, total_docs=total_docs,
                           logins_today=logins_today, week_users=week_users, recent=recent)


@app.route("/admin/users")
@admin_required
def admin_users():
    """User management: list, block/unblock, reset password, delete."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT u.id, u.username, u.email, u.first_name, u.is_admin, u.status, u.created_at,
               (SELECT MAX(login_time) FROM login_history lh WHERE lh.user_id = u.id) AS last_login
        FROM users u ORDER BY u.id
    """)
    return render_template("admin/users.html", users=cursor.fetchall())


@app.route("/admin/users/<int:user_id>/status", methods=["POST"])
@admin_required
def admin_user_status(user_id):
    """Blocks or unblocks an account (blocked users cannot log in)."""
    action = request.form.get("action", "")
    new_status = "blocked" if action == "block" else "active"
    if user_id == session["user_id"]:
        flash("You cannot block your own account.", "error")
        return redirect(url_for("admin_users"))
    db = get_db()
    db.execute("UPDATE users SET status = ? WHERE id = ?", (new_status, user_id))
    db.commit()
    log_activity("user_blocked" if new_status == "blocked" else "user_unblocked",
                 user_id=user_id, username=session.get("username"))
    flash("Saved.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_user_reset_password(user_id):
    """Admin sets a new temporary password for a user."""
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8 or not re.search(r"[A-Za-z]", new_password) or not re.search(r"\d", new_password):
        flash("New password must be at least 8 characters and include a letter and a number.", "error")
        return redirect(url_for("admin_users"))
    db = get_db()
    db.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new_password), user_id))
    db.commit()
    log_activity("password_reset_by_admin", user_id=user_id, username=session.get("username"))
    flash("Password changed.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    """Deletes a user and all their data. An admin cannot delete their own
    account here (keeps at least one admin alive)."""
    if user_id == session["user_id"]:
        flash("You cannot delete your own account here.", "error")
        return redirect(url_for("admin_users"))
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        flash("User not found.", "error")
        return redirect(url_for("admin_users"))
    for table in ("documents", "reading_sessions", "word_events", "user_settings", "login_history"):
        cursor.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    log_activity("account_deleted", user_id=None, username=row["username"], details="by admin")
    flash("User deleted.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/activity")
@admin_required
def admin_activity():
    """Activity log with user + date filters."""
    filter_user = request.args.get("user", "").strip()
    filter_date = request.args.get("date", "").strip()
    query = "SELECT username, event, ip_address, session_duration, details, created_at FROM activity_log WHERE 1=1"
    params = []
    if filter_user:
        query += " AND username LIKE ?"
        params.append(f"%{filter_user}%")
    if filter_date:
        query += " AND created_at LIKE ?"
        params.append(f"{filter_date}%")
    query += " ORDER BY id DESC LIMIT 300"
    db = get_db()
    rows = db.execute(query, params).fetchall()
    return render_template("admin/activity.html", events=rows,
                           filter_user=filter_user, filter_date=filter_date)


@app.route("/admin/activity/export")
@admin_required
def admin_activity_export():
    """Builds the activity Excel file FROM THE DATABASE at download time."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT username, event, ip_address, session_duration, details, created_at FROM activity_log ORDER BY id DESC")
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Activity"
    sheet.append(["Username", "Event", "IP address", "Session duration", "Details", "Date & time"])
    for row in cursor.fetchall():
        sheet.append([row[0], row[1], row[2], row[3] or "", row[4] or "", row[5]])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.column_dimensions["F"].width = 22

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return send_file(
        out,
        as_attachment=True,
        download_name=f"dyslexilens-activity-{timestamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/admin/database")
@admin_required
def admin_database():
    """Database viewer: users / documents / settings / activity with search,
    pagination, and sensitive columns shown only as 'hidden'."""
    table = request.args.get("table", "users")
    page = max(1, int(request.args.get("page", 1)))
    search = request.args.get("q", "").strip()
    per_page = 25

    TABLES = {
        "users": {"columns": ["id", "username", "email", "first_name", "is_admin", "status", "created_at"], "search": ["username", "email", "first_name"], "order": "id"},
        "documents": {"columns": ["id", "user_id", "filename", "char_count", "created_at"], "search": ["filename"], "order": "id"},
        "user_settings": {"columns": ["user_id", "font_size", "letter_spacing", "line_spacing", "background", "speech_rate", "page_zoom", "voice_guide", "updated_at"], "search": [], "order": "user_id"},
        "activity_log": {"columns": ["id", "user_id", "username", "event", "ip_address", "session_duration", "details", "created_at"], "search": ["username", "event"], "order": "id"},
    }
    if table not in TABLES:
        table = "users"
    spec = TABLES[table]

    where = ""
    params = []
    if search and spec["search"]:
        like = " OR ".join(f"{col} LIKE ?" for col in spec["search"])
        where = f" WHERE ({like})"
        params = [f"%{search}%"] * len(spec["search"])

    db = get_db()
    cursor = db.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table}{where}", params)  # table name from a fixed whitelist
    total = cursor.fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    offset = (page - 1) * per_page

    cursor.execute(
        f"SELECT {', '.join(spec['columns'])} FROM {table}{where} ORDER BY {spec['order']} LIMIT ? OFFSET ?",
        params + [per_page, offset]
    )
    rows = cursor.fetchall()
    columns = spec["columns"]
    hidden_columns = {"users": {"password"}}  # hashes are never selected anyway

    return render_template("admin/database.html", table=table, tables=TABLES.keys(),
                           columns=columns, rows=rows, page=page, pages=pages,
                           total=total, search=search, hidden_columns=hidden_columns)


# Only read-only SELECTs are allowed here. The query also runs on a
# READ-ONLY database connection, so even a crafted statement cannot write.
_BLOCKED_SQL = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|vacuum|attach|detach|pragma)\b", re.IGNORECASE)


@app.route("/admin/query", methods=["GET", "POST"])
@admin_required
def admin_query():
    """Run a read-only SELECT and show the results. Every query is logged."""
    sql = request.form.get("query", "").strip() if request.method == "POST" else ""
    results = None
    columns = None
    error = None

    if sql:
        if not sql.lower().startswith("select"):
            error = "Only SELECT queries are allowed."
        elif _BLOCKED_SQL.search(sql):
            error = "Only read-only SELECT queries are allowed."
        elif ";" in sql.rstrip(";"):
            error = "One query at a time."
        else:
            try:
                ro_conn = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)  # read-only connection
                ro_conn.row_factory = sqlite3.Row
                cursor = ro_conn.execute(sql)
                columns = [d[0] for d in cursor.description] if cursor.description else []
                results = cursor.fetchmany(100)
                ro_conn.close()
                log_activity("sql_query", user_id=session["user_id"], username=session.get("username"), details=sql[:200])
            except sqlite3.Error as exc:
                error = f"SQL error: {exc}"

    return render_template("admin/query.html", sql=sql, results=results, columns=columns, error=error)


# ---------------------------------------------------------------------------
# ERROR PAGES (custom, same design, no raw error details)
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


@app.errorhandler(403)
def forbidden(error):
    return render_template("403.html"), 403


@app.errorhandler(500)
def server_error(error):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=DEBUG_MODE, host="127.0.0.1", port=5000)
