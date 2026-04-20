/* ALTA Playoff Optimizer — Static Frontend (no backend) */

// ── State ──
let teamData = null;

const state = {
  roster: [],
  rosterRendered: false,
  mode: 'balanced',
  topN: 3,
  unavailable: new Set(),
  helpMessages: [
    { type: 'answer', text: 'Welcome! Ask me anything about ALTA rules, lineup strategy, or how to use this tool.\n\nTry: "checker number", "elo", "modes", "2/3 rule", or just press Enter to see all topics.' },
  ],
  lineups: null,
};

// ── Load data.json once ──
async function loadData() {
  const resp = await fetch('static/data.json');
  teamData = await resp.json();
}

function buildAgent(opts = {}) {
  const { mode = 'balanced', topN = 3, unavailable = [] } = opts;
  const agent = new PlayoffLineupAgent(teamData, { topN, mode });
  agent.phase1DataIngestion();
  for (const name of unavailable) {
    if (agent.players[name]) agent.players[name].available = false;
  }
  agent.phase2StrengthAnalysis();
  agent.phase3LineupGeneration();
  agent.phase4Optimization();
  return agent;
}

function buildRoster() {
  const agent = buildAgent();
  const players = Object.values(agent.players)
    .sort((a, b) => a.strength_number - b.strength_number);
  return players.map(p => ({
    name: p.name,
    strength_number: p.strength_number,
    win_rate: p.win_rate,
    total_matches: p.total_matches,
    elo_rating: Math.round(p.elo_rating * 10) / 10,
    elo_rd: Math.round(p.elo_rd * 10) / 10,
    confidence: Math.round(p.confidence * 100) / 100,
    available: p.available,
    eligible_lines: p.eligible_lines,
  }));
}

// ── Navigation ──
function switchPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(`panel-${name}`).classList.add('active');
  document.querySelector(`[data-panel="${name}"]`).classList.add('active');
  if (name === 'roster' && !state.rosterRendered) loadRoster();
}

// ── Team Header ──
function loadTeam() {
  const team = teamData.team || {};
  document.getElementById('team-name').textContent = team.name || 'Unknown';
  document.getElementById('team-league').textContent = team.league || '';
  document.getElementById('team-rank').textContent = `#${team.division_rank || '?'}`;
  document.getElementById('team-record').textContent = team.division_record || '?';
  document.getElementById('team-roster-size').textContent = (teamData.players || []).length;
}

// ── Roster ──
function loadRoster() {
  const el = document.getElementById('roster-body');
  el.innerHTML = '<tr><td colspan="9"><div class="loading-overlay"><span class="spinner"></span> Computing roster...</div></td></tr>';

  setTimeout(() => {
    if (state.roster.length === 0) state.roster = buildRoster();
    state.rosterRendered = true;
    renderRoster();
  }, 10);
}

function renderRoster() {
  const el = document.getElementById('roster-body');
  el.innerHTML = state.roster.map((p, i) => `
    <tr>
      <td>${i + 1}</td>
      <td><strong>${p.name}</strong></td>
      <td class="num">${p.elo_rating.toFixed(0)}</td>
      <td class="num">±${p.elo_rd.toFixed(0)}</td>
      <td class="num">${p.strength_number.toFixed(2)}</td>
      <td class="num">${(p.win_rate * 100).toFixed(0)}%</td>
      <td class="num">${p.total_matches}</td>
      <td>${confBadge(p.confidence)}</td>
      <td>${p.eligible_lines.map(l => 'L' + l).join(', ')}</td>
    </tr>`).join('');
}

function confBadge(c) {
  const cls = c >= 0.8 ? 'badge-green' : c >= 0.5 ? 'badge-yellow' : 'badge-red';
  const label = c >= 0.8 ? 'HIGH' : c >= 0.5 ? 'MED' : 'LOW';
  return `<span class="badge ${cls}">${label} ${(c * 100).toFixed(0)}%</span>`;
}

// ── Generate Lineup ──
function generateLineup() {
  const btn = document.getElementById('btn-generate');
  const container = document.getElementById('lineup-results');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Optimizing...';
  container.innerHTML = '<div class="loading-overlay"><span class="spinner"></span> Running 5-phase optimizer with Elo ratings...</div>';

  state.mode = document.getElementById('sel-mode').value;
  state.topN = parseInt(document.getElementById('sel-topn').value);

  setTimeout(() => {
    try {
      const agent = buildAgent({
        mode: state.mode,
        topN: state.topN,
        unavailable: [...state.unavailable],
      });

      btn.disabled = false;
      btn.innerHTML = '🎾 Generate Lineup';

      if (!agent.legalLineups.length) {
        state.lineups = { error: 'No legal lineups found', lineups: [] };
        renderLineups(state.lineups);
        return;
      }

      const top = agent.legalLineups.slice(0, state.topN);
      const data = {
        lineups: top.map((lu, i) => lineupToDict(lu, i + 1)),
        total_found: agent.legalLineups.length,
        mode: state.mode,
      };

      if (top.length >= 2 && top[0].decision_trace) {
        data.comparison = LineupExplainer.explainComparison(top[0], top[1], 1, 2);
      }

      state.lineups = data;
      renderLineups(data);
    } catch (e) {
      btn.disabled = false;
      btn.innerHTML = '🎾 Generate Lineup';
      container.innerHTML = `<div class="card"><p style="color:var(--red)">❌ Error: ${escHtml(e.message)}</p></div>`;
    }
  }, 20);
}

function lineupToDict(lineup, rank) {
  return {
    rank,
    team_win_probability: Math.round(lineup.team_win_probability * 10000) / 10000,
    performance_score: Math.round(lineup.performance_score * 10000) / 10000,
    confidence: Math.round(lineup.confidence * 1000) / 1000,
    pairings: lineup.pairings.map((p, i) => ({
      line: i + 1,
      player_a: p.player_a,
      player_b: p.player_b,
      checker_number: Math.round(p.checker_number * 100) / 100,
      estimated_win_prob: Math.round(p.estimated_win_prob * 1000) / 1000,
      chemistry_score: Math.round(p.chemistry_score * 1000) / 1000,
      times_played_together: p.times_played_together,
      record_together: p.record_together,
    })),
    decision_trace: lineup.decision_trace || [],
    explanation: lineup.decision_trace ? LineupExplainer.explainLineup(lineup, rank) : '',
  };
}

function renderLineups(data) {
  const container = document.getElementById('lineup-results');
  if (!data.lineups || data.lineups.length === 0) {
    container.innerHTML = '<div class="card"><p style="color:var(--red)">❌ No legal lineups found. Check availability and eligibility rules.</p></div>';
    return;
  }

  let html = `<p style="color:var(--text-dim);margin-bottom:16px;font-size:13px">Found ${data.total_found.toLocaleString()} legal lineups · Showing top ${data.lineups.length} · Mode: ${data.mode}</p>`;
  data.lineups.forEach(lu => { html += renderOneLineup(lu); });

  if (data.comparison) {
    html += `<div class="card"><h3>📊 Why #1 Over #2</h3><div style="font-family:var(--mono);font-size:12px;white-space:pre-wrap;color:var(--text-dim);line-height:1.8">${escHtml(data.comparison)}</div></div>`;
  }
  container.innerHTML = html;
}

function renderOneLineup(lu) {
  const winPct = (lu.team_win_probability * 100).toFixed(1);
  const confPct = (lu.confidence * 100).toFixed(0);
  const explainId = `explain-${lu.rank}`;

  let html = '<div class="lineup-card">';
  html += `<div class="lineup-header">
    <div>
      <span class="lineup-rank">Lineup #${lu.rank}</span>
      <span class="badge ${lu.confidence >= 0.8 ? 'badge-green' : lu.confidence >= 0.5 ? 'badge-yellow' : 'badge-red'}" style="margin-left:8px">Conf: ${confPct}%</span>
    </div>
    <div style="text-align:right">
      <div class="lineup-win">${winPct}%</div>
      <div class="lineup-win-label">Team Win Prob</div>
    </div>
  </div>`;

  html += '<div class="lineup-body">';
  lu.pairings.forEach(p => {
    const wpClass = p.estimated_win_prob >= 0.55 ? 'wp-high' : p.estimated_win_prob >= 0.45 ? 'wp-mid' : 'wp-low';
    const chem = p.times_played_together > 0 ? p.record_together : 'new pair';
    html += `<div class="line-row">
      <span class="line-num">L${p.line}</span>
      <span class="line-player">${p.player_a}</span>
      <span class="line-player">${p.player_b}</span>
      <span class="line-checker">chk ${p.checker_number.toFixed(1)} · ${chem}</span>
      <span class="line-winprob ${wpClass}">${(p.estimated_win_prob * 100).toFixed(0)}%</span>
    </div>`;
  });
  html += '</div>';

  if (lu.explanation) {
    html += `<div class="explain-toggle" onclick="toggleExplain('${explainId}')">
      <span id="${explainId}-arrow">▶</span> SLM Explanation — Why this lineup?
    </div>
    <div class="explain-content" id="${explainId}">${escHtml(lu.explanation)}</div>`;
  }

  html += '</div>';
  return html;
}

function toggleExplain(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  el.classList.toggle('open');
  arrow.textContent = el.classList.contains('open') ? '▼' : '▶';
}

// ── Availability ──
function loadAvailability() {
  if (state.roster.length === 0) state.roster = buildRoster();
  renderAvailability();
}

function renderAvailability() {
  const grid = document.getElementById('avail-grid');
  grid.innerHTML = state.roster.map(p => {
    const off = state.unavailable.has(p.name);
    return `<div class="avail-toggle ${off ? 'unavailable' : ''}" onclick="toggleAvail('${escAttr(p.name)}')">
      <span class="avail-dot"></span>
      ${p.name}
    </div>`;
  }).join('');
  document.getElementById('avail-count').textContent =
    `${state.roster.length - state.unavailable.size} available, ${state.unavailable.size} out`;
}

function toggleAvail(name) {
  if (state.unavailable.has(name)) state.unavailable.delete(name);
  else state.unavailable.add(name);
  renderAvailability();
}

function resetAvailability() {
  state.unavailable.clear();
  renderAvailability();
}

// ── What-If ──
function loadWhatIf() {
  if (state.roster.length === 0) state.roster = buildRoster();
  renderWhatIfPlayers();
}

function renderWhatIfPlayers() {
  const grid = document.getElementById('whatif-players');
  grid.innerHTML = state.roster.map(p =>
    `<label class="avail-toggle" style="cursor:pointer">
      <input type="checkbox" value="${escAttr(p.name)}" style="accent-color:var(--red)"> ${p.name}
    </label>`
  ).join('');
}

function runWhatIf() {
  const btn = document.getElementById('btn-whatif');
  const container = document.getElementById('whatif-results');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Comparing...';

  setTimeout(() => {
    try {
      const checked = [...document.querySelectorAll('#whatif-players input:checked')].map(cb => cb.value);
      const scenarioUnavail = [...state.unavailable, ...checked];

      const baseAgent = buildAgent({ mode: state.mode, topN: 1, unavailable: [...state.unavailable] });
      const scenarioAgent = buildAgent({ mode: state.mode, topN: 1, unavailable: scenarioUnavail });

      const baseWp = baseAgent.legalLineups.length ? baseAgent.legalLineups[0].team_win_probability : 0;
      const scenWp = scenarioAgent.legalLineups.length ? scenarioAgent.legalLineups[0].team_win_probability : 0;
      const impact = scenWp - baseWp;

      btn.disabled = false;
      btn.innerHTML = '⚡ Run Scenario';

      const cls = impact > 0.005 ? 'positive' : impact < -0.005 ? 'negative' : 'neutral';
      const sign = impact >= 0 ? '+' : '';

      let html = `<div class="impact-box ${cls}">
        <div class="impact-value">${sign}${(impact * 100).toFixed(1)}%</div>
        <div class="impact-label">Impact on Team Win Probability</div>
        <div style="margin-top:12px;font-size:13px;color:var(--text-dim)">
          Baseline: ${(baseWp * 100).toFixed(1)}% → Scenario: ${(scenWp * 100).toFixed(1)}%
        </div>
      </div>`;

      if (scenarioAgent.legalLineups.length) {
        html += '<h3 style="margin:16px 0 8px">Best Lineup Under Scenario</h3>';
        html += renderOneLineup(lineupToDict(scenarioAgent.legalLineups[0], 1));
      }

      container.innerHTML = html;
    } catch (e) {
      btn.disabled = false;
      btn.innerHTML = '⚡ Run Scenario';
      container.innerHTML = `<div class="card"><p style="color:var(--red)">❌ Error: ${escHtml(e.message)}</p></div>`;
    }
  }, 20);
}

// ── Floating Chat Widget ──
let chatOpen = false;

function toggleChat() {
  chatOpen = !chatOpen;
  const widget = document.getElementById('chat-widget');
  const fab = document.getElementById('chat-fab-icon');
  widget.classList.toggle('open', chatOpen);
  fab.textContent = chatOpen ? '✕' : '💬';
  if (chatOpen) {
    renderChat();
    document.getElementById('chat-input').focus();
  }
}

function openChat() {
  if (!chatOpen) toggleChat();
}

function chatSend() {
  const input = document.getElementById('chat-input');
  const q = input.value.trim();
  if (!q) return;
  input.value = '';

  state.helpMessages.push({ type: 'question', text: q });
  renderChat();

  const answer = ALTAHelpSystem.answer(q);
  state.helpMessages.push({ type: 'answer', text: answer });
  renderChat();
}

function renderChat() {
  const container = document.getElementById('chat-messages');
  container.innerHTML = state.helpMessages
    .map(m => `<div class="help-msg ${m.type}">${escHtml(m.text)}</div>`)
    .join('');
  container.scrollTop = container.scrollHeight;
}

// Backward compat
function askQuestion() { chatSend(); }
function renderHelp() { renderChat(); }
function helpKeydown(e) { if (e.key === 'Enter') chatSend(); }

// ── Utils ──
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function escAttr(s) {
  return s.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// ── Auth ──
const AUTH_USERNAME = 'admin';
const AUTH_PASSWORD = '123';

function handleLogin(e) {
  e.preventDefault();
  const username = document.getElementById('login-username').value;
  const password = document.getElementById('login-password').value;
  if (username === AUTH_USERNAME && password === AUTH_PASSWORD) {
    sessionStorage.setItem('logged_in', 'true');
    showApp();
  } else {
    document.getElementById('login-error').classList.add('visible');
  }
  return false;
}

function handleLogout() {
  sessionStorage.removeItem('logged_in');
  showLogin();
}

function showApp() {
  document.getElementById('login-overlay').style.display = 'none';
  document.getElementById('app-container').classList.remove('hidden');
  initApp();
}

function showLogin() {
  document.getElementById('login-overlay').style.display = 'flex';
  document.getElementById('app-container').classList.add('hidden');
  document.getElementById('login-error').classList.remove('visible');
  document.getElementById('login-username').value = '';
  document.getElementById('login-password').value = '';
}

async function initApp() {
  try {
    await loadData();
    loadTeam();
    switchPanel('generate');
    renderChat();
    setTimeout(() => { state.roster = buildRoster(); }, 100);
  } catch (e) {
    document.querySelector('.main').innerHTML =
      `<div class="card"><p style="color:var(--red)">❌ Failed to load data: ${escHtml(e.message)}</p></div>`;
  }
}

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  if (sessionStorage.getItem('logged_in') === 'true') {
    showApp();
  }
});
