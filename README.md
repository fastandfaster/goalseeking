# ALTA Playoff Lineup Optimizer

A **Goal-Seeking Agent** that generates optimal, ALTA-legal playoff lineups for **Sunday Women's Doubles** teams.

> **Configured for:** 2026 Spring Sunday Women C-8
> - 5 lines of doubles (10 players per match)
> - Matches: Sundays at 1:00 PM
> - Regular Season: Mar 16 – Apr 27 (7 weeks)
> - Playoffs: May 3 | C-Flight City Finals: May 18

## How It Works (Goal-Seeking Agent Pattern)

This tool follows the [Goal-Seeking Agent Pattern](https://mcpmarket.com/tools/skills/goal-seeking-agent-pattern) — an autonomous AI design pattern where the agent pursues a high-level objective through flexible, multi-phase execution with self-recovery.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  GOAL: Maximize team match win probability with legal lineup    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Phase 1: Data Ingestion ──► Phase 2: Strength Analysis         │
│       │                           │                             │
│       ▼                           ▼                             │
│  Phase 3: Constraint-Aware Lineup Generation                    │
│       │         │                                               │
│       │    [recovery: relax constraints if no legal lineups]    │
│       ▼                                                         │
│  Phase 4: Performance Scoring & Optimization                    │
│       │                                                         │
│       ▼                                                         │
│  Phase 5: Validation & Recommendations                          │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  TWO SEPARATE MODELS:                                           │
│    • Checker Model (legality) — ALTA ascending-order rules      │
│    • Performance Model (strategy) — win probability estimation  │
└─────────────────────────────────────────────────────────────────┘
```

### Why Goal-Seeking Fits This Problem

| Question | Answer |
|----------|--------|
| Well-defined objective, flexible path? | ✅ Optimize lineup — many valid pairings exist |
| Multiple phases with dependencies? | ✅ 5 phases, each builds on the previous |
| Autonomous recovery valuable? | ✅ Illegal lineup → try alternatives automatically |
| Context affects approach? | ✅ Player availability, opponent, matchup history |
| Complexity justified? | ✅ Playoffs are high-stakes, rules are complex |

## Quick Start

```bash
# 1. Run with your team data
python lineup_optimizer.py alta_team_data.json

# 2. Interactive playoff manager (recommended)
python lineup_optimizer.py alta_team_data.json -i

# 3. Full playoff plan (all rounds with DP)
python lineup_optimizer.py alta_team_data.json --playoff-rounds 4

# 4. Show Elo-based explanations
python lineup_optimizer.py alta_team_data.json --explain --top 2

# 5. Options
python lineup_optimizer.py my_team.json --top 5          # show top 5 lineups
python lineup_optimizer.py my_team.json --mode aggressive # maximize upset potential
python lineup_optimizer.py my_team.json --mode conservative # maximize floor
```

## Features

### Elo Rating System
- Line-adjusted Elo ratings (L1 opponents rated 1600, L5 rated 1400)
- High K-factor (K=40) for fast adaptation with small sample sizes
- Rating deviation (±RD) models uncertainty — fewer matches = wider uncertainty
- Win probability computed via Elo formula, not hand-coded heuristics

### Interactive Playoff Manager (`-i`)
Menu-driven UX with 9 options:
1. **View Roster & Elo** — Player strengths, ratings, eligibility
2. **Generate Lineup** — Optimal lineup for a single round
3. **Plan All Rounds** — DP-optimized multi-round plan with movement rules
4. **Update Availability** — Per-round player availability
5. **What-If Scenario** — Test "what if player X is out?" scenarios
6. **Compare Runs** — Side-by-side comparison of saved lineups
7. **Settings** — Mode, top-N, forced/excluded pairs
8. **Reload Data** — Re-read JSON after new match results
9. **Ask a Question** — SLM-powered help for ALTA rules, strategy, and usage

### SLM Knowledge Engine
Built-in help system that explains:
- **Lineup decisions**: Per-line Elo analysis, chemistry, confidence factors
- **ALTA rules**: Checker numbers, 2/3 eligibility, movement constraints
- **Strategy**: When to use aggressive/conservative mode, how Elo works
- **Tool usage**: How to use each feature, CLI flags, data management

Uses real decision traces from the optimizer (not reconstructed explanations) for lineup reasoning.

## Data File Format

Edit `sample_data.json` with your team's data. Key sections:

### Team Info
```json
{
  "team": {
    "name": "Your Team Name",
    "facility": "Your Tennis Center",
    "league": "Women's Doubles",
    "num_lines": 5
  }
}
```

### Players & Match History
```json
{
  "players": [
    {
      "name": "Jane Smith",
      "available_for_playoffs": true,
      "regular_season": [
        { "match_date": "2026-01-15", "line": 1, "partner": "Mary Jones", "result": "W", "score": "6-3, 6-4" }
      ]
    }
  ]
}
```

### Captain Overrides
```json
{
  "captain_overrides": {
    "forced_pairs": [["Jane", "Mary"]],
    "excluded_pairs": [["Jane", "Sue"]],
    "optimization_mode": "balanced"
  }
}
```

## How to Get Your Data from ALTA

1. Log into [ALTA Member Portal](https://www.altatennis.org/Member/Dashboard.aspx)
2. Navigate to **Team Dashboard** → your Sunday Women C-8 team
3. Go to your team's **schedule/match results**
4. For each week (Mar 16 – Apr 27), for each player, record:
   - **Date** (e.g., 2026-03-16)
   - **Line number** (1-5)
   - **Partner name**
   - **Result** (W or L)
   - **Score** (e.g., 6-3, 6-4)
5. Note which players are **available** for May 3 playoff and beyond
6. Replace the sample players in `sample_data.json` with your real data
7. Run: `python lineup_optimizer.py sample_data.json`

> **Tip:** The `sample_data.json` file has a 12-player roster template matching
> the Sunday Women minimum. Just replace "Player 1" etc. with real names and
> fill in actual match results.

## Optimization Modes

| Mode | Strategy | Best For |
|------|----------|----------|
| `balanced` | Maximize expected team match wins | Default — most matches |
| `conservative` | Maximize worst-case performance | When you're the favorite |
| `aggressive` | Maximize upset potential | When you're the underdog |

## What the Agent Does

1. **Validates** your roster and flags issues (not enough players, missing data)
2. **Calculates** ALTA checker numbers from regular season line history
3. **Generates** all legal lineups (ascending checker order, each player once)
4. **Scores** lineups using a separate performance model (win rates + chemistry)
5. **Recommends** the best lineup with confidence scores and explanations
6. **Recovers** automatically when things go wrong (relaxes constraints, adapts lines)

## ALTA Rules Implemented

- ✅ **2/3 Eligibility Rule** — players can only play a playoff line if ≥2/3 of their season matches were at that line or higher
- ✅ Checker numbers (partner strength sum) in ascending order
- ✅ Each player used exactly once per lineup
- ✅ Only available, eligible players
- ✅ Captain forced/excluded pairs
- ✅ Adaptive line count when roster is short
- ✅ Sunday Women format (5 lines, 12+ player roster)

## Limitations

- **Data quality**: Results are only as good as your input data
- **Low confidence**: With few matches, estimates have high uncertainty
- **Opponent analysis**: Currently optimizes based on your team's data only
- **Always verify**: Run your final lineup through ALTA's official Lineup Checker!
