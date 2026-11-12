// DyslexiLens voice and access features (November update)
//
// Sections:
//   1. Helpers         — speak(), listenOnce(), status message
//   2. Page zoom       — A- / A / A+ buttons, saved per user (or per browser)
//   3. Voice guide     — speaks the page name and messages when a page opens
//   4. Voice login     — Start -> "voice or typing?" -> username by voice
//   5. Voice commands  — Voice button or V key, then one spoken command
//   6. Keyboard shortcuts
//
// Two browser rules shape this file:
//   * A page may not make sound until the user clicks or presses a key on
//     it. Speech asked for before that is queued and played on the first
//     click or key press.
//   * Listening (speech recognition) only exists in Chrome and Edge. It
//     sends the audio to the browser maker's server, so it needs internet
//     and microphone permission. Every voice feature has a typing fallback.

document.addEventListener('DOMContentLoaded', () => {
  'use strict';

  const html = document.documentElement;
  const loggedIn = html.dataset.loggedIn === '1';
  const LANG = 'en-IN';   // English as spoken in India; change to 'en-US' if needed

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const CSRF_TOKEN = csrfMeta ? csrfMeta.content : '';

  /* ======================================================================
   * 1. HELPERS
   * ==================================================================== */
  const statusBox = document.getElementById('voice-status');
  let statusTimer = 0;

  // Shows a short message at the bottom right (also read by screen readers)
  function showStatus(text, ms) {
    if (!statusBox) return;
    clearTimeout(statusTimer);
    statusBox.textContent = text;
    statusBox.hidden = false;
    if (ms) statusTimer = setTimeout(() => { statusBox.hidden = true; }, ms);
  }

  function hideStatus() {
    if (statusBox) statusBox.hidden = true;
  }

  const canSpeak = 'speechSynthesis' in window;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  const canListen = !!Recognition;

  // Speech asked for before the first click/key press waits here
  let pendingSpeech = null;
  function hasUserActivation() {
    // Chrome and Edge report whether the user has interacted with the page
    if (navigator.userActivation) return navigator.userActivation.hasBeenActive;
    return true;  // other browsers: just try
  }
  function playPendingSpeech() {
    if (!pendingSpeech) return;
    const job = pendingSpeech;
    pendingSpeech = null;
    speak(job.text, job.onEnd);
  }
  ['pointerdown', 'keydown'].forEach(evt =>
    document.addEventListener(evt, () => setTimeout(playPendingSpeech, 30), true));

  // Says `text` aloud, then calls onEnd (if given)
  function speak(text, onEnd) {
    if (!canSpeak || !text) { if (onEnd) onEnd(); return; }
    if (!hasUserActivation()) { pendingSpeech = { text, onEnd }; return; }
    // Something was waiting to be said (for example a login error message):
    // say it first, in the same utterance, instead of cutting it off.
    if (pendingSpeech) {
      const waiting = pendingSpeech;
      pendingSpeech = null;
      if (waiting.text !== text) text = `${waiting.text} ${text}`;
    }
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = LANG;
    utter.rate = 0.95;   // slightly slow and calm
    let done = false;
    const finish = () => { if (!done) { done = true; if (onEnd) onEnd(); } };
    utter.onend = finish;
    utter.onerror = (e) => {
      if (e.error === 'not-allowed') { pendingSpeech = { text, onEnd }; return; }
      finish();
    };
    window.__dlUtter = utter;   // keeps Chrome from dropping the utterance early
    window.speechSynthesis.speak(utter);
  }

  // Listens for ONE phrase. onResult gets a list of possible transcripts
  // (lower case, best guess first). onError gets a short reason code.
  let activeRecognition = null;
  function listenOnce(onResult, onError) {
    if (!canListen) { onError('unsupported'); return; }
    if (activeRecognition) { try { activeRecognition.abort(); } catch (e) {} }
    const rec = new Recognition();
    activeRecognition = rec;
    rec.lang = LANG;
    rec.interimResults = false;
    rec.maxAlternatives = 3;
    let gotResult = false;
    rec.onresult = (event) => {
      gotResult = true;
      const alts = Array.from(event.results[0]).map(a => a.transcript.toLowerCase().trim());
      onResult(alts);
    };
    rec.onerror = (event) => {
      gotResult = true;
      // 'not-allowed' = microphone blocked, 'no-speech' = silence,
      // 'network' = no internet (Chrome sends audio to its server)
      onError(event.error || 'error');
    };
    rec.onend = () => {
      activeRecognition = null;
      if (!gotResult) onError('no-speech');
    };
    try { rec.start(); } catch (e) { onError('error'); }
  }

  function voiceErrorMessage(code) {
    if (code === 'unsupported') return 'Voice input is not available in this browser. Please use Chrome or Edge, or type instead.';
    if (code === 'not-allowed' || code === 'service-not-allowed') return 'The microphone is blocked. Allow the microphone in the browser, or type instead.';
    if (code === 'network') return 'Voice input needs the internet. Please check your connection, or type instead.';
    if (code === 'no-speech') return 'I did not hear anything.';
    return 'Voice input did not work. Please try again, or type instead.';
  }

  const YES_WORDS = ['yes', 'yeah', 'yep', 'yup', 'sure', 'okay', 'ok', 'correct', 'right', 'haan', 'han', 'ha', 'voice'];
  const NO_WORDS = ['no', 'nope', 'nah', 'na', 'nahi', 'wrong', 'type', 'typing'];
  function yesOrNo(alternatives) {
    for (const phrase of alternatives) {
      const words = phrase.replace(/[^a-z\s]/g, ' ').split(/\s+/);
      if (words.some(w => NO_WORDS.includes(w))) return 'no';
      if (words.some(w => YES_WORDS.includes(w))) return 'yes';
    }
    return null;
  }

  function postSettings(data) {
    if (!loggedIn) return Promise.resolve();
    return fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': CSRF_TOKEN },
      body: JSON.stringify(data)
    }).catch(() => {});
  }

  function storageGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function storageSet(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
  function storageRemove(key) { try { localStorage.removeItem(key); } catch (e) {} }

  /* ======================================================================
   * 2. PAGE ZOOM: changes the root font size, so everything sized in rem
   *    (text, buttons, spacing) grows together. 100% to 150%.
   * ==================================================================== */
  const ZOOM_MIN = 100, ZOOM_MAX = 150, ZOOM_STEP = 10;
  let zoom = loggedIn ? parseInt(html.dataset.zoom, 10) || 100
                      : parseInt(storageGet('dl_page_zoom'), 10) || 100;

  function applyZoom(value) {
    zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, value));
    html.style.fontSize = `${zoom}%`;
    const reset = document.getElementById('zoom-reset');
    if (reset) reset.title = `Page size ${zoom}%. Click for normal size.`;
  }

  function setZoom(value) {
    applyZoom(value);
    storageSet('dl_page_zoom', String(zoom));
    postSettings({ page_zoom: zoom });
    showStatus(`Page size ${zoom}%`, 1500);
  }

  applyZoom(zoom);
  const zoomIn = document.getElementById('zoom-in');
  const zoomOut = document.getElementById('zoom-out');
  const zoomReset = document.getElementById('zoom-reset');
  if (zoomIn) zoomIn.addEventListener('click', () => setZoom(zoom + ZOOM_STEP));
  if (zoomOut) zoomOut.addEventListener('click', () => setZoom(zoom - ZOOM_STEP));
  if (zoomReset) zoomReset.addEventListener('click', () => setZoom(100));

  /* ======================================================================
   * 3. VOICE GUIDE: when it is on, each page says its name and any message
   *    (for example "Wrong username or password.") as it opens.
   * ==================================================================== */
  const voiceGuideOn = loggedIn ? html.dataset.voiceGuide === '1'
                                : storageGet('dl_voice_mode') === '1';
  if (voiceGuideOn) {
    const pageName = (document.title.split('–')[0] || '').trim();
    const messages = Array.from(document.querySelectorAll('.flash-banner span'))
      .map(el => el.textContent.trim()).filter(Boolean);
    const intro = pageName ? `${pageName} page.` : '';
    speak([intro].concat(messages).join(' '));
  }

  /* ======================================================================
   * 4. VOICE LOGIN (login page only)
   *    Start -> "Would you like to log in using your voice?" -> yes: say the
   *    username, the app spells it back -> password is ALWAYS typed.
   * ==================================================================== */
  const voiceCard = document.getElementById('voice-login-card');
  if (voiceCard) {
    const cardText = document.getElementById('voice-card-text');
    const startRow = document.getElementById('voice-card-start');
    const choiceRow = document.getElementById('voice-card-choice');
    const heard = document.getElementById('voice-card-heard');
    const startBtn = document.getElementById('voice-start-btn');
    const skipBtn = document.getElementById('voice-skip-btn');
    const yesBtn = document.getElementById('voice-yes-btn');
    const noBtn = document.getElementById('voice-no-btn');
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    const voiceModeField = document.getElementById('voice-mode-field');
    let flowId = 0;   // a newer flow cancels older callbacks

    const say = (text, onEnd) => { cardText.textContent = text; speak(text, onEnd); };

    const QUESTION = 'Welcome to DyslexiLens. Would you like to log in using your voice? ' +
                     'Say yes to use your voice. Say no to log in by typing.';

    function startGuide() {
      const id = ++flowId;
      startRow.hidden = true;
      choiceRow.hidden = false;
      heard.hidden = true;
      say(QUESTION, () => { if (id === flowId) askYesNo(id, 0); });
    }

    function askYesNo(id, attempt) {
      if (!canListen) {
        say('Voice input is not available in this browser. Please type your details, or use the buttons.');
        return;
      }
      showStatus('Listening... say yes or no.');
      listenOnce((alts) => {
        hideStatus();
        if (id !== flowId) return;
        const answer = yesOrNo(alts);
        if (answer === 'yes') useVoice();
        else if (answer === 'no') useTyping();
        else if (attempt < 1) say('Sorry, I did not understand. Please say yes or no.', () => askYesNo(id, attempt + 1));
        else say('Please choose with the buttons: Use my voice, or Type instead.');
      }, (code) => {
        hideStatus();
        if (id !== flowId) return;
        if (code === 'no-speech' && attempt < 1) {
          say('I did not hear anything. Please say yes or no.', () => askYesNo(id, attempt + 1));
        } else {
          say(voiceErrorMessage(code) + ' You can also use the buttons.');
        }
      });
    }

    function useVoice() {
      const id = ++flowId;
      if (voiceModeField) voiceModeField.value = '1';
      storageSet('dl_voice_mode', '1');
      choiceRow.hidden = true;
      askUsername(id, 0);
    }

    function useTyping() {
      ++flowId;
      if (voiceModeField) voiceModeField.value = '0';
      storageRemove('dl_voice_mode');
      choiceRow.hidden = true;
      startRow.hidden = false;
      say('Please type your username and password in the boxes on the screen, then press the Log in button.');
      if (usernameInput) usernameInput.focus();
    }

    // "deep underscore test one" -> "deep_test1"
    const NUMBER_WORDS = { zero: '0', one: '1', two: '2', three: '3', four: '4', five: '5',
                           six: '6', seven: '7', eight: '8', nine: '9' };
    function toUsername(phrase) {
      let s = ` ${phrase.toLowerCase()} `;
      s = s.replace(/ under ?score /g, ' _ ').replace(/ (dot|point|full stop) /g, ' . ')
           .replace(/ (dash|hyphen|minus) /g, ' - ');
      s = s.split(/\s+/).map(w => NUMBER_WORDS[w] || w).join('');
      return s.replace(/[^a-z0-9_.-]/g, '');
    }

    // Spells the username so the user can check every character
    function spellOut(name) {
      const names = { '_': 'underscore', '.': 'dot', '-': 'dash' };
      return name.split('').map(ch => names[ch] || ch).join(', ');
    }

    function askUsername(id, attempt) {
      say('Please say your username.', () => {
        if (id !== flowId) return;
        showStatus('Listening for your username...');
        listenOnce((alts) => {
          hideStatus();
          if (id !== flowId) return;
          const name = toUsername(alts[0] || '');
          if (!name) { retryUsername(id, attempt); return; }
          if (usernameInput) usernameInput.value = name;
          heard.hidden = false;
          heard.textContent = `I heard: ${name}`;
          say(`I heard ${spellOut(name)}. Is that correct? Say yes or no.`, () => confirmUsername(id, attempt));
        }, (code) => {
          hideStatus();
          if (id !== flowId) return;
          if (code === 'no-speech') retryUsername(id, attempt);
          else { say(voiceErrorMessage(code) + ' Please type your username.'); if (usernameInput) usernameInput.focus(); }
        });
      });
    }

    function retryUsername(id, attempt) {
      if (attempt < 2) askUsername(id, attempt + 1);
      else { say('Please type your username in the first box.'); if (usernameInput) usernameInput.focus(); }
    }

    function confirmUsername(id, attempt) {
      showStatus('Listening... say yes or no.');
      listenOnce((alts) => {
        hideStatus();
        if (id !== flowId) return;
        if (yesOrNo(alts) === 'yes') askPassword();
        else retryUsername(id, attempt);
      }, () => {
        hideStatus();
        if (id !== flowId) return;
        say('Please check the username on the screen, then type your password.');
        if (passwordInput) passwordInput.focus();
      });
    }

    function askPassword() {
      // Security: a password is never spoken (people nearby could hear it,
      // and the audio goes to the browser's speech server).
      say('Thank you. For your security, please type your password and press Enter.');
      if (passwordInput) passwordInput.focus();
    }

    startBtn.addEventListener('click', startGuide);
    skipBtn.addEventListener('click', useTyping);
    yesBtn.addEventListener('click', useVoice);
    noBtn.addEventListener('click', useTyping);

    // Enter on the page starts the guide (the Start button has focus)
    if (usernameInput && usernameInput.value && storageGet('dl_voice_mode') !== '1') {
      usernameInput.focus();   // came back after a typing attempt: keep typing
    } else {
      startBtn.focus();
    }
  }

  /* ======================================================================
   * 5. VOICE COMMANDS (logged-in pages): Voice button or V key, then say
   *    one command. Commands that belong to the reader open /home first.
   * ==================================================================== */
  const $ = (id) => document.getElementById(id);
  const onReader = !!$('reading-preview');

  function readerHasText() {
    const box = $('reading-preview');
    return box && !box.querySelector('.sandbox-placeholder') && box.textContent.trim().length > 0;
  }

  function go(url, message) {
    let moved = false;
    const move = () => { if (!moved) { moved = true; window.location.href = url; } };
    showStatus(message);
    speak(message, move);
    setTimeout(move, 2500);   // never wait forever if speech is silent
  }

  function clickIf(id, message, emptyMessage) {
    const el = $(id);
    if (!el || el.disabled) { if (emptyMessage) say(emptyMessage); return false; }
    el.click();
    if (message) say(message);
    return true;
  }

  function say(text) { showStatus(text, 4000); speak(text); }

  function setToggle(id, on) {
    const box = $(id);
    if (!box) return;
    if (box.checked !== on) { box.checked = on; box.dispatchEvent(new Event('change')); }
  }

  function nudgeSlider(id, delta) {
    const slider = $(id);
    if (!slider) return false;
    slider.value = String(parseFloat(slider.value) + delta);
    slider.dispatchEvent(new Event('input'));
    return true;
  }

  const NO_TEXT = 'There is no text in the reader yet. Say use sample text, or upload a document.';

  // Order matters: more specific phrases come first ("read sentence"
  // before "read", "sentence mode off" before "sentence mode").
  const COMMANDS = [
    { id: 'logout', say: ['log out', 'logout', 'sign out'], run: () => go('/logout', 'Logging out.') },
    { id: 'help', say: ['help', 'what can i say', 'commands'], run: () => {
        if (location.pathname === '/help') say('You can say: go home, open settings, use sample text, read aloud, pause, stop, next sentence, zoom in, or log out. The full list is on this page.');
        else go('/help', 'Opening help.');
      } },
    { id: 'settings', say: ['setting'], run: () => go('/settings', 'Opening settings.') },
    { id: 'profile', say: ['profile'], run: () => go('/profile', 'Opening profile.') },
    { id: 'write', say: ['writing pad', 'write', 'writing'], run: () => go('/write', 'Opening the writing pad.') },
    { id: 'home', say: ['go home', 'home', 'reader', 'go back'], run: () => go('/home', 'Opening the reader.') },

    { id: 'voice-guide-on', say: ['voice guide on', 'turn on voice guide', 'start voice guide'], run: () => { postSettings({ voice_guide: 1 }); say('Voice guide is on.'); } },
    { id: 'voice-guide-off', say: ['voice guide off', 'turn off voice guide', 'stop voice guide'], run: () => { postSettings({ voice_guide: 0 }); say('Voice guide is off.'); } },

    { id: 'zoom-in', say: ['zoom in', 'bigger page', 'larger page'], run: () => { setZoom(zoom + ZOOM_STEP); say(`Page size ${zoom} percent.`); } },
    { id: 'zoom-out', say: ['zoom out', 'smaller page'], run: () => { setZoom(zoom - ZOOM_STEP); say(`Page size ${zoom} percent.`); } },
    { id: 'zoom-reset', say: ['normal size', 'reset zoom'], run: () => { setZoom(100); say('Normal page size.'); } },

    // Reader commands
    { id: 'sample', reader: true, say: ['sample'], run: () => clickIf('btn-sample-doc', 'Loading the sample text. Say read this text to continue.') },
    { id: 'use-text', reader: true, say: ['read this text', 'use this text', 'use the text'], run: () => {
        const panel = $('review-panel');
        if (panel && panel.classList.contains('is-visible')) clickIf('btn-use-text', 'The text is in the reader.');
        else say('There is no text waiting to be checked.');
      } },
    { id: 'recent', reader: true, say: ['recent', 'my documents'], run: () => clickIf('btn-recent-docs', 'Here are your recent documents. Please choose one.') },
    { id: 'upload', reader: true, say: ['upload', 'scan', 'choose file', 'open file'], run: () => {
        const input = $('document-file-input');
        if (input) { input.focus(); input.scrollIntoView({ block: 'center' }); }
        say('Please press Enter or click Choose file to select your document. Then say scan, or press the Scan button.');
      } },
    { id: 'sentence-off', reader: true, say: ['sentence mode off', 'stop sentence mode', 'show all text', 'whole text view'], run: () => { setToggle('toggle-sentence-mode', false); say('Showing the whole text.'); } },
    { id: 'sentence-on', reader: true, say: ['sentence mode', 'one sentence', 'sentence by sentence'], run: () => {
        if (!readerHasText()) { say(NO_TEXT); return; }
        setToggle('toggle-sentence-mode', true); say('One sentence at a time. Say next sentence, or read sentence.');
      } },
    { id: 'next-sentence', reader: true, say: ['next sentence', 'next'], run: () => clickIf('sentence-next', null, 'Turn on sentence mode first, or this is the last sentence.') },
    { id: 'prev-sentence', reader: true, say: ['previous sentence', 'previous', 'back one'], run: () => clickIf('sentence-prev', null, 'Turn on sentence mode first, or this is the first sentence.') },
    { id: 'read-sentence', reader: true, say: ['read sentence', 'read the sentence', 'read this sentence'], run: () => {
        const view = $('sentence-view');
        if (view && !view.hidden) clickIf('sentence-read');
        else say('Turn on sentence mode first.');
      } },
    { id: 'syllables', reader: true, say: ['syllable'], run: () => { if (!readerHasText()) { say(NO_TEXT); return; } clickIf('btn-read-syllables'); } },
    { id: 'read', reader: true, say: ['read aloud', 'read whole', 'read everything', 'start reading', 'read'], run: () => { if (!readerHasText()) { say(NO_TEXT); return; } clickIf('btn-read-whole'); } },
    { id: 'pause', reader: true, say: ['pause', 'resume', 'continue'], run: () => clickIf('btn-pause', null, 'Nothing is being read right now.') },
    { id: 'magnifier-off', reader: true, say: ['magnifier off', 'stop magnifier', 'turn off magnifier'], run: () => { setToggle('toggle-magnifier', false); say('Magnifier off.'); } },
    { id: 'stop', reader: true, say: ['stop'], run: () => { clickIf('btn-stop'); if (canSpeak) window.speechSynthesis.cancel(); } },
    { id: 'bigger', reader: true, say: ['bigger text', 'larger text', 'increase text', 'bigger font'], run: () => { nudgeSlider('font-size-slider', 2); say('Bigger text.'); } },
    { id: 'smaller', reader: true, say: ['smaller text', 'decrease text', 'smaller font'], run: () => { nudgeSlider('font-size-slider', -2); say('Smaller text.'); } },
    { id: 'magnifier-on', reader: true, say: ['magnifier'], run: () => { setToggle('toggle-magnifier', true); say('Magnifier on. Move the mouse over the text.'); } },
    { id: 'bg-peach', reader: true, say: ['peach'], run: () => { const sw = document.querySelector('.bg-swatch[data-bg="peach"]'); if (sw) sw.click(); say('Peach background.'); } },
    { id: 'bg-cream', reader: true, say: ['cream'], run: () => { const sw = document.querySelector('.bg-swatch[data-bg="cream"]'); if (sw) sw.click(); say('Cream background.'); } },
    { id: 'bg-yellow', reader: true, say: ['yellow'], run: () => { const sw = document.querySelector('.bg-swatch[data-bg="yellow"]'); if (sw) sw.click(); say('Yellow background.'); } },
    { id: 'bg-turquoise', reader: true, say: ['turquoise', 'blue green'], run: () => { const sw = document.querySelector('.bg-swatch[data-bg="turquoise"]'); if (sw) sw.click(); say('Turquoise background.'); } },
  ];

  function findCommand(alternatives) {
    for (const phrase of alternatives) {
      for (const cmd of COMMANDS) {
        if (cmd.say.some(words => phrase.includes(words))) return cmd;
      }
    }
    return null;
  }

  function runCommand(cmd) {
    if (cmd.reader && !onReader) {
      // Reader commands open the reader first, then run there
      window.location.href = `/home?voice=${encodeURIComponent(cmd.id)}`;
      return;
    }
    cmd.run();
  }

  const voiceBtn = $('voice-command-btn');
  let listening = false;

  function startVoiceCommand() {
    if (!loggedIn || listening) return;
    if (!canListen) { say(voiceErrorMessage('unsupported')); return; }
    if (canSpeak) window.speechSynthesis.cancel();
    listening = true;
    if (voiceBtn) voiceBtn.classList.add('is-listening');
    showStatus('Listening... say a command, for example "read aloud" or "help".');
    listenOnce((alts) => {
      listening = false;
      if (voiceBtn) voiceBtn.classList.remove('is-listening');
      const cmd = findCommand(alts);
      if (cmd) { showStatus(`Heard: "${alts[0]}"`, 2500); runCommand(cmd); }
      else say(`Sorry, I did not understand "${alts[0] || ''}". Say help to hear the commands.`);
    }, (code) => {
      listening = false;
      if (voiceBtn) voiceBtn.classList.remove('is-listening');
      say(voiceErrorMessage(code));
    });
  }

  if (voiceBtn) voiceBtn.addEventListener('click', startVoiceCommand);

  // A reader command was spoken on another page: run it now (once)
  if (onReader) {
    const params = new URLSearchParams(location.search);
    const pending = params.get('voice');
    if (pending) {
      history.replaceState(null, '', location.pathname);
      const cmd = COMMANDS.find(c => c.id === pending);
      if (cmd) setTimeout(() => cmd.run(), 400);
    }
  }

  /* ======================================================================
   * 6. KEYBOARD SHORTCUTS (only when the user is not typing in a box)
   * ==================================================================== */
  function isTyping(target) {
    if (!target) return false;
    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
  }

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
    const key = e.key;
    const sentenceOn = $('sentence-view') && !$('sentence-view').hidden;

    if (key === 'v' || key === 'V') { if (loggedIn) { e.preventDefault(); startVoiceCommand(); } return; }
    if (key === '+' || key === '=') { e.preventDefault(); setZoom(zoom + ZOOM_STEP); return; }
    if (key === '-' || key === '_') { e.preventDefault(); setZoom(zoom - ZOOM_STEP); return; }
    if (key === '0') { e.preventDefault(); setZoom(100); return; }
    if (key === 'h' || key === 'H') { window.location.href = '/help'; return; }
    if (!onReader) return;

    if (key === 'r' || key === 'R') {
      e.preventDefault();
      if (sentenceOn) { clickIf('sentence-read'); return; }
      const pause = $('btn-pause');
      if (pause && !pause.disabled) pause.click(); else clickIf('btn-read-whole');
    } else if (key === 's' || key === 'S') {
      e.preventDefault(); clickIf('btn-read-syllables');
    } else if (key === 'Escape') {
      clickIf('btn-stop');
      if (canSpeak) window.speechSynthesis.cancel();
    } else if ((key === 'n' || key === 'N' || key === 'ArrowRight') && sentenceOn) {
      e.preventDefault(); clickIf('sentence-next');
    } else if ((key === 'p' || key === 'P' || key === 'ArrowLeft') && sentenceOn) {
      e.preventDefault(); clickIf('sentence-prev');
    }
  });
});
