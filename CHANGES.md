# DyslexiLens: November update

This file lists every change in this update, in simple words: what changed,
why, and where it is in the code. Read it together with
`PROJECT_EXPLANATION.md`, which explains the whole project.

---

## 1. New features

### 1.1 Voice login (login page)
- **What:** a "Voice guide" box on the login page. Press **Enter** or click
  **Start**. The app asks: "Would you like to log in using your voice? Say yes
  to use your voice. Say no to log in by typing."
  - **Yes:** "Please say your username." The app fills the box and **spells it
    back** ("d, e, e, p, underscore, t...") and asks if it is correct.
    Then: "For your security, please type your password and press Enter."
  - **No:** it explains how to type the username and password.
  - **Browser cannot listen** (Firefox) or **microphone blocked**: it says so
    and explains how to type.
- **Why the password is typed:** people nearby could hear it, and Chrome sends
  recorded speech to its server to turn it into text.
- **Why there is a Start button:** browsers do not allow a page to make sound
  until the user clicks or presses a key on it.
- **After a voice login** the voice guide stays on (see 1.3).
- **Where:** `templates/login.html` (the box and a hidden `voice_mode` field),
  `static/js/voice.js` section 4, `app.py` → `login()`.

### 1.2 Voice commands (every page after login)
- **What:** a **Voice** button at the bottom right. Click it or press **V**,
  then say one command. Examples: "open settings", "use sample text",
  "read this text", "read aloud", "pause", "stop", "sentence mode",
  "next sentence", "zoom in", "yellow background", "help", "log out".
  The full list is on the Help page.
- A reader command said on another page (for example "read aloud" on the
  Settings page) opens the reader first and then runs.
- Choosing a file needs a real click (browser rule), so "upload" moves the
  focus to the file chooser and tells the user to press Enter.
- **Where:** `templates/base.html` (the button), `static/js/voice.js` section 5.

### 1.3 Voice guide setting
- **What:** when it is on, every page says its name and any message aloud,
  for example "Log in page. Wrong username or password."
- Turned on automatically after a voice login. Can be changed in Settings or
  by saying "voice guide on" / "voice guide off".
- **Where:** `static/js/voice.js` section 3, database column
  `user_settings.voice_guide`.

### 1.4 Page zoom (A−, A, A+)
- **What:** three buttons in the header make the **whole page** bigger or
  smaller, from 100% to 150%. Also on the keyboard (+, −, 0) and by voice.
- Saved per user. Before login it is remembered in the browser.
- **How it works:** it changes the root font size. Everything sized in `rem`
  (text, buttons, spacing) grows together.
- **Where:** `templates/base.html`, `static/js/voice.js` section 2, database
  column `user_settings.page_zoom`.

### 1.5 One sentence at a time (reader)
- **What:** a new switch in the reader. It shows only one sentence, using the
  same font, spacing and background, with **Previous**, **Read this sentence**
  and **Next**. Hard words are still clickable.
- **Why:** long, dense paragraphs are tiring. This lets the student take the
  text in small pieces.
- **Where:** `templates/home.html`, `static/js/main.js` section 2c.

### 1.6 Remember where I stopped
- **What:** for saved documents, the sentence reached in sentence mode is
  saved. When the document is opened again from Recent documents, the reader
  says "last read at sentence 4" and sentence mode starts there.
- **Where:** new route `POST /api/documents/<id>/position` in `app.py`, new
  column `documents.last_position`, `saveSentencePosition()` in `main.js`.

### 1.7 Writing pad (new page)
- **What:** Menu → **Writing pad**. Type, or press **Dictate** and speak
  (say "full stop", "comma", "new line" for punctuation). The browser
  underlines spelling mistakes. **Read back** reads the text aloud.
  **Download** saves a `.txt` file. **Open in reader** sends the text to the
  reader. The draft is kept in the browser, separately for each user.
- **Why:** writing and spelling are hard for many dyslexic students, not only
  reading.
- **Where:** `templates/write.html`, `static/js/write.js`, route `/write`.

### 1.8 Keyboard shortcuts
- V voice command, R read aloud or pause, S read in syllables, Esc stop,
  N/P next and previous sentence, + − 0 page size, H help.
- They do nothing while the user is typing in a box.
- **Where:** `static/js/voice.js` section 6.

### 1.9 Help page
- Steps for reading a document, every voice command, every shortcut, voice
  login, the writing pad and the browser limits. Open to everyone.
- **Where:** `templates/help.html`, route `/help`.

---

## 2. Design changes (formal and calm)
- One accent colour only: **navy** (#23395D), for buttons, links and active
  states. The plum colour is gone.
- The header is now **light** (white with a thin line), not a coloured bar.
- Pages stay light. **No dark backgrounds anywhere.** The soft reading colours
  (peach, cream, yellow, turquoise) are used only inside the reading box.
- Removed: card tilting and lifting on hover, the glow behind the login box,
  entrance animations, the coloured badges, the "LISTEN" capital letters.
- Buttons are at least 44px tall, so they are easy to click.
- Landing page simplified to three plain steps: Scan, Read, Listen.
- The Lexend font is now stored in `static/fonts`, so it also works offline.
- Line focus was already removed earlier. Its old database column
  (`focus_mode`) is kept but no longer used.

---

## 3. Bugs found and fixed
1. **Repeated words changed their capitals.** In the reader,
   "many people with dyslexia" showed as "Dyslexia", because a repeated word
   copied the first spelling. Fixed in `analyze_tough_words()` in `app.py`.
2. **The Settings page did not save anything.** The form fields had no
   `name`, so "Saved." appeared but nothing changed. Fixed in
   `templates/settings.html` and `settings_page()` in `app.py`.
3. **"Delete my account" and the admin "Reset password" / "Delete" buttons did
   nothing.** Their code was in inline `<script>` tags, which the
   Content-Security-Policy blocks. Moved into `static/js/main.js`.
4. **The sample PDF was cut off** (lines ran past the page edge). Replaced
   with a clean one-page PDF.
5. **Small template error:** a `<div>` in the profile menu was never closed.

---

## 4. Database changes (added automatically when the app starts)
| Table | New column | Meaning |
|---|---|---|
| user_settings | page_zoom | Page size, 100 to 150 (%) |
| user_settings | voice_guide | 1 = speak page names and messages |
| documents | last_position | Last sentence reached (0 = start) |

Nothing is deleted. Old data keeps working.

---

## 5. Tests
- `python test_app.py` now runs **28 tests** (was 21). All pass.
- New tests: zoom limits, the settings form saving, voice login turning on the
  voice guide, saving the sentence position (and another user not being able
  to change it), the capital-letters bug, the help and writing pages, and a
  check that no page has an inline script.
- The voice features were also tested in a real browser with a simulated
  microphone (voice login yes/no, every main command, dictation).

---

## 6. Please test these on your laptop (they need a real microphone)
Use **Chrome or Edge**, with internet. When the browser asks for the
microphone, click **Allow**.
1. Restart the app: `python app.py`, then open `127.0.0.1:5000/login`.
2. Press **Enter**, say **"yes"**, say your username, say **"yes"**, then type
   your password.
3. Click **Voice** (or press **V**) and say **"use sample text"**, then
   **"read this text"**, then **"read aloud"**.
4. Say **"sentence mode"**, then **"next sentence"**.
5. Open the Writing pad, press **Dictate** and say a sentence ending with
   "full stop".

If a username with a number is not heard correctly, simply type it. Speech
recognition is not perfect.

---

## 7. Limits to mention honestly
- Voice input works only in Chrome and Edge, needs the internet and
  microphone permission.
- Voice commands are simple phrase matching, in English only.
- A page cannot speak until the user clicks or presses a key once.
- One-sentence mode guesses sentence ends from full stops and line breaks,
  so abbreviations like "e.g." can split a sentence.
- The writing pad draft is kept in the browser, not on the server.

---

## 8. If sir asks "is this research-backed?"
- **Reading aloud (text-to-speech):** yes. A 2017 meta-analysis
  (Perelmutter and colleagues, *Computers & Education*) found a small positive
  effect on reading comprehension for adolescents and adults with learning
  disabilities.
- **Warm reading backgrounds:** Rello and Bigham (2017, ACM ASSETS).
- **Dictation, sentence mode, zoom:** comfort and access features. The same
  meta-analysis could not measure the effect of speech-to-text because the
  studies were too different, so do not call these "proven".
