/**
 * SignalFFT Dashboard — Main Application
 *
 * Handles authentication (Cognito), API polling, and rendering
 * all dashboard panels: pipeline flow, metrics, signals, waves,
 * narratives, trade candidates, attention field, and queue health.
 */

import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
} from 'amazon-cognito-identity-js';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const CONFIG = {
  cognitoUserPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID || '',
  cognitoClientId: import.meta.env.VITE_COGNITO_CLIENT_ID || '',
  refreshIntervalMs: 15_000,   // 15-second auto-refresh
  apiBase: import.meta.env.VITE_API_BASE || '/api',
};

// Stage icons for the pipeline flow visualization
const STAGE_ICONS = {
  collectors: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 12V4l6-3 6 3v8l-6 3-6-3z" stroke="currentColor" stroke-width="1.2"/></svg>',
  features: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8h10M8 3v10M5 5l6 6M11 5l-6 6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
  signals: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 10l3-4 3 2 3-5 3 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  waves: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M1 8c2-4 4 4 6 0s4 4 6 0" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  narratives: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="4" cy="4" r="2" stroke="currentColor" stroke-width="1.2"/><circle cx="12" cy="4" r="2" stroke="currentColor" stroke-width="1.2"/><circle cx="8" cy="12" r="2" stroke="currentColor" stroke-width="1.2"/><path d="M5.5 5.5L7 10.5M10.5 5.5L9 10.5" stroke="currentColor" stroke-width="1"/></svg>',
  risk_gate: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="3" y="2" width="10" height="12" rx="1.5" stroke="currentColor" stroke-width="1.2"/><path d="M6 6h4M6 9h4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
};

// Component names for signal scoring bars
const COMPONENT_NAMES = [
  'novelty', 'velocity', 'cross_source', 'semantic_impact',
  'entity_sensitivity', 'historical_pattern', 'noise_penalty',
];

const COMPONENT_COLORS = [
  '#38bdf8', '#34d399', '#fbbf24', '#a78bfa',
  '#22d3ee', '#fb923c', '#f87171',
];


// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let userPool = null;
let currentUser = null;
let refreshTimer = null;
let isConnected = true;

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

function initAuth() {
  if (CONFIG.cognitoUserPoolId && CONFIG.cognitoClientId) {
    userPool = new CognitoUserPool({
      UserPoolId: CONFIG.cognitoUserPoolId,
      ClientId: CONFIG.cognitoClientId,
    });
    currentUser = userPool.getCurrentUser();
    if (currentUser) {
      currentUser.getSession((err) => {
        if (!err) {
          showDashboard();
          return;
        }
        showLogin();
      });
      return;
    }
  }
  // If no Cognito config, skip auth for local dev
  if (!CONFIG.cognitoUserPoolId) {
    showDashboard();
    return;
  }
  showLogin();
}

function showLogin() {
  document.getElementById('login-screen').style.display = '';
  document.getElementById('dashboard-screen').style.display = 'none';
  stopAutoRefresh();
}

function showDashboard() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('dashboard-screen').style.display = '';
  loadAllData();
  startAutoRefresh();
}

function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById('email').value;
  const password = document.getElementById('password').value;
  const errorEl = document.getElementById('login-error');
  errorEl.textContent = '';

  if (!userPool) {
    // No Cognito configured — skip auth
    showDashboard();
    return;
  }

  const authDetails = new AuthenticationDetails({
    Username: email,
    Password: password,
  });

  const cognitoUser = new CognitoUser({
    Username: email,
    Pool: userPool,
  });

  cognitoUser.authenticateUser(authDetails, {
    onSuccess: () => {
      currentUser = cognitoUser;
      showDashboard();
    },
    onFailure: (err) => {
      errorEl.textContent = err.message || 'Authentication failed';
    },
    newPasswordRequired: () => {
      document.getElementById('new-password-group').style.display = '';
      const newPw = document.getElementById('new-password').value;
      if (newPw) {
        cognitoUser.completeNewPasswordChallenge(newPw, {}, {
          onSuccess: () => {
            currentUser = cognitoUser;
            showDashboard();
          },
          onFailure: (err) => {
            errorEl.textContent = err.message || 'Password change failed';
          },
        });
      }
    },
  });
}

function handleLogout() {
  if (currentUser) currentUser.signOut();
  currentUser = null;
  showLogin();
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function apiFetch(path) {
  try {
    const resp = await fetch(`${CONFIG.apiBase}${path}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    setConnected(true);
    return await resp.json();
  } catch (err) {
    setConnected(false);
    console.warn(`API error (${path}):`, err.message);
    return null;
  }
}

function setConnected(connected) {
  isConnected = connected;
  const dot = document.getElementById('connection-dot');
  if (dot) {
    dot.classList.toggle('error', !connected);
    dot.title = connected ? 'Connected' : 'Connection lost';
  }
}

// ---------------------------------------------------------------------------
// Data Loading
// ---------------------------------------------------------------------------

async function loadAllData() {
  const results = await Promise.allSettled([
    apiFetch('/pipeline/flow'),
    apiFetch('/metrics/summary'),
    apiFetch('/signals/recent?limit=15'),
    apiFetch('/waves/active?limit=15'),
    apiFetch('/narratives/active?limit=20'),
    apiFetch('/candidates/recent?limit=15'),
    apiFetch('/attention/current'),
    apiFetch('/queues/status'),
    apiFetch('/triage/recent'),
    apiFetch('/outcomes/recent'),
    apiFetch('/deltas/recent'),
    apiFetch('/shadows/comparison'),
    apiFetch('/filing-pipeline/status'),
    apiFetch('/metrics/extended'),
  ]);

  const val = (i) => results[i].status === 'fulfilled' ? results[i].value : null;

  if (val(0)) renderPipelineFlow(val(0));
  if (val(1)) renderMetrics(val(1), val(13));
  if (val(2)) renderSignals(val(2));
  if (val(3)) renderWaves(val(3));
  if (val(4)) renderNarratives(val(4));
  if (val(5)) renderCandidates(val(5));
  if (val(6)) renderAttention(val(6));
  if (val(7)) renderQueues(val(7));
  renderTriage(val(8));
  renderOutcomes(val(9));
  renderDeltas(val(10));
  renderShadows(val(11));
  renderFilingPipeline(val(12));

  document.getElementById('last-updated').textContent =
    `Updated ${new Date().toLocaleTimeString()}`;
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function renderPipelineFlow(data) {
  const container = document.getElementById('pipeline-flow');
  const stages = data.stages || [];
  let allHealthy = true;
  let html = '';

  stages.forEach((stage, i) => {
    if (stage.status !== 'healthy') allHealthy = false;
    const icon = STAGE_ICONS[stage.id] || STAGE_ICONS.signals;
    html += `
      <div class="pipeline-stage">
        <div class="pipeline-stage-card ${stage.status}">
          <div class="stage-icon" style="color: var(--accent)">${icon}</div>
          <div class="stage-label">${esc(stage.label)}</div>
          <div class="stage-sublabel">${esc(stage.sublabel)}</div>
          <div class="stage-count">${formatNum(stage.count)}</div>
          <div class="stage-count-label">items</div>
          ${stage.queue_depth > 0 ? `<div class="stage-queue">queue: ${formatNum(stage.queue_depth)}</div>` : ''}
        </div>
      </div>`;
    if (i < stages.length - 1) {
      html += '<div class="pipeline-arrow"></div>';
    }
  });

  container.innerHTML = html;

  const badge = document.getElementById('pipeline-status-badge');
  if (allHealthy) {
    badge.textContent = 'Healthy';
    badge.className = 'section-badge';
  } else {
    badge.textContent = 'Degraded';
    badge.className = 'section-badge warning';
  }
}

function renderMetrics(data, extended) {
  const m = data.metrics || {};
  const ext = extended || {};
  const cards = [
    { label: 'Events Ingested', value: m.total_events, color: '' },
    { label: 'Features Extracted', value: m.total_features, color: '' },
    { label: 'Signals Scored', value: m.total_signals, color: 'accent' },
    { label: 'Active Waves', value: m.active_waves, color: 'amber' },
    { label: 'Narratives', value: m.active_narratives, color: 'purple' },
    { label: 'Trade Candidates', value: m.trade_candidates, color: 'green' },
    { label: 'Entities Tracked', value: m.tracked_entities, color: '' },
    { label: 'Queue Backlog', value: m.total_queue_depth, color: m.total_queue_depth > 500 ? 'amber' : '' },
    { label: 'Outcomes Tracked', value: ext.outcomes_tracked, color: 'accent' },
    { label: 'Filing Pairs', value: ext.filing_pairs, color: 'purple' },
    { label: 'Shadow Scores', value: ext.shadow_scores, color: 'purple' },
    { label: 'High-Materiality', value: ext.triage_high_materiality, color: 'amber' },
  ];

  const container = document.getElementById('metrics-strip');
  container.innerHTML = cards.map(c => `
    <div class="metric-card">
      <span class="metric-label">${c.label}</span>
      <span class="metric-value ${c.color}">${formatNum(c.value ?? 0)}</span>
    </div>
  `).join('');
}

function renderSignals(data) {
  const items = data.items || [];
  const tbody = document.getElementById('signals-tbody');
  document.getElementById('signals-count').textContent = data.count || 0;

  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No signals recorded yet</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(s => {
    const score = typeof s.score === 'number' ? s.score : 0;
    const scoreColor = scoreToColor(score);
    const components = s.components || {};
    const componentBars = COMPONENT_NAMES.map((name, i) => {
      const val = components[name] ?? 0;
      const h = Math.max(2, Math.min(20, Math.abs(val) * 20));
      return `<div class="component-bar" title="${name}: ${val.toFixed(2)}" style="height:${h}px; background:${COMPONENT_COLORS[i]}"></div>`;
    }).join('');

    const ds = typeof s.direction_score === 'number' ? s.direction_score : 0;
    const dl = s.direction_label || 'NEUTRAL';
    const dirClass = dl === 'LONG' ? 'long' : dl === 'SHORT' ? 'short' : 'neutral';
    const dirArrow = dl === 'LONG' ? '▲' : dl === 'SHORT' ? '▼' : '—';

    return `<tr>
      <td class="entity-cell">${esc(shortId(s.entity_id))}</td>
      <td>
        <div class="score-bar-wrap">
          <span class="score-value" style="color:${scoreColor}">${score.toFixed(2)}</span>
          <div class="score-bar">
            <div class="score-bar-fill" style="width:${Math.min(100, score * 100)}%; background:${scoreColor}"></div>
          </div>
        </div>
      </td>
      <td><span class="direction-badge ${dirClass}">${dirArrow} ${dl}</span><span class="direction-sub">${ds >= 0 ? '+' : ''}${ds.toFixed(2)}</span></td>
      <td><div class="component-bars">${componentBars}</div></td>
      <td class="timestamp-cell">${formatTime(s.created_at)}</td>
    </tr>`;
  }).join('');
}

function renderWaves(data) {
  const items = data.items || [];
  const tbody = document.getElementById('waves-tbody');
  document.getElementById('waves-count').textContent = data.count || 0;

  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No active waves</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(w => {
    const strength = typeof w.amplitude === 'number' ? w.amplitude : (typeof w.strength === 'number' ? w.strength : 0);
    const color = scoreToColor(strength);
    return `<tr>
      <td class="entity-cell">${esc(shortId(w.entity_id))}</td>
      <td>
        <div class="score-bar-wrap">
          <span class="score-value" style="color:${color}">${strength.toFixed(2)}</span>
          <div class="score-bar">
            <div class="score-bar-fill" style="width:${Math.min(100, strength * 100)}%; background:${color}"></div>
          </div>
        </div>
      </td>
      <td style="font-family:var(--font-mono)">${w.signal_count ?? 1}</td>
      <td class="timestamp-cell">${formatTime(w.created_at || w.window_end)}</td>
    </tr>`;
  }).join('');
}

function renderNarratives(data) {
  const items = data.items || [];
  const panel = document.getElementById('narratives-panel');
  document.getElementById('narratives-count').textContent = data.count || 0;

  if (!items.length) {
    panel.innerHTML = '<p class="empty-state">No active narratives</p>';
    return;
  }

  panel.innerHTML = items.map(n => {
    const state = (n.lifecycle_state || 'EMERGING').toUpperCase();
    const gravity = typeof n.gravity_score === 'number' ? n.gravity_score : 0;
    const entities = Array.isArray(n.entities) ? n.entities : (n.entity_id ? [n.entity_id] : []);
    const label = n.claude_label || n.entity_id || n.narrative_id || 'Unnamed';

    return `
      <div class="narrative-card">
        <div class="narrative-top">
          <span class="narrative-label">${esc(label)}</span>
          <span class="narrative-state ${state}">${state}</span>
        </div>
        <div class="narrative-gravity">
          <span class="narrative-gravity-label">Gravity</span>
          <div class="narrative-gravity-bar">
            <div class="narrative-gravity-fill" style="width:${Math.min(100, gravity * 100)}%"></div>
          </div>
          <span class="narrative-gravity-value">${gravity.toFixed(2)}</span>
        </div>
        ${entities.length ? `
          <div class="narrative-entities">
            ${entities.map(e => `<span class="entity-tag">${esc(shortId(e))}</span>`).join('')}
          </div>
        ` : ''}
      </div>`;
  }).join('');
}

function renderCandidates(data) {
  const items = data.items || [];
  const tbody = document.getElementById('candidates-tbody');
  document.getElementById('candidates-count').textContent = data.count || 0;

  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No trade candidates</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(c => {
    const status = (c.risk_status || '').toUpperCase();
    const statusClass = status === 'APPROVED' ? 'approved' : 'rejected';
    const score = typeof c.score === 'number' ? c.score : 0;
    const color = scoreToColor(score);

    return `<tr>
      <td class="entity-cell">${esc(shortId(c.entity_id))}</td>
      <td>
        <div class="score-bar-wrap">
          <span class="score-value" style="color:${color}">${score.toFixed(2)}</span>
          <div class="score-bar">
            <div class="score-bar-fill" style="width:${Math.min(100, score * 100)}%; background:${color}"></div>
          </div>
        </div>
      </td>
      <td><span class="status-pill ${statusClass}">${status}</span></td>
      <td class="timestamp-cell">${formatTime(c.created_at)}</td>
    </tr>`;
  }).join('');
}

function renderAttention(data) {
  const panel = document.getElementById('attention-panel');
  const af = data.attention_field;

  if (!af) {
    panel.innerHTML = '<p class="empty-state">No attention field data</p>';
    return;
  }

  const temp = typeof af.density === 'number' ? af.density : (typeof af.temperature === 'number' ? af.temperature : 0);
  const nfs = typeof af.avg_score === 'number' ? af.avg_score : (typeof af.narrative_field_strength === 'number' ? af.narrative_field_strength : 0);
  const signalCount = af.signal_count ?? 0;

  panel.innerHTML = `
    <div class="attention-gauges">
      <div class="gauge-card">
        <div class="gauge-value" style="color: ${temp > 0.7 ? 'var(--red)' : temp > 0.4 ? 'var(--amber)' : 'var(--accent)'}">${temp.toFixed(2)}</div>
        <div class="gauge-label">Signal Density</div>
      </div>
      <div class="gauge-card">
        <div class="gauge-value" style="color: ${nfs > 0.7 ? 'var(--purple)' : 'var(--accent)'}">${nfs.toFixed(2)}</div>
        <div class="gauge-label">Avg Score</div>
      </div>
      <div class="gauge-card">
        <div class="gauge-value" style="color: var(--accent)">${signalCount}</div>
        <div class="gauge-label">Signals</div>
      </div>
    </div>
    ${af.entity_id ? `<div style="margin-top:0.5rem;font-size:0.75rem;color:var(--text-muted)">Entity: ${esc(shortId(af.entity_id))}</div>` : ''}`;
}

function renderQueues(data) {
  const panel = document.getElementById('queues-panel');
  const queues = data.queues || {};
  const names = Object.keys(queues);

  if (!names.length) {
    panel.innerHTML = '<p class="empty-state">No queue data available</p>';
    return;
  }

  const maxDepth = Math.max(1, ...names.map(n => Math.max(0, queues[n].depth || 0)));

  panel.innerHTML = names.map(name => {
    const q = queues[name];
    const depth = Math.max(0, q.depth || 0);
    const pct = (depth / maxDepth) * 100;
    const fillClass = depth === 0 ? 'ok' : depth < 100 ? 'ok' : depth < 1000 ? 'warn' : 'critical';
    const statusClass = q.status || 'unconfigured';
    const displayName = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

    return `
      <div class="queue-row">
        <span class="queue-status-dot ${statusClass}"></span>
        <span class="queue-name">${esc(displayName)}</span>
        <div class="queue-depth-bar">
          <div class="queue-depth-fill ${fillClass}" style="width:${pct}%"></div>
        </div>
        <span class="queue-depth-value">${depth >= 0 ? formatNum(depth) : '--'}</span>
      </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// New Panel Renderers
// ---------------------------------------------------------------------------

function renderTriage(data) {
  const tbody = document.getElementById('triage-tbody');
  const countEl = document.getElementById('triage-count');

  if (!data) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="7">⚠ Unable to load</td></tr>';
    return;
  }

  const items = data.triages || [];
  countEl.textContent = data.high_materiality_count || 0;

  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No quiet filing alerts yet.</td></tr>';
    return;
  }

  tbody.innerHTML = items.map((t, idx) => {
    const mat = typeof t.materiality_score === 'number' ? t.materiality_score : 0;
    const matClass = mat >= 8 ? 'mat-high' : mat >= 6 ? 'mat-medium' : 'mat-low';
    const attn = (t.attention_likelihood || 'medium').toLowerCase();
    const dir = (t.direction || 'neutral').toLowerCase();
    const dirClass = dir === 'bullish' ? 'long' : dir === 'bearish' ? 'short' : 'neutral';
    const dirArrow = dir === 'bullish' ? '▲' : dir === 'bearish' ? '▼' : '—';
    const dirLabel = dir.toUpperCase();
    const urgency = (t.suggested_urgency || 'monitor').toLowerCase();
    const urgencyClass = urgency === 'act' ? 'act' : urgency === 'investigate' ? 'investigate' : 'monitor';

    const reasoning = esc(t.reasoning || '');
    const keyItems = Array.isArray(t.key_material_items) ? t.key_material_items.map(k => esc(k)).join(', ') : '';
    const afterHours = t.is_after_hours != null ? (t.is_after_hours ? 'Yes' : 'No') : '—';
    const friday = t.is_friday != null ? (t.is_friday ? 'Yes' : 'No') : '—';

    return `<tr class="triage-clickable" data-triage-idx="${idx}">
      <td class="entity-cell">${esc(t.entity_id)}</td>
      <td>${esc(t.form_type)}</td>
      <td><span class="${matClass}" style="font-family:var(--font-mono)">${mat}</span></td>
      <td><span class="attn-badge ${attn}">${attn.toUpperCase()}</span></td>
      <td><span class="direction-badge ${dirClass}">${dirArrow} ${dirLabel}</span></td>
      <td><span class="urgency-badge ${urgencyClass}">${urgency.toUpperCase()}</span></td>
      <td class="timestamp-cell">${formatTime(t.created_at)}</td>
    </tr>
    <tr class="triage-detail-row" data-triage-detail="${idx}">
      <td colspan="7">
        <div class="triage-detail">
          <div class="triage-detail-reasoning">${reasoning}</div>
          ${keyItems ? `<div class="triage-detail-items"><strong>Key Items:</strong> ${keyItems}</div>` : ''}
          <div class="triage-detail-flags">After Hours: ${afterHours} | Friday: ${friday} | Boost Applied: ${t.signal_boost_applied ? 'Yes' : 'No'}</div>
        </div>
      </td>
    </tr>`;
  }).join('');

  // Wire up click-to-expand
  tbody.querySelectorAll('.triage-clickable').forEach(row => {
    row.addEventListener('click', () => {
      const idx = row.dataset.triageIdx;
      const detail = tbody.querySelector(`[data-triage-detail="${idx}"] .triage-detail`);
      if (detail) detail.classList.toggle('open');
    });
  });
}

function renderOutcomes(data) {
  const tbody = document.getElementById('outcomes-tbody');
  const countEl = document.getElementById('outcomes-count');
  const summaryEl = document.getElementById('outcomes-summary');

  if (!data) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">⚠ Unable to load</td></tr>';
    summaryEl.innerHTML = '';
    return;
  }

  const items = data.outcomes || [];
  const summary = data.summary || {};
  countEl.textContent = summary.total_with_t1d || 0;

  // Summary bar
  const pctClass = (summary.positive_t1d_pct || 0) >= 50 ? 'positive' : 'negative';
  summaryEl.innerHTML = `<span>${formatNum(summary.total_with_t1d || 0)} outcomes</span>
    <span class="${pctClass}">${(summary.positive_t1d_pct || 0).toFixed(1)}% positive at T+1d</span>
    <span>avg ${formatPct(summary.avg_pct_change_t1d)}</span>`;

  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">Outcome data accumulating.</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(o => {
    const score = typeof o.signal_score === 'number' ? o.signal_score : 0;
    const scoreColor = scoreToColor(score);
    const ds = typeof o.direction_score === 'number' ? o.direction_score : 0;
    const dirClass = ds > 0.1 ? 'long' : ds < -0.1 ? 'short' : 'neutral';
    const dirArrow = ds > 0.1 ? '▲' : ds < -0.1 ? '▼' : '—';
    const dirLabel = ds > 0.1 ? 'LONG' : ds < -0.1 ? 'SHORT' : 'NEUTRAL';
    const price = typeof o.price_at_signal === 'number' ? formatDollar(o.price_at_signal) : '—';
    const spread = typeof o.spread_at_signal === 'number' ? o.spread_at_signal : null;
    const spreadStr = spread != null ? formatDollar(spread) : '—';
    const spreadWarn = spread != null && o.price_at_signal > 0 && (spread / o.price_at_signal) > 0.02;

    return `<tr>
      <td class="entity-cell">${esc(o.entity_id || o.ticker)}</td>
      <td>
        <div class="score-bar-wrap">
          <span class="score-value" style="color:${scoreColor}">${score.toFixed(2)}</span>
          <div class="score-bar">
            <div class="score-bar-fill" style="width:${Math.min(100, score * 100)}%; background:${scoreColor}"></div>
          </div>
        </div>
      </td>
      <td><span class="direction-badge ${dirClass}">${dirArrow} ${dirLabel}</span></td>
      <td style="font-family:var(--font-mono)">${price}</td>
      <td>${formatPct(o.raw_pct_change_t1d)}</td>
      <td>${formatPct(o.raw_pct_change_t5d)}</td>
      <td class="${spreadWarn ? 'spread-warn' : ''}" style="font-family:var(--font-mono)">${spreadStr}</td>
      <td style="font-family:var(--font-mono)">${formatVolume(o.addv_20d)}</td>
    </tr>`;
  }).join('');
}

function renderDeltas(data) {
  const tbody = document.getElementById('deltas-tbody');
  const countEl = document.getElementById('deltas-count');

  if (!data) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">⚠ Unable to load</td></tr>';
    return;
  }

  if (data.status === 'table_not_provisioned') {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">Semantic deltas table not yet provisioned. Run terraform apply.</td></tr>';
    countEl.textContent = 0;
    return;
  }

  const items = data.deltas || [];
  countEl.textContent = data.total_count || 0;

  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">No semantic deltas yet. Filing pair comparisons will appear here when Edge 2 is active.</td></tr>';
    return;
  }

  const SECTION_LABELS = {
    item_7: 'MD&A', md_and_a: 'MD&A', part1_item2: 'MD&A',
    item_1a: 'Risk Factors', risk_factors: 'Risk Factors', part2_item1a: 'Risk Factors',
  };

  tbody.innerHTML = items.map((d, idx) => {
    const section = SECTION_LABELS[d.section_name] || d.section_name || '—';
    const shifts = typeof d.shift_count === 'number' ? d.shift_count : 0;
    const shiftsClass = shifts >= 3 ? 'high' : shifts >= 1 ? 'medium' : 'low';
    const sev = typeof d.max_severity === 'number' ? d.max_severity : 0;
    const sevClass = `severity-${Math.min(5, Math.max(1, sev))}`;
    const tone = (d.overall_tone_change || 'unchanged').toLowerCase();
    const toneLabel = tone.replace(/_/g, ' ').toUpperCase();
    const toneClass = tone.includes('less') ? 'less_confident' : tone.includes('more') ? 'more_confident' : 'unchanged';
    const dir = (d.direction_consensus || 'neutral').toLowerCase();
    const dirClass = dir === 'bullish' ? 'long' : dir === 'bearish' ? 'short' : 'neutral';
    const dirArrow = dir === 'bullish' ? '▲' : dir === 'bearish' ? '▼' : '—';
    const impact = typeof d.mapped_semantic_impact === 'number' ? d.mapped_semantic_impact : 0;
    const impactPct = Math.min(100, Math.abs(impact) * 100);

    const shiftDetails = (d.shifts || []).map(s =>
      `<div class="shift-card">
        <span class="shift-type-label">${esc(s.shift_type || '')}</span>
        <span class="severity-badge ${`severity-${Math.min(5, Math.max(1, s.severity || 1))}`}">${s.severity || 0}</span>
        <span class="direction-badge ${s.direction === 'bullish' ? 'long' : s.direction === 'bearish' ? 'short' : 'neutral'}">${s.direction === 'bullish' ? '▲' : s.direction === 'bearish' ? '▼' : '—'}</span>
        <span style="flex:1;color:var(--text-secondary);font-size:0.72rem">${esc(s.interpretation || s.description || '')}</span>
      </div>`
    ).join('');

    return `<tr class="delta-clickable" data-delta-idx="${idx}">
      <td class="entity-cell">${esc(d.entity_id)}</td>
      <td>${esc(d.form_type)}</td>
      <td>${section}</td>
      <td><span class="shifts-count ${shiftsClass}">${shifts}</span></td>
      <td><span class="severity-badge ${sevClass}">${sev}</span></td>
      <td><span class="tone-label ${toneClass}">${toneLabel}</span></td>
      <td><span class="direction-badge ${dirClass}">${dirArrow}</span></td>
      <td>
        <div class="impact-bar-wrap">
          <div class="impact-bar"><div class="impact-bar-fill" style="width:${impactPct}%"></div></div>
          <span class="impact-value">${impact.toFixed(2)}</span>
        </div>
      </td>
    </tr>
    <tr class="delta-detail-row" data-delta-detail="${idx}">
      <td colspan="8">
        <div class="delta-detail">${shiftDetails || '<span style="color:var(--text-muted)">No shift details</span>'}</div>
      </td>
    </tr>`;
  }).join('');

  // Wire up click-to-expand
  tbody.querySelectorAll('.delta-clickable').forEach(row => {
    row.addEventListener('click', () => {
      const idx = row.dataset.deltaIdx;
      const detail = tbody.querySelector(`[data-delta-detail="${idx}"] .delta-detail`);
      if (detail) detail.classList.toggle('open');
    });
  });
}

function renderShadows(data) {
  const tbody = document.getElementById('shadows-tbody');
  const countEl = document.getElementById('shadows-count');

  if (!data) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">⚠ Unable to load</td></tr>';
    return;
  }

  const items = data.comparisons || [];
  countEl.textContent = items.length;

  if (!items.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">Shadow scores accumulating.</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(s => {
    const orig = typeof s.original_score === 'number' ? s.original_score : 0;
    const boost = typeof s.triage_boost === 'number' ? s.triage_boost : 0;
    const deltaImpact = typeof s.delta_impact === 'number' ? s.delta_impact : 0;
    const shadowVal = orig + boost + deltaImpact;
    const scoreDelta = typeof s.score_delta === 'number' ? s.score_delta : 0;
    const deltaClass = scoreDelta > 0.001 ? 'delta-score-positive' : scoreDelta < -0.001 ? 'delta-score-negative' : 'delta-score-zero';
    const highlight = Math.abs(scoreDelta) > 0.10;
    const dir = (s.direction_consensus || '').toLowerCase();
    const dirClass = dir === 'bullish' ? 'long' : dir === 'bearish' ? 'short' : 'neutral';
    const dirArrow = dir === 'bullish' ? '▲' : dir === 'bearish' ? '▼' : '—';
    const dirLabel = dir ? dir.toUpperCase() : '—';
    const edges = (s.edges_contributing || []).map(e => {
      const cls = e.includes('triage') ? 'triage' : 'delta';
      const label = e.includes('triage') ? 'TRIAGE' : 'DELTA';
      return `<span class="edge-badge ${cls}">${label}</span>`;
    }).join('');

    return `<tr${highlight ? ' class="shadow-highlight"' : ''}>
      <td class="entity-cell">${esc(s.entity_id)}</td>
      <td>
        <div class="shadow-bar-wrap">
          <span class="shadow-value" style="color:var(--accent)">${orig.toFixed(2)}</span>
          <div class="shadow-bar"><div class="shadow-bar-fill live" style="width:${Math.min(100, orig * 100)}%"></div></div>
        </div>
      </td>
      <td>
        <div class="shadow-bar-wrap">
          <span class="shadow-value" style="color:var(--purple)">${shadowVal.toFixed(2)}</span>
          <div class="shadow-bar"><div class="shadow-bar-fill shadow" style="width:${Math.min(100, shadowVal * 100)}%"></div></div>
        </div>
      </td>
      <td><span class="${deltaClass}" style="font-family:var(--font-mono);font-weight:700">${scoreDelta > 0 ? '+' : ''}${scoreDelta.toFixed(2)}</span></td>
      <td><span class="direction-badge ${dirClass}">${dirArrow} ${dirLabel}</span></td>
      <td>${edges}</td>
    </tr>`;
  }).join('');
}

function renderFilingPipeline(data) {
  const tbody = document.getElementById('filing-tbody');
  const countEl = document.getElementById('filing-count');
  const summaryEl = document.getElementById('filing-summary');

  if (!data) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">⚠ Unable to load</td></tr>';
    summaryEl.innerHTML = '';
    return;
  }

  const fp = data.filing_pairs || {};
  const total = fp.total || 0;
  const byType = fp.by_form_type || {};
  const recent = fp.recent || [];
  countEl.textContent = total;

  summaryEl.innerHTML = `<span><span class="count-accent">${total}</span> Filing Pairs</span>` +
    Object.entries(byType).map(([k, v]) => `<span>${v} ${esc(k)}</span>`).join('');

  if (!recent.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">Filing pairs will appear as sequential filings are processed.</td></tr>';
    return;
  }

  tbody.innerHTML = recent.map(p => {
    const pending = p.sections_pending;
    const sectionsHtml = pending
      ? '<span class="section-tag pending">⏳ pending</span>'
      : '—';

    return `<tr>
      <td class="entity-cell">${esc(p.entity_id)}</td>
      <td>${esc(p.form_type)}</td>
      <td style="font-family:var(--font-mono);font-size:0.72rem">${esc(p.current_date) || '—'}</td>
      <td style="font-family:var(--font-mono);font-size:0.72rem">${esc(p.previous_date) || '—'}</td>
      <td>${sectionsHtml}</td>
    </tr>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Auto-refresh
// ---------------------------------------------------------------------------

function startAutoRefresh() {
  stopAutoRefresh();
  const checkbox = document.getElementById('auto-refresh');
  if (checkbox && checkbox.checked) {
    refreshTimer = setInterval(loadAllData, CONFIG.refreshIntervalMs);
  }
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function esc(str) {
  if (!str) return '';
  const el = document.createElement('span');
  el.textContent = String(str);
  return el.innerHTML;
}

function shortId(id) {
  if (!id) return '—';
  if (id.length <= 16) return id;
  return id.slice(0, 8) + '...' + id.slice(-4);
}

function formatNum(n) {
  if (n == null || n < 0) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toLocaleString();
}

function formatTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    if (diff < 60_000) return 'just now';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return iso.slice(0, 16);
  }
}

function scoreToColor(score) {
  if (score >= 0.7) return 'var(--green)';
  if (score >= 0.4) return 'var(--amber)';
  return 'var(--red)';
}

function formatPct(val) {
  if (val == null) return '<span class="pct-null">—</span>';
  const sign = val >= 0 ? '+' : '';
  const cls = val > 0 ? 'pct-positive' : val < 0 ? 'pct-negative' : 'pct-null';
  return `<span class="${cls}">${sign}${val.toFixed(2)}%</span>`;
}

function formatDollar(val) {
  if (val == null) return '—';
  return '$' + Number(val).toFixed(2);
}

function formatVolume(val) {
  if (val == null || val < 0) return '<span class="pct-null">—</span>';
  const warn = val < 100_000;
  let str;
  if (val >= 1_000_000) str = '$' + (val / 1_000_000).toFixed(1) + 'M';
  else if (val >= 1_000) str = '$' + (val / 1_000).toFixed(0) + 'K';
  else str = '$' + val.toFixed(0);
  return warn ? `<span class="addv-warn">${str}</span>` : str;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('login-form').addEventListener('submit', handleLogin);
  document.getElementById('logout-btn').addEventListener('click', handleLogout);
  document.getElementById('auto-refresh').addEventListener('change', (e) => {
    if (e.target.checked) {
      startAutoRefresh();
    } else {
      stopAutoRefresh();
    }
  });

  initAuth();
});
