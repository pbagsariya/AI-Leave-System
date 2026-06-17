/* ============================================================================
 * Leave Assistant — Phase 2 frontend
 * Interactive chat state wired against the frozen API contract.
 * Flip USE_MOCK to false in Phase 3 to hit the real FastAPI backend; no other
 * change needed (mockApi and realApi expose the same 5 methods).
 * ==========================================================================*/

const USE_MOCK = false;

// Pinned "today" so the demo is repeatable (matches the design doc examples).
const TODAY = new Date('2026-06-13T00:00:00'); // a Saturday

// ---- presentation metadata -------------------------------------------------
const BAL_META = {
  SICK:     { label: 'Sick',     cap: 10, color: 'bg-brand-500'  },
  CASUAL:   { label: 'Casual',   cap: 8,  color: 'bg-sky-500'    },
  EARNED:   { label: 'Earned',   cap: 15, color: 'bg-violet-500' },
  COMP_OFF: { label: 'Comp-off', cap: 5,  color: 'bg-amber-500'  },
};
const CODE_LABEL = { SICK:'Sick Leave', CASUAL:'Casual Leave', EARNED:'Earned Leave',
                     COMP_OFF:'Comp-off', WFH:'Work From Home', LOP:'Loss of Pay' };
const STATUS_BADGE = {
  Pending:   'bg-amber-100 text-amber-700',
  Approved:  'bg-emerald-100 text-emerald-700',
  Rejected:  'bg-rose-100 text-rose-700',
  Cancelled: 'bg-slate-100 text-slate-500',
};

/* ============================================================================
 * MOCK BACKEND — in-memory store + the 5 endpoints (same shapes as Phase 3).
 * ==========================================================================*/
const mockDB = {
  employees: [
    { id: 'e1', name: 'Asha Menon',       dept: 'Engineering', role: 'Manager' },
    { id: 'e2', name: 'Ravi Kapoor',      dept: 'Sales',       role: 'Employee' },
    { id: 'e3', name: 'Meera Iyer',       dept: 'Design',      role: 'Employee' },
    { id: 'e4', name: 'Prakash Bagsariya',dept: 'Developer',   role: 'Employee' },
    { id: 'e5', name: 'Krupal Tasare',    dept: 'Engineer',    role: 'Employee' },
  ],
  balances: {
    e1: { SICK: 8, CASUAL: 5, EARNED: 12, COMP_OFF: 2 },
    e2: { SICK: 6, CASUAL: 7, EARNED: 9,  COMP_OFF: 1 },
    e3: { SICK: 10, CASUAL: 4, EARNED: 14, COMP_OFF: 3 },
    e4: { SICK: 8, CASUAL: 6, EARNED: 12, COMP_OFF: 2 },
    e5: { SICK: 9, CASUAL: 5, EARNED: 11, COMP_OFF: 3 },
  },
  history: {
    e1: [
      { id: '#AB-10391', code:'SICK',   label:'Sick · 02 Jun',  status:'Approved' },
      { id: '#AB-10355', code:'WFH',    label:'WFH · 28 May',   status:'Approved' },
      { id: '#AB-10310', code:'CASUAL', label:'Casual · 19 May',status:'Cancelled' },
    ],
    e2: [ { id: '#AB-10288', code:'EARNED', label:'Earned · 10 May', status:'Approved' } ],
    e3: [], e4: [], e5: [],
  },
  drafts: {}, // session_id -> parsed draft
  current: null, // logged-in employee id (mock)
  creds: {
    asha: { pw: 'asha123', id: 'e1' }, ravi: { pw: 'ravi123', id: 'e2' },
    meera: { pw: 'meera123', id: 'e3' }, prakash: { pw: 'prakash123', id: 'e4' },
    krupal: { pw: 'krupal123', id: 'e5' },
  },
  resets: {}, // reset token -> employee id (mock)
};

const DOC_REQUIRED_OVER = { SICK: 2 };

function fmtDate(d) {
  return d.toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' });
}
function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate()+n); return x; }
function newRequestId() { return '#AB-' + (10400 + Math.floor(Math.random()*599)); }

// crude NL parser — stand-in for the Claude extraction (Phase 3 replaces this).
function mockParse(message) {
  const t = message.toLowerCase();
  let code = null;
  if (/\bwfh\b|work from home/.test(t)) code = 'WFH';
  else if (/sick|fever|unwell|\bill\b|medical/.test(t)) code = 'SICK';
  else if (/casual/.test(t)) code = 'CASUAL';
  else if (/earned|privilege|vacation|family function|wedding/.test(t)) code = 'EARNED';
  else if (/comp[- ]?off|comp\b/.test(t)) code = 'COMP_OFF';

  const WD = ['sunday','monday','tuesday','wednesday','thursday','friday','saturday'];
  let start = null;
  if (/today/.test(t)) start = new Date(TODAY);
  else if (/tomorrow/.test(t)) start = addDays(TODAY, 1);
  else {
    for (let i=0;i<WD.length;i++) if (t.includes(WD[i])) {
      let ahead = (i - TODAY.getDay() + 7) % 7; ahead = ahead || 7;
      start = addDays(TODAY, ahead); break;
    }
  }

  const half = /half[- ]?day/.test(t);
  let duration = half ? 0.5 : null;
  const m = t.match(/(\d+(?:\.\d+)?)\s*day/);
  if (m) duration = parseFloat(m[1]);
  else if (duration === null && start) duration = 1;

  const hasAttachment = /attach|note|certificate|\.pdf|📎/.test(t);

  const missing = [];
  if (!start) missing.push('start_date');
  if (!code)  missing.push('absence_code');
  if (duration === null) missing.push('duration_days');

  return { code, start, duration, half, hasAttachment, missing };
}

const mockApi = {
  async me() {
    const id = mockDB.current;
    return id ? mockDB.employees.find(e => e.id === id) : null;
  },
  async login({ username, password }) {
    const c = mockDB.creds[(username || '').trim().toLowerCase()];
    if (c && c.pw === password) { mockDB.current = c.id; return mockDB.employees.find(e => e.id === c.id); }
    return { error: 'Invalid username or password' };
  },
  async logout() { mockDB.current = null; },
  async forgotPassword({ identifier }) {
    const id = (identifier || '').trim().toLowerCase();
    for (const [u, c] of Object.entries(mockDB.creds)) {
      const emp = mockDB.employees.find(e => e.id === c.id);
      if (u === id || (emp && (emp.email || '').toLowerCase() === id)) {
        const token = 'tok' + Math.random().toString(16).slice(2, 12);
        mockDB.resets[token] = c.id;
        console.log('[mock] reset link: ' + location.pathname + '?reset=' + token);
        break;
      }
    }
    return { ok: true };   // always succeed (don't reveal existence)
  },
  async resetPassword({ token, password, confirm_password }) {
    const id = mockDB.resets[token];
    if (!id) return { error: 'This reset link is invalid or has expired.' };
    if (!password || password.length < 4) return { error: 'Password must be at least 4 characters' };
    if (password !== confirm_password) return { error: 'Passwords do not match' };
    for (const c of Object.values(mockDB.creds)) if (c.id === id) c.pw = password;
    delete mockDB.resets[token];
    return { ok: true };
  },
  async signup({ name, dept, email, username, password, confirm_password, role }) {
    const u = (username || '').trim().toLowerCase();
    if (!name || !email || !dept || !u || !password) return { error: 'All fields are required' };
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return { error: 'Please enter a valid email address' };
    if (u.includes(' ')) return { error: 'Username cannot contain spaces' };
    if (u.length < 3) return { error: 'Username must be at least 3 characters' };
    if (password.length < 4) return { error: 'Password must be at least 4 characters' };
    if (password !== confirm_password) return { error: 'Passwords do not match' };
    if (mockDB.creds[u]) return { error: 'That username is already taken' };
    const id = 'u' + Math.random().toString(16).slice(2, 10);
    const emp = { id, name: name.trim(), dept: (dept || '—').trim(), email: (email || '').trim(), role: role === 'Manager' ? 'Manager' : 'Employee' };
    mockDB.employees.push(emp);
    mockDB.balances[id] = { SICK: 10, CASUAL: 8, EARNED: 15, COMP_OFF: 4 };
    mockDB.history[id] = [];
    mockDB.creds[u] = { pw: password, id };
    mockDB.current = id;
    return emp;
  },

  async balances() { return mockDB.balances[mockDB.current]; },
  async history() { return mockDB.history[mockDB.current] || []; },
  async approvals() {
    const out = [];
    for (const [eid, items] of Object.entries(mockDB.history)) {
      if (eid === mockDB.current) continue;
      const emp = mockDB.employees.find(e => e.id === eid);
      for (const h of items) if (h.status === 'Pending')
        out.push({ id: h.id, employee_id: eid, employee_name: emp ? emp.name : eid,
                   dept: emp ? emp.dept : '', code: h.code, label: h.label, duration_days: h.duration });
    }
    return out;
  },
  _find(id) { for (const items of Object.values(mockDB.history)) { const h = items.find(x => x.id === id); if (h) return h; } return null; },
  async approve({ request_id, comment }) {
    const h = this._find(request_id);
    if (h && h.status === 'Pending') { h.status = 'Approved'; h.decision_comment = comment || ''; }
    return { ok: true };
  },
  async reject({ request_id, comment }) {
    for (const [eid, items] of Object.entries(mockDB.history)) {
      const h = items.find(x => x.id === request_id);
      if (h && h.status === 'Pending') {
        h.status = 'Rejected'; h.decision_comment = comment || '';
        if (mockDB.balances[eid] && mockDB.balances[eid][h.code] !== undefined && h.duration) mockDB.balances[eid][h.code] += h.duration;
        return { ok: true };
      }
    }
    return { error: 'Request is no longer pending' };
  },

  async chat({ message, has_attachment }) {
    await delay(550);
    const employee_id = mockDB.current;
    const t = message.toLowerCase();
    const bal = mockDB.balances[employee_id];

    // intent: check_balance
    if (/balance|how many.*(leave|day)|leaves? (do i|left)|remaining/.test(t)) {
      return { reply_type: 'balance', balances: bal };
    }
    // intent: view_history (with optional status filter)
    const st = (t.match(/approved|pending|cancelled/) || [])[0];
    if (/history|past (leave|request)|previous|recent request/.test(t) || (st && /leave|request/.test(t))) {
      let items = mockDB.history[employee_id] || [];
      if (st) items = items.filter(h => h.status.toLowerCase() === st);
      return { reply_type: 'history', history: items };
    }
    // intent: cancel_leave
    if (/cancel/.test(t)) {
      const m = t.match(/#?\s*ab[-\s]?(\d+)/i);
      if (!m) return { reply_type: 'clarification',
        question: 'Sure — which request should I cancel? Give me the ID (e.g. #AB-10391).' };
      const id = '#AB-' + m[1];
      const req = (mockDB.history[employee_id] || []).find(h => h.id === id);
      if (!req) return { reply_type: 'policy_block', message: `I couldn't find request ${id} under your name.` };
      if (req.status !== 'Pending') return { reply_type: 'policy_block',
        message: `${id} is ${req.status} — only pending requests can be cancelled here.` };
      req.status = 'Cancelled';
      let restored = '';
      if (bal[req.code] !== undefined && req.duration) {
        bal[req.code] += req.duration;
        restored = ` ${req.duration} day(s) returned to your ${BAL_META[req.code]?.label || req.code} balance.`;
      }
      return { reply_type: 'cancelled', message: `Done — ${id} has been cancelled.${restored}`, refresh: true };
    }

    // intent: apply_leave
    const p = mockParse(message);
    if (has_attachment) p.hasAttachment = true;
    if (p.missing.length) {
      let q = 'Could you give me a bit more detail?';
      if (p.missing.includes('absence_code') && p.missing.includes('start_date'))
        q = 'Which day(s) do you need off, and what type of leave (sick, casual, earned)?';
      else if (p.missing.includes('absence_code')) q = 'What type of leave is this — sick, casual, or earned?';
      else if (p.missing.includes('start_date'))   q = 'Which day(s) should the leave start?';
      else if (p.missing.includes('duration_days')) q = 'How many days do you need?';
      return { reply_type: 'clarification', question: q };
    }

    const end = p.duration > 1 ? addDays(p.start, Math.round(p.duration) - 1) : p.start;

    // policy: document required
    if (p.code in DOC_REQUIRED_OVER && p.duration > DOC_REQUIRED_OVER[p.code] && !p.hasAttachment) {
      return { reply_type: 'policy_block',
        message: `Sick leave over ${DOC_REQUIRED_OVER[p.code]} days needs a medical certificate. Please attach one to proceed.` };
    }
    // policy: insufficient balance (WFH/LOP not balance-tracked)
    if (bal[p.code] !== undefined && p.duration > bal[p.code]) {
      return { reply_type: 'policy_block',
        message: `You only have ${bal[p.code]} day(s) of ${BAL_META[p.code]?.label || p.code} left, but this request is for ${p.duration}.` };
    }

    const session_id = 's' + Date.now();
    const balanceAfter = bal[p.code] !== undefined ? bal[p.code] - p.duration : null;
    const card = {
      code: p.code,
      label: CODE_LABEL[p.code] || p.code,
      start: fmtDate(p.start),
      end: fmtDate(end),
      sameDay: +p.start === +end,
      duration: p.duration,
      comment: deriveComment(message),
      attachment: p.hasAttachment ? 'document.pdf' : null,
      balanceAfter,
    };
    mockDB.drafts[session_id] = { employee_id, code: p.code, duration: p.duration,
                                   start: p.start, end, label: card.label };
    return { reply_type: 'confirmation', session_id, card };
  },

  async confirm({ session_id }) {
    await delay(500);
    const d = mockDB.drafts[session_id];
    if (!d) return { error: 'draft expired' };
    if (mockDB.balances[d.employee_id][d.code] !== undefined)
      mockDB.balances[d.employee_id][d.code] -= d.duration;
    const id = newRequestId();
    const range = (+d.start === +d.end)
      ? fmtShort(d.start) : `${fmtShort(d.start)}–${fmtShort(d.end)}`;
    mockDB.history[d.employee_id].unshift(
      { id, code: d.code, label: `${BAL_META[d.code]?.label || d.label} · ${range}`, status: 'Pending', duration: d.duration });
    delete mockDB.drafts[session_id];
    return { request_id: id, status: 'Pending', balances: mockDB.balances[d.employee_id] };
  },
};

function deriveComment(msg) {
  // keep it short; the real model summarises this
  return msg.length > 60 ? msg.slice(0, 57) + '…' : msg;
}
function fmtShort(d) { return d.toLocaleDateString('en-GB', { day:'2-digit', month:'short' }); }
function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

/* ============================================================================
 * REAL BACKEND — same interface, used when USE_MOCK = false (Phase 3).
 * ==========================================================================*/
async function postJSON(url, body) {
  return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}) });
}

const realApi = {
  async me() { const r = await fetch('/api/me'); return r.ok ? r.json() : null; },
  async login(body) {
    const r = await postJSON('/api/login', body);
    if (r.ok) return r.json();
    const d = await r.json().catch(() => ({}));
    return { error: d.detail || 'Invalid username or password' };
  },
  async logout() { await postJSON('/api/logout'); },
  async signup(body) {
    const r = await postJSON('/api/signup', body);
    if (r.ok) return r.json();
    const d = await r.json().catch(() => ({}));
    return { error: d.detail || 'Could not create account' };
  },
  async forgotPassword(body) { return (await postJSON('/api/forgot-password', body)).json(); },
  async resetPassword(body) {
    const r = await postJSON('/api/reset-password', body);
    if (r.ok) return r.json();
    const d = await r.json().catch(() => ({}));
    return { error: d.detail || 'Could not reset password' };
  },

  async balances() { return (await fetch('/api/balances')).json(); },
  async history() { return (await fetch('/api/history')).json(); },
  async chat(body) { return (await postJSON('/api/chat', body)).json(); },
  async confirm(body) { return (await postJSON('/api/confirm', body)).json(); },
  async approvals() { return (await fetch('/api/approvals')).json(); },
  async approve(body) { return (await postJSON('/api/approve', body)).json(); },
  async reject(body) { return (await postJSON('/api/reject', body)).json(); },
};

const api = USE_MOCK ? mockApi : realApi;

/* ============================================================================
 * UI STATE + RENDERING
 * ==========================================================================*/
const state = { user: null, attached: false };

const $ = (s) => document.querySelector(s);
const messagesEl = $('#messages');

function initials(name) { return name.split(' ').map(w => w[0]).join('').slice(0,2).toUpperCase(); }
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function scrollDown() { messagesEl.scrollTop = messagesEl.scrollHeight; }

function bubbleUser(text) {
  const row = document.createElement('div');
  row.className = 'flex justify-end';
  row.innerHTML = `<div class="bubble-user">${esc(text)}</div>`;
  messagesEl.appendChild(row); scrollDown();
}
function botRow(innerHTML, avatar = 'L', avatarBg = 'bg-brand-500') {
  const row = document.createElement('div');
  row.className = 'flex gap-2.5';
  row.innerHTML = `<div class="w-7 h-7 rounded-full ${avatarBg} text-white grid place-items-center text-[11px] font-bold shrink-0">${avatar}</div>${innerHTML}`;
  messagesEl.appendChild(row); scrollDown();
  return row;
}
function bubbleBot(text) { botRow(`<div class="bubble-bot">${text}</div>`); }

function typing() {
  const row = botRow(`<div class="bubble-bot py-3"><span class="dot-typing"></span></div>`);
  return row;
}

function renderBalanceCard(bal) {
  const cells = Object.keys(BAL_META).map(code => `
    <div class="bg-white px-4 py-3">
      <p class="text-2xl font-bold">${bal[code] ?? 0}<span class="text-sm font-medium text-slate-400"> days</span></p>
      <p class="text-xs text-slate-500 mt-0.5">${BAL_META[code].label}</p>
    </div>`).join('');
  botRow(`<div class="w-full max-w-md">
    <div class="bubble-bot mb-2">Here's your current balance:</div>
    <div class="rounded-xl border border-slate-200 overflow-hidden">
      <div class="bg-brand-50 px-4 py-2.5 flex items-center justify-between">
        <span class="text-sm font-semibold text-brand-700">Leave balance</span>
        <span class="text-[11px] text-brand-600">as of ${fmtDate(TODAY)}</span>
      </div>
      <div class="grid grid-cols-2 gap-px bg-slate-100">${cells}</div>
      <div class="px-4 py-2.5 bg-slate-50 text-[11px] text-slate-500">Want to apply for one of these? Just tell me the dates.</div>
    </div></div>`);
}

function renderHistoryCard(items) {
  if (!items.length) { bubbleBot('You have no leave requests yet.'); return; }
  const rows = items.map(h => `
    <div class="px-4 py-2.5">
      <div class="flex items-center justify-between">
        <div><p class="text-sm font-medium">${esc(h.label)}</p><p class="text-[11px] text-slate-400">${esc(h.id)}</p></div>
        <span class="badge ${STATUS_BADGE[h.status]||'bg-slate-100 text-slate-500'}">${h.status}</span>
      </div>
      ${h.decision_comment ? `<p class="text-[11px] text-slate-500 italic mt-1">💬 ${esc(h.decision_comment)}</p>` : ''}
    </div>`).join('<div class="h-px bg-slate-100"></div>');
  botRow(`<div class="w-full max-w-md">
    <div class="bubble-bot mb-2">Here are your recent requests:</div>
    <div class="rounded-xl border border-slate-200 overflow-hidden bg-white">${rows}</div></div>`);
}

function renderConfirmation(card, sessionId) {
  const dates = card.sameDay
    ? `${card.start} (${card.duration} day)`
    : `${card.start} → ${card.end} (${card.duration} days)`;
  const balLine = card.balanceAfter !== null
    ? `<div class="flex justify-between py-1.5"><dt class="text-slate-500">${BAL_META[card.code]?.label||card.code} balance after</dt><dd class="font-medium">${card.balanceAfter} days</dd></div>` : '';
  const attLine = `<div class="flex justify-between py-1.5"><dt class="text-slate-500">Attachment</dt><dd class="font-medium">${card.attachment ? esc(card.attachment)+' ✓' : 'none'}</dd></div>`;
  botRow(`<div class="w-full max-w-md" data-card data-session="${sessionId}">
    <div class="bubble-bot mb-2">Here's what I'll submit — please confirm:</div>
    <div class="rounded-xl border border-slate-200 overflow-hidden">
      <div class="bg-brand-50 px-4 py-2.5 flex items-center justify-between">
        <span class="text-sm font-semibold text-brand-700">${esc(card.label)}</span>
        <span class="text-[11px] px-2 py-0.5 rounded-full bg-white text-brand-600 border border-brand-100">Draft</span>
      </div>
      <dl class="px-4 py-3 text-sm divide-y divide-slate-100">
        <div class="flex justify-between py-1.5"><dt class="text-slate-500">Dates</dt><dd class="font-medium text-right">${dates}</dd></div>
        <div class="flex justify-between py-1.5"><dt class="text-slate-500">Comment</dt><dd class="font-medium text-right max-w-[60%]">${esc(card.comment)}</dd></div>
        ${attLine}${balLine}
      </dl>
      <div class="px-4 py-3 bg-slate-50 flex gap-2">
        <button class="btn-primary" data-act="confirm">✓ Confirm</button>
        <button class="btn-ghost" data-act="edit">✏️ Edit</button>
        <button class="btn-ghost" data-act="cancel">✕ Cancel</button>
      </div>
    </div></div>`);
}

function renderReply(res) {
  switch (res.reply_type) {
    case 'balance':       renderBalanceCard(res.balances); break;
    case 'history':       renderHistoryCard(res.history); break;
    case 'clarification': botRow(`<div class="bubble-bot">${esc(res.question)}<span class="block mt-1 text-[11px] text-amber-600">⌖ I need a little more info</span></div>`); break;
    case 'policy_block':  botRow(`<div class="bubble-bot border border-rose-200 bg-rose-50 text-rose-700">${esc(res.message)}</div>`, '!', 'bg-rose-500'); break;
    case 'cancelled':     botRow(`<div class="bubble-bot border border-emerald-200 bg-emerald-50 text-emerald-700">${esc(res.message)}</div>`, '✓', 'bg-emerald-500'); break;
    case 'confirmation':  renderConfirmation(res.card, res.session_id); break;
    default:              botRow(`<div class="bubble-bot border border-rose-200 bg-rose-50 text-rose-600">Something went wrong. Please try again.</div>`, '⚠', 'bg-rose-500');
  }
}

// ---- side panel ------------------------------------------------------------
function renderBalances(bal) {
  $('#balances').innerHTML = Object.keys(BAL_META).map(code => {
    const m = BAL_META[code]; const days = bal[code] ?? 0;
    const w = Math.min(100, Math.round(days / m.cap * 100));
    return `<div class="balance-row"><span>${m.label}</span>
      <div class="bar"><span style="width:${w}%" class="${m.color}"></span></div><b>${days}</b></div>`;
  }).join('');
}
function renderHistoryPanel(items) {
  const el = $('#history');
  if (!items.length) { el.innerHTML = `<li class="px-5 py-4 text-sm text-slate-400">No requests yet.</li>`; return; }
  el.innerHTML = items.map(h => `
    <li class="px-5 py-3">
      <div class="flex items-center justify-between">
        <div><p class="text-sm font-medium">${esc(h.label)}</p><p class="text-[11px] text-slate-400">${esc(h.id)}</p></div>
        <span class="badge ${STATUS_BADGE[h.status]||'bg-slate-100 text-slate-500'}">${h.status}</span>
      </div>
      ${h.decision_comment ? `<p class="text-[11px] text-slate-500 italic mt-1">💬 ${esc(h.decision_comment)}</p>` : ''}
    </li>`).join('');
}
async function refreshPanels() {
  const [bal, hist] = await Promise.all([api.balances(), api.history()]);
  renderBalances(bal); renderHistoryPanel(hist);
}

function renderApprovals(items) {
  const el = $('#approvals');
  if (!items.length) { el.innerHTML = `<p class="px-5 py-4 text-sm text-slate-400">No pending requests right now.</p>`; return; }
  el.innerHTML = items.map(r => `
    <div class="px-5 py-3 border-b border-slate-100 last:border-0" data-row="${esc(r.id)}">
      <div class="flex items-center justify-between gap-2">
        <div>
          <p class="text-sm font-medium">${esc(r.employee_name)}</p>
          <p class="text-[11px] text-slate-400">${esc(r.label)} · ${esc(r.id)}</p>
        </div>
        <div class="flex gap-1.5 shrink-0">
          <button data-act="approve" class="text-xs font-semibold px-2.5 py-1 rounded-lg bg-emerald-500 text-white hover:bg-emerald-600">Approve</button>
          <button data-act="reject" class="text-xs font-semibold px-2.5 py-1 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50">Reject</button>
        </div>
      </div>
      <div class="cmt hidden mt-2">
        <textarea class="cmt-input w-full border border-slate-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-brand-500/30" rows="2" placeholder="Add a comment (optional)"></textarea>
        <div class="flex gap-2 mt-1.5">
          <button data-confirm class="cmt-confirm text-xs font-semibold px-3 py-1.5 rounded-lg text-white">Confirm</button>
          <button data-cancel class="text-xs font-medium px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50">Cancel</button>
        </div>
      </div>
    </div>`).join('');
}

async function refreshApprovals() {
  const items = await api.approvals();
  renderApprovals(Array.isArray(items) ? items : []);
}

/* ============================================================================
 * ACTIONS
 * ==========================================================================*/
async function send(text) {
  const msg = text.trim();
  if (!msg) return;
  bubbleUser(state.attached ? `${msg}  📎 document.pdf` : msg);
  $('#input').value = '';
  setAttached(false);
  const attached = state.attached;
  const t = typing();
  try {
    const res = await api.chat({ message: msg, session_id: null, has_attachment: attached });
    t.remove();
    renderReply(res);
    if (res.refresh) await refreshPanels();
  } catch (e) {
    t.remove();
    botRow(`<div class="bubble-bot border border-rose-200 bg-rose-50 text-rose-600">Couldn't reach the assistant. Please retry.</div>`, '⚠', 'bg-rose-500');
  }
}

async function confirmDraft(cardEl) {
  const sessionId = cardEl.dataset.session;
  cardEl.querySelectorAll('button').forEach(b => b.disabled = true);
  const res = await api.confirm({ session_id: sessionId });
  if (res.error) {
    botRow(`<div class="bubble-bot border border-rose-200 bg-rose-50 text-rose-600">${esc(res.error)}</div>`, '⚠', 'bg-rose-500');
    return;
  }
  botRow(`<div class="bubble-bot border border-emerald-200 bg-emerald-50">
    <span class="font-medium text-emerald-700">Submitted.</span> Request
    <span class="font-mono text-xs bg-white px-1.5 py-0.5 rounded border border-emerald-200">${esc(res.request_id)}</span>
    sent to your manager for approval.
    <span class="block mt-1 text-[11px] text-emerald-600">📧 A confirmation was emailed to you, and your manager was notified.</span></div>`, '✓', 'bg-emerald-500');
  await refreshPanels();
}

function setAttached(on) {
  state.attached = on;
  $('#attach').classList.toggle('!bg-brand-50', on);
  $('#attach').classList.toggle('!border-brand-300', on);
  $('#attachnote').textContent = on
    ? '📎 document.pdf attached — nothing is submitted until you confirm.'
    : 'ⓘ Nothing is submitted until you confirm.';
}

/* ============================================================================
 * BOOT
 * ==========================================================================*/
// ---- views -----------------------------------------------------------------
function hideAuthViews() {
  $('#login').classList.add('hidden');
  $('#signup').classList.add('hidden');
  $('#forgot').classList.add('hidden');
  $('#reset').classList.add('hidden');
}

function showForgot() {
  $('#app').classList.add('hidden');
  hideAuthViews();
  $('#forgot').classList.remove('hidden');
  $('#fpIdentifier').value = '';
  $('#forgotError').classList.add('hidden');
  $('#forgotMsg').classList.add('hidden');
  $('#fpIdentifier').focus();
}

function showReset(token) {
  state.resetToken = token;
  $('#app').classList.add('hidden');
  hideAuthViews();
  $('#reset').classList.remove('hidden');
  $('#rpPassword').value = '';
  $('#rpConfirm').value = '';
  $('#resetError').classList.add('hidden');
  $('#resetMsg').classList.add('hidden');
  $('#rpMatch').classList.add('hidden');
  $('#rpPassword').focus();
}

function showLogin() {
  state.user = null;
  $('#app').classList.add('hidden');
  hideAuthViews();
  $('#login').classList.remove('hidden');
  $('#username').value = '';
  $('#password').value = '';
  $('#loginError').classList.add('hidden');
  $('#username').focus();
}

function showSignup() {
  $('#app').classList.add('hidden');
  hideAuthViews();
  $('#signup').classList.remove('hidden');
  $('#signupError').classList.add('hidden');
  $('#suMatch').classList.add('hidden');
  $('#suName').focus();
}

async function showApp(user) {
  state.user = user;
  hideAuthViews();
  $('#app').classList.remove('hidden');
  $('#userName').textContent = user.name;
  $('#userDept').textContent = [user.dept, user.role].filter(Boolean).join(' · ');
  $('#avatar').textContent = initials(user.name);

  // A Manager only reviews others' requests — no chat/apply or personal balances.
  const mgr = user.role === 'Manager';
  $('#chatPanel').classList.toggle('hidden', mgr);
  $('#balancesCard').classList.toggle('hidden', mgr);
  $('#historyCard').classList.toggle('hidden', mgr);
  $('#approvalsCard').classList.toggle('hidden', !mgr);
  $('#main').classList.toggle('lg:grid-cols-[1fr_320px]', !mgr);
  $('#main').classList.toggle('lg:grid-cols-1', mgr);

  if (mgr) {
    await refreshApprovals();
  } else {
    messagesEl.innerHTML = '';
    bubbleBot(`Hi ${esc(user.name.split(' ')[0])} 👋 Tell me about the leave you'd like to take — or ask "what's my leave balance?"`);
    await refreshPanels();
  }
}

async function doLogin(ev) {
  ev.preventDefault();
  const btn = $('#loginBtn');
  const err = $('#loginError');
  err.classList.add('hidden');

  const username = $('#username').value.trim();
  const password = $('#password').value;
  let msg = '';
  if (!username) msg = 'Username is required';
  else if (!password) msg = 'Password is required';
  if (msg) { err.textContent = msg; err.classList.remove('hidden'); return; }

  btn.disabled = true; btn.textContent = 'Signing in…';
  try {
    const res = await api.login({ username, password });
    if (res && !res.error) { await showApp(res); }
    else { err.textContent = (res && res.error) || 'Invalid username or password'; err.classList.remove('hidden'); }
  } catch (e) {
    err.textContent = 'Could not sign in. Please try again.'; err.classList.remove('hidden');
  } finally {
    btn.disabled = false; btn.textContent = 'Sign in';
  }
}

function checkPwMatch() {
  const pw = $('#suPassword').value;
  const cpw = $('#suConfirm').value;
  const el = $('#suMatch');
  if (!cpw) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  if (pw === cpw) {
    el.textContent = '✓ Passwords match';
    el.className = 'text-[11px] mt-1 text-emerald-600';
  } else {
    el.textContent = '✗ Passwords do not match';
    el.className = 'text-[11px] mt-1 text-rose-600';
  }
}

async function doSignup(ev) {
  ev.preventDefault();
  const btn = $('#signupBtn');
  const err = $('#signupError');
  err.classList.add('hidden');
  const role = $('#suRole').value || 'Employee';
  const name = $('#suName').value.trim();
  const email = $('#suEmail').value.trim();
  const dept = $('#suDept').value.trim();
  const username = $('#suUsername').value.trim();
  const pw = $('#suPassword').value;
  const cpw = $('#suConfirm').value;

  // client-side validation (server re-checks authoritatively)
  let msg = '';
  if (!name) msg = 'Full name is required';
  else if (!email) msg = 'Email is required';
  else if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) msg = 'Please enter a valid email address';
  else if (!dept) msg = 'Department is required';
  else if (!username) msg = 'Username is required';
  else if (username.includes(' ')) msg = 'Username cannot contain spaces';
  else if (username.length < 3) msg = 'Username must be at least 3 characters';
  else if (!pw) msg = 'Password is required';
  else if (pw.length < 4) msg = 'Password must be at least 4 characters';
  else if (!cpw) msg = 'Please confirm your password';
  else if (pw !== cpw) msg = 'Passwords do not match';
  if (msg) { err.textContent = msg; err.classList.remove('hidden'); return; }

  const body = { name, dept, email, username, password: pw, confirm_password: cpw, role };
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const res = await api.signup(body);
    if (res && !res.error) { await showApp(res); }
    else { err.textContent = res.error || 'Could not create account'; err.classList.remove('hidden'); }
  } catch (e) {
    err.textContent = 'Could not create account. Please try again.'; err.classList.remove('hidden');
  } finally {
    btn.disabled = false; btn.textContent = 'Create account';
  }
}

async function doForgot(ev) {
  ev.preventDefault();
  const btn = $('#forgotBtn'), err = $('#forgotError'), msg = $('#forgotMsg');
  err.classList.add('hidden'); msg.classList.add('hidden');
  const identifier = $('#fpIdentifier').value.trim();
  if (!identifier) { err.textContent = 'Please enter your username or email'; err.classList.remove('hidden'); return; }
  btn.disabled = true; btn.textContent = 'Sending…';
  try {
    await api.forgotPassword({ identifier });
    msg.textContent = 'If an account matches, a password-reset link has been emailed to it.';
    msg.classList.remove('hidden');
  } catch (e) {
    err.textContent = 'Something went wrong. Please try again.'; err.classList.remove('hidden');
  } finally {
    btn.disabled = false; btn.textContent = 'Send reset link';
  }
}

function checkResetMatch() {
  const pw = $('#rpPassword').value, cpw = $('#rpConfirm').value, el = $('#rpMatch');
  if (!cpw) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  if (pw === cpw) { el.textContent = '✓ Passwords match'; el.className = 'text-[11px] mt-1 text-emerald-600'; }
  else { el.textContent = '✗ Passwords do not match'; el.className = 'text-[11px] mt-1 text-rose-600'; }
}

async function doReset(ev) {
  ev.preventDefault();
  const btn = $('#resetBtn'), err = $('#resetError'), msg = $('#resetMsg');
  err.classList.add('hidden'); msg.classList.add('hidden');
  const pw = $('#rpPassword').value, cpw = $('#rpConfirm').value;
  let m = '';
  if (!pw) m = 'Password is required';
  else if (pw.length < 4) m = 'Password must be at least 4 characters';
  else if (pw !== cpw) m = 'Passwords do not match';
  if (m) { err.textContent = m; err.classList.remove('hidden'); return; }
  btn.disabled = true; btn.textContent = 'Resetting…';
  try {
    const res = await api.resetPassword({ token: state.resetToken, password: pw, confirm_password: cpw });
    if (res && !res.error) {
      history.replaceState(null, '', location.pathname);   // drop ?reset= from the URL
      msg.textContent = 'Password reset! Redirecting to sign in…';
      msg.classList.remove('hidden');
      setTimeout(showLogin, 1400);
    } else {
      err.textContent = res.error || 'Could not reset password'; err.classList.remove('hidden');
    }
  } catch (e) {
    err.textContent = 'Could not reset password. Please try again.'; err.classList.remove('hidden');
  } finally {
    btn.disabled = false; btn.textContent = 'Reset password';
  }
}

async function doLogout() {
  await api.logout();
  setAttached(false);
  showLogin();
}

// ---- boot ------------------------------------------------------------------
async function boot() {
  $('#mockflag').style.display = USE_MOCK ? '' : 'none';

  // show/hide password toggles
  document.querySelectorAll('.pw-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const inp = document.getElementById(btn.dataset.target);
      if (!inp) return;
      const reveal = inp.type === 'password';
      inp.type = reveal ? 'text' : 'password';
      btn.querySelector('.eye-show').classList.toggle('hidden', reveal);
      btn.querySelector('.eye-hide').classList.toggle('hidden', !reveal);
      btn.setAttribute('aria-label', reveal ? 'Hide password' : 'Show password');
    });
  });

  // auth screens
  $('#loginForm').addEventListener('submit', doLogin);
  $('#signupForm').addEventListener('submit', doSignup);
  $('#suPassword').addEventListener('input', checkPwMatch);
  $('#suConfirm').addEventListener('input', checkPwMatch);
  $('#toSignup').addEventListener('click', showSignup);
  $('#toLogin').addEventListener('click', showLogin);
  $('#logout').addEventListener('click', doLogout);

  // password reset
  $('#toForgot').addEventListener('click', showForgot);
  $('#forgotToLogin').addEventListener('click', showLogin);
  $('#forgotForm').addEventListener('submit', doForgot);
  $('#resetToLogin').addEventListener('click', () => { history.replaceState(null, '', location.pathname); showLogin(); });
  $('#resetForm').addEventListener('submit', doReset);
  $('#rpPassword').addEventListener('input', checkResetMatch);
  $('#rpConfirm').addEventListener('input', checkResetMatch);

  // composer
  $('#send').addEventListener('click', () => send($('#input').value));
  $('#input').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); send($('#input').value); }
  });
  $('#attach').addEventListener('click', () => setAttached(!state.attached));
  $('#chips').addEventListener('click', (ev) => {
    const fill = ev.target.closest('[data-fill]');
    const prompt = ev.target.closest('[data-prompt]');
    if (fill) send(fill.dataset.fill);                       // complete request → run it
    else if (prompt) { bubbleBot(esc(prompt.dataset.prompt)); $('#input').focus(); }  // apply → ask the user
  });

  // approvals (event delegation) — Approve/Reject reveal a comment box, then Confirm
  $('#approvals').addEventListener('click', async (ev) => {
    const row = ev.target.closest('[data-row]'); if (!row) return;
    const id = row.dataset.row;
    const act = ev.target.closest('[data-act]');
    const cmt = row.querySelector('.cmt');
    const confirmBtn = row.querySelector('.cmt-confirm');

    if (act) {
      row.dataset.pending = act.dataset.act;
      cmt.classList.remove('hidden');
      if (act.dataset.act === 'approve') {
        confirmBtn.textContent = 'Confirm approve';
        confirmBtn.className = 'cmt-confirm text-xs font-semibold px-3 py-1.5 rounded-lg text-white bg-emerald-500 hover:bg-emerald-600';
      } else {
        confirmBtn.textContent = 'Confirm reject';
        confirmBtn.className = 'cmt-confirm text-xs font-semibold px-3 py-1.5 rounded-lg text-white bg-rose-500 hover:bg-rose-600';
      }
      row.querySelector('.cmt-input').focus();
    } else if (ev.target.closest('[data-cancel]')) {
      cmt.classList.add('hidden'); delete row.dataset.pending;
    } else if (ev.target.closest('[data-confirm]')) {
      const comment = row.querySelector('.cmt-input').value.trim();
      confirmBtn.disabled = true;
      if (row.dataset.pending === 'approve') await api.approve({ request_id: id, comment });
      else await api.reject({ request_id: id, comment });
      await refreshApprovals();
    }
  });

  // confirmation card buttons (event delegation)
  messagesEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-act]'); if (!btn) return;
    const card = btn.closest('[data-card]');
    const act = btn.dataset.act;
    if (act === 'confirm') confirmDraft(card);
    else if (act === 'cancel') { card.remove(); bubbleBot('No problem — cancelled. Nothing was submitted.'); }
    else if (act === 'edit') { $('#input').focus(); bubbleBot('Sure — tell me what to change (dates, type, or duration).'); }
  });

  // password-reset link from email (?reset=token) takes priority
  const resetToken = new URLSearchParams(location.search).get('reset');
  if (resetToken) { showReset(resetToken); return; }

  // resume an existing session or show login
  const user = await api.me();
  if (user) await showApp(user); else showLogin();
}

boot();
