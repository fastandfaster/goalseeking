"""
ALTA Playoff Lineup Optimizer — Web UI
Flask backend serving REST API + SPA frontend.
"""

import json
import sys
import os

# Prevent lineup_optimizer's stdout reconfigure from breaking Flask
_orig_reconfigure = getattr(sys.stdout, 'reconfigure', None)
sys.stdout.reconfigure = lambda **kw: None

from flask import Flask, render_template, jsonify, request, session
from lineup_optimizer import (
    PlayoffLineupAgent, EloEngine, LineupExplainer, ALTAHelpSystem,
    Player, Pairing, Lineup
)

# Restore
if _orig_reconfigure:
    sys.stdout.reconfigure = _orig_reconfigure

app = Flask(__name__)
app.secret_key = os.urandom(24)

DATA_FILE = "alta_team_data.json"

# ── Helpers ──

def _load_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_agent(data=None, mode="balanced", top_n=3, unavailable=None):
    """Build a fresh agent, run all phases, return it."""
    agent = PlayoffLineupAgent(DATA_FILE, top_n=top_n, mode=mode)

    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        # Run phase 1 first to populate players
        agent.phase1_data_ingestion()

        # Apply availability overrides AFTER players are loaded
        if unavailable:
            for name in unavailable:
                if name in agent.players:
                    agent.players[name].available = False

        # Run remaining phases
        agent.phase2_strength_analysis()
        agent.phase3_lineup_generation()
        agent.phase4_optimization()
        agent.phase5_reporting()
    finally:
        sys.stdout = old_stdout

    return agent


def _player_to_dict(p: Player) -> dict:
    return {
        "name": p.name,
        "strength_number": p.strength_number,
        "win_rate": p.win_rate,
        "total_matches": p.total_matches,
        "elo_rating": round(p.elo_rating, 1),
        "elo_rd": round(p.elo_rd, 1),
        "confidence": round(p.confidence, 2),
        "available": p.available,
        "eligible_lines": p.eligible_lines,
    }


def _pairing_to_dict(p: Pairing, idx: int) -> dict:
    return {
        "line": idx + 1,
        "player_a": p.player_a,
        "player_b": p.player_b,
        "checker_number": round(p.checker_number, 2),
        "estimated_win_prob": round(p.estimated_win_prob, 3),
        "chemistry_score": round(p.chemistry_score, 3),
        "times_played_together": p.times_played_together,
        "record_together": p.record_together,
    }


def _lineup_to_dict(lineup: Lineup, rank: int) -> dict:
    return {
        "rank": rank,
        "team_win_probability": round(lineup.team_win_probability, 4),
        "performance_score": round(lineup.performance_score, 4),
        "confidence": round(lineup.confidence, 3),
        "pairings": [_pairing_to_dict(p, i) for i, p in enumerate(lineup.pairings)],
        "decision_trace": lineup.decision_trace or [],
        "explanation": LineupExplainer.explain_lineup(lineup, rank) if lineup.decision_trace else "",
    }


# ── Routes ──

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/team")
def api_team():
    data = _load_data()
    team = data.get("team", {})
    return jsonify({
        "name": team.get("name", "Unknown"),
        "league": team.get("league", ""),
        "division_rank": team.get("division_rank", "?"),
        "division_record": team.get("division_record", "?"),
        "num_lines": team.get("num_lines", 5),
        "roster_size": len(data.get("players", [])),
    })


@app.route("/api/roster")
def api_roster():
    agent = _build_agent()
    players = sorted(agent.players.values(), key=lambda p: p.strength_number)
    return jsonify([_player_to_dict(p) for p in players])


@app.route("/api/generate", methods=["POST"])
def api_generate():
    body = request.get_json(force=True) or {}
    mode = body.get("mode", "balanced")
    top_n = min(body.get("top_n", 3), 10)
    unavailable = body.get("unavailable", [])

    agent = _build_agent(mode=mode, top_n=top_n, unavailable=unavailable)

    if not agent.legal_lineups:
        return jsonify({"error": "No legal lineups found", "lineups": []})

    top = agent.legal_lineups[:top_n]
    result = {
        "lineups": [_lineup_to_dict(lu, i + 1) for i, lu in enumerate(top)],
        "total_found": len(agent.legal_lineups),
        "mode": mode,
    }

    # Comparison if we have 2+
    if len(top) >= 2 and top[0].decision_trace:
        result["comparison"] = LineupExplainer.explain_comparison(top[0], top[1], 1, 2)

    return jsonify(result)


@app.route("/api/ask", methods=["POST"])
def api_ask():
    body = request.get_json(force=True) or {}
    question = body.get("question", "")
    answer = ALTAHelpSystem.answer(question)
    return jsonify({"question": question, "answer": answer})


@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    body = request.get_json(force=True) or {}
    mode = body.get("mode", "balanced")
    top_n = body.get("top_n", 1)
    unavailable_base = body.get("unavailable_base", [])
    unavailable_scenario = body.get("unavailable_scenario", [])

    # Baseline
    base_agent = _build_agent(mode=mode, top_n=1, unavailable=unavailable_base)
    # Scenario
    scenario_agent = _build_agent(mode=mode, top_n=1, unavailable=unavailable_scenario)

    base_wp = base_agent.legal_lineups[0].team_win_probability if base_agent.legal_lineups else 0
    scen_wp = scenario_agent.legal_lineups[0].team_win_probability if scenario_agent.legal_lineups else 0

    result = {
        "baseline_win_prob": round(base_wp, 4),
        "scenario_win_prob": round(scen_wp, 4),
        "impact": round(scen_wp - base_wp, 4),
    }

    if scenario_agent.legal_lineups:
        result["scenario_lineup"] = _lineup_to_dict(scenario_agent.legal_lineups[0], 1)
    if base_agent.legal_lineups:
        result["baseline_lineup"] = _lineup_to_dict(base_agent.legal_lineups[0], 1)

    return jsonify(result)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        DATA_FILE = sys.argv[1]
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("RENDER") is None  # disable debug on Render
    print(f"Starting ALTA Optimizer Web UI — data: {DATA_FILE}")
    print(f"Open http://localhost:{port} in your browser")
    app.run(debug=debug, host="0.0.0.0", port=port)
