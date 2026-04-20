/* ALTA Playoff Optimizer — Frontend Logic */

const API = {
  async get(url) {
    const r = await fetch(url);
    return r.json();
  },
  async post(url, data) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return r.json();
  },
};

// ── State ──
const state = {
  roster: [],
  rosterRendered: false,
  mode: "balanced",
  topN: 3,
  unavailable: new Set(),
  helpMessages: [
    { type: "answer", text: 'Welcome! Ask me anything about ALTA rules, lineup strategy, or how to use this tool.\n\nTry: "checker number", "elo", "modes", "2/3 rule", or just press Enter to see all topics.' },
  ],
  lineups: null,
};

// ── Navigation ──
function switchPanel(name) {
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
  document.getElementById(`panel-${name}`).classList.add("active");
  document.querySelector(`[data-panel="${name}"]`).classList.add("active");

  // Lazy load roster table on first visit
  if (name === "roster" && !state.rosterRendered) loadRoster();
}

// ── Team Header ──
async function loadTeam() {
  const team = await API.get("/api/team");
  document.getElementById("team-name").textContent = team.name;
  document.getElementById("team-league").textContent = team.league;
  document.getElementById("team-rank").textContent = `#${team.division_rank}`;
  document.getElementById("team-record").textContent = team.division_record;
  document.getElementById("team-roster-size").textContent = team.roster_size;
}

// ── Roster ──
async function loadRoster() {
  const el = document.getElementById("roster-body");
  el.innerHTML = `<tr><td colspan="8"><div class="loading-overlay"><span class="spinner"></span> Loading roster...</div></td></tr>`;

  if (state.roster.length === 0) {
    state.roster = await API.get("/api/roster");
  }
  state.rosterRendered = true;
  renderRoster();
}

function renderRoster() {
  const el = document.getElementById("roster-body");
  el.innerHTML = state.roster
    .map(
      (p, i) => `
    <tr>
      <td>${i + 1}</td>
      <td><strong>${p.name}</strong></td>
      <td class="num">${p.elo_rating.toFixed(0)}</td>
      <td class="num">±${p.elo_rd.toFixed(0)}</td>
      <td class="num">${p.strength_number.toFixed(2)}</td>
      <td class="num">${(p.win_rate * 100).toFixed(0)}%</td>
      <td class="num">${p.total_matches}</td>
      <td>${confBadge(p.confidence)}</td>
      <td>${p.eligible_lines.map((l) => `L${l}`).join(", ")}</td>
    </tr>`
    )
    .join("");
}

function confBadge(c) {
  const cls = c >= 0.8 ? "badge-green" : c >= 0.5 ? "badge-yellow" : "badge-red";
  const label = c >= 0.8 ? "HIGH" : c >= 0.5 ? "MED" : "LOW";
  return `<span class="badge ${cls}">${label} ${(c * 100).toFixed(0)}%</span>`;
}

// ── Generate Lineup ──
async function generateLineup() {
  const btn = document.getElementById("btn-generate");
  const container = document.getElementById("lineup-results");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Optimizing...`;
  container.innerHTML = `<div class="loading-overlay"><span class="spinner"></span> Running 5-phase optimizer with Elo ratings...</div>`;

  state.mode = document.getElementById("sel-mode").value;
  state.topN = parseInt(document.getElementById("sel-topn").value);

  const data = await API.post("/api/generate", {
    mode: state.mode,
    top_n: state.topN,
    unavailable: [...state.unavailable],
  });

  btn.disabled = false;
  btn.innerHTML = `🎾 Generate Lineup`;

  state.lineups = data;
  renderLineups(data);
}

function renderLineups(data) {
  const container = document.getElementById("lineup-results");

  if (!data.lineups || data.lineups.length === 0) {
    container.innerHTML = `<div class="card"><p style="color:var(--red)">❌ No legal lineups found. Check availability and eligibility rules.</p></div>`;
    return;
  }

  let html = `<p style="color:var(--text-dim);margin-bottom:16px;font-size:13px">Found ${data.total_found.toLocaleString()} legal lineups · Showing top ${data.lineups.length} · Mode: ${data.mode}</p>`;

  data.lineups.forEach((lu) => {
    html += renderOneLineup(lu);
  });

  if (data.comparison) {
    html += `<div class="card"><h3>📊 Why #1 Over #2</h3><div style="font-family:var(--mono);font-size:12px;white-space:pre-wrap;color:var(--text-dim);line-height:1.8">${escHtml(data.comparison)}</div></div>`;
  }

  container.innerHTML = html;
}

function renderOneLineup(lu) {
  const winPct = (lu.team_win_probability * 100).toFixed(1);
  const confPct = (lu.confidence * 100).toFixed(0);
  const explainId = `explain-${lu.rank}`;

  let html = `<div class="lineup-card">`;
  html += `<div class="lineup-header">
    <div>
      <span class="lineup-rank">Lineup #${lu.rank}</span>
      <span class="badge ${lu.confidence >= 0.8 ? "badge-green" : lu.confidence >= 0.5 ? "badge-yellow" : "badge-red"}" style="margin-left:8px">Conf: ${confPct}%</span>
    </div>
    <div style="text-align:right">
      <div class="lineup-win">${winPct}%</div>
      <div class="lineup-win-label">Team Win Prob</div>
    </div>
  </div>`;

  html += `<div class="lineup-body">`;
  lu.pairings.forEach((p) => {
    const wpClass = p.estimated_win_prob >= 0.55 ? "wp-high" : p.estimated_win_prob >= 0.45 ? "wp-mid" : "wp-low";
    const chem =
      p.times_played_together > 0
        ? `${p.record_together}`
        : "new pair";
    html += `<div class="line-row">
      <span class="line-num">L${p.line}</span>
      <span class="line-player">${p.player_a}</span>
      <span class="line-player">${p.player_b}</span>
      <span class="line-checker">chk ${p.checker_number.toFixed(1)} · ${chem}</span>
      <span class="line-winprob ${wpClass}">${(p.estimated_win_prob * 100).toFixed(0)}%</span>
    </div>`;
  });
  html += `</div>`;

  // Explanation toggle
  if (lu.explanation) {
    html += `<div class="explain-toggle" onclick="toggleExplain('${explainId}')">
      <span id="${explainId}-arrow">▶</span> SLM Explanation — Why this lineup?
    </div>
    <div class="explain-content" id="${explainId}">${escHtml(lu.explanation)}</div>`;
  }

  html += `</div>`;
  return html;
}

function toggleExplain(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById(id + "-arrow");
  el.classList.toggle("open");
  arrow.textContent = el.classList.contains("open") ? "▼" : "▶";
}

// ── Availability ──
async function loadAvailability() {
  if (state.roster.length === 0) {
    state.roster = await API.get("/api/roster");
  }
  renderAvailability();
}

function renderAvailability() {
  const grid = document.getElementById("avail-grid");
  grid.innerHTML = state.roster
    .map((p) => {
      const off = state.unavailable.has(p.name);
      return `<div class="avail-toggle ${off ? "unavailable" : ""}" onclick="toggleAvail('${escAttr(p.name)}')">
      <span class="avail-dot"></span>
      ${p.name}
    </div>`;
    })
    .join("");

  document.getElementById("avail-count").textContent = `${state.roster.length - state.unavailable.size} available, ${state.unavailable.size} out`;
}

function toggleAvail(name) {
  if (state.unavailable.has(name)) {
    state.unavailable.delete(name);
  } else {
    state.unavailable.add(name);
  }
  renderAvailability();
}

function resetAvailability() {
  state.unavailable.clear();
  renderAvailability();
}

// ── What-If ──
async function loadWhatIf() {
  if (state.roster.length === 0) {
    state.roster = await API.get("/api/roster");
  }
  renderWhatIfPlayers();
}

function renderWhatIfPlayers() {
  const grid = document.getElementById("whatif-players");
  grid.innerHTML = state.roster
    .map(
      (p) => `<label class="avail-toggle" style="cursor:pointer">
      <input type="checkbox" value="${escAttr(p.name)}" style="accent-color:var(--red)"> ${p.name}
    </label>`
    )
    .join("");
}

async function runWhatIf() {
  const btn = document.getElementById("btn-whatif");
  const container = document.getElementById("whatif-results");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Comparing...`;

  const checked = [...document.querySelectorAll("#whatif-players input:checked")].map((cb) => cb.value);
  const scenarioUnavail = [...state.unavailable, ...checked];

  const data = await API.post("/api/whatif", {
    mode: state.mode,
    unavailable_base: [...state.unavailable],
    unavailable_scenario: scenarioUnavail,
  });

  btn.disabled = false;
  btn.innerHTML = `⚡ Run Scenario`;

  const impact = data.impact;
  const cls = impact > 0.005 ? "positive" : impact < -0.005 ? "negative" : "neutral";
  const sign = impact >= 0 ? "+" : "";

  let html = `<div class="impact-box ${cls}">
    <div class="impact-value">${sign}${(impact * 100).toFixed(1)}%</div>
    <div class="impact-label">Impact on Team Win Probability</div>
    <div style="margin-top:12px;font-size:13px;color:var(--text-dim)">
      Baseline: ${(data.baseline_win_prob * 100).toFixed(1)}% → Scenario: ${(data.scenario_win_prob * 100).toFixed(1)}%
    </div>
  </div>`;

  if (data.scenario_lineup) {
    html += `<h3 style="margin:16px 0 8px">Best Lineup Under Scenario</h3>`;
    html += renderOneLineup(data.scenario_lineup);
  }

  container.innerHTML = html;
}

// ── Floating Chat Widget ──
let chatOpen = false;

function toggleChat() {
  chatOpen = !chatOpen;
  const widget = document.getElementById("chat-widget");
  const fab = document.getElementById("chat-fab-icon");
  widget.classList.toggle("open", chatOpen);
  fab.textContent = chatOpen ? "✕" : "💬";
  if (chatOpen) {
    renderChat();
    document.getElementById("chat-input").focus();
  }
}

function openChat() {
  if (!chatOpen) toggleChat();
}

async function chatSend() {
  const input = document.getElementById("chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";

  state.helpMessages.push({ type: "question", text: q });
  renderChat();

  const data = await API.post("/api/ask", { question: q });
  state.helpMessages.push({ type: "answer", text: data.answer });
  renderChat();
}

function renderChat() {
  const container = document.getElementById("chat-messages");
  container.innerHTML = state.helpMessages
    .map((m) => `<div class="help-msg ${m.type}">${escHtml(m.text)}</div>`)
    .join("");
  container.scrollTop = container.scrollHeight;
}

// Keep old names for backward compat with tests
function askQuestion() { chatSend(); }
function renderHelp() { renderChat(); }
function helpKeydown(e) { if (e.key === "Enter") chatSend(); }

// ── Utils ──
function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function escAttr(s) {
  return s.replace(/'/g, "\\'").replace(/"/g, "&quot;");
}

// ── Init ──
document.addEventListener("DOMContentLoaded", () => {
  loadTeam();
  switchPanel("generate");
  renderChat();

  // Pre-load roster in background
  API.get("/api/roster").then((r) => {
    state.roster = r;
  });
});
