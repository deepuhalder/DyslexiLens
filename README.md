# DyslexiLens

A dyslexia-friendly reading web app. Upload a PDF or a photo of a page, and DyslexiLens turns it into text you can resize, respace and listen to.

**Project-1, Software Engineering & Project Management Lab, IEM Kolkata**
Team: Deepsikha Halder (39), Anushka Maitra (33), Sim Akhter (40)
Guide: Dr. Rupayan Das

## Features
- Text from PDF, JPG and PNG (PDF text layer, or Tesseract OCR for scans)
- Font size, letter spacing, line spacing and warm reading backgrounds
- Hard words highlighted and split into syllables
- Read aloud: the whole text, one sentence, or one word syllable by syllable
- One sentence at a time, and the app remembers where you stopped
- Voice login (username by voice, password typed), voice commands, keyboard shortcuts
- Writing pad with dictation, spellcheck and read-back
- Page zoom (100% to 150%) and a Help page
- Admin panel: users, activity log, Excel export, read-only SQL page

## Tech
Python, Flask, SQLite, HTML/CSS/JavaScript, PyMuPDF, Tesseract OCR, wordfreq, pyphen, Web Speech API

## How to run
1. Install Python 3 and Tesseract OCR.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your own values.
4. `python create_admin.py` (creates the admin account)
5. `python app.py`, then open http://127.0.0.1:5000

Voice features work in Chrome and Edge and need microphone permission.

## Tests
`python test_app.py`
