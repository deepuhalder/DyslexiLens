import os
import sqlite3
import unittest
from unittest.mock import patch
from werkzeug.security import check_password_hash
from app import app, init_db, DATABASE, analyze_tough_words, TOUGH_MAX_ZIPF


class DyslexiLensTestCase(unittest.TestCase):
    def setUp(self):
        import app as dyslexi_app
        self.test_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_dyslexilens.db")
        dyslexi_app.DATABASE = self.test_db
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test_secret_key"
        app.config["WTF_CSRF_ENABLED"] = False  # tests post directly; CSRF is verified separately
        self.client = app.test_client()

        # Re-initialize fresh test db
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except Exception:
                pass
        dyslexi_app.init_db()

    def tearDown(self):
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except Exception:
                pass

    def test_database_tables_exist(self):
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("users", tables)
        self.assertIn("login_history", tables)
        conn.close()

    def test_signup_and_password_hashing(self):
        # 1. Signup request
        response = self.client.post("/signup", data={
            "first_name": "dyslexia",
            "username": "dyslexia_friend",
            "email": "friend@example.com",
            "password": "Password123"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Account created", response.data)

        # 2. Check DB storage and password hashing
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT username, email, password FROM users WHERE username = ?", ("dyslexia_friend",))
        user = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(user)
        self.assertEqual(user[0], "dyslexia_friend")
        self.assertEqual(user[1], "friend@example.com")
        # Ensure password is NEVER stored plain text
        self.assertNotEqual(user[2], "Password123")
        self.assertTrue(check_password_hash(user[2], "Password123"))

    def test_login_and_login_history(self):
        # First sign up
        self.client.post("/signup", data={
            "first_name": "Claire",
            "username": "reader_claire",
            "email": "claire@example.com",
            "password": "CalmPassword456"
        }, follow_redirects=True)

        # Invalid password
        bad_login = self.client.post("/login", data={
            "username": "reader_claire",
            "password": "WrongPassword"
        }, follow_redirects=True)
        self.assertIn(b"Wrong username or password", bad_login.data)

        # Valid login: lands on the workspace, greeted by FIRST NAME
        # capitalized (never the lowercase username)
        good_login = self.client.post("/login", data={
            "username": "reader_claire",
            "password": "CalmPassword456"
        }, follow_redirects=True)
        self.assertEqual(good_login.status_code, 200)
        self.assertIn(b"Hi, Claire", good_login.data)

        # Fresh workspace: profile menu (username + log out only), NO session
        # history, NO previously scanned text auto-loaded
        self.assertIn(b"profile-menu", good_login.data)
        self.assertIn(b"Your scanned text will appear here", good_login.data)
        self.assertNotIn(b"Session History", good_login.data)
        self.assertNotIn(b"Account Details", good_login.data)

        # Check login_history has a row with null logout_time
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, login_time, logout_time FROM login_history")
        history = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(history)
        self.assertIsNotNone(history[2])  # login_time exists
        self.assertIsNone(history[3])     # logout_time is None during active session

    def test_logout_updates_login_history(self):
        # 1. Sign up
        self.client.post("/signup", data={
            "first_name": "Sam",
            "username": "sam_reader",
            "email": "sam@example.com",
            "password": "SafePassword789"
        }, follow_redirects=True)

        # 2. Login
        self.client.post("/login", data={
            "username": "sam_reader",
            "password": "SafePassword789"
        }, follow_redirects=True)

        # 3. Logout
        logout_resp = self.client.get("/logout", follow_redirects=True)
        self.assertEqual(logout_resp.status_code, 200)
        # (the apostrophe is HTML-escaped in the page: You&#39;ve ...)
        self.assertIn(b"been logged out", logout_resp.data)

        # 4. Check DB: logout_time should now be populated
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT login_time, logout_time FROM login_history WHERE user_id = (SELECT id FROM users WHERE username = 'sam_reader')")
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])  # login_time
        self.assertIsNotNone(row[1])  # logout_time updated!

    def test_upload_pdf_extraction(self):
        # 1. Sign up & login
        self.client.post("/signup", data={
            "first_name": "Pdf",
            "username": "pdf_tester",
            "email": "pdf@test.org",
            "password": "Password123"
        }, follow_redirects=True)
        self.client.post("/login", data={
            "username": "pdf_tester",
            "password": "Password123"
        }, follow_redirects=True)

        # 2. Upload sample_document.pdf via the XHR path (the workspace is a
        # fresh scan screen now; the extracted text is returned as JSON and
        # stored under Recent documents, never auto-loaded on /home)
        with open("sample_document.pdf", "rb") as f:
            resp = self.client.post("/upload", data={
                "document": (f, "sample_document.pdf")
            }, headers={"X-Requested-With": "XMLHttpRequest"})

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("Understanding Dyslexia", data["text"])
        self.assertIsNotNone(data.get("document_id"))

        # 3. The document is retrievable through the Recent documents API
        doc = self.client.get(f"/api/documents/{data['document_id']}").get_json()
        self.assertTrue(doc["success"])
        self.assertIn("Understanding Dyslexia", doc["document"]["text"])

    def test_upload_image_ocr(self):
        # 1. Sign up & login
        self.client.post("/signup", data={
            "first_name": "Img",
            "username": "img_tester",
            "email": "img@test.org",
            "password": "Password123"
        }, follow_redirects=True)
        self.client.post("/login", data={
            "username": "img_tester",
            "password": "Password123"
        }, follow_redirects=True)

        # 2. Upload sample_quote.png via the XHR path (see note above)
        with open("sample_quote.png", "rb") as f:
            resp = self.client.post("/upload", data={
                "document": (f, "sample_quote.png")
            }, headers={"X-Requested-With": "XMLHttpRequest"})

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("Accessible Reading Workspace", data["text"])

    # ------------------------------------------------------------------
    # Tough Word Detection
    # ------------------------------------------------------------------

    def login_user(self, username, password):
        # Always start from a clean session: /signup and /login both redirect
        # away when a session is already active, so log out first.
        self.client.get("/logout")
        self.client.post("/signup", data={
            "first_name": username.capitalize(),
            "username": username,
            "email": f"{username}@test.org",
            "password": password
        }, follow_redirects=True)
        return self.client.post("/login", data={
            "username": username, "password": password
        }, follow_redirects=True)

    def test_analyze_tough_words_rule(self):
        """Tough = LONG (7+ letters) AND RARE (zipf < 4.0). Both must hold."""
        text = "The comprehension was straightforward. Different elephants understand dyslexia."
        tokens = analyze_tough_words(text)
        flagged = {t["word"].lower() for t in tokens if t["tough"]}

        # Rare AND long -> flagged, with pyphen syllable breakdown
        self.assertIn("dyslexia", flagged)
        # Long but COMMON -> never flagged (zipf 5.59 / 4.82 are above the threshold).
        # ('elephants' IS correctly flagged: its plural form is genuinely rare.)
        self.assertNotIn("different", flagged)
        self.assertNotIn("understand", flagged)
        # Short (but rare) -> not flagged on length alone
        self.assertNotIn("the", flagged)
        self.assertNotIn("was", flagged)

        # Flagged entries carry syllables + zipf; normal ones do not
        for t in tokens:
            if t["tough"]:
                self.assertIn("syllables", t)
                self.assertIn("-", t["syllables"])
                self.assertLess(t["zipf"], TOUGH_MAX_ZIPF)
            else:
                self.assertNotIn("syllables", t)

    def test_analyze_tough_words_uses_both_conditions(self):
        """Neither long-only nor rare-only words may be flagged."""
        tokens = analyze_tough_words("Spectacular xyzqw read go")
        flagged = {t["word"].lower() for t in tokens if t["tough"]}
        # rare-only (short): xyzqw is rare but < 7 letters
        self.assertNotIn("xyzqw", flagged)
        # normal short common words
        self.assertNotIn("read", flagged)
        self.assertNotIn("go", flagged)

    def test_analyze_api_requires_login(self):
        resp = self.client.post("/api/analyze-text", json={"text": "hello world"})
        self.assertEqual(resp.status_code, 302)  # redirected to login

    def test_analyze_api_flags_words_and_returns_syllables(self):
        self.login_user("api_tester", "Password123")

        text = ("Dyslexia and neurodiversity require comprehension of "
                "assistive technology and straightforward different understanding")
        resp = self.client.post("/api/analyze-text", json={"text": text})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertTrue(data["success"])
        self.assertGreater(data["tough_count"], 0)
        self.assertLess(data["tough_count"], data["word_count"])  # some, not everything

        words = {t["word"].lower(): t for t in data["tokens"]}
        self.assertTrue(words["dyslexia"]["tough"])
        self.assertEqual(words["dyslexia"]["syllables"], "dys-lex-i-a")
        self.assertTrue(words["neurodiversity"]["tough"])
        self.assertFalse(words["different"]["tough"])
        self.assertFalse(words["understanding"]["tough"])
        self.assertNotIn("syllables", words["different"])

    def test_analyze_api_rejects_empty_text(self):
        self.login_user("empty_tester", "Password123")
        resp = self.client.post("/api/analyze-text", json={"text": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["success"])

    def test_analyze_api_on_real_pdf_text(self):
        """On the actual extracted sample document: some words flagged (not all, not none)."""
        with open("sample_document.pdf", "rb") as f:
            resp = self.client.post("/upload", data={
                "document": (f, "sample_document.pdf")
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        from app import extract_text_from_file
        with open("sample_document.pdf", "rb") as f:
            class FS:
                filename = "sample_document.pdf"
                read = staticmethod(lambda: f.read())
            ok, text = extract_text_from_file(FS())
        self.assertTrue(ok)

        self.login_user("pdf_analyzer", "Password123")
        api = self.client.post("/api/analyze-text", json={"text": text})
        data = api.get_json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["tough_count"], 3)   # e.g. dyslexia, assistive, legibility
        self.assertLess(data["tough_count"], data["word_count"])

    def test_analyze_api_graceful_without_wordfreq(self):
        """If the frequency DB is unavailable, words must degrade to normal (no crash)."""
        self.login_user("degraded", "Password123")
        with patch("app.zipf_frequency", side_effect=RuntimeError("db missing")):
            resp = self.client.post("/api/analyze-text",
                                    json={"text": "Dyslexia comprehension test"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tough_count"], 0)
        self.assertTrue(all(not t["tough"] for t in data["tokens"]))

    # ------------------------------------------------------------------
    # Dynamic-analysis guarantees: the rule runs fresh on ANY text
    # ------------------------------------------------------------------

    LONG_DOCUMENT_TEXT = """The Antikythera mechanism is an ancient Greek hand-powered orrery, a model of the Solar System, built in the second century BC and discovered in 1901. It is the oldest known example of an analogue computer. It could be used to predict astronomical positions and eclipses decades in advance. It could also be used to track the four-year cycle of athletic games similar to an olympiad.

The artefact was among wreckage retrieved from a shipwreck off the coast of the Greek island Antikythera in 1901. The device, housed in the remains of a wooden-framed case of uncertain overall size, was found as one lump, later separated into three main fragments which are now divided into eighty-two separate fragments after conservation efforts. Four of these fragments contain gears, while inscriptions are found on many others. All these fragments of the mechanism are kept at the museum, along with reconstructions and replicas.

In 2005, a team used computer X-ray tomography and high resolution scanning to image inside fragments of the crust-encased mechanism and read faint inscriptions that once covered the outer casing. These scans suggest that the mechanism had thirty-five meshing bronze gears, enabling it to model the irregular orbit of the Moon, where its speed is higher in its perigee than in its apogee. The inscriptions were further deciphered, revealing numbers connected with the synodic cycles of Venus and Saturn. The mechanism appeared to be a lump of corroded bronze and wood, resulting in deformational changes."""

    def test_analyze_tough_words_long_document_is_dynamic(self):
        """On a long, real document the rule flags some (not all) words, and every
        flag genuinely satisfies LONG (7+) AND RARE (zipf < 4.0)."""
        tokens = analyze_tough_words(self.LONG_DOCUMENT_TEXT)
        words = [t for t in tokens if not t.get("separator")]
        flagged = [t for t in tokens if t["tough"]]

        # Some — but only a minority — flagged: not everything, not nothing
        self.assertGreaterEqual(len(flagged), 10)
        self.assertLess(len(flagged), len(words) * 0.3)

        # Every flag conforms to the rule exactly
        for t in flagged:
            self.assertGreaterEqual(len(t["word"]), 7)
            self.assertLess(t["zipf"], TOUGH_MAX_ZIPF)
            self.assertIn("-", t["syllables"])  # pyphen breakdown present

        flagged_set = {t["word"].lower() for t in flagged}
        # Genuinely difficult (long AND rare) words must be caught...
        for hard in ("shipwreck", "inscriptions", "tomography", "synodic", "wreckage"):
            self.assertIn(hard, flagged_set)
        # ...while long-but-common words must NOT be over-flagged
        for common in ("mechanism", "constructed", "resolution", "complexity", "centuries"):
            self.assertNotIn(common, flagged_set)

    def test_analyze_tough_words_simple_text_flags_nothing(self):
        """A long paragraph with NO genuinely tough words flags zero — never
        force-flagging something just to show the feature working."""
        simple_text = ("The dog ran across the yard and jumped over the fence. "
                       "Every morning she makes breakfast for her family before "
                       "they leave for school and work. The weather was warm and "
                       "sunny, so we decided to walk to the park instead of taking "
                       "the bus. My brother plays football with his friends every "
                       "weekend when the field is dry. Please remember to close the "
                       "door and turn off the lights before you go to bed tonight. "
                       "The teacher asked the students to open their books and read "
                       "the story quietly. We watched the birds build a nest in the "
                       "tree outside our window last spring. After dinner, the "
                       "family sat together and talked about their plans for the "
                       "summer holiday. He carried the heavy box up the stairs and "
                       "set it down near the kitchen table.")
        tokens = analyze_tough_words(simple_text)
        tough = [t for t in tokens if t["tough"]]
        self.assertEqual(len(tough), 0)

    # ------------------------------------------------------------------
    # Reader features: syllabifier overrides, settings, progress, export
    # ------------------------------------------------------------------

    def test_syllabify_endpoint_and_overrides(self):
        """Real syllables (never letters) + pronunciation dictionary."""
        self.login_user("syl_tester", "Password123")
        data = self.client.get("/api/syllabify?word=DyslexiLens").get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["syllables"], "dys-lex-i-a-lens")
        self.assertEqual(data["say"], "dyslexia lens")

        for word, expected in (("vibrate", "vi-brate"), ("contrast", "con-trast"),
                               ("visual", "vis-u-al"), ("dyslexia", "dys-lex-i-a")):
            got = self.client.get(f"/api/syllabify?word={word}").get_json()
            self.assertEqual(got["syllables"], expected)

    def test_sample_document_endpoint(self):
        self.login_user("sample_tester", "Password123")
        data = self.client.get("/api/sample-document").get_json()
        self.assertTrue(data["success"])
        self.assertIn("DyslexiLens", data["text"])
        self.assertGreater(len(data["text"].split()), 50)

    def test_settings_roundtrip_and_validation(self):
        self.login_user("settings_tester", "Password123")
        resp = self.client.post("/api/settings", json={
            "font_size": 22, "letter_spacing": 0.08, "line_spacing": 1.8,
            "background": "yellow", "speech_rate": 1.3,
            "page_zoom": 120, "voice_guide": True
        })
        self.assertEqual(resp.status_code, 200)
        settings = resp.get_json()["settings"]
        self.assertEqual(settings["background"], "yellow")
        self.assertEqual(settings["font_size"], 22)
        self.assertEqual(settings["page_zoom"], 120)
        self.assertEqual(settings["voice_guide"], 1)
        self.assertNotIn("focus_mode", settings)   # line focus was removed

        # Persisted: a second GET returns the same values
        again = self.client.get("/api/settings").get_json()["settings"]
        self.assertEqual(again["background"], "yellow")
        self.assertEqual(again["speech_rate"], 1.3)

        # Invalid background rejected; out-of-range values clamped
        self.assertEqual(self.client.post("/api/settings", json={"background": "sage"}).status_code, 400)
        clamped = self.client.post("/api/settings", json={"font_size": 99}).get_json()["settings"]
        self.assertEqual(clamped["font_size"], 32)

    def test_progress_recording_and_summary(self):
        self.login_user("progress_tester", "Password123")
        self.client.post("/api/progress", json={
            "type": "document_opened", "filename": "book.pdf", "words": 120
        })
        self.client.post("/api/progress", json={
            "type": "reading_session", "filename": "book.pdf",
            "words": 120, "words_spoken": 40, "seconds": 25
        })
        data = self.client.post("/api/progress", json={
            "type": "word_practiced", "word": "dyslexia"
        }).get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["progress"]["documents_read"], 1)
        self.assertEqual(data["progress"]["words_practised"], 1)

        # Unknown event types are rejected cleanly
        self.assertEqual(
            self.client.post("/api/progress", json={"type": "nonsense"}).status_code, 400
        )

    def test_recent_documents_listing_and_ownership(self):
        self.login_user("docs_tester", "Password123")
        with open("sample_document.pdf", "rb") as f:
            up = self.client.post("/upload", data={"document": (f, "sample_document.pdf")},
                                  headers={"X-Requested-With": "XMLHttpRequest"})
        doc_id = up.get_json()["document_id"]

        listing = self.client.get("/api/documents").get_json()
        self.assertTrue(listing["success"])
        self.assertEqual(listing["documents"][0]["filename"], "sample_document.pdf")

        # Another user cannot open someone else's document
        self.login_user("docs_other", "Password123")
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}").status_code, 404)

    def test_excel_export_requires_login_and_returns_xlsx(self):
        self.assertEqual(self.client.get("/export/excel").status_code, 302)
        self.login_user("export_tester", "Password123")
        resp = self.client.get("/export/excel")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.headers["Content-Type"])
        self.assertTrue(resp.data.startswith(b"PK"))  # a real xlsx zip


    # ------------------------------------------------------------------
    # November update: zoom, voice guide, sentence position, new pages
    # ------------------------------------------------------------------
    def test_page_zoom_is_clamped_and_rounded(self):
        self.login_user("zoom_tester", "Password123")
        big = self.client.post("/api/settings", json={"page_zoom": 400}).get_json()["settings"]
        self.assertEqual(big["page_zoom"], 150)
        small = self.client.post("/api/settings", json={"page_zoom": 20}).get_json()["settings"]
        self.assertEqual(small["page_zoom"], 100)
        rounded = self.client.post("/api/settings", json={"page_zoom": 123}).get_json()["settings"]
        self.assertEqual(rounded["page_zoom"], 120)
        # The zoom is applied to every page through base.html
        page = self.client.get("/home").get_data(as_text=True)
        self.assertIn('style="font-size: 120%"', page)

    def test_settings_page_form_saves(self):
        """The /settings form posts real field values (it used to save nothing)."""
        self.login_user("form_tester", "Password123")
        self.client.post("/settings", data={
            "font_size": "20", "letter_spacing": "0.05", "line_spacing": "2.0",
            "speech_rate": "1.5", "background": "cream", "page_zoom": "130",
            "voice_guide": "1"
        }, follow_redirects=True)
        s = self.client.get("/api/settings").get_json()["settings"]
        self.assertEqual((s["font_size"], s["background"], s["page_zoom"], s["voice_guide"]),
                         (20, "cream", 130, 1))
        # Unticking the voice guide checkbox sends nothing for it -> turned off
        self.client.post("/settings", data={"font_size": "20"}, follow_redirects=True)
        self.assertEqual(self.client.get("/api/settings").get_json()["settings"]["voice_guide"], 0)

    def test_voice_login_turns_on_voice_guide(self):
        self.login_user("voice_tester", "Password123")
        self.assertEqual(self.client.get("/api/settings").get_json()["settings"]["voice_guide"], 0)
        self.client.get("/logout")
        self.client.post("/login", data={
            "username": "voice_tester", "password": "Password123", "voice_mode": "1"
        }, follow_redirects=True)
        self.assertEqual(self.client.get("/api/settings").get_json()["settings"]["voice_guide"], 1)

    def test_sentence_position_is_saved_for_own_documents_only(self):
        self.login_user("pos_owner", "Password123")
        with open("sample_document.pdf", "rb") as f:
            doc_id = self.client.post("/upload", data={"document": (f, "sample_document.pdf")},
                                      headers={"X-Requested-With": "XMLHttpRequest"}).get_json()["document_id"]
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}").get_json()["document"]["last_position"], 0)
        resp = self.client.post(f"/api/documents/{doc_id}/position", json={"sentence": 3})
        self.assertTrue(resp.get_json()["success"])
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}").get_json()["document"]["last_position"], 3)
        self.assertEqual(self.client.post(f"/api/documents/{doc_id}/position", json={"sentence": "x"}).status_code, 400)
        # Another user cannot change it
        self.login_user("pos_other", "Password123")
        self.assertEqual(self.client.post(f"/api/documents/{doc_id}/position", json={"sentence": 9}).status_code, 404)

    def test_repeated_words_keep_their_own_capitals(self):
        """Bug fix: a repeated word used to copy the first spelling ("Dyslexia" twice)."""
        tokens = analyze_tough_words("Dyslexia is common. Many people with dyslexia read well.")
        words = [t["word"] for t in tokens if not t.get("separator")]
        self.assertIn("Dyslexia", words)
        self.assertIn("dyslexia", words)

    def test_help_and_write_pages(self):
        self.assertEqual(self.client.get("/help").status_code, 200)   # open to everyone
        self.assertEqual(self.client.get("/write").status_code, 302)  # needs login
        self.login_user("writer", "Password123")
        page = self.client.get("/write").get_data(as_text=True)
        self.assertIn('spellcheck="true"', page)

    def test_login_page_has_voice_guide_and_no_inline_scripts(self):
        page = self.client.get("/login").get_data(as_text=True)
        self.assertIn('id="voice-login-card"', page)
        self.assertIn('name="voice_mode"', page)
        # The Content-Security-Policy blocks inline scripts, so none may exist
        import re as _re
        self.assertIsNone(_re.search(r"<script>(?!\s*$)", page))
        csp = self.client.get("/login").headers["Content-Security-Policy"]
        self.assertIn("font-src 'self'", csp)


if __name__ == "__main__":
    unittest.main()
