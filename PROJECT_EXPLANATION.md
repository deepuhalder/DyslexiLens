# DyslexiLens — Project Explanation

This document explains the whole project in simple language: what every file
does, every route (URL, method, who can access it), every database table and
column, every feature (file + function), and every security measure with a
one-line explanation.

---

## a) Folder structure — every file and what it does

```
DyslexiLens/
├── app.py                    ← The WHOLE backend (Flask): routes, database,
│                                OCR, text cleaning, tough-word detection,
│                                syllables, settings, progress, exports,
│                                password reset, admin pages, security.
├── create_admin.py           ← Creates the admin account from env variables
│                                (ADMIN_USERNAME / ADMIN_PASSWORD). No website
│                                path can make a user an admin.
├── dyslexilens.db            ← SQLite database (created automatically).
├── requirements.txt          ← Python libraries to install.
├── test_app.py               ← Automated backend tests (28, all passing).
├── CHANGES.md                ← What changed in the November update, and why.
├── .env.example              ← Placeholder environment variables; copy to .env.
├── .gitignore                ← Keeps .env, databases and logs out of git.
├── PROJECT_EXPLANATION.md    ← This file.
├── .freebuff/run.md          ← How to start the server (developer notes).
├── sample_document.pdf       ← Sample PDF for the tests.
├── sample_quote.png          ← Sample image for the tests.
│
├── templates/                ← HTML pages (Jinja2)
│   ├── base.html             ← Shared frame: header, profile menu (Settings /
│   │                            Profile / Log out, Admin for admins), flashes,
│   │                            favicon, CSRF meta tag. No footer by design.
│   ├── landing.html          ← Public landing page.
│   ├── login.html            ← Log in (show/hide password eye) + the voice
│   │                            guide card (log in using your voice).
│   ├── signup.html           ← Sign up: first name, username, email,
│   │                            password + confirm.
│   ├── forgot_password.html  ← Ask for a reset link (or contact-admin note).
│   ├── reset_password.html   ← Choose a new password from a one-time link.
│   ├── home.html             ← Reader workspace: greeting, stats, upload,
│   │                            review step, audio bar, sliders,
│   │                            magnifier + "one sentence at a time"
│   │                            toggles, reading text, first-time hint.
│   ├── settings.html         ← Reading defaults + page size + voice guide.
│   ├── help.html             ← How to use, voice commands, keyboard shortcuts.
│   ├── write.html            ← Writing pad: type or dictate, read back, download.
│   ├── profile.html          ← Edit details, change password, delete account.
│   ├── 404.html / 403.html / 500.html ← Custom error pages ("Go home").
│   └── admin/                ← Admin-only pages
│       ├── base_admin.html   ← Shared admin layout + sub-navigation.
│       ├── dashboard.html    ← Totals + recent activity.
│       ├── users.html        ← User management (block, reset, delete).
│       ├── activity.html     ← Activity log with filters + Excel download.
│       ├── database.html     ← Table viewer (search, pagination).
│       └── query.html        ← Read-only SELECT box.
│
└── static/
    ├── fonts/                ← Lexend font files (stored locally, works offline).
    ├── css/style.css         ← All styling (navy accent, light pages, peach
    │                            reading box, admin tables, responsive).
    └── js/
        ├── main.js           ← Reader behaviour: audio engine, tough-word
        │                        popups, magnifier, one-sentence mode, upload
        │                        flow, settings, profile/admin buttons, hint.
        ├── voice.js          ← Page zoom, voice guide, voice login, voice
        │                        commands and keyboard shortcuts.
        └── write.js          ← Writing pad: dictation, read back, download.
```

---

## b) Full flow, step by step

1. **Landing** — `/` shows what the app does with Log in / Sign up.
2. **Sign up** — First name, username, email, password (min 8 chars with a
   letter and a number). Password is hashed before storage.
3. **Log in** — Optionally with the voice guide: press Start, say "yes",
   say the username (the app spells it back), then TYPE the password. The
   hash is compared. 5 failed attempts lock the username + IP
   for 15 minutes. On success the session ID is renewed and the workspace
   opens fresh.
4. **Scan / Upload** — Pick a PDF/JPG/PNG (max 10 MB) and press Scan. The
   real file type is verified by magic bytes; PDFs use their text layer,
   scans/photos get cleaned up and OCR'd.
5. **Review** — The extracted text opens in an editable box to fix mistakes.
6. **Reader** — The text loads into the reading sandbox; hard words get a
   soft tint with syllable popups; read aloud, magnifier, sliders,
   background colours and "one sentence at a time" all work here. Everything
   can also be done by voice command or keyboard shortcut.
7. **Writing pad** — Type or dictate, hear it read back, download it, or
   open it in the reader.
8. **Progress** — Documents read, words read aloud and hard words practised
   are stored and shown as three simple numbers, exportable to Excel.

---

## c) Each feature: which file and function, and how it works

| Feature | Where | How it works |
|---|---|---|
| **Syllable breakdown** | `app.py` → `syllabify_word()` | Hand-fixed dictionary first (dyslexia → dys-lex-i-a), then pyphen, then a vowel-group fallback. Never single letters. |
| **Pronunciation override** | `app.py` → `SAY_OVERRIDES`; `main.js` → `audio.spokenOverrides` | "DyslexiLens" is spoken as "dyslexia lens". |
| **Tough-word detection** | `app.py` → `analyze_tough_words()` | Tough = 7+ letters AND Zipf frequency < 4.0. Runs fresh on any text. |
| **Hard-word styling** | `style.css` → `.tough-word` | ONE subtle mark: a soft rounded tint, normal weight, same text colour. No underline, no bold, no italics. |
| **Tough-word popup** | `main.js` → `openSyllablePopupFor()` | Click/hover opens a popup with the word, its syllables and audio — meaning never relies on colour alone. |
| **Read whole / in syllables** | `main.js` → `startReading()`, `buildChain()` | One utterance chain; syllable mode speaks syllables slowly then the whole word; the current word is highlighted. |
| **Pause / Resume / Stop** | `main.js` → `pauseReading()`, `resumeReading()`, `stopReading()` | Pause cancels and remembers the position; `audio.suppressEnd` stops Chrome's cancel-events ending the session. |
| **Line magnifier** | `main.js` → `updateMagnifier()`, `getTextOfLine()`; CSS `#line-magnifier-strip` | Optional strip just above the text mirrors the hovered line at 1.5× (strip base font = reader font size × 1.5). The original text never changes. |
| **Backgrounds / sliders** | `home.html`, `settings.html`; `main.js` → `applyBackground()`, slider events; `app.py` → `/api/settings` | Four soft pastels plus font size, letter spacing, line spacing and speed, saved per user. |
| **Review step** | `home.html` review panel; `main.js` → `openReview()` | Editable box between scan and read. |
| **Sample text** | `app.py` → `SAMPLE_DOCUMENT_TEXT`, `/api/sample-document` | One click loads built-in text through the review step. |
| **Recent documents** | `app.py` → `/api/documents` (GET/DELETE); `main.js` recent menu | List with dates; open goes through review; delete asks for confirmation. Empty state: "No documents yet." Ownership checked in SQL. |
| **First-time hint** | `home.html` → `#first-time-hint`; `main.js` hint block | "1. Upload a file 2. Check the text 3. Read or listen", dismissed once, never again (localStorage). |
| **Profile** | `app.py` → `profile_update()`, `profile_password()`, `profile_delete()` | Edit details with duplicate checks; change password needs the current one; delete account needs the password and removes the user's documents and settings. |
| **Password reset** | `app.py` → `forgot_password()`, `reset_password()` | One-time token, 30-minute expiry, SMTP from env vars. Without SMTP: "Contact the admin to reset your password." |
| **Admin suite** | `app.py` → `admin_*` routes; `templates/admin/*` | Dashboard totals, user management (block/unblock/reset/delete — never yourself), activity log with filters + Excel built at download time, database viewer, read-only SQL box. |
| **One sentence at a time** | `main.js` section 2c → `buildSentences()`, `renderSentence()`, `goToSentence()` | Splits the reader text into sentences (a full stop, ! or ? followed by a space, a blank line, or a short heading line). Shows one with Previous / Next / Read this sentence. Hard words stay clickable. |
| **Remember where I stopped** | `app.py` → `/api/documents/<id>/position`, column `documents.last_position`; `main.js` → `saveSentencePosition()` | Every move in sentence mode saves the sentence number for saved documents. Reopening from Recent documents starts there. |
| **Page zoom** | `voice.js` section 2 → `setZoom()`; `base.html` `<html style="font-size: …%">`; column `user_settings.page_zoom` | A−/A/A+ change the root font size from 100% to 150%. Everything sized in rem grows together. Saved per user (per browser before login). |
| **Voice guide** | `voice.js` section 3; column `user_settings.voice_guide` | When on, each page speaks its name and messages. Turned on automatically after a voice login; can be changed in Settings or by saying "voice guide off". |
| **Voice login** | `login.html` voice card; `voice.js` section 4; `app.py` `login()` reads `voice_mode` | Start → "Would you like to log in using your voice?" → yes: username by voice, spelled back for checking → password always typed. No / no microphone / unsupported browser → typing instructions. |
| **Voice commands** | `voice.js` section 5 → `COMMANDS`, `findCommand()`, `runCommand()` | Voice button or V key, then one phrase. Matches simple phrases ("read aloud", "next sentence", "open settings"). Reader commands spoken on another page open `/home?voice=…` and run there. |
| **Keyboard shortcuts** | `voice.js` section 6 | V voice, R read/pause, S syllables, Esc stop, N/P sentences, + − 0 zoom, H help. Ignored while typing in a box. |
| **Writing pad** | `write.html`; `write.js` | Dictation (continuous speech recognition, spoken punctuation), browser spellcheck, read back, .txt download, "Open in reader" (sessionStorage hand-off), draft kept per user in localStorage. |
| **Help page** | `help.html`, route `/help` | Steps, all voice commands and shortcuts, and the browser limits. |
| **Excel exports** | `app.py` → `/export/excel`, `/admin/activity/export` | Built from the database at the moment of download (openpyxl), never a file on disk. |

---

## d) Database tables and their columns

All tables live in `dyslexilens.db` (SQLite). Created by `init_db()` in `app.py`.

**users**
| Column | Meaning |
|---|---|
| id | Row number (primary key) |
| username | Account name (unique) |
| email | Email address (unique) |
| password | HASHED password (never plain text) |
| first_name | The capitalized name shown in the interface |
| is_admin | 1 for admins (only set by create_admin.py) |
| status | "active" or "blocked" |
| created_at | When the account was made |

**login_history**
| Column | Meaning |
|---|---|
| id | Row number |
| user_id | Which user |
| login_time / logout_time | Session window (logout empty while active) |

**documents**
| Column | Meaning |
|---|---|
| id | Row number |
| user_id | Owner (all queries filter by it) |
| filename | Original file name |
| raw_text / clean_text | Extracted text (clean_text is shown) |
| char_count | Size of the text |
| last_position | Last sentence reached in one-sentence mode (0 = start) |
| created_at | When it was uploaded |

**reading_sessions**
| Column | Meaning |
|---|---|
| id, user_id | Row number, which user |
| document_id / filename | What was read |
| words_total / words_spoken / seconds_read | Amounts |
| started_at / ended_at | Time window |

**word_events**
| Column | Meaning |
|---|---|
| id, user_id, document_id | Row number, user, document |
| word | The hard word clicked/practised |
| event_type | e.g. word_practiced |
| created_at | When |

**user_settings** (one row per user)
| Column | Meaning |
|---|---|
| user_id | The user (primary key) |
| font_size (default 24 px = 18 pt), letter_spacing, line_spacing | Reader layout |
| background | peach / cream / yellow / turquoise |
| focus_mode | Old column, no longer used (line focus was removed) |
| speech_rate | Saved speech speed |
| page_zoom | Page size in percent (100 to 150) |
| voice_guide | 1 = speak page names and messages aloud |
| updated_at | Last change |

**password_reset_tokens**
| Column | Meaning |
|---|---|
| id, user_id | Row number, which user |
| token | One-time random token (shown as "hidden" in the admin viewer) |
| expires_at | 30 minutes after the request |
| used | Set to 1 after use |
| created_at | When |

**activity_log**
| Column | Meaning |
|---|---|
| id | Row number |
| user_id / username | Who (username kept after account deletion) |
| event | login, logout, login_failed, login_locked, registration (via signup logging), password_change, password_reset, account_deleted, user_blocked/unblocked, sql_query, admin_access_denied |
| ip_address | Visitor IP |
| session_duration | On logout, e.g. "4m 12s" |
| details | e.g. the SQL query run |
| created_at | When |

---

## e) All Flask routes

| Route | Method | Access | What it does |
|---|---|---|---|
| `/` | GET | public | Landing page (logged-in users go to /home). |
| `/signup` | GET/POST | public | Create account (first name, username, email, password rules, duplicate checks). |
| `/login` | GET/POST | public | Log in; rate limited (5 fails → 15 min lock). |
| `/logout` | GET | any | Ends the session, records duration, back to /login. |
| `/forgot-password` | GET/POST | public | Sends a one-time reset link (or shows the contact-admin note). |
| `/reset-password/<token>` | GET/POST | public | One-time token (30 min) → new password. |
| `/home` | GET | login | The reader workspace (always fresh). |
| `/upload` | POST | login | PDF/JPG/PNG → extracted text (magic-byte verified, 10 MB cap). |
| `/api/analyze-text` | POST | login | Tough-word analysis of any text. |
| `/api/syllabify` | GET | login | Syllables + spoken form of one word. |
| `/api/sample-document` | GET | login | Built-in sample text. |
| `/api/settings` | GET/POST | login | Read/save reader settings (validated, clamped). |
| `/api/documents` | GET | login | The user's recent documents. |
| `/api/documents/<id>` | GET | login | One document's text — only the owner's. |
| `/api/documents/<id>` | DELETE | login | Delete one document — only the owner's. |
| `/api/documents/<id>/position` | POST | login | Save the last sentence reached — only the owner's. |
| `/help` | GET | public | Help: steps, voice commands, keyboard shortcuts. |
| `/write` | GET | login | Writing pad. |
| `/api/progress` | GET/POST | login | Progress events + summary. |
| `/export/excel` | GET | login | Personal progress workbook. |
| `/settings` | GET/POST | login | Settings page (save defaults). |
| `/profile` | GET | login | Profile page. |
| `/profile/update` | POST | login | Save first name/username/email. |
| `/profile/password` | POST | login | Change password (needs current). |
| `/profile/delete` | POST | login | Delete account (needs password). |
| `/admin` | GET | **admin** | Admin dashboard. |
| `/admin/users` | GET | **admin** | User management. |
| `/admin/users/<id>/status` | POST | **admin** | Block/unblock (never yourself). |
| `/admin/users/<id>/reset-password` | POST | **admin** | Set a new password for a user. |
| `/admin/users/<id>/delete` | POST | **admin** | Delete a user (never yourself). |
| `/admin/activity` | GET | **admin** | Activity log with user/date filters. |
| `/admin/activity/export` | GET | **admin** | Activity log as Excel, built live. |
| `/admin/database` | GET | **admin** | Table viewer (search, pagination, hashes/tokens "hidden"). |
| `/admin/query` | GET/POST | **admin** | Read-only SELECT box (logged). |
| anything else | — | — | Custom 404 / 403 / 500 pages. |

---

## f) Security measures (one line each)

- **Hashed passwords** — werkzeug `generate_password_hash`; plain text is never stored or shown.
- **Parameterized SQL** — every query uses `?` placeholders; no string-joined SQL (table names in the admin viewer come from a fixed whitelist).
- **CSRF protection** — Flask-WTF `CSRFProtect` on every form; all JS POSTs send the `X-CSRFToken` header from a meta tag.
- **Escaped content** — Jinja auto-escaping everywhere; `|safe` is never used on user input.
- **Login rate limiting** — 5 failed attempts lock the username + IP for 15 minutes and are logged.
- **Server-side roles** — every `/admin/*` route re-checks `is_admin` in the database (`admin_required`); hiding links is not the security boundary.
- **Upload safety** — only PDF/JPG/PNG, verified by magic bytes (and parsing libraries), 10 MB cap, text is extracted and the file itself is never stored or served.
- **Session security** — HttpOnly + SameSite cookies (Secure behind HTTPS), 30-minute inactivity logout (`PERMANENT_SESSION_LIFETIME` + `session.permanent`), `session.clear()` then re-set on login (new session ID).
- **Secrets from environment** — SECRET_KEY, admin credentials and SMTP come from `.env` (python-dotenv); `.env` is gitignored, `.env.example` has placeholders only.
- **Debug off by default** — the debugger only starts with `FLASK_DEBUG=1`.
- **Security headers** — every response sends X-Content-Type-Options, X-Frame-Options, Referrer-Policy and a basic Content-Security-Policy.
- **No-cache** — `Cache-Control: no-store` on every response, so the Back button after logout cannot show protected pages.
- **Ownership checks** — documents and settings are always queried with the session user's id; other users' rows return 404.
- **Admin creation offline** — `create_admin.py` from env variables; the website can never grant the role.
- **Query logging** — every admin SQL query is written to the activity log with the user's name.
- **Passwords are never spoken** — voice login takes only the username by voice; people nearby could hear a spoken password, and the audio goes to the browser's speech server.
- **No inline scripts** — the Content-Security-Policy allows scripts only from our own files, so all page code lives in `static/js/`.
- **Local fonts** — Lexend is served from `static/fonts`, so the policy can be `font-src 'self'` (no outside font servers).
- **Writing drafts per user** — the writing pad keeps drafts under a key that includes the user id, so people sharing a computer do not see each other's text.

---

## g) All libraries used and why

**Python (requirements.txt)**
| Library | Why it is needed |
|---|---|
| Flask | The web framework: routes, pages, sessions. |
| Flask-WTF | CSRF protection on every form. |
| python-dotenv | Loads `.env` (SECRET_KEY, admin + SMTP settings). |
| Werkzeug | Password hashing (comes with Flask). |
| PyMuPDF (fitz) | Reads digital PDF text layers; renders scanned pages to images. |
| pdf2image | Alternative PDF→image converter (used when Poppler is available). |
| Pillow (PIL) | Image cleanup before OCR: grayscale, contrast, rotation, upscaling. |
| pytesseract | Talks to the Tesseract OCR engine. |
| wordfreq | Zipf frequency of English words for the rare-word rule. |
| pyphen | Hyphenation dictionary for real syllables. |
| openpyxl | Builds the Excel exports. |

**Browser** — Web Speech API: `speechSynthesis` (read aloud, all modern
browsers) and `SpeechRecognition` (voice login, voice commands, dictation;
Chrome and Edge only, needs internet and microphone permission). Lexend font
(stored locally in `static/fonts`).
**External program** — Tesseract OCR (Windows path configured in `app.py`).

---

## Quick facts for questions

- **Why peach?** Research (Rello & Bigham, ACM ASSETS 2017) found soft warm
  backgrounds improved reading for dyslexic users. The app also offers Cream,
  Soft Yellow and Soft Turquoise — never pure white.
- **Why is a word only hard when long AND rare?** Flagging every long word
  would mark common words like "understanding". Requiring both keeps the
  tinting useful.
- **How do the syllable pauses work?** Each syllable is a separate
  SpeechSynthesisUtterance queued one after another.
- **What does the magnifier change?** Nothing in the original text — it only
  copies the hovered line into a strip above it, at 1.5×.
- **How is progress kept but not noisy?** Everything is stored in the database
  (and exportable), while the page shows just three numbers.
- **Why is the password not spoken in voice login?** Anyone nearby could hear
  it, and Chrome sends recorded speech to its server to turn it into text. So
  the username can be spoken, but the password is always typed.
- **Why can the login page not speak as soon as it opens?** Browsers block
  sound until the user clicks or presses a key on the page. That is why the
  voice guide starts with "Press Enter or click Start".
- **Which browsers support voice input?** Chrome and Edge. Other browsers
  still get every feature by mouse and keyboard; the app says so politely.
- **Is "one sentence at a time" proven by research?** No. It is a comfort
  option for long, dense text. The research-backed part of the app is reading
  aloud (text-to-speech), which a 2017 meta-analysis by Perelmutter and
  colleagues found had a small positive effect on reading comprehension.

