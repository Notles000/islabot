'use strict';

// ─── Auth guard ───────────────────────────────────────────────────────────────

const TOKEN = localStorage.getItem('token');
if (!TOKEN) { window.location.href = 'index.html'; }

const USER = JSON.parse(localStorage.getItem('user') || '{}');

function authHeaders() {
  return { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };
}

// ─── marked.js config ─────────────────────────────────────────────────────────

if (typeof marked !== 'undefined') {
  marked.setOptions({ breaks: true, gfm: true });
}

// ─── State ────────────────────────────────────────────────────────────────────

const state = {
  course:        null,
  courses:       [],
  sessionId:     null,
  isTyping:      false,
  lastQuestion:  null,
  abortCtrl:     null,
  lang:          localStorage.getItem('answerLang') || 'pt',
  tab:           'chats',
  allSessions:   [],
};

// Per-bubble state (thinking mode tracking)
const _bubble = {};

// ─── DOM refs ─────────────────────────────────────────────────────────────────

const chatInner    = () => document.getElementById('chatInner');
const welcomeScr   = () => document.getElementById('welcomeScreen');
const chatArea     = () => document.getElementById('chatArea');
const msgInput     = () => document.getElementById('msgInput');
const sendBtn      = () => document.getElementById('sendBtn');
const stopBtn      = () => document.getElementById('stopBtn');
const activeBadge  = () => document.getElementById('activeCourseLabel');
const courseSelect = () => document.getElementById('courseSelector');
const historyList  = () => document.getElementById('historyList');

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  const userNameEl = document.getElementById('userName');
  if (userNameEl) userNameEl.textContent = USER.name || 'Utilizador';

  const avatarEl = document.getElementById('userAvatar');
  if (avatarEl && USER.name)
    avatarEl.textContent = USER.name.split(' ').map(w => w[0]).join('').substring(0, 2).toUpperCase();

  const roleEl = document.getElementById('userRole');
  if (roleEl && USER.role)
    roleEl.textContent = USER.role === 'admin' ? 'Administrador' : USER.role === 'instructor' ? 'Docente' : 'Estudante';

  const adminLink = document.getElementById('adminLink');
  if (adminLink && USER.role === 'admin')
    adminLink.style.display = '';

  const instructorLink = document.getElementById('instructorLink');
  if (instructorLink && USER.role === 'instructor')
    instructorLink.style.display = '';

  const welcomeTitleName = document.getElementById('welcomeTitleName');
  if (welcomeTitleName && USER.name)
    welcomeTitleName.textContent = `${USER.name.split(' ')[0]}!`;
  else if (welcomeTitleName)
    welcomeTitleName.textContent = 'Utilizador!';

  // Apply persisted preferences
  applyDarkMode(localStorage.getItem('darkMode') === '1');
  applyFontSize(localStorage.getItem('fontSize') || 'md');
  applyLang(state.lang);

  // Keyboard shortcuts
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); msgInput().focus(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') { e.preventDefault(); openSearch(); }
    if (e.key === 'Escape') {
      if (state.isTyping) stopGeneration();
      closeSettings();
      closeSearch({ target: document.getElementById('searchModal') });
      closeSummary({ target: document.getElementById('summaryModal') });
    }
  });

  // Close settings / more panel on outside click
  document.addEventListener('click', e => {
    const settingsPanel = document.getElementById('settingsPanel');
    const settingsBtn   = document.getElementById('settingsBtn');
    if (settingsPanel && !settingsPanel.contains(e.target) && !settingsBtn?.contains(e.target))
      settingsPanel.classList.remove('open');

    const morePanel = document.getElementById('morePanel');
    const moreBtn   = document.getElementById('moreBtn');
    if (morePanel && !morePanel.contains(e.target) && !moreBtn?.contains(e.target))
      morePanel.classList.remove('open');
  });

  msgInput().addEventListener('input', onInputChange);
  await loadCourses();
  await loadSessions();
  await loadPreferences();

  // Onboarding: show on first login
  if (!localStorage.getItem('onboarded')) {
    const el = document.getElementById('onboardingOverlay');
    if (el) el.style.display = 'flex';
  }
});

// ─── Courses ──────────────────────────────────────────────────────────────────

async function loadCourses() {
  try {
    const res = await fetch('/api/courses/mine', { headers: authHeaders() });
    if (!res.ok) { if (res.status === 401) logout(); return; }
    state.courses = await res.json();
  } catch { state.courses = []; }

  const sel = courseSelect();
  if (!sel) return;

  sel.innerHTML = state.courses.length
    ? state.courses.map(c => `<option value="${c.id}">${c.short_name || c.code} — ${c.name}</option>`).join('')
    : '<option value="">Sem UCs disponíveis</option>';

  if (state.courses.length) {
    state.course = state.courses[0];
    updateCourseUI(state.course);
  }
}

function changeCourse(selectEl) {
  const id = parseInt(selectEl.value);
  const c  = state.courses.find(x => x.id === id);
  if (!c) return;
  state.course    = c;
  state.sessionId = null;
  updateCourseUI(c);
  clearMessages();
}

function updateCourseUI(c) {
  const badge = document.getElementById('activeBadge');
  if (badge) {
    badge.classList.remove('switching');
    void badge.offsetWidth; // force reflow to restart animation
    badge.classList.add('switching');
  }
  if (activeBadge()) activeBadge().textContent = c.name;
  msgInput().placeholder = `Faz uma pergunta sobre ${c.short_name || c.name}...`;
}

// ─── Sessions (sidebar) ───────────────────────────────────────────────────────

async function loadSessions() {
  try {
    const res      = await fetch('/api/chat/sessions', { headers: authHeaders() });
    if (!res.ok) return;
    state.allSessions = await res.json();
    renderSessions(state.allSessions);
    // Show search if there are sessions
    const sw = document.getElementById('searchWrap');
    if (sw) sw.style.display = state.allSessions.length > 3 ? '' : 'none';
  } catch {}
}

function renderSessions(sessions) {
  const list = historyList();
  if (!list) return;
  if (!sessions.length) {
    list.innerHTML = '<div style="padding:.5rem 1rem;font-size:.8rem;color:var(--text-3);">Sem conversas anteriores</div>';
    return;
  }

  const today = new Date().toDateString();
  const week  = Date.now() - 7 * 86400000;
  const groups = { today: [], week: [], older: [] };

  sessions.forEach(s => {
    const d = new Date(s.updated_at);
    if (d.toDateString() === today) groups.today.push(s);
    else if (d >= week)             groups.week.push(s);
    else                            groups.older.push(s);
  });

  let html = '';
  const render = (label, items) => {
    if (!items.length) return;
    html += `<div class="history-group-label">${label}</div>`;
    items.forEach(s => {
      const c     = state.courses.find(x => x.id === s.course_id);
      const badge = c ? `<span class="hist-badge">${c.short_name || c.code}</span>` : '';
      html += `
        <div class="history-item" data-id="${s.id}" onclick="loadSession(${s.id}, ${s.course_id}, this)">
          ${badge}
          <span ondblclick="event.stopPropagation(); startRename(${s.id}, this)">${escHtml(s.title || 'Conversa')}</span>
          <button class="hist-delete" onclick="event.stopPropagation(); deleteSession(${s.id}, this)" title="Apagar">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>`;
    });
  };

  render('Hoje', groups.today);
  render('Últimos 7 dias', groups.week);
  render('Anterior', groups.older);
  list.innerHTML = html;
}

async function loadSession(sessionId, courseId, el) {
  document.querySelectorAll('.history-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');

  const c = state.courses.find(x => x.id === courseId);
  if (c) { state.course = c; updateCourseUI(c); }
  state.sessionId = sessionId;
  clearMessages(false);

  const exportItem  = document.getElementById('exportItem');
  const summaryItem = document.getElementById('summaryItem');
  if (exportItem)  exportItem.style.display  = '';
  if (summaryItem) summaryItem.style.display = '';

  try {
    const res  = await fetch(`/api/chat/sessions/${sessionId}/messages`, { headers: authHeaders() });
    const msgs = await res.json();
    hideWelcome();
    let lastUserContent = null;
    msgs.forEach(m => {
      if (m.role === 'user') {
        appendUserMessage(m.content, false, null, false);
        lastUserContent = m.content;
      } else if (m.role === 'assistant') {
        appendBotMessage(m.content, m.sources || [], false, m.id, true, lastUserContent);
      }
    });
    scrollBottom();
  } catch {}
}

async function deleteSession(sessionId, btn) {
  try {
    await fetch(`/api/chat/sessions/${sessionId}`, { method: 'DELETE', headers: authHeaders() });
  } catch {}

  // Remove from sidebar
  const item = btn.closest('.history-item');
  if (item) item.remove();

  // If this was the active session, start fresh
  if (state.sessionId === sessionId) {
    state.sessionId = null;
    clearMessages(true);
  }
}

function startRename(sessionId, spanEl) {
  const current = spanEl.textContent;
  const input   = document.createElement('input');
  input.className   = 'hist-rename-input';
  input.value       = current;
  input.maxLength   = 80;
  spanEl.replaceWith(input);
  input.focus();
  input.select();

  const finish = async () => {
    const title = input.value.trim() || current;
    const span  = document.createElement('span');
    span.ondblclick = (e) => { e.stopPropagation(); startRename(sessionId, span); };
    span.textContent = title;
    input.replaceWith(span);

    if (title !== current) {
      try {
        await fetch(`/api/chat/sessions/${sessionId}`, {
          method:  'PATCH',
          headers: authHeaders(),
          body:    JSON.stringify({ title }),
        });
      } catch {}
    }
  };

  input.addEventListener('blur',    finish);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = current; input.blur(); }
  });
}

// ─── Input ────────────────────────────────────────────────────────────────────

function onInputChange() {
  sendBtn().disabled = !msgInput().value.trim() || state.isTyping;
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  onInputChange();
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!sendBtn().disabled) sendMessage();
  }
}

// ─── Send / Stream ────────────────────────────────────────────────────────────

async function sendMessage() {
  let text = msgInput().value.trim();
  if (!text && !_activeMode) return;
  if (state.isTyping) return;
  if (!state.course) { alert('Seleciona uma UC primeiro.'); return; }

  // Prepend mode context if a mode tag is active
  if (_activeMode) {
    const mode = _MODES.find(m => m.key === _activeMode);
    text = text ? `[${mode.context}] ${text}` : mode.context;
    _setModeTag(null);  // clear tag after sending
  }

  await sendMessageWith(text);
}

async function sendMessageWith(text) {
  hideWelcome();
  appendUserMessage(text, true, null, true);
  clearInput();
  setTyping(true);

  const bubbleId = createStreamBubble();

  try {
    state.abortCtrl = new AbortController();
    const res = await fetch('/api/chat/message/stream', {
      method:  'POST',
      headers: authHeaders(),
      body:    JSON.stringify({ course_id: state.course.id, question: text, session_id: state.sessionId || undefined, language: state.lang }),
      signal:  state.abortCtrl.signal,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const msg = res.status === 429
        ? (err.detail || 'Demasiadas perguntas. Aguarda um momento.')
        : `Erro: ${err.detail || 'Sem resposta.'}`;
      finalizeStreamBubble(bubbleId, msg, [], null, false, text);
      setTyping(false);
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer  = '';
    let sources = [];
    let rawText = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let data;
        try { data = JSON.parse(line.slice(6)); } catch { continue; }

        if (data.type === 'meta') {
          state.sessionId = data.session_id;
          sources = data.sources || [];
        } else if (data.type === 'thinking_start') {
          startThinking(bubbleId);
        } else if (data.type === 'thinking_token') {
          appendThinkingToken(bubbleId, data.text);
        } else if (data.type === 'thinking_end') {
          endThinking(bubbleId);
        } else if (data.type === 'token') {
          rawText += data.text;
          appendToStreamBubble(bubbleId, data.text);
        } else if (data.type === 'end') {
          finalizeStreamBubble(bubbleId, rawText, sources, data.message_id, true, text);
        }
      }
    }

  } catch (err) {
    if (err.name !== 'AbortError') {
      finalizeStreamBubble(bubbleId, 'Erro de ligação ao servidor.', [], null, false, text);
    } else {
      // Stopped by user — finalize with whatever was accumulated
      const textEl = document.getElementById(bubbleId + '-text');
      if (textEl) {
        const cursor = textEl.querySelector('.stream-cursor');
        if (cursor) cursor.remove();
        const raw = textEl.textContent.trim();
        if (raw) finalizeStreamBubble(bubbleId, raw, [], null, false, text);
      }
    }
  }

  state.abortCtrl = null;
  setTyping(false);
  await loadSessions();
}

function stopGeneration() {
  if (state.abortCtrl) {
    state.abortCtrl.abort();
    state.abortCtrl = null;
  }
}

function setTyping(on) {
  state.isTyping = on;
  sendBtn().disabled = on;
  const sb = stopBtn();
  if (sb) sb.classList.toggle('visible', on);
  onInputChange();
}

// ─── Streaming bubble ─────────────────────────────────────────────────────────

function createStreamBubble() {
  const id = 'sb-' + Date.now();
  _bubble[id] = { hadThinking: false };

  const html = `
    <div class="msg-wrap bot" id="${id}">
      <div class="think-blob-wrap" id="${id}-blobwrap">
        <div class="think-blob blob-thinking" id="${id}-blob"></div>
        <span class="think-blob-label" id="${id}-bloblabel" style="color:#a78bfa;">A pensar...</span>
      </div>
      <div class="think-section" id="${id}-think" style="display:none">
        <button class="think-toggle" onclick="toggleThinking('${id}')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          <span id="${id}-thinklabel">A pensar...</span>
          <span class="think-chevron">▾</span>
        </button>
        <div class="think-content" id="${id}-thinktext"></div>
      </div>
      <div class="msg-row" id="${id}-row" style="display:none">
        <div class="bot-avatar">
          <img src="assets/images/islasantarem-inverse.svg" alt="Bot" />
        </div>
        <div class="bubble answer-reveal" id="${id}-text"><span class="stream-cursor"></span></div>
      </div>
    </div>`;
  chatInner().insertAdjacentHTML('beforeend', html);
  scrollBottom();
  return id;
}

function startThinking(id) {
  _bubble[id].hadThinking = true;
  const el = document.getElementById(id + '-think');
  if (el) { el.style.display = ''; }
}

function appendThinkingToken(id, text) {
  const el = document.getElementById(id + '-thinktext');
  if (el) el.insertAdjacentText('beforeend', text);
}

function endThinking(id) {
  // Blob: purple → teal (halfway) → green on first answer token
  const blob  = document.getElementById(id + '-blob');
  const label = document.getElementById(id + '-bloblabel');
  const tlabel = document.getElementById(id + '-thinklabel');
  if (blob)  { blob.classList.remove('blob-thinking'); blob.classList.add('blob-transitioning'); }
  if (label) { label.style.color = '#0ea5e9'; label.textContent = 'A responder...'; }
  if (tlabel) tlabel.textContent = 'Ver raciocínio';

  // Show answer row
  const row = document.getElementById(id + '-row');
  if (row) row.style.display = '';
}

function toggleThinking(id) {
  const el = document.getElementById(id + '-think');
  if (el) el.classList.toggle('open');
}

function appendToStreamBubble(id, token) {
  // Show answer row if not already visible
  const row = document.getElementById(id + '-row');
  if (row && row.style.display === 'none') row.style.display = '';

  // Blob transitions purple → green when first answer token arrives
  const blob = document.getElementById(id + '-blob');
  if (blob && !blob.classList.contains('blob-answering')) {
    blob.classList.remove('blob-thinking', 'blob-transitioning');
    blob.classList.add('blob-answering');
    const label = document.getElementById(id + '-bloblabel');
    if (label) { label.style.color = '#10b981'; label.textContent = 'A responder...'; }
  }

  const textEl = document.getElementById(id + '-text');
  if (!textEl) return;
  const cursor = textEl.querySelector('.stream-cursor');
  if (cursor) cursor.remove();
  textEl.insertAdjacentText('beforeend', token);
  textEl.insertAdjacentHTML('beforeend', '<span class="stream-cursor"></span>');
  scrollBottom();
}

function finalizeStreamBubble(id, rawText, sources, messageId, hadResults, question) {
  const wrap   = document.getElementById(id);
  const textEl = document.getElementById(id + '-text');
  if (!wrap || !textEl) return;

  // Remove blob wrap
  const blobwrap = document.getElementById(id + '-blobwrap');
  if (blobwrap) blobwrap.remove();

  const cursor = textEl.querySelector('.stream-cursor');
  if (cursor) cursor.remove();

  const { clean: modeClean, mode } = _extractMode(rawText);
  const { clean, chips } = _extractChips(modeClean);
  textEl.innerHTML = formatText(clean);
  textEl.classList.add('answer-reveal');

  const time        = now();
  const sourcesHtml = buildSourcesHtml(sources);
  const footerHtml  = buildFooterHtml(time, messageId, hadResults, clean, question);

  wrap.insertAdjacentHTML('beforeend', sourcesHtml + footerHtml + _buildChipsHtml(chips) + _buildModeBadgeHtml(mode, question));
  scrollBottom();
  state.lastQuestion = question;
  delete _bubble[id];

  // Show export + summary items now that session has messages
  const exportItem  = document.getElementById('exportItem');
  const summaryItem = document.getElementById('summaryItem');
  if (exportItem)  exportItem.style.display  = '';
  if (summaryItem) summaryItem.style.display = '';
}

// ─── Non-streaming messages (history load) ────────────────────────────────────

function appendUserMessage(text, scroll = true, _unused = null, showEdit = true) {
  const editBtn = showEdit ? `
    <button class="user-edit-btn" onclick="editMessage(${JSON.stringify(text)})" title="Editar e reenviar">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
      </svg>
    </button>` : '';

  const html = `
    <div class="msg-wrap user fade-in">
      <div class="msg-row">
        <div class="bubble">${escHtml(text)}</div>
        ${editBtn}
      </div>
      <div class="msg-time">${now()}</div>
    </div>`;
  chatInner().insertAdjacentHTML('beforeend', html);
  if (scroll) scrollBottom();
}

function appendBotMessage(text, sources = [], scroll = true, messageId = null, hadResults = true, question = null) {
  const { clean: modeClean, mode } = _extractMode(text);
  const { clean, chips } = _extractChips(modeClean);
  const sourcesHtml = buildSourcesHtml(sources);
  const footerHtml  = buildFooterHtml(now(), messageId, hadResults, clean, question || state.lastQuestion);
  const html = `
    <div class="msg-wrap bot fade-in">
      <div class="msg-row">
        <div class="bot-avatar">
          <img src="assets/images/islasantarem-inside.svg" alt="Bot" />
        </div>
        <div class="bubble">${formatText(clean)}</div>
      </div>
      ${sourcesHtml}
      ${footerHtml}
      ${_buildChipsHtml(chips)}
      ${_buildModeBadgeHtml(mode, question || state.lastQuestion)}
    </div>`;
  chatInner().insertAdjacentHTML('beforeend', html);
  if (scroll) scrollBottom();
}

// ─── Quick-reply chips ────────────────────────────────────────────────────────

const _CHIP_RE = /\[\[([^\]]+)\]\]\s*$/;
const _MODE_RE = /\[\[MODE:(CURSO|GERAL)\]\]\s*$/;

function _extractChips(text) {
  const m = text.match(_CHIP_RE);
  if (!m) return { clean: text, chips: [] };
  const chips = m[1].split('|').map(s => s.trim()).filter(Boolean);
  return { clean: text.replace(_CHIP_RE, '').trimEnd(), chips };
}

function _extractMode(text) {
  const m = text.match(_MODE_RE);
  if (!m) return { clean: text, mode: null };
  return { clean: text.replace(_MODE_RE, '').trimEnd(), mode: m[1] };
}

function _buildChipsHtml(chips) {
  if (!chips.length) return '';
  const keys = ['curso', 'geral'];
  return `<div class="quick-chips">${chips.map((c, i) =>
    `<button class="quick-chip" onclick="sendQuickReply(${JSON.stringify(keys[i] || 'geral')})">${escHtml(c)}</button>`
  ).join('')}</div>`;
}

function _buildModeBadgeHtml(mode, question) {
  if (!mode) return '';
  const isCurso    = mode === 'CURSO';
  const label      = isCurso ? '📚 CURSO' : '🌐 GERAL';
  const otherKey   = isCurso ? 'geral' : 'curso';
  const otherLabel = isCurso ? 'GERAL' : 'CURSO';
  if (question) {
    // escHtml on the JSON string so double-quotes don't break the HTML attribute
    const q = escHtml(JSON.stringify(question));
    return `<div class="answer-mode-badge clickable" onclick="reaskWithMode(${q}, '${otherKey}')" title="Resposta ${label} — clica para repetir como ${otherLabel}">${label}<span class="mode-switch">→ ${otherLabel}</span></div>`;
  }
  return `<div class="answer-mode-badge" title="${label}">${label}</div>`;
}

function reaskWithMode(question, modeKey) {
  if (state.isTyping) return;
  const mode = _MODES.find(m => m.key === modeKey);
  sendMessageWith(`[${mode.context}] ${question}`);
}

// ─── Mode tag (Curso / Geral) ─────────────────────────────────────────────────

const _MODES = [
  { key: 'curso',  label: '📚 /sobre_o_curso',  context: 'Sobre o curso'      },
  { key: 'geral',  label: '🌐 /curiosidade_geral', context: 'Curiosidade geral' },
];
let _activeMode = null;  // null | 'curso' | 'geral'

function _setModeTag(key) {
  _activeMode = key;
  const tag = document.getElementById('modeTag');
  if (!key) { tag.style.display = 'none'; return; }
  const mode = _MODES.find(m => m.key === key);
  tag.textContent = mode.label;
  tag.style.display = 'inline-flex';
  tag.dataset.mode  = key;
  document.getElementById('msgInput').focus();
}

function toggleModeTag() {
  const next = _activeMode === 'curso' ? 'geral' : 'curso';
  _setModeTag(next);
}

function sendQuickReply(modeKey) {
  document.querySelectorAll('.quick-chips').forEach(el => el.remove());
  if (!state.lastQuestion || state.isTyping) return;
  const mode = _MODES.find(m => m.key === modeKey);
  sendMessageWith(`[${mode.context}] ${state.lastQuestion}`);
}

// ─── HTML builders ────────────────────────────────────────────────────────────

function buildSourcesHtml(sources) {
  if (!sources || !sources.length) return '';
  return `<div class="sources">${sources.map(s => `
    <div class="source-chip" onclick="searchBySource(${JSON.stringify(s.label)})" title="Pesquisar outras respostas desta fonte">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      ${escHtml(s.label)}${s.page ? ' · ' + escHtml(s.page) : ''}
    </div>`).join('')}</div>`;
}

function searchBySource(label) {
  openSearch();
  const input = document.getElementById('searchModalInput');
  if (input) {
    input.value = label;
    runSearch(label);
  }
}

function buildFooterHtml(time, messageId, hadResults, rawText, question) {
  const copyBtn = `
    <button class="msg-action-btn" onclick="copyMessage(this, ${JSON.stringify(rawText)})" title="Copiar resposta">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
      </svg>
    </button>`;

  const regenBtn = question ? `
    <button class="msg-action-btn" onclick="regenerateMessage()" title="Regenerar resposta">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
      </svg>
    </button>` : '';

  const bookmarkBtn = messageId ? `
    <button class="msg-action-btn" id="bm-${messageId}" onclick="toggleBookmark(${messageId}, ${JSON.stringify(rawText)}, this)" title="Guardar resposta">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>
      </svg>
    </button>` : '';

  const ratingHtml = messageId && hadResults ? `
    <div class="msg-rating" data-id="${messageId}">
      <button class="rate-btn" title="Resposta útil" onclick="rateMessage(${messageId}, 1, this)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/>
          <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
        </svg>
      </button>
      <button class="rate-btn" title="Resposta incorrecta" onclick="rateMessage(${messageId}, -1, this)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/>
          <path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/>
        </svg>
      </button>
    </div>` : '';

  return `
    <div class="msg-footer" style="padding-left:40px;display:flex;align-items:center;gap:.3rem;">
      <span class="msg-time">${time}</span>
      <div class="msg-actions">${copyBtn}${regenBtn}${bookmarkBtn}</div>
      ${ratingHtml}
    </div>`;
}

// ─── User actions ─────────────────────────────────────────────────────────────

async function copyMessage(btn, text) {
  try {
    await navigator.clipboard.writeText(text);
    btn.classList.add('copied');
    btn.title = 'Copiado!';
    setTimeout(() => { btn.classList.remove('copied'); btn.title = 'Copiar resposta'; }, 2000);
  } catch {}
}

function regenerateMessage() {
  if (!state.lastQuestion || state.isTyping) return;
  sendMessageWith(state.lastQuestion);
}

function editMessage(text) {
  msgInput().value = text;
  autoResize(msgInput());
  state.sessionId = null;
  msgInput().focus();
  window.scrollTo(0, document.body.scrollHeight);
}

async function rateMessage(messageId, rating, btn) {
  const wrap = btn.closest('.msg-rating');
  if (wrap.dataset.rated) return;
  wrap.dataset.rated = '1';
  try {
    await fetch(`/api/chat/messages/${messageId}/rating`, {
      method:  'PATCH',
      headers: authHeaders(),
      body:    JSON.stringify({ rating }),
    });
    wrap.querySelectorAll('.rate-btn').forEach(b => b.classList.remove('rated'));
    btn.classList.add('rated');
  } catch {}
}

function sendQuick(text) {
  msgInput().value = text;
  autoResize(msgInput());
  sendMessage();
}

// ─── Formatting ───────────────────────────────────────────────────────────────

function formatText(text) {
  if (typeof marked !== 'undefined') {
    return marked.parse(text || '');
  }
  return escHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function hideWelcome() {
  const w = welcomeScr();
  if (w) w.style.display = 'none';
}

function clearInput() {
  const el = msgInput();
  el.value = '';
  el.style.height = 'auto';
  sendBtn().disabled = true;
  removeAttachment();  // clear any pending attachment
}

// ─── Voice input ──────────────────────────────────────────────────────────────

let _recognition = null;
let _voiceActive  = false;

function toggleVoice() {
  if (_voiceActive) {
    _stopVoice();
    return;
  }
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    showNotif('Reconhecimento de voz não suportado neste browser (usa Chrome ou Edge).', true);
    return;
  }
  _recognition = new SpeechRecognition();
  _recognition.lang             = 'pt-PT';
  _recognition.interimResults   = true;
  _recognition.maxAlternatives  = 1;
  _recognition.continuous       = false;

  const micBtn = document.getElementById('micBtn');
  const defIcon  = micBtn.querySelector('.mic-icon-default');
  const stopIcon = micBtn.querySelector('.mic-icon-stop');

  _recognition.onstart = () => {
    _voiceActive = true;
    micBtn.classList.add('recording');
    if (defIcon)  defIcon.style.display  = 'none';
    if (stopIcon) stopIcon.style.display = '';
    msgInput().placeholder = 'A ouvir...';
  };

  _recognition.onresult = (event) => {
    const transcript = Array.from(event.results).map(r => r[0].transcript).join('');
    msgInput().value = transcript;
    autoResize(msgInput());
    onInputChange();
  };

  _recognition.onend = _stopVoice;
  _recognition.onerror = _stopVoice;
  _recognition.start();
}

function _stopVoice() {
  _voiceActive = false;
  if (_recognition) { try { _recognition.stop(); } catch (_) {} _recognition = null; }
  const micBtn  = document.getElementById('micBtn');
  if (!micBtn) return;
  micBtn.classList.remove('recording');
  const defIcon  = micBtn.querySelector('.mic-icon-default');
  const stopIcon = micBtn.querySelector('.mic-icon-stop');
  if (defIcon)  defIcon.style.display  = '';
  if (stopIcon) stopIcon.style.display = 'none';
  const ph = state.course ? `Faz uma pergunta sobre ${state.course.short_name || state.course.name}...` : 'Faz uma pergunta...';
  msgInput().placeholder = ph;
}

// ─── Attachment (PDF / image OCR) ────────────────────────────────────────────

let _attachment = null;  // { file, text, type, ready }

async function handleAttachment(event) {
  const file = event.target.files[0];
  event.target.value = '';  // reset so same file can be re-selected
  if (!file) return;

  const ext = file.name.split('.').pop().toLowerCase();
  const isPdf = ext === 'pdf';

  // Show preview strip immediately
  _attachment = { file, text: '', type: isPdf ? 'pdf' : 'image', ready: false };
  _showAttachPreview(file.name, 'A processar...', false);

  const token = localStorage.getItem('token');
  const fd = new FormData();
  fd.append('file', file);

  try {
    const res = await fetch('/api/chat/process-attachment', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` },
      body: fd,
    });
    if (!res.ok) { _attachError('Erro ao processar ficheiro'); return; }
    const data = await res.json();

    if (data.warning) {
      _attachError(data.warning);
      return;
    }
    if (!data.text) {
      _attachError('Não foi possível extrair texto deste ficheiro.');
      return;
    }
    _attachment = { file, text: data.text, type: data.type, ready: true };
    const label = isPdf
      ? `PDF · ${data.chars ? (data.chars/1000).toFixed(1)+'k chars' : 'extraído'}`
      : 'Imagem · texto extraído';
    _showAttachPreview(file.name, label, true);
    // Put extracted text into input so user can review / edit
    if (!msgInput().value.trim()) {
      msgInput().value = data.text.substring(0, 800);
      autoResize(msgInput());
      onInputChange();
    }
  } catch {
    _attachError('Erro de ligação');
  }
}

function _showAttachPreview(name, status, ready) {
  const strip  = document.getElementById('attachPreview');
  const nameEl = document.getElementById('attachName');
  const statEl = document.getElementById('attachStatus');
  const iconEl = document.getElementById('attachIcon');
  if (!strip) return;

  strip.style.display = '';
  if (nameEl) nameEl.textContent = name;
  if (statEl) {
    statEl.textContent = status;
    statEl.className = 'attach-preview-status' + (ready ? ' ready' : '');
  }
  if (iconEl) {
    const isPdf = name.toLowerCase().endsWith('.pdf');
    iconEl.innerHTML = isPdf
      ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`
      : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`;
  }
}

function _attachError(msg) {
  const statEl = document.getElementById('attachStatus');
  if (statEl) { statEl.textContent = msg; statEl.className = 'attach-preview-status error'; }
  _attachment = null;
}

function removeAttachment() {
  _attachment = null;
  const strip = document.getElementById('attachPreview');
  if (strip) strip.style.display = 'none';
}

// Helper: show a notification toast if showNotif exists, else fall back to console
function showNotif(msg, isError = false) {
  const fn = window.showToast || ((m) => console.warn(m));
  fn(msg, isError);
}

function clearMessages(showWelcome = true) {
  chatInner().querySelectorAll('.msg-wrap, .typing-wrap, .date-sep').forEach(el => el.remove());
  if (showWelcome && welcomeScr()) welcomeScr().style.display = '';
}

function scrollBottom() {
  const area = chatArea();
  area.scrollTo({ top: area.scrollHeight, behavior: 'smooth' });
}

function now() {
  return new Date().toLocaleTimeString('pt-PT', { hour: '2-digit', minute: '2-digit' });
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── Nav actions ──────────────────────────────────────────────────────────────

function newChat() {
  document.querySelectorAll('.history-item').forEach(el => el.classList.remove('active'));
  state.sessionId    = null;
  state.lastQuestion = null;
  clearMessages(true);
  clearInput();
}

function clearChat() {
  clearMessages(true);
  state.sessionId    = null;
  state.lastQuestion = null;
  clearInput();
}

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  const isOpen  = sidebar.classList.toggle('open');
  if (overlay) overlay.classList.toggle('visible', isOpen);
}

function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  window.location.href = 'index.html';
}

// ─── Search modal ─────────────────────────────────────────────────────────────

let _searchTimer = null;

function openSearch() {
  const modal = document.getElementById('searchModal');
  if (!modal) return;
  modal.style.display = 'flex';
  setTimeout(() => document.getElementById('searchModalInput')?.focus(), 50);
}

function closeSearch(e) {
  if (e && e.target !== document.getElementById('searchModal')) return;
  const modal = document.getElementById('searchModal');
  if (modal) modal.style.display = 'none';
}

function runSearch(q) {
  clearTimeout(_searchTimer);
  const results = document.getElementById('searchResults');
  if (!q.trim()) {
    results.innerHTML = '<div class="search-empty">Escreve para pesquisar nas tuas conversas.</div>';
    return;
  }
  results.innerHTML = '<div class="search-empty">A pesquisar...</div>';
  _searchTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/chat/search?q=${encodeURIComponent(q)}`, { headers: authHeaders() });
      const items = await res.json();
      if (!items.length) {
        results.innerHTML = '<div class="search-empty">Sem resultados para "' + escHtml(q) + '".</div>';
        return;
      }
      const hl = (text, term) => {
        const re = new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        return escHtml(text).replace(re, '<span class="search-highlight">$1</span>');
      };
      results.innerHTML = items.map(r => {
        const s = state.allSessions.find(x => x.id === r.session_id);
        const title = s?.title || 'Conversa';
        return `<div class="search-result-item" onclick="goToSearchResult(${r.session_id}, ${r.message_id})">
          <div class="search-result-title">${escHtml(title)}</div>
          <div class="search-result-snippet">${hl(r.content, q)}</div>
          <div class="search-result-meta">${new Date(r.created_at).toLocaleDateString('pt-PT')}</div>
        </div>`;
      }).join('');
    } catch {
      results.innerHTML = '<div class="search-empty" style="color:#ef4444">Erro ao pesquisar.</div>';
    }
  }, 300);
}

async function goToSearchResult(sessionId, messageId) {
  closeSearch({ target: document.getElementById('searchModal') });
  // Load the session
  const s = state.allSessions.find(x => x.id === sessionId);
  if (!s) return;
  const item = document.querySelector(`.history-item[data-id="${sessionId}"]`);
  if (item) {
    await loadSession(sessionId, s.course_id, item);
  } else {
    // Session not in visible list — switch to chats tab and reload
    switchTab('chats');
    await loadSessions();
    const fresh = document.querySelector(`.history-item[data-id="${sessionId}"]`);
    if (fresh) await loadSession(sessionId, s.course_id, fresh);
  }
  // Scroll to the specific message if visible
  setTimeout(() => {
    const el = document.getElementById('bm-' + messageId);
    if (el) el.closest('.msg-wrap')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, 400);
}

// ─── Session summary ───────────────────────────────────────────────────────────

async function summariseSession() {
  if (!state.sessionId) return;
  const modal = document.getElementById('summaryModal');
  const content = document.getElementById('summaryContent');
  if (!modal || !content) return;
  modal.style.display = 'flex';
  content.innerHTML = '<div style="text-align:center;padding:20px 0;color:var(--text-3)">A gerar resumo...</div>';
  try {
    const res = await fetch(`/api/chat/sessions/${state.sessionId}/summary`, {
      method: 'POST', headers: authHeaders(),
    });
    const data = await res.json();
    if (!res.ok) { content.innerHTML = `<div style="color:#ef4444">${escHtml(data.detail || 'Erro.')}</div>`; return; }
    content.innerHTML = formatText(data.summary || 'Sem resumo disponível.');
  } catch {
    content.innerHTML = '<div style="color:#ef4444">Erro ao gerar resumo.</div>';
  }
}

function closeSummary(e) {
  if (e && e.target !== document.getElementById('summaryModal')) return;
  const modal = document.getElementById('summaryModal');
  if (modal) modal.style.display = 'none';
}

// ─── Dark mode ────────────────────────────────────────────────────────────────

function applyDarkMode(on) {
  document.body.classList.toggle('dark', on);
  document.documentElement.setAttribute('data-dark', on ? '1' : '0');
  const toggle = document.getElementById('darkToggle');
  if (toggle) toggle.checked = on;
  localStorage.setItem('darkMode', on ? '1' : '0');
}

function setDarkMode(on) {
  applyDarkMode(on);
  savePreferences();
}

// ─── Font size ────────────────────────────────────────────────────────────────

function applyFontSize(size) {
  document.body.classList.remove('font-sm', 'font-lg');
  if (size === 'sm') document.body.classList.add('font-sm');
  if (size === 'lg') document.body.classList.add('font-lg');
  const sel = document.getElementById('fontSizeSelect');
  if (sel) sel.value = size;
  localStorage.setItem('fontSize', size);
}

function setFontSize(size) {
  applyFontSize(size);
  savePreferences();
}

// ─── Language toggle ──────────────────────────────────────────────────────────

function applyLang(lang) {
  state.lang = lang;
  const lbl = document.getElementById('langLabel');
  if (lbl) lbl.textContent = lang === 'en' ? 'EN' : 'PT';
  const btn = document.getElementById('langBtn');
  if (btn) btn.classList.toggle('active', lang === 'en');
  localStorage.setItem('answerLang', lang);
}

function toggleLang() {
  applyLang(state.lang === 'pt' ? 'en' : 'pt');
  savePreferences();
}

// ─── Settings panel ───────────────────────────────────────────────────────────

function toggleSettings(e) {
  e.stopPropagation();
  const panel = document.getElementById('settingsPanel');
  if (panel) panel.classList.toggle('open');
}

function closeSettings() {
  const panel = document.getElementById('settingsPanel');
  if (panel) panel.classList.remove('open');
}

// ─── More-actions dropdown ─────────────────────────────────────────────────────

function toggleMore(e) {
  e.stopPropagation();
  const panel = document.getElementById('morePanel');
  if (panel) panel.classList.toggle('open');
}

function closeMore() {
  const panel = document.getElementById('morePanel');
  if (panel) panel.classList.remove('open');
}

// ─── Preferences (API sync) ───────────────────────────────────────────────────

async function loadPreferences() {
  try {
    const res = await fetch('/api/chat/preferences', { headers: authHeaders() });
    if (!res.ok) return;
    const p = await res.json();
    if (p.theme)     applyDarkMode(p.theme === 'dark');
    if (p.font_size) {
      const sizeMap = { medium: 'md', small: 'sm', large: 'lg' };
      applyFontSize(sizeMap[p.font_size] || p.font_size);
    }
    if (p.language)  applyLang(p.language);
  } catch {}
}

async function savePreferences() {
  try {
    await fetch('/api/chat/preferences', {
      method:  'PUT',
      headers: authHeaders(),
      body:    JSON.stringify({
        theme:     document.body.classList.contains('dark') ? 'dark' : 'light',
        font_size: localStorage.getItem('fontSize') || 'md',
        language:  state.lang,
      }),
    });
  } catch {}
}

// ─── Session search ───────────────────────────────────────────────────────────

function filterSessions(query) {
  const q = query.toLowerCase().trim();
  if (!q) { renderSessions(state.allSessions); return; }
  const filtered = state.allSessions.filter(s =>
    (s.title || '').toLowerCase().includes(q)
  );
  renderSessions(filtered);
}

// ─── Sidebar tabs ─────────────────────────────────────────────────────────────

function switchTab(tab) {
  state.tab = tab;
  document.getElementById('tabChats').classList.toggle('active', tab === 'chats');
  document.getElementById('tabBookmarks').classList.toggle('active', tab === 'bookmarks');
  document.getElementById('historyList').style.display    = tab === 'chats'     ? '' : 'none';
  document.getElementById('bookmarkList').style.display  = tab === 'bookmarks'  ? '' : 'none';
  document.getElementById('searchWrap').style.display    = tab === 'chats' && state.allSessions.length > 3 ? '' : 'none';
  if (tab === 'bookmarks') loadBookmarks();
}

// ─── Bookmarks ────────────────────────────────────────────────────────────────

async function toggleBookmark(messageId, text, btn) {
  const isBookmarked = btn.classList.contains('bookmarked');
  if (isBookmarked) {
    try {
      const list = await fetch('/api/chat/bookmarks', { headers: authHeaders() }).then(r => r.json());
      const bm = list.find(b => b.message_id === messageId);
      if (bm) {
        await fetch(`/api/chat/bookmarks/${bm.id}`, { method: 'DELETE', headers: authHeaders() });
        btn.classList.remove('bookmarked');
        btn.title = 'Guardar resposta';
        btn.querySelector('svg').setAttribute('fill', 'none');
        showChatToast('Guardado removido');
      }
    } catch {}
  } else {
    try {
      await fetch('/api/chat/bookmarks', {
        method:  'POST',
        headers: authHeaders(),
        body:    JSON.stringify({ message_id: messageId }),
      });
      btn.classList.add('bookmarked');
      btn.title = 'Guardado';
      btn.querySelector('svg').setAttribute('fill', 'currentColor');
      showChatToast('Resposta guardada em Guardados');
    } catch {}
  }
}

function showChatToast(msg, type = 'success') {
  let t = document.getElementById('chatToast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'chatToast';
    t.style.cssText = 'position:fixed;bottom:88px;left:50%;transform:translateX(-50%);background:#1e293b;color:#f1f5f9;padding:8px 18px;border-radius:20px;font-size:12.5px;z-index:8000;opacity:0;transition:opacity .2s;white-space:nowrap;pointer-events:none';
    document.body.appendChild(t);
  }
  if (type === 'error') t.style.background = '#7f1d1d';
  else t.style.background = '#1e293b';
  t.textContent = msg;
  t.style.opacity = '1';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.style.opacity = '0'; }, 2000);
}

async function loadBookmarks() {
  const list = document.getElementById('bookmarkList');
  if (!list) return;
  list.innerHTML = '<div style="padding:12px 14px;font-size:12px;color:rgba(255,255,255,0.3)">A carregar...</div>';
  try {
    const bms = await fetch('/api/chat/bookmarks', { headers: authHeaders() }).then(r => r.json());
    if (!bms.length) {
      list.innerHTML = '<div style="padding:14px;font-size:12px;color:rgba(255,255,255,0.3);text-align:center">Sem guardados ainda.<br>Usa o ícone 🔖 nas respostas.</div>';
      return;
    }
    list.innerHTML = bms.map(b => `
      <div class="bookmark-item" onclick="jumpToBookmark(${b.message_id})">
        <div class="bookmark-item-text">${escHtml(b.content || '')}</div>
        <div class="bookmark-item-meta">
          <span>${escHtml(b.session_title || '')}</span>
          <button class="bookmark-del" onclick="event.stopPropagation();deleteBookmark(${b.id},this)" title="Remover">✕</button>
        </div>
      </div>`).join('');
  } catch {
    list.innerHTML = '<div style="padding:14px;font-size:12px;color:#ef4444">Erro ao carregar.</div>';
  }
}

async function deleteBookmark(id, btn) {
  await fetch(`/api/chat/bookmarks/${id}`, { method: 'DELETE', headers: authHeaders() });
  btn.closest('.bookmark-item').remove();
}

function jumpToBookmark(messageId) {
  // Try to scroll to the message if visible, otherwise just close sidebar
  const el = document.getElementById('bm-' + messageId);
  if (el) el.closest('.msg-wrap')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (window.innerWidth < 768) toggleSidebar();
}

// ─── Export session ───────────────────────────────────────────────────────────

async function exportSession() {
  if (!state.sessionId) return;
  try {
    const res = await fetch(`/api/chat/sessions/${state.sessionId}/export`, { headers: authHeaders() });
    if (!res.ok) return;
    const text = await res.text();
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `conversa-${state.sessionId}.txt`;
    a.click();
  } catch {}
}

// ─── Onboarding ───────────────────────────────────────────────────────────────

function closeOnboarding(e) {
  if (e && e.target !== document.getElementById('onboardingOverlay')) return;
  const el = document.getElementById('onboardingOverlay');
  if (el) el.style.display = 'none';
  localStorage.setItem('onboarded', '1');
}
