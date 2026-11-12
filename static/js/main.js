// DyslexiLens Reader Controller
//
// Sections:
//   1. Audio engine  — read whole / syllables / pause / stop / speed, one stream
//   2. Reading text  — tough words, magnifier, one sentence at a time
//   3. Document flow — upload -> review -> read; sample document; recent docs
//   4. Settings      — sliders, background picker, speech rate (saved per user)
//
// Golden rule of this file: the reading text NEVER moves, scales or reflows.
// Everything visual is done with colour, background and inline wrappers.

document.addEventListener('DOMContentLoaded', () => {
  const previewBox = document.getElementById('reading-preview');
  const previewWrapper = document.querySelector('.reading-preview-wrapper');

  // CSRF token for every POST (meta tag is injected by base.html)
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const CSRF_TOKEN = csrfMeta ? csrfMeta.content : '';
  function fetchOptions(options = {}) {
    const headers = Object.assign({ 'X-CSRFToken': CSRF_TOKEN }, options.headers || {});
    return Object.assign({}, options, { headers });
  }

  /* ======================================================================
   * 1. AUDIO ENGINE
   * One stream of queued utterances. The Web Speech API has no pause for
   * syllable chains, so pause/stop work by cancelling and remembering
   * where we were — the chain restarts from that token.
   * ==================================================================== */
  const audio = {
    mode: 'idle',          // 'whole' | 'syllables' | 'idle'
    chain: [],             // tokens left to speak [{text, rate, gap, wordIndex}]
    tokenIndex: 0,
    words: [],             // word wrapper spans for highlighting
    wordIndex: -1,
    paused: false,
    pausedAt: 0,           // ms of the chain already spoken before pause
    startedAt: 0,          // Date.now() when the chain started
    rate: 1,
    documentId: null,
    filename: '',
    // Chrome fires onend for the speaking utterance when cancel() is called;
    // this flag tells the engine that a cancel was PROGRAMMATIC (pause/stop/
    // restart) so it must not be treated as the session finishing.
    suppressEnd: false,
    // Pronunciation overrides: special words spoken as whole phrases
    // (kept in sync with SAY_OVERRIDES in app.py)
    spokenOverrides: { dyslexilens: 'dyslexia lens' }
  };

  // Cancel without tripping the current utterance's onend handler
  function cancelSpeechQuietly() {
    if (!supportsSpeech()) return;
    audio.suppressEnd = true;
    window.speechSynthesis.cancel();
    // onend fires asynchronously in Chrome; clear the flag after it settles
    setTimeout(() => { audio.suppressEnd = false; }, 60);
  }

  // Chrome garbage-collects utterances that nothing references, even while
  // queued — the utterance then silently never speaks. Holding a strong
  // reference until it ends prevents that.
  const liveUtterances = new Set();
  function keepAlive(utter) {
    liveUtterances.add(utter);
    const release = () => liveUtterances.delete(utter);
    utter.addEventListener('end', release);
    utter.addEventListener('error', release);
  }

  const btnWhole = document.getElementById('btn-read-whole');
  const btnSyllables = document.getElementById('btn-read-syllables');
  const btnPause = document.getElementById('btn-pause');
  const btnStop = document.getElementById('btn-stop');
  const rateSlider = document.getElementById('speech-rate');
  const rateValue = document.getElementById('speech-rate-val');

  function supportsSpeech() {
    return 'speechSynthesis' in window;
  }

  // How a word is spoken whole: honour the pronunciation dictionary
  function spokenForm(word) {
    return audio.spokenOverrides[word.toLowerCase()] || word;
  }

  function estimateMs(text, rate) {
    // Rough speech timing so pause-resume lands close to the right word
    return (text.length * 62) / Math.max(0.4, rate) + 260;
  }

  // Build the whole-token chain for one pass over the text
  function buildChain(words, mode) {
    const chain = [];
    words.forEach((span, wordIndex) => {
      const word = span.textContent;
      if (mode === 'syllables' && span.dataset.syllables) {
        const syllables = span.dataset.syllables.split('-').filter(Boolean);
        syllables.forEach(syl => chain.push({ text: syl, rate: 0.6, gap: 190, wordIndex }));
        chain.push({ text: spokenForm(span.dataset.word || word), rate: 1, gap: 260, wordIndex });
      } else {
        chain.push({ text: spokenForm(span.dataset.word || word), rate: 1, gap: 130, wordIndex });
      }
    });
    return chain;
  }

  function clearWordHighlight() {
    if (audio.words[audio.wordIndex]) {
      audio.words[audio.wordIndex].classList.remove('speaking-word');
    }
    audio.wordIndex = -1;
  }

  function highlightWord(index) {
    if (index === audio.wordIndex) return;
    clearWordHighlight();
    const span = audio.words[index];
    if (span) {
      span.classList.add('speaking-word');
      audio.wordIndex = index;
    }
  }

  function speakStep() {
    if (!audio.paused && audio.tokenIndex < audio.chain.length) {
      const token = audio.chain[audio.tokenIndex];
      highlightWord(token.wordIndex);
      const utter = new SpeechSynthesisUtterance(token.text);
      utter.lang = 'en-US';
      utter.rate = Math.max(0.5, Math.min(2, token.rate * audio.rate));
      utter.pitch = 1;
      // Chrome fires BOTH onend and onerror('interrupted') when cancel() is
      // called on a speaking utterance — both must ignore programmatic stops.
      const isProgrammatic = () => audio.suppressEnd || audio.paused || audio.mode === 'idle';
      utter.onend = () => {
        if (audio.mode === 'idle') return; // stopped meanwhile
        if (isProgrammatic()) return;      // pause/stop cancel, not an end
        audio.tokenIndex += 1;
        audio.pausedAt += estimateMs(token.text, token.rate) + token.gap;
        if (audio.tokenIndex >= audio.chain.length) {
          finishReading(true);
        } else {
          setTimeout(speakStep, token.gap);
        }
      };
      utter.onerror = () => {
        // 'interrupted'/'canceled' errors come from our own cancel() calls —
        // only a genuine synthesis failure may end the session.
        if (isProgrammatic()) return;
        finishReading(false);
      };
      keepAlive(utter);
      window.speechSynthesis.speak(utter);
    }
  }

  function startReading(mode) {
    if (!supportsSpeech() || !previewBox) return;
    const words = getReadingWords();
    if (words.length === 0) return;

    stopReading(true); // stop anything already playing (no UI flash)

    audio.mode = mode;
    audio.words = words;
    audio.chain = buildChain(words, mode);
    audio.tokenIndex = 0;
    audio.paused = false;
    audio.pausedAt = 0;
    audio.startedAt = Date.now();
    audio.documentId = previewBox.dataset.documentId || null;
    audio.filename = previewBox.dataset.filename || '';

    btnPause.disabled = false;
    btnStop.disabled = false;
    btnWhole.disabled = true;      // no double-start while reading
    btnSyllables.disabled = true;
    btnWhole.classList.add('is-active');
    btnSyllables.classList.toggle('is-active', mode === 'syllables');
    btnPause.querySelector('span').textContent = 'Pause';

    speakStep();
  }

  function finishReading(completed) {
    const elapsed = Math.min((Date.now() - audio.startedAt) / 1000, 3600);
    const mode = audio.mode;
    const chainLength = audio.chain.length;
    const tokensDone = audio.paused ? audio.tokenIndex : chainLength;
    clearWordHighlight();

    audio.mode = 'idle';
    audio.paused = false;
    btnPause.disabled = true;
    btnStop.disabled = true;
    btnWhole.disabled = false;
    btnSyllables.disabled = false;
    btnWhole.classList.remove('is-active');
    btnSyllables.classList.remove('is-active');
    btnPause.querySelector('span').textContent = 'Pause';

    cancelSpeechQuietly();

    // Record progress (best-effort; never blocks the UI)
    if (chainLength > 0) {
      recordProgress({
        type: 'reading_session',
        document_id: audio.documentId,
        filename: audio.filename,
        words: getReadingWords().length,
        words_spoken: Math.round(tokensDone * 0.35),
        seconds: Math.round(elapsed)
      });
    }
  }

  function pauseReading() {
    if (audio.mode === 'idle' || audio.paused) return;
    audio.paused = true;
    // Quiet cancel: the onend of the interrupted utterance must NOT end the
    // session — we want to resume from this exact word later.
    cancelSpeechQuietly();
    btnPause.querySelector('span').textContent = 'Resume';
  }

  function resumeReading() {
    if (audio.mode === 'idle' || !audio.paused) return;
    audio.paused = false;
    btnPause.querySelector('span').textContent = 'Pause';
    speakStep();
  }

  function stopReading(silent) {
    if (audio.mode === 'idle') { if (!silent) return; }
    audio.mode = 'idle';
    audio.paused = false;
    clearWordHighlight();
    cancelSpeechQuietly();
    btnPause.disabled = true;
    btnStop.disabled = true;
    btnWhole.disabled = false;
    btnSyllables.disabled = false;
    btnWhole.classList.remove('is-active');
    btnSyllables.classList.remove('is-active');
    btnPause.querySelector('span').textContent = 'Pause';
  }

  if (btnWhole && btnSyllables && btnPause && btnStop) {
    btnWhole.addEventListener('click', () => startReading('whole'));
    btnSyllables.addEventListener('click', () => startReading('syllables'));
    btnPause.addEventListener('click', () => (audio.paused ? resumeReading() : pauseReading()));
    btnStop.addEventListener('click', () => stopReading(false));

    if (rateSlider && rateValue) {
      rateSlider.addEventListener('input', () => {
        audio.rate = parseFloat(rateSlider.value) || 1;
        rateValue.textContent = audio.rate.toFixed(1) + '\u00d7';
        scheduleSettingsSave();
      });
    }
  }

  // Only one audio stream at a time: starting a popup playback stops reading
  function ensureIdleAudio() {
    if (audio.mode !== 'idle') stopReading(true);
  }

  /* ======================================================================
   * Reading words: the tokens Read Aloud walks through
   * ==================================================================== */
  function getReadingWords() {
    if (!previewBox) return [];
    // The placeholder hint is never read aloud
    if (previewBox.querySelector('.sandbox-placeholder')) return [];
    let spans = Array.from(previewBox.querySelectorAll('span[data-word]'));
    if (spans.length === 0) {
      // Plain (unedited) text: wrap words in spans so they can be highlighted
      const text = previewBox.textContent;
      if (!text.trim()) return [];
      previewBox.textContent = '';
      text.split(/(\s+)/).forEach(part => {
        if (!part) return;
        if (/^\s+$/.test(part)) {
          previewBox.appendChild(document.createTextNode(part));
        } else {
          const s = document.createElement('span');
          s.className = 'reading-word';
          s.dataset.word = part;
          s.textContent = part;
          previewBox.appendChild(s);
        }
      });
      spans = Array.from(previewBox.querySelectorAll('span[data-word]'));
    }
    return spans.filter(s => s.textContent.trim().length > 0);
  }

  /* ======================================================================
   * 2a. TOUGH WORDS (long AND rare words -> dotted underline + popup)
   * ==================================================================== */
  const toughWordsState = { renderRunId: 0, analyzedText: null, hadTough: false };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
  }

  async function renderToughWords(text, retries = 1) {
    if (!previewBox || !text || !text.trim()) return;
    const runId = ++toughWordsState.renderRunId;
    let data = null;
    try {
      const response = await fetch('/api/analyze-text', fetchOptions({
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ text })
      }));
      if (!response.ok || !(data = await response.json()).success) return;
    } catch (err) {
      return; // analysis is optional decoration: fail silent, keep text readable
    }
    if (runId !== toughWordsState.renderRunId) return;

    toughWordsState.tokens = data.tokens;
    toughWordsState.analyzedText = text;
    toughWordsState.hadTough = data.tokens.some(t => t.tough);
    hideSyllablePopup();
    rebuildSandboxFromTokens(data.tokens);
    initToughWordPopups();
    refreshSentencesAfterRender();
  }

  // Rebuilds the sandbox with words wrapped in spans (all words get data-word
  // so Read Aloud can highlight; tough words get data-syllables as well).
  function rebuildSandboxFromTokens(tokens) {
    previewBox.innerHTML = '';
    tokens.forEach(t => {
      if (t.separator) {
        const frag = document.createDocumentFragment();
        t.word.split('\n').forEach((linePart, i) => {
          if (i > 0) frag.appendChild(document.createElement('br'));
          if (linePart) frag.appendChild(document.createTextNode(linePart));
        });
        previewBox.appendChild(frag);
        return;
      }
      const span = document.createElement('span');
      span.textContent = t.word;
      span.dataset.word = t.word;
      if (t.tough) {
        span.className = 'tough-word';
        span.dataset.syllables = t.syllables || '';
        span.dataset.say = t.say || t.word;
        span.setAttribute('tabindex', '0');
        span.setAttribute('role', 'button');
        span.setAttribute('aria-label', `Tough word: ${t.word}. Press Enter for syllables.`);
      }
      previewBox.appendChild(span);
    });
  }

  // --- Syllable popup -----------------------------------------------------
  let syllablePopup = null;
  let activeToughWord = null;

  function resetSoundButton() {
    const btn = syllablePopup && syllablePopup.querySelector('.syllable-sound-btn');
    if (btn) btn.classList.remove('is-speaking');
  }

  function hideSyllablePopup() {
    if (syllablePopup) syllablePopup.classList.remove('is-visible');
    if (activeToughWord) activeToughWord.classList.remove('is-open');
    activeToughWord = null;
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
      resetSoundButton();
    }
  }

  function positionSyllablePopup(word) {
    const wordRect = word.getBoundingClientRect();
    const popRect = syllablePopup.getBoundingClientRect();
    const margin = 10;
    let left = wordRect.left + window.scrollX + (wordRect.width / 2) - (popRect.width / 2);
    left = Math.max(margin, Math.min(left, window.scrollX + document.documentElement.clientWidth - popRect.width - margin));
    let top = wordRect.top + window.scrollY - popRect.height - 8;
    if (top < window.scrollY + margin) top = wordRect.bottom + window.scrollY + 8;
    syllablePopup.style.left = `${Math.round(left)}px`;
    syllablePopup.style.top = `${Math.round(top)}px`;
  }

  function initToughWordPopups() {
    if (!syllablePopup) {
      syllablePopup = document.createElement('div');
      syllablePopup.className = 'syllable-popup';
      syllablePopup.setAttribute('role', 'dialog');
      syllablePopup.setAttribute('aria-label', 'Tough word syllable breakdown');
      syllablePopup.innerHTML = `
        <div class="syllable-popup-word"></div>
        <div class="syllable-popup-syllables"></div>
        <button type="button" class="syllable-sound-btn" aria-label="Say the word slowly, then normally">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>
          </svg>
          <span>Slow &amp; Whole</span>
        </button>`;
      document.body.appendChild(syllablePopup);

      syllablePopup.querySelector('.syllable-sound-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        if (activeToughWord) speakToughWord(activeToughWord);
      });

      document.addEventListener('mousedown', (e) => {
        if (!syllablePopup.classList.contains('is-visible')) return;
        if (syllablePopup.contains(e.target) || e.target.closest('.tough-word')) return;
        hideSyllablePopup();
      });
      window.addEventListener('resize', () => {
        if (activeToughWord) positionSyllablePopup(activeToughWord);
      });
    }

    previewBox.querySelectorAll('.tough-word').forEach(word => {
      word.addEventListener('click', (e) => {
        e.stopPropagation();
        openSyllablePopupFor(word);
      });
      word.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openSyllablePopupFor(word);
        }
      });
    });

    // Delegated fallback: a click resolves from the element actually clicked,
    // so the popup can only ever show the word under the pointer.
    if (!previewBox.dataset.toughClickDelegated) {
      previewBox.dataset.toughClickDelegated = 'true';
      previewBox.addEventListener('click', (e) => {
        const word = e.target.closest('.tough-word');
        if (word && previewBox.contains(word)) openSyllablePopupFor(word);
      });
    }
  }

  function openSyllablePopupFor(word) {
    const syllables = word.dataset.syllables || word.textContent.toLowerCase();
    syllablePopup.querySelector('.syllable-popup-word').textContent = word.textContent;
    syllablePopup.querySelector('.syllable-popup-syllables').textContent = syllables;

    // Opening a tough word's popup counts as practising it (the direct click
    // handler stops propagation, so tracking lives here — one place, both paths)
    recordProgress({
      type: 'word_practiced',
      document_id: previewBox.dataset.documentId ? Number(previewBox.dataset.documentId) : null,
      word: word.textContent.trim()
    });
    if (activeToughWord && activeToughWord !== word) activeToughWord.classList.remove('is-open');
    activeToughWord = word;
    word.classList.add('is-open');
    syllablePopup.classList.remove('is-visible');
    syllablePopup.style.visibility = 'hidden';
    syllablePopup.style.display = 'block';
    positionSyllablePopup(word);
    syllablePopup.style.visibility = 'visible';
    requestAnimationFrame(() => syllablePopup.classList.add('is-visible'));
    setTimeout(() => syllablePopup.classList.add('is-visible'), 60);
  }

  // --- Tough word audio: syllables slowly, then the whole word -------------
  function speakToughWord(word) {
    if (!supportsSpeech()) return;
    ensureIdleAudio(); // only one audio stream at a time
    const synth = window.speechSynthesis;
    synth.cancel();
    resetSoundButton();

    const syllables = (word.dataset.syllables || '').split('-').filter(Boolean);
    const wholeWord = word.dataset.say || word.dataset.word || word.textContent || '';
    const btn = syllablePopup.querySelector('.syllable-sound-btn');
    btn.classList.add('is-speaking');

    const finish = () => resetSoundButton();

    if (syllables.length === 0) {
      speakText(wholeWord, audio.rate, 1.1, finish);
      return;
    }

    syllables.forEach((syl, i) => {
      const isLast = i === syllables.length - 1;
      speakText(syl, 0.6 * audio.rate, 1.1, isLast ? () => {
        setTimeout(() => speakText(wholeWord, audio.rate, 0.95, finish), 650);
      } : null);
    });
  }

  function speakText(text, rate, pitch, onEnd) {
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'en-US';
    utter.rate = Math.max(0.5, Math.min(2, rate));
    utter.pitch = pitch;
    if (onEnd) {
      let ended = false;
      const done = () => { if (!ended) { ended = true; onEnd(); } };
      utter.onend = done;
      utter.onerror = done;
    }
    keepAlive(utter);
    window.speechSynthesis.speak(utter);
  }

  /* ======================================================================
   * 2b. LINE MAGNIFIER — fixed strip above the text showing hovered line
   * ==================================================================== */
  const magToggle = document.getElementById('toggle-magnifier');
  const magStrip = document.getElementById('line-magnifier-strip');
  let magnifierEnabled = false;
  let hoverAttached = false;

  function getVisualLineAtPoint(y) {
    if (!previewBox) return null;
    const range = document.createRange();
    range.selectNodeContents(previewBox);
    const rects = Array.from(range.getClientRects()).filter(r => r.width > 2 && r.height > 2);
    if (rects.length === 0) return null;

    // Merge rects on the same visual row
    const lines = [];
    rects.forEach(rect => {
      const line = lines.find(l => Math.abs(l.top - rect.top) < 6);
      if (line) {
        line.top = Math.min(line.top, rect.top);
        line.bottom = Math.max(line.bottom, rect.bottom);
      } else {
        lines.push({ top: rect.top, bottom: rect.bottom });
      }
    });

    let matched = lines.find(l => y >= l.top - 2 && y <= l.bottom + 2);
    if (!matched) {
      let minDiff = Infinity;
      lines.forEach(l => {
        const diff = Math.abs(y - (l.top + l.bottom) / 2);
        if (diff < minDiff) { minDiff = diff; matched = l; }
      });
    }
    return matched;
  }

  /* --- Line magnifier: a fixed strip above the text mirrors the hovered
         line at ~1.5x. Purely informational; the original text is untouched. */
  function sandboxFontPx() {
    return parseFloat(window.getComputedStyle(previewBox).fontSize) || 24;
  }

  function getTextOfLine(line) {
    if (!previewBox || !line) return '';
    const walker = document.createTreeWalker(previewBox, NodeFilter.SHOW_TEXT, null);
    const chars = [];
    let node;
    while ((node = walker.nextNode())) {
      for (let i = 0; i < node.length; i++) {
        const r = document.createRange();
        r.setStart(node, i);
        r.setEnd(node, i + 1);
        const cr = r.getClientRects();
        if (cr.length && cr[0].height > 0 && cr[0].top >= line.top - 4 && cr[0].bottom <= line.bottom + 4) {
          chars.push(node.textContent[i]);
        }
      }
    }
    return chars.join('');
  }

  function updateMagnifier(line) {
    if (!magnifierEnabled || !magStrip || !line) return;
    const lineEl = magStrip.querySelector('.magnifier-line');
    if (!lineEl) return;
    lineEl.textContent = getTextOfLine(line);
    // Strip base font follows the reader's font-size slider; the inner line
    // renders at 1.5em of that, so the magnified text scales with settings.
    magStrip.style.fontSize = `${sandboxFontPx()}px`;
  }

  function hideMagnifierLine() {
    if (!magStrip) return;
    const lineEl = magStrip.querySelector('.magnifier-line');
    if (lineEl) lineEl.textContent = '';
  }

  function onReaderMouseMove(e) {
    if (!previewBox || !magnifierEnabled) return;
    const line = getVisualLineAtPoint(e.clientY);
    if (!line) return;
    updateMagnifier(line);
  }

  function syncReaderHoverListener() {
    if (magnifierEnabled && !hoverAttached) {
      previewBox.addEventListener('mousemove', onReaderMouseMove);
      previewBox.addEventListener('mouseleave', hideMagnifierLine);
      hoverAttached = true;
    } else if (!magnifierEnabled && hoverAttached) {
      previewBox.removeEventListener('mousemove', onReaderMouseMove);
      previewBox.removeEventListener('mouseleave', hideMagnifierLine);
      hoverAttached = false;
      hideMagnifierLine();
    }
  }

  if (magToggle && magStrip) {
    magToggle.addEventListener('change', () => {
      magnifierEnabled = magToggle.checked;
      magStrip.classList.toggle('is-visible', magnifierEnabled);
      syncReaderHoverListener();
    });
  }

  /* ======================================================================
   * 2c. ONE SENTENCE AT A TIME (November update)
   * Shows just one sentence, with Previous / Next / Read this sentence.
   * Long, dense paragraphs are tiring, so the reader can take the text
   * one small piece at a time. For saved documents the last sentence
   * reached is stored (documents.last_position) and reopened next time.
   * ==================================================================== */
  const sentenceToggle = document.getElementById('toggle-sentence-mode');
  const sentenceView = document.getElementById('sentence-view');
  const sentenceText = document.getElementById('sentence-text');
  const sentencePos = document.getElementById('sentence-position');
  const sentencePrev = document.getElementById('sentence-prev');
  const sentenceNext = document.getElementById('sentence-next');
  const sentenceRead = document.getElementById('sentence-read');

  const sentenceMode = {
    on: false,
    sentences: [],      // each sentence = list of tokens {word, tough, syllables, say, separator}
    index: 0,
    resumeIndex: 0,     // last saved position of the open document
    saveTimer: 0
  };

  // Splits the reader text into sentences. Uses the analysed tokens (so hard
  // words keep their syllables) when they match the text on screen.
  function buildSentences() {
    if (!previewBox || previewBox.querySelector('.sandbox-placeholder')) return [];
    // innerText keeps line breaks (<br>), textContent would drop them
    const text = (previewBox.innerText || previewBox.textContent || '').trim();
    if (!text) return [];
    const squash = str => str.replace(/\s+/g, '');
    const sameText = toughWordsState.tokens && toughWordsState.analyzedText &&
      squash(toughWordsState.analyzedText) === squash(text);
    const tokens = sameText
      ? toughWordsState.tokens
      : text.split(/(\s+)/).filter(Boolean).map(part => ({ word: part, separator: /^\s+$/.test(part) }));

    const sentences = [];
    let current = [];
    const hasWords = list => list.some(t => !t.separator && t.word.trim());
    const close = () => { if (hasWords(current)) sentences.push(current); current = []; };

    const wordCount = list => list.filter(t => !t.separator && t.word.trim()).length;
    tokens.forEach((t, i) => {
      if (t.separator && /\n/.test(t.word)) {
        // A blank line (new paragraph) always ends a sentence. A single line
        // break ends it only after a short heading-like line; otherwise it is
        // just where a PDF wrapped the line, so it counts as a space.
        const next = tokens[i + 1];
        const isParagraph = /\n\s*\n/.test(t.word);
        const isHeading = wordCount(current) > 0 && wordCount(current) < 8 &&
          next && /^[A-Z]/.test(next.word);
        if (isParagraph || isHeading) { close(); return; }
        current.push({ word: ' ', separator: true });
        return;
      }
      current.push(t);
      const next = tokens[i + 1];
      const endsHere = /[.!?]["'”)\]]*$/.test(t.word);
      if (endsHere && (!next || /^\s/.test(next.word))) close();
    });
    close();
    return sentences;
  }

  function syncSentenceStyle() {
    if (!sentenceText || !previewBox) return;
    sentenceText.style.fontSize = previewBox.style.fontSize;
    sentenceText.style.letterSpacing = previewBox.style.letterSpacing;
    sentenceText.style.lineHeight = previewBox.style.lineHeight;
    ['bg-cream', 'bg-yellow', 'bg-turquoise'].forEach(c => {
      sentenceText.classList.toggle(c, previewBox.classList.contains(c));
    });
  }

  function sentencePlainText(tokens) {
    return tokens.map(t => t.word).join('').trim();
  }

  function renderSentence() {
    if (!sentenceView) return;
    const total = sentenceMode.sentences.length;
    sentenceText.innerHTML = '';
    if (total === 0) {
      sentenceText.textContent = 'Load a document first, then turn this on.';
      sentencePos.textContent = '';
      sentencePrev.disabled = sentenceNext.disabled = sentenceRead.disabled = true;
      return;
    }
    sentenceMode.index = Math.max(0, Math.min(sentenceMode.index, total - 1));
    const tokens = sentenceMode.sentences[sentenceMode.index];
    // Trim leading spaces so the sentence starts cleanly
    let started = false;
    tokens.forEach(t => {
      if (!started && t.separator && !t.word.trim()) return;
      started = true;
      if (t.separator || !t.tough) {
        sentenceText.appendChild(document.createTextNode(t.word));
        return;
      }
      const span = document.createElement('span');
      span.className = 'tough-word';
      span.textContent = t.word;
      span.dataset.word = t.word;
      span.dataset.syllables = t.syllables || '';
      span.dataset.say = t.say || t.word;
      span.setAttribute('tabindex', '0');
      span.setAttribute('role', 'button');
      span.setAttribute('aria-label', `Hard word: ${t.word}. Press Enter for syllables.`);
      const open = (e) => { e.stopPropagation(); if (syllablePopup) openSyllablePopupFor(span); };
      span.addEventListener('click', open);
      span.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(e); } });
      sentenceText.appendChild(span);
    });
    let label = `Sentence ${sentenceMode.index + 1} of ${total}`;
    if (sentenceMode.resumeIndex > 0 && sentenceMode.index === sentenceMode.resumeIndex) {
      label += ' (where you stopped last time)';
    }
    sentencePos.textContent = label;
    sentencePrev.disabled = sentenceMode.index === 0;
    sentenceNext.disabled = sentenceMode.index >= total - 1;
    sentenceRead.disabled = false;
    syncSentenceStyle();
  }

  // Remember the position for saved documents (debounced, best-effort)
  function saveSentencePosition() {
    const docId = previewBox && previewBox.dataset.documentId;
    if (!docId) return;
    clearTimeout(sentenceMode.saveTimer);
    sentenceMode.saveTimer = setTimeout(() => {
      fetch(`/api/documents/${docId}/position`, fetchOptions({
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ sentence: sentenceMode.index })
      })).catch(() => {});
    }, 500);
  }

  function goToSentence(index) {
    window.speechSynthesis && cancelSpeechQuietly();
    sentenceMode.index = index;
    renderSentence();
    saveSentencePosition();
  }

  function readCurrentSentence() {
    if (!supportsSpeech() || sentenceMode.sentences.length === 0) return;
    ensureIdleAudio();
    window.speechSynthesis.cancel();
    let text = sentencePlainText(sentenceMode.sentences[sentenceMode.index]);
    // Same pronunciation dictionary as the rest of the reader
    Object.keys(audio.spokenOverrides).forEach(key => {
      text = text.replace(new RegExp(`\\b${key}\\b`, 'gi'), audio.spokenOverrides[key]);
    });
    speakText(text, audio.rate, 1, null);
  }

  function setSentenceMode(on) {
    sentenceMode.on = !!on;
    if (sentenceToggle) sentenceToggle.checked = sentenceMode.on;
    if (!sentenceView || !previewWrapper) return;
    if (sentenceMode.on) {
      stopReading(true);
      sentenceMode.sentences = buildSentences();
      sentenceMode.index = sentenceMode.resumeIndex || 0;
      previewWrapper.hidden = true;
      sentenceView.hidden = false;
      renderSentence();
    } else {
      window.speechSynthesis && cancelSpeechQuietly();
      sentenceView.hidden = true;
      previewWrapper.hidden = false;
    }
  }

  // New text was analysed: rebuild the sentence list if the mode is on
  function refreshSentencesAfterRender() {
    if (!sentenceMode.on) return;
    sentenceMode.sentences = buildSentences();
    renderSentence();
  }

  if (sentenceToggle && sentenceView) {
    sentenceToggle.addEventListener('change', () => setSentenceMode(sentenceToggle.checked));
    sentencePrev.addEventListener('click', () => goToSentence(sentenceMode.index - 1));
    sentenceNext.addEventListener('click', () => goToSentence(sentenceMode.index + 1));
    sentenceRead.addEventListener('click', readCurrentSentence);
  }

  /* ======================================================================
   * 3. DOCUMENT FLOW: upload -> review -> read; sample; recent documents
   * ==================================================================== */
  const uploadForm = document.getElementById('upload-doc-form');
  const fileInput = document.getElementById('document-file-input');
  const extractionStatus = document.getElementById('extraction-status');
  const statusMessage = document.getElementById('status-message');
  const extractBtn = document.getElementById('btn-extract-doc');
  const sampleBtn = document.getElementById('btn-sample-doc');
  const recentBtn = document.getElementById('btn-recent-docs');
  const recentMenu = document.getElementById('recent-docs-menu');
  const reviewPanel = document.getElementById('review-panel');
  const reviewTextarea = document.getElementById('review-textarea');
  const reviewTitle = document.getElementById('review-title');
  const btnUseText = document.getElementById('btn-use-text');
  const btnDismissReview = document.getElementById('btn-dismiss-review');
  const readerDocName = document.getElementById('reader-doc-name');

  let pendingDocumentId = null;
  let pendingFilename = '';
  let pendingLastPosition = 0;

  function showStatus(message, isError) {
    if (!extractionStatus || !statusMessage) return;
    extractionStatus.style.display = 'flex';
    extractionStatus.classList.toggle('is-error', !!isError);
    statusMessage.textContent = message;
  }

  function hideStatus() {
    if (extractionStatus) extractionStatus.style.display = 'none';
  }

  function openReview(text, filename, documentId, lastPosition) {
    pendingDocumentId = documentId || null;
    pendingFilename = filename || '';
    pendingLastPosition = lastPosition || 0;
    if (reviewTitle) {
      reviewTitle.textContent = filename
        ? `Review the scanned text from "${filename}"`
        : 'Review the text before reading';
    }
    if (reviewTextarea) reviewTextarea.value = text;
    if (reviewPanel) reviewPanel.classList.add('is-visible');
    if (reviewTextarea) {
      reviewTextarea.focus();
      reviewTextarea.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  function closeReview() {
    if (reviewPanel) reviewPanel.classList.remove('is-visible');
    if (reviewTextarea) reviewTextarea.value = '';
    pendingDocumentId = null;
    pendingFilename = '';
    pendingLastPosition = 0;
  }

  function loadTextIntoReader(text, filename, documentId, lastPosition) {
    if (!previewBox) return;
    hideSyllablePopup();
    stopReading(true);
    hideMagnifierLine();
    previewBox.dataset.documentId = documentId ? String(documentId) : '';
    previewBox.dataset.filename = filename || '';
    previewBox.textContent = text;   // plain first; renderToughWords re-wraps it
    renderToughWords(text);
    sentenceMode.resumeIndex = lastPosition || 0;
    if (readerDocName) {
      readerDocName.textContent = (filename || 'Pasted text') +
        (lastPosition > 0 ? `, last read at sentence ${lastPosition + 1}` : '');
    }
    if (sentenceMode.on) {
      sentenceMode.sentences = buildSentences();
      sentenceMode.index = sentenceMode.resumeIndex;
      renderSentence();
    }
    previewBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
    recordProgress({
      type: 'document_opened',
      document_id: documentId ? Number(documentId) : null,
      filename: filename || '',
      words: (text.trim().match(/\S+/g) || []).length
    });
  }

  // Upload
  if (uploadForm && fileInput && previewBox) {
    uploadForm.addEventListener('submit', async (e) => {
      if (!fileInput.files || fileInput.files.length === 0) return;
      e.preventDefault();

      showStatus('Scanning your document...', false);
      extractBtn.disabled = true;
      extractBtn.style.opacity = '0.6';

      try {
        const formData = new FormData();
        formData.append('document', fileInput.files[0]);
        const response = await fetch('/upload', fetchOptions({
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: formData
        }));
        const data = await response.json();

        if (response.ok && data.success) {
          hideStatus();
          openReview(data.text, data.filename, data.document_id);
        } else {
          showStatus(data.error || 'Sorry, that document could not be read.', true);
        }
      } catch (err) {
        showStatus('Something went wrong while scanning. Please try again.', true);
      } finally {
        extractBtn.disabled = false;
        extractBtn.style.opacity = '1';
        fileInput.value = '';
      }
    });
  }

  // Sample document button
  if (sampleBtn) {
    sampleBtn.addEventListener('click', async () => {
      try {
        const response = await fetch('/api/sample-document');
        const data = await response.json();
        if (data.success) openReview(data.text, data.filename, null);
      } catch (err) {
        showStatus('Could not load the sample document.', true);
      }
    });
  }

  // Recent documents dropdown
  function closeRecentMenu() {
    if (recentMenu) recentMenu.classList.remove('is-open');
    if (recentBtn) recentBtn.setAttribute('aria-expanded', 'false');
  }

  if (recentBtn && recentMenu) {
    recentBtn.addEventListener('click', async () => {
      const isOpen = recentMenu.classList.contains('is-open');
      if (isOpen) { closeRecentMenu(); return; }
      recentMenu.classList.add('is-open');
      recentBtn.setAttribute('aria-expanded', 'true');
      recentMenu.innerHTML = '<div class="recent-empty">Loading...</div>';
      try {
        const response = await fetch('/api/documents');
        const data = await response.json();
        recentMenu.innerHTML = '';
        if (data.success && data.documents.length > 0) {
          data.documents.forEach(doc => {
            const row = document.createElement('div');
            row.className = 'recent-doc-row';

            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'recent-doc-item';
            item.setAttribute('role', 'menuitem');
            const name = document.createElement('span');
            name.className = 'doc-name';
            name.textContent = doc.filename;
            name.title = doc.filename;
            const meta = document.createElement('span');
            meta.className = 'doc-meta';
            // "16 May 2026" style date from the stored timestamp
            const d = doc.created_at ? new Date(String(doc.created_at).replace(' ', 'T')) : null;
            meta.textContent = d && !isNaN(d)
              ? d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
              : '';
            item.appendChild(name);
            item.appendChild(meta);
            item.addEventListener('click', async () => {
              closeRecentMenu();
              try {
                const r = await fetch(`/api/documents/${doc.id}`);
                const d2 = await r.json();
                if (d2.success) {
                  openReview(d2.document.text, d2.document.filename, d2.document.id, d2.document.last_position);
                } else {
                  showStatus('That document could not be opened.', true);
                }
              } catch (err) {
                showStatus('Could not open that document.', true);
              }
            });

            const del = document.createElement('button');
            del.type = 'button';
            del.className = 'doc-delete-btn';
            del.textContent = 'Delete';
            del.setAttribute('aria-label', `Delete ${doc.filename}`);
            del.addEventListener('click', (e) => {
              e.stopPropagation();
              if (!window.confirm(`Delete "${doc.filename}"? This cannot be undone.`)) return;
              fetch(`/api/documents/${doc.id}`, fetchOptions({ method: 'DELETE' }))
                .then(r => r.json())
                .then(out => {
                  if (out.success) {
                    row.remove();
                    if (!recentMenu.querySelector('.recent-doc-row')) {
                      recentMenu.innerHTML = '<div class="recent-empty">No documents yet.</div>';
                    }
                  } else {
                    showStatus(out.error || 'That document could not be deleted.', true);
                  }
                })
                .catch(() => showStatus('Could not delete that document.', true));
            });

            row.appendChild(item);
            row.appendChild(del);
            recentMenu.appendChild(row);
          });
        } else {
          const empty = document.createElement('div');
          empty.className = 'recent-empty';
          empty.textContent = 'No documents yet.';
          recentMenu.appendChild(empty);
        }
      } catch (err) {
        recentMenu.innerHTML = '<div class="recent-empty">Could not load documents.</div>';
      }
    });

    document.addEventListener('mousedown', (e) => {
      if (!recentMenu.classList.contains('is-open')) return;
      if (!recentMenu.contains(e.target) && !recentBtn.contains(e.target)) closeRecentMenu();
    });
  }

  // Review actions
  if (btnUseText && reviewTextarea) {
    btnUseText.addEventListener('click', () => {
      const text = reviewTextarea.value.trim();
      if (!text) {
        showStatus('There is no text to read yet.', true);
        return;
      }
      loadTextIntoReader(text, pendingFilename, pendingDocumentId, pendingLastPosition);
      closeReview();
    });
  }
  if (btnDismissReview) btnDismissReview.addEventListener('click', closeReview);

  // Manual paste into the reader: re-analyze on blur (never mid-edit)
  if (previewBox) {
    previewBox.addEventListener('blur', () => {
      const text = (previewBox.textContent || '').trim();
      const lostRendering = toughWordsState.hadTough && !previewBox.querySelector('.tough-word');
      if (text && (text !== toughWordsState.analyzedText || lostRendering)) {
        renderToughWords(text);
      }
    });
  }

  /* ======================================================================
   * 4. SETTINGS: sliders, background picker, speech rate — saved per user
   * ==================================================================== */
  const fontSlider = document.getElementById('font-size-slider');
  const fontVal = document.getElementById('font-size-val');
  const letterSlider = document.getElementById('letter-spacing-slider');
  const letterVal = document.getElementById('letter-spacing-val');
  const lineSlider = document.getElementById('line-spacing-slider');
  const lineVal = document.getElementById('line-spacing-val');
  const bgSwatches = Array.from(document.querySelectorAll('.bg-swatch'));

  let settingsSaveTimer = 0;
  function scheduleSettingsSave() {
    clearTimeout(settingsSaveTimer);
    settingsSaveTimer = setTimeout(persistSettings, 600);
  }

  function currentSettings() {
    return {
      font_size: fontSlider ? parseInt(fontSlider.value, 10) : 18,
      letter_spacing: letterSlider ? parseFloat(letterSlider.value) : 0.02,
      line_spacing: lineSlider ? parseFloat(lineSlider.value) : 1.5,
      background: getActiveBackground(),
      speech_rate: rateSlider ? parseFloat(rateSlider.value) : 1
    };
  }

  function persistSettings() {
    fetch('/api/settings', fetchOptions({
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(currentSettings())
    })).catch(() => {}); // saving preferences is best-effort
  }

  function getActiveBackground() {
    const active = document.querySelector('.bg-swatch.is-active');
    return active ? active.dataset.bg : 'peach';
  }

  function applyBackground(name) {
    if (!previewBox) return;
    previewBox.classList.remove('bg-cream', 'bg-yellow', 'bg-turquoise');
    if (name && name !== 'peach') previewBox.classList.add(`bg-${name}`);
    syncSentenceStyle();
    bgSwatches.forEach(sw => {
      const isActive = sw.dataset.bg === name;
      sw.classList.toggle('is-active', isActive);
      sw.setAttribute('aria-pressed', String(isActive));
    });
  }

  // Slider events (live) + persistence (debounced)
  if (previewBox && fontSlider && fontVal) {
    fontSlider.addEventListener('input', () => {
      fontVal.textContent = `${fontSlider.value}px`;
      previewBox.style.fontSize = `${fontSlider.value}px`;
      syncSentenceStyle();
      scheduleSettingsSave();
    });
  }
  if (previewBox && letterSlider && letterVal) {
    letterSlider.addEventListener('input', () => {
      const val = parseFloat(letterSlider.value).toFixed(3);
      letterVal.textContent = `${parseFloat(val)}em`;
      previewBox.style.letterSpacing = `${val}em`;
      syncSentenceStyle();
      scheduleSettingsSave();
    });
  }
  if (previewBox && lineSlider && lineVal) {
    lineSlider.addEventListener('input', () => {
      const val = parseFloat(lineSlider.value).toFixed(2);
      lineVal.textContent = `${val}x`;
      previewBox.style.lineHeight = `${val}`;
      syncSentenceStyle();
      scheduleSettingsSave();
    });
  }
  bgSwatches.forEach(sw => {
    sw.addEventListener('click', () => {
      applyBackground(sw.dataset.bg);
      scheduleSettingsSave();
    });
  });

  // Restore saved settings on load (template injects them as JSON)
  const settingsBoot = document.getElementById('settings-boot');
  if (settingsBoot) {
    try {
      const s = JSON.parse(settingsBoot.textContent);
      if (fontSlider) {
        fontSlider.value = s.font_size;
        fontVal.textContent = `${s.font_size}px`;
        previewBox.style.fontSize = `${s.font_size}px`;
      }
      if (letterSlider) {
        letterSlider.value = s.letter_spacing;
        letterVal.textContent = `${s.letter_spacing}em`;
        previewBox.style.letterSpacing = `${s.letter_spacing}em`;
      }
      if (lineSlider) {
        lineSlider.value = s.line_spacing;
        lineVal.textContent = `${s.line_spacing}x`;
        previewBox.style.lineHeight = `${s.line_spacing}`;
      }
      applyBackground(s.background);
      if (rateSlider && s.speech_rate) {
        rateSlider.value = s.speech_rate;
        audio.rate = parseFloat(s.speech_rate) || 1;
        rateValue.textContent = audio.rate.toFixed(1) + '\u00d7';
      }
    } catch (err) { /* defaults are fine */ }
  }

  /* ======================================================================
   * SETTINGS PAGE (/settings): live values next to the sliders, and the
   * background swatches fill a hidden field so the form really saves.
   * ==================================================================== */
  const settingsForm = document.getElementById('settings-form');
  if (settingsForm) {
    const bindPill = (inputId, pillId, format) => {
      const input = document.getElementById(inputId);
      const pill = document.getElementById(pillId);
      if (!input || !pill) return;
      input.addEventListener('input', () => { pill.textContent = format(input.value); });
    };
    bindPill('font-size-slider', 'font-size-val', v => `${v}px`);
    bindPill('letter-spacing-slider', 'letter-spacing-val', v => `${parseFloat(v)}em`);
    bindPill('line-spacing-slider', 'line-spacing-val', v => `${parseFloat(v).toFixed(2)}x`);
    bindPill('speech-rate', 'speech-rate-val', v => `${parseFloat(v).toFixed(1)}\u00d7`);
    const bgField = document.getElementById('settings-bg-field');
    settingsForm.querySelectorAll('.bg-swatch').forEach(sw => {
      sw.addEventListener('click', () => {
        if (bgField) bgField.value = sw.dataset.bg;
        settingsForm.querySelectorAll('.bg-swatch').forEach(other => {
          const active = other === sw;
          other.classList.toggle('is-active', active);
          other.setAttribute('aria-pressed', String(active));
        });
      });
    });
  }

  /* ======================================================================
   * WRITING PAD -> READER: "Open in reader" on /write stores the text in
   * sessionStorage; the reader picks it up here and opens the review box.
   * ==================================================================== */
  if (previewBox && reviewTextarea) {
    try {
      const handoff = sessionStorage.getItem('dl_write_handoff');
      if (handoff) {
        sessionStorage.removeItem('dl_write_handoff');
        openReview(handoff, 'My writing', null, 0);
      }
    } catch (err) { /* storage blocked: nothing to hand over */ }
  }

  /* ======================================================================
   * PROFILE + ADMIN PAGE BUTTONS
   * (Moved here from inline <script> tags: the Content-Security-Policy only
   * allows scripts from our own files, so inline scripts never ran.)
   * ==================================================================== */
  const deleteAccountBtn = document.getElementById('btn-delete-account');
  const deleteConfirmBox = document.getElementById('delete-confirm-box');
  const cancelDeleteBtn = document.getElementById('btn-cancel-delete');
  if (deleteAccountBtn && deleteConfirmBox) {
    deleteAccountBtn.addEventListener('click', () => { deleteConfirmBox.hidden = false; deleteAccountBtn.hidden = true; });
  }
  if (cancelDeleteBtn && deleteConfirmBox && deleteAccountBtn) {
    cancelDeleteBtn.addEventListener('click', () => { deleteConfirmBox.hidden = true; deleteAccountBtn.hidden = false; });
  }
  document.querySelectorAll('[data-toggle-reset]').forEach(btn => {
    btn.addEventListener('click', () => {
      const form = document.getElementById('reset-form-' + btn.dataset.toggleReset);
      if (form) form.hidden = !form.hidden;
    });
  });
  document.querySelectorAll('[data-toggle-delete]').forEach(btn => {
    btn.addEventListener('click', () => {
      const form = document.getElementById('delete-form-' + btn.dataset.toggleDelete);
      if (form) form.hidden = !form.hidden;
    });
  });
  // <form data-confirm="..."> asks before submitting
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  /* ======================================================================
   * DOWNLOAD BUTTONS (Excel exports): disable while the file is built so
   * they cannot be double-clicked, then restore after a short wait.
   * ==================================================================== */
  document.querySelectorAll('a[href*="/export"], a[href*="/admin/activity/export"]').forEach(link => {
    link.addEventListener('click', () => {
      const original = link.textContent;
      link.textContent = 'Preparing...';
      link.style.pointerEvents = 'none';
      link.style.opacity = '0.6';
      setTimeout(() => {
        link.textContent = original;
        link.style.pointerEvents = '';
        link.style.opacity = '';
      }, 4000);
    });
  });

  /* ======================================================================
   * HEADER PROFILE MENU: click the name -> Settings / Profile / Log out
   * ==================================================================== */
  const profileMenuBtn = document.getElementById('profile-menu-btn');
  const profileDropdown = document.getElementById('profile-dropdown');
  if (profileMenuBtn && profileDropdown) {
    profileMenuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = profileDropdown.classList.toggle('is-open');
      profileMenuBtn.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('mousedown', (e) => {
      if (!profileDropdown.classList.contains('is-open')) return;
      if (profileDropdown.contains(e.target) || profileMenuBtn.contains(e.target)) return;
      profileDropdown.classList.remove('is-open');
      profileMenuBtn.setAttribute('aria-expanded', 'false');
    });
  }

  /* ======================================================================
   * FIRST-TIME HINT: three steps, dismissed once, never shown again
   * (localStorage key is global, not per-document)
   * ==================================================================== */
  const hintBox = document.getElementById('first-time-hint');
  const hintDismiss = document.getElementById('hint-dismiss');
  try {
    if (hintBox && !localStorage.getItem('dyslexilens_hint_dismissed')) {
      hintBox.hidden = false;
    }
  } catch (err) { /* private mode: hint simply stays hidden */ }
  if (hintDismiss && hintBox) {
    hintDismiss.addEventListener('click', () => {
      hintBox.hidden = true;
      try { localStorage.setItem('dyslexilens_hint_dismissed', '1'); } catch (err) {}
    });
  }

  /* ======================================================================
   * PASSWORD SHOW/HIDE EYE (login, signup, profile pages)
   * ==================================================================== */
  document.querySelectorAll('.pw-eye').forEach(eyeBtn => {
    eyeBtn.addEventListener('click', () => {
      const input = document.querySelector(eyeBtn.dataset.eyeTarget);
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      eyeBtn.setAttribute('aria-pressed', String(show));
      eyeBtn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      const open = eyeBtn.querySelector('.pw-eye-open');
      const closed = eyeBtn.querySelector('.pw-eye-closed');
      if (open) open.hidden = show;
      if (closed) closed.hidden = !show;
    });
  });

  /* ======================================================================
   * PROGRESS: record + refresh the summary chips
   * ==================================================================== */
  function recordProgress(payload) {
    fetch('/api/progress', fetchOptions({
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(payload)
    })).then(r => (r.ok ? r.json() : null)).then(d => {
      if (!d || !d.success) return;
      const docs = document.getElementById('stat-documents');
      const words = document.getElementById('stat-words');
      const practised = document.getElementById('stat-practised');
      if (docs) docs.textContent = d.progress.documents_read;
      if (words) words.textContent = d.progress.words_spoken;
      if (practised) practised.textContent = d.progress.words_practised;
    }).catch(() => {});
  }

});
