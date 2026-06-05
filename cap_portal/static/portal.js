/**
 * CAP Command Portal — client logic
 */

// ── Mission log (localStorage) ─────────────────────────────────────────────
const MISSION_STORE = 'cp_missions';

function loadMissions() {
  try { return JSON.parse(localStorage.getItem(MISSION_STORE)) || []; }
  catch(_) { return []; }
}

function saveMissions(list) {
  localStorage.setItem(MISSION_STORE, JSON.stringify(list));
}

function recordMission(entry) {
  const list = loadMissions();
  list.unshift({ ...entry, ts: new Date().toISOString() });
  saveMissions(list.slice(0, 200)); // keep last 200
}

// ── API wrapper ────────────────────────────────────────────────────────────
async function apiPost(path, body = {}) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const json = await r.json();
  if (!r.ok) throw new Error(json.message || r.statusText);
  return json;
}

// ── Wizard state ───────────────────────────────────────────────────────────
let wiz = {
  missionNumber: '',
  username: '',
  password: '',
  groupOk: false,
  userOk: false,
  groundOk: false,
  airOk: false,
  recipients: [],
};

function resetWizard() {
  wiz = { missionNumber:'', username:'', password:'', groupOk:false, userOk:false, groundOk:false, airOk:false, recipients:[] };
  document.getElementById('wiz-mission-input').value = '';
  document.getElementById('wiz-cred-card').classList.add('hidden');
  clearStatus('wiz-step2-status');
  clearStatus('wiz-step3-status');
  clearStatus('wiz-step4-status');
  clearStatus('wiz-step5-status');
  wiz.recipients = [];
  renderChips();
}

function openWizard() {
  resetWizard();
  showView('view-wizard');
  showStep(1);
}

// ── Wizard steps ───────────────────────────────────────────────────────────
function showStep(n) {
  document.querySelectorAll('.wizard-step').forEach(s => s.classList.remove('active'));
  const step = document.getElementById(`wiz-step${n}`);
  if (step) step.classList.add('active');

  // Update indicator dots
  document.querySelectorAll('.step-dot').forEach((dot, i) => {
    dot.classList.remove('active', 'done');
    if (i + 1 < n) dot.classList.add('done');
    else if (i + 1 === n) dot.classList.add('active');
  });
}

// Step 1 → 2
document.getElementById('wiz-step1-next').addEventListener('click', () => {
  const mn = document.getElementById('wiz-mission-input').value.trim();
  if (!mn) { toast('Enter a mission number first.', 'error'); return; }
  wiz.missionNumber = mn;
  document.getElementById('wiz-step2-mn').textContent = mn;
  showStep(2);
});

// Step 2 ← / →
document.getElementById('wiz-step2-back').addEventListener('click', () => showStep(1));
document.getElementById('wiz-step2-next').addEventListener('click', async () => {
  const btn = document.getElementById('wiz-step2-next');
  btn.disabled = true;
  setStatus('wiz-step2-status', 'info', 'Creating group…');
  try {
    await apiPost('/api/group', { mission_number: wiz.missionNumber });
    wiz.groupOk = true;
    setStatus('wiz-step2-status', 'success', `✓ Group <strong>${wiz.missionNumber}</strong> created (IN + OUT)`);
    showStep(3);
  } catch(e) {
    setStatus('wiz-step2-status', 'error', `✗ ${e.message}`);
  } finally { btn.disabled = false; }
});

// Step 3 ← / →
document.getElementById('wiz-step3-back').addEventListener('click', () => showStep(2));
document.getElementById('wiz-step3-next').addEventListener('click', async () => {
  const btn = document.getElementById('wiz-step3-next');
  btn.disabled = true;
  setStatus('wiz-step3-status', 'info', 'Creating user…');
  try {
    const resp = await apiPost('/api/user', { mission_number: wiz.missionNumber });
    wiz.userOk   = true;
    wiz.username = resp.data?.username || wiz.missionNumber;
    wiz.password = resp.data?.password || '';
    wiz.userId   = resp.data?.user_id  || '';
    wiz.enrollUrl = '';
    wiz.qrCode    = '';
    setStatus('wiz-step3-status', 'success', `✓ User <strong>${wiz.username}</strong> created`);
    // Show credential card
    document.getElementById('cred-username').textContent = wiz.username;
    document.getElementById('cred-password').textContent = wiz.password;
    document.getElementById('wiz-cred-card').classList.remove('hidden');
    // Fetch enrollment QR from TAK Portal
    if (wiz.userId && typeof TAK_PORTAL_CONFIGURED !== 'undefined' && TAK_PORTAL_CONFIGURED) {
      try {
        setStatus('wiz-step3-status', 'info', '✓ User created — fetching enrollment QR…');
        const qrResp = await apiPost('/api/enrollment-qr', { user_id: wiz.userId, username: wiz.username });
        if (qrResp.data?.enrollUrl) {
          wiz.enrollUrl = qrResp.data.enrollUrl;
          wiz.qrCode    = qrResp.data.qrCode || '';
          document.getElementById('cred-enroll-section').classList.remove('hidden');
          const link = document.getElementById('cred-enroll-url');
          link.href = wiz.enrollUrl;
          link.textContent = wiz.enrollUrl;
          if (wiz.qrCode) {
            document.getElementById('cred-qr-img').src = 'data:image/png;base64,' + wiz.qrCode;
          }
        }
        setStatus('wiz-step3-status', 'success', `✓ User <strong>${wiz.username}</strong> created with enrollment QR`);
      } catch(_) {
        setStatus('wiz-step3-status', 'success', `✓ User <strong>${wiz.username}</strong> created (QR unavailable)`);
      }
    }
    showStep(4);
  } catch(e) {
    setStatus('wiz-step3-status', 'error', `✗ ${e.message}`);
  } finally { btn.disabled = false; }
});

// Step 4 ← / →
document.getElementById('wiz-step4-back').addEventListener('click', () => showStep(3));
document.getElementById('wiz-step4-next').addEventListener('click', async () => {
  const btn = document.getElementById('wiz-step4-next');
  btn.disabled = true;
  setStatus('wiz-step4-status', 'info', 'Creating DataSync missions…');

  let groundOk = false, airOk = false, errors = [];

  const groundEnabled = document.getElementById('toggle-ground').checked;
  const airEnabled    = document.getElementById('toggle-air').checked;

  try {
    if (groundEnabled) {
      await apiPost('/api/mission/ground', { mission_number: wiz.missionNumber });
      groundOk = true;
    }
  } catch(e) { errors.push(`GROUND: ${e.message}`); }

  try {
    if (airEnabled) {
      await apiPost('/api/mission/air', { mission_number: wiz.missionNumber });
      airOk = true;
    }
  } catch(e) { errors.push(`AIR: ${e.message}`); }

  wiz.groundOk = groundOk;
  wiz.airOk    = airOk;

  if (errors.length) {
    setStatus('wiz-step4-status', 'error', errors.map(e => `✗ ${e}`).join('<br>'));
  } else {
    const parts = [];
    if (groundOk) parts.push(`✓ ${wiz.missionNumber}-GROUND created`);
    if (airOk)    parts.push(`✓ ${wiz.missionNumber}-AIR created`);
    if (!groundEnabled && !airEnabled) parts.push('No DataSync missions selected — skipped.');
    setStatus('wiz-step4-status', 'success', parts.join('<br>'));
    showStep(5);
  }
  btn.disabled = false;
});

// Step 5 ← / send / skip
document.getElementById('wiz-step5-back').addEventListener('click', () => showStep(4));

document.getElementById('wiz-send').addEventListener('click', async () => {
  if (wiz.recipients.length === 0) { toast('Add at least one recipient email.', 'error'); return; }
  const btn = document.getElementById('wiz-send');
  btn.disabled = true;
  setStatus('wiz-step5-status', 'info', 'Sending credentials…');

  const subject = document.getElementById('cred-subject').value.trim();
  const notes   = document.getElementById('cred-notes').value.trim();

  try {
    const resp = await apiPost('/api/send-credentials', {
      mission_number: wiz.missionNumber,
      username: wiz.username,
      password: wiz.password,
      recipients: wiz.recipients,
      subject,
      notes,
      enroll_url: wiz.enrollUrl || '',
      qr_code_base64: wiz.qrCode || '',
    });
    const sent = resp.results?.filter(r => r.success).length || 0;
    setStatus('wiz-step5-status', 'success', `✓ Sent to ${sent} recipient(s)`);
    completeWizard();
  } catch(e) {
    setStatus('wiz-step5-status', 'error', `✗ ${e.message}`);
  } finally { btn.disabled = false; }
});

document.getElementById('wiz-skip').addEventListener('click', () => {
  completeWizard();
});

function completeWizard() {
  recordMission({
    number: wiz.missionNumber,
    username: wiz.username,
    group: wiz.groupOk,
    ground: wiz.groundOk,
    air: wiz.airOk,
    status: 'provisioned',
  });

  // Show success screen
  document.getElementById('wiz-step5').classList.remove('active');
  const success = document.getElementById('wiz-success');
  success.classList.remove('hidden');
  success.querySelector('.success-mission').textContent = wiz.missionNumber;

  const items = success.querySelector('.summary-list');
  items.innerHTML = '';
  const add = (t) => { const li = document.createElement('li'); li.textContent = t; items.appendChild(li); };
  if (wiz.groupOk)  add(`Group ${wiz.missionNumber} created`);
  if (wiz.userOk)   add(`User ${wiz.username} created`);
  if (wiz.groundOk) add(`DataSync: ${wiz.missionNumber}-GROUND`);
  if (wiz.airOk)    add(`DataSync: ${wiz.missionNumber}-AIR`);
}

document.getElementById('wiz-done-btn').addEventListener('click', () => {
  document.getElementById('wiz-success').classList.add('hidden');
  showView('view-dashboard');
  loadDashboard();
});

// ── Chip UI ────────────────────────────────────────────────────────────────
function renderChips() {
  const wrap = document.getElementById('chip-wrap');
  wrap.querySelectorAll('.chip').forEach(c => c.remove());
  wiz.recipients.forEach((email, i) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.innerHTML = `${email}<button class="chip-remove" data-i="${i}">×</button>`;
    wrap.insertBefore(chip, document.getElementById('chip-input'));
  });
}

document.getElementById('chip-wrap').addEventListener('click', e => {
  if (e.target.matches('.chip-remove')) {
    wiz.recipients.splice(Number(e.target.dataset.i), 1);
    renderChips();
  }
});

document.getElementById('chip-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    const val = e.target.value.trim().replace(/,$/, '');
    if (val && !wiz.recipients.includes(val)) {
      wiz.recipients.push(val);
      renderChips();
    }
    e.target.value = '';
  }
});

// ── Status helpers ─────────────────────────────────────────────────────────
function setStatus(id, type, html) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `step-status status-${type}`;
  el.innerHTML = html;
  el.classList.remove('hidden');
}
function clearStatus(id) {
  const el = document.getElementById(id);
  if (el) { el.innerHTML = ''; el.classList.add('hidden'); }
}

// ── View routing ───────────────────────────────────────────────────────────
function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const v = document.getElementById(id);
  if (v) v.classList.add('active');
  document.querySelectorAll('.nav-btn[data-view]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === id);
  });
}

document.querySelectorAll('.nav-btn[data-view]').forEach(btn => {
  btn.addEventListener('click', () => {
    const id = btn.dataset.view;
    showView(id);
    if (id === 'view-dashboard')    loadDashboard();
    if (id === 'view-missions')     loadMissionsList();
    if (id === 'view-credentials')  loadCredentials();
  });
});

document.getElementById('open-wizard-btn').addEventListener('click', openWizard);

// ── Dashboard ──────────────────────────────────────────────────────────────
function loadDashboard() {
  const missions = loadMissions();
  document.getElementById('stat-total').textContent    = missions.length;
  document.getElementById('stat-provisioned').textContent = missions.filter(m => m.status === 'provisioned').length;
  document.getElementById('stat-ground').textContent   = missions.filter(m => m.ground).length;
  document.getElementById('stat-air').textContent      = missions.filter(m => m.air).length;
  document.getElementById('stat-creds').textContent    = missions.filter(m => m.username).length;

  const tbody = document.getElementById('recent-missions-body');
  tbody.innerHTML = '';
  missions.slice(0, 5).forEach(m => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${m.number}</td>
      <td>${m.username || '—'}</td>
      <td><span class="badge badge-success">${m.status}</span></td>
      <td>${new Date(m.ts).toLocaleDateString()}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── Missions list ──────────────────────────────────────────────────────────
function loadMissionsList() {
  renderMissionsTable(loadMissions());
}

function renderMissionsTable(missions) {
  const tbody = document.getElementById('missions-tbody');
  tbody.innerHTML = '';
  if (!missions.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#6b7280;padding:2rem">No missions yet.</td></tr>';
    return;
  }
  missions.forEach((m, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${m.number}</td>
      <td>${m.username || '—'}</td>
      <td><span class="badge badge-${m.group ? 'success' : 'danger'}">${m.group ? 'Yes' : 'No'}</span></td>
      <td><span class="badge badge-${m.ground ? 'success' : 'danger'}">${m.ground ? 'Yes' : 'No'}</span></td>
      <td><span class="badge badge-${m.air ? 'success' : 'danger'}">${m.air ? 'Yes' : 'No'}</span></td>
      <td><span class="badge badge-success">${m.status}</span></td>
      <td>${new Date(m.ts).toLocaleDateString()}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── Credentials view ───────────────────────────────────────────────────────
let credRecipients = [];

function loadCredentials() {
  const missions = loadMissions();
  const sel = document.getElementById('cred-mission-select');
  sel.innerHTML = '<option value="">— select mission —</option>';
  missions.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.number;
    opt.textContent = `${m.number}  (${m.username || 'no user'})`;
    sel.appendChild(opt);
  });
  credRecipients = [];
  renderCredChips();
}

sel_change: {
  const sel = document.getElementById('cred-mission-select');
  sel.addEventListener('change', () => {
    const missions = loadMissions();
    const m = missions.find(x => x.number === sel.value);
    document.getElementById('cred-view-username').value = m?.username || '';
    document.getElementById('cred-view-password').value = '';
  });
}

function renderCredChips() {
  const wrap = document.getElementById('cred-chip-wrap');
  wrap.querySelectorAll('.chip').forEach(c => c.remove());
  credRecipients.forEach((email, i) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.innerHTML = `${email}<button class="chip-remove" data-i="${i}">×</button>`;
    wrap.insertBefore(chip, document.getElementById('cred-chip-input'));
  });
}

document.getElementById('cred-chip-wrap').addEventListener('click', e => {
  if (e.target.matches('.chip-remove')) {
    credRecipients.splice(Number(e.target.dataset.i), 1);
    renderCredChips();
  }
});

document.getElementById('cred-chip-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    const val = e.target.value.trim().replace(/,$/, '');
    if (val && !credRecipients.includes(val)) {
      credRecipients.push(val);
      renderCredChips();
    }
    e.target.value = '';
  }
});

document.getElementById('cred-send-btn').addEventListener('click', async () => {
  const mn       = document.getElementById('cred-mission-select').value;
  const username = document.getElementById('cred-view-username').value.trim();
  const password = document.getElementById('cred-view-password').value.trim();
  const subject  = document.getElementById('cred-view-subject').value.trim();
  const notes    = document.getElementById('cred-view-notes').value.trim();

  if (!mn)                    { toast('Select a mission.', 'error'); return; }
  if (!password)              { toast('Enter the password.', 'error'); return; }
  if (!credRecipients.length) { toast('Add at least one recipient.', 'error'); return; }

  const btn = document.getElementById('cred-send-btn');
  btn.disabled = true;
  try {
    const resp = await apiPost('/api/send-credentials', {
      mission_number: mn, username, password,
      recipients: credRecipients, subject, notes,
      enroll_url: '', qr_code_base64: '',
    });
    const sent = resp.results?.filter(r => r.success).length || 0;
    toast(`Sent to ${sent} recipient(s)`, 'success');
  } catch(e) {
    toast(e.message, 'error');
  } finally { btn.disabled = false; }
});

// ── Toast ──────────────────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ── Boot ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  showView('view-dashboard');
  loadDashboard();
});
