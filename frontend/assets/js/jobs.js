/**
 * Background job polling — survives page navigation.
 *
 * When the admin starts an LLM organize job it stores a record here.
 * Every page that includes this script will poll pending jobs and show
 * a notification banner when one finishes.
 */

const JOBS_KEY    = 'isla_pending_jobs';
const RESULTS_KEY = 'isla_job_results';
const POLL_MS     = 3000;
const API         = '/api';

// ── Storage helpers ────────────────────────────────────────────────────────────

function _getPending() {
  try { return JSON.parse(localStorage.getItem(JOBS_KEY) || '[]'); } catch { return []; }
}
function _savePending(jobs) {
  localStorage.setItem(JOBS_KEY, JSON.stringify(jobs));
}
function _getResults() {
  try { return JSON.parse(localStorage.getItem(RESULTS_KEY) || '{}'); } catch { return {}; }
}
function _saveResults(map) {
  localStorage.setItem(RESULTS_KEY, JSON.stringify(map));
}

/** Register a new pending job (called from admin.html after /jobs/organize). */
function jobsAdd(job_id, meta) {
  const jobs = _getPending();
  jobs.push({ job_id, ...meta, started: Date.now() });
  _savePending(jobs);
}

/** Remove a completed/errored job from pending list. */
function jobsRemove(job_id) {
  _savePending(_getPending().filter(j => j.job_id !== job_id));
}

/** Store the result so admin.html can retrieve it on return. */
function jobsSaveResult(job_id, result) {
  const map = _getResults();
  map[job_id] = result;
  _saveResults(map);
}

/** Retrieve and consume a stored result (one-time read). */
function jobsConsumeResult(job_id) {
  const map = _getResults();
  const val = map[job_id] ?? null;
  if (val !== null) { delete map[job_id]; _saveResults(map); }
  return val;
}

// ── Notification banner ────────────────────────────────────────────────────────

function _showNotification(meta, isError = false) {
  document.getElementById('job-notif-banner')?.remove();

  const banner  = document.createElement('div');
  banner.id     = 'job-notif-banner';
  banner.className = `job-notif-banner ${isError ? 'job-notif-error' : 'job-notif-success'}`;

  const icon    = isError ? '✕' : '✓';
  const label   = meta.filename || meta.doc_label || 'Documento';
  const msg     = isError
    ? `Erro ao organizar <strong>${label}</strong>`
    : `<strong>${label}</strong> foi organizado pela IA`;
  const link    = isError ? '' : `<a class="job-notif-action" href="admin.html?job=${meta.job_id}">Ver resultado</a>`;

  banner.innerHTML = `
    <span class="job-notif-icon">${icon}</span>
    <span class="job-notif-msg">${msg}</span>
    ${link}
    <button class="job-notif-close" onclick="document.getElementById('job-notif-banner').remove()">×</button>
  `;

  document.body.appendChild(banner);
  // Auto-dismiss after 12 s (errors stay longer)
  setTimeout(() => banner?.remove(), isError ? 20000 : 12000);
}

// ── Polling ────────────────────────────────────────────────────────────────────

let _polling = false;

async function _pollOnce() {
  const token = localStorage.getItem('token');
  if (!token) return;

  const pending = _getPending();
  if (!pending.length) { _polling = false; return; }

  for (const meta of pending) {
    try {
      const res  = await fetch(`${API}/admin/jobs/${meta.job_id}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) { jobsRemove(meta.job_id); continue; }
      const data = await res.json();

      if (data.status === 'done') {
        jobsRemove(meta.job_id);
        jobsSaveResult(meta.job_id, { ...data, ...meta });
        _showNotification({ ...meta, job_id: meta.job_id });
        // If admin page is already open and listening, dispatch an event
        window.dispatchEvent(new CustomEvent('isla-job-done', { detail: { ...data, ...meta } }));
      } else if (data.status === 'error') {
        jobsRemove(meta.job_id);
        _showNotification(meta, true);
      }
      // 'pending' → keep polling
    } catch { /* network error — try again next tick */ }
  }

  if (_getPending().length) {
    setTimeout(_pollOnce, POLL_MS);
  } else {
    _polling = false;
  }
}

/** Start polling if not already running. Safe to call multiple times. */
function jobsStartPolling() {
  if (_polling) return;
  _polling = true;
  setTimeout(_pollOnce, POLL_MS);
}

// ── Auto-start on load ────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  if (_getPending().length) jobsStartPolling();
});
