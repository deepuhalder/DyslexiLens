"""Creates the admin account from environment variables.

Usage (set the variables first, in .env or the shell):
    ADMIN_USERNAME=admin
    ADMIN_PASSWORD=choose-a-strong-password

Then run:  python create_admin.py

There is no way to become an admin from the website — only this script.
"""

import os
import sqlite3
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from werkzeug.security import generate_password_hash

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dyslexilens.db")


def main():
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")

    if not username or not password:
        print("Set ADMIN_USERNAME and ADMIN_PASSWORD (in .env or the environment) first.")
        sys.exit(1)

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Make sure the columns exist (app.py owns the full schema; this is a
    # minimal safety net so the script also works on a fresh database).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            first_name TEXT DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("SELECT id, is_admin FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    hashed = generate_password_hash(password)

    if row:
        cursor.execute("UPDATE users SET password = ?, is_admin = 1, status = 'active' WHERE id = ?", (hashed, row[0]))
        print(f"Admin '{username}' updated (is_admin=1).")
    else:
        email = os.environ.get("ADMIN_EMAIL", f"{username}@admin.local")
        cursor.execute(
            "INSERT INTO users (username, email, password, first_name, is_admin, status) VALUES (?, ?, ?, ?, 1, 'active')",
            (username, email, hashed, username.capitalize())
        )
        print(f"Admin '{username}' created.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
