// DyslexiLens writing pad (November update)
//
// Writing is hard for many dyslexic students, so this page offers:
//   * Dictate     — speak and the words are typed for you (Chrome / Edge)
//   * Spellcheck  — the browser underlines spelling mistakes (spellcheck="true")
//   * Read back   — hear what you wrote, to catch mistakes by ear
//   * Download    — save the text as a .txt file
//   * Open in reader — send the text to the reader page
// The draft is kept in this browser (localStorage) so it is not lost on refresh.

document.addEventListener('DOMContentLoaded', () => {
  'use strict';

  const area = document.getElementById('write-area');
  if (!area) return;

  const btnDictate = document.getElementById('write-dictate');
  const btnReadBack = document.getElementById('write-read-back');
  const btnStop = document.getElementById('write-stop');
  const btnDownload = document.getElementById('write-download');
  const btnToReader = document.getElementById('write-to-reader');
  const btnClear = document.getElementById('write-clear');
  const status = document.getElementById('write-status');
  const count = document.getElementById('write-count');

  // One draft per user, so two people sharing a computer never see each other's text
  const DRAFT_KEY = `dl_write_draft_${document.documentElement.dataset.user || 'guest'}`;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;

  function setStatus(text) { status.textContent = text; }

  function updateCount() {
    const words = (area.value.trim().match(/\S+/g) || []).length;
    count.textContent = `${words} word${words === 1 ? '' : 's'}`;
  }

  // --- Draft is kept in the browser ------------------------------------
  try { area.value = localStorage.getItem(DRAFT_KEY) || ''; } catch (e) {}
  updateCount();
  let saveTimer = 0;
  area.addEventListener('input', () => {
    updateCount();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try { localStorage.setItem(DRAFT_KEY, area.value); } catch (e) {}
    }, 400);
  });

  // Adds dictated words where the cursor is
  function insertAtCursor(text) {
    const start = area.selectionStart, end = area.selectionEnd;
    const before = area.value.slice(0, start);
    const needsSpace = before && !/\s$/.test(before);
    const piece = (needsSpace ? ' ' : '') + text;
    area.value = before + piece + area.value.slice(end);
    const pos = start + piece.length;
    area.setSelectionRange(pos, pos);
    area.dispatchEvent(new Event('input'));
  }

  // Simple spoken punctuation: "full stop", "comma", "question mark", "new line"
  function applySpokenPunctuation(text) {
    return text
      .replace(/\s*\b(full stop|period)\b/gi, '.')
      .replace(/\s*\bcomma\b/gi, ',')
      .replace(/\s*\bquestion mark\b/gi, '?')
      .replace(/\s*\bnew (line|paragraph)\b\s*/gi, '\n');
  }

  // --- Dictation ------------------------------------------------------
  let recognition = null;
  let dictating = false;

  function stopDictation() {
    dictating = false;
    btnDictate.textContent = 'Dictate';
    btnDictate.classList.remove('is-listening');
    if (recognition) { try { recognition.stop(); } catch (e) {} }
  }

  btnDictate.addEventListener('click', () => {
    if (dictating) { stopDictation(); setStatus('Dictation stopped.'); return; }
    if (!Recognition) {
      setStatus('Dictation works in Chrome and Edge. In this browser, please type.');
      return;
    }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    recognition = new Recognition();
    recognition.lang = 'en-IN';
    recognition.continuous = true;        // keep listening until Stop
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          insertAtCursor(applySpokenPunctuation(event.results[i][0].transcript.trim()));
        }
      }
    };
    recognition.onerror = (event) => {
      const messages = {
        'not-allowed': 'The microphone is blocked. Allow it in the browser to dictate.',
        'network': 'Dictation needs the internet. Please check your connection.',
        'no-speech': 'I did not hear anything. Press Dictate and try again.'
      };
      setStatus(messages[event.error] || 'Dictation stopped because of an error.');
      stopDictation();
    };
    recognition.onend = () => { if (dictating) stopDictation(); };
    try {
      recognition.start();
      dictating = true;
      btnDictate.textContent = 'Stop dictating';
      btnDictate.classList.add('is-listening');
      setStatus('Listening. Say "full stop", "comma" or "new line" for punctuation.');
      area.focus();
    } catch (e) {
      setStatus('Dictation could not start. Please try again.');
    }
  });

  // --- Read back ------------------------------------------------------
  btnReadBack.addEventListener('click', () => {
    const text = area.value.trim();
    if (!text) { setStatus('There is nothing to read yet.'); return; }
    if (!('speechSynthesis' in window)) { setStatus('Reading aloud is not available in this browser.'); return; }
    stopDictation();
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'en-IN';
    utter.rate = 0.95;
    utter.onend = () => setStatus('');
    window.__dlWriteUtter = utter;
    window.speechSynthesis.speak(utter);
    setStatus('Reading your text aloud. Press Stop to stop.');
  });

  btnStop.addEventListener('click', () => {
    stopDictation();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    setStatus('Stopped.');
  });

  // --- Download as a .txt file ----------------------------------------
  btnDownload.addEventListener('click', () => {
    const text = area.value;
    if (!text.trim()) { setStatus('There is nothing to download yet.'); return; }
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const link = document.createElement('a');
    const date = new Date().toISOString().slice(0, 10);
    link.href = URL.createObjectURL(blob);
    link.download = `my-writing-${date}.txt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    setStatus('Downloaded as a text file.');
  });

  // --- Send to the reader (picked up by main.js on /home) --------------
  btnToReader.addEventListener('click', () => {
    const text = area.value.trim();
    if (!text) { setStatus('There is nothing to open yet.'); return; }
    try { sessionStorage.setItem('dl_write_handoff', text); } catch (e) {}
    window.location.href = '/home';
  });

  btnClear.addEventListener('click', () => {
    if (!area.value) return;
    if (!window.confirm('Clear all the text? This cannot be undone.')) return;
    area.value = '';
    area.dispatchEvent(new Event('input'));
    try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
    setStatus('Cleared.');
  });
});
