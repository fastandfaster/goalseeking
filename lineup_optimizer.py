#!/usr/bin/env python3
"""
ALTA Women's Doubles Playoff Lineup Optimizer
==============================================
A Goal-Seeking Agent that autonomously generates optimal, ALTA-legal
playoff lineups for Women's Doubles teams.

Architecture follows the Goal-Seeking Agent Pattern:
  Phase 1: Data Ingestion & Validation
  Phase 2: Strength Analysis (Checker Numbers)
  Phase 3: Constraint-Aware Lineup Generation
  Phase 4: Performance Scoring & Optimization
  Phase 5: Validation, Reporting & Recommendations

Usage:
    python lineup_optimizer.py                    # uses sample_data.json
    python lineup_optimizer.py my_team.json       # uses your data file
    python lineup_optimizer.py my_team.json --top 5 --mode aggressive
"""

import json
import sys
import os
import argparse
from itertools import combinations
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict
import math

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────

@dataclass
class MatchRecord:
    date: str
    line: int
    partner: str
    result: str  # "W" or "L"
    score: str

@dataclass
class Player:
    name: str
    available: bool
    notes: str
    matches: list  # list of MatchRecord
    strength_number: float = 0.0
    win_rate: float = 0.0
    avg_line: float = 0.0
    total_matches: int = 0
    confidence: float = 0.0  # 0-1, how confident we are in this player's rating
    eligible_lines: list = field(default_factory=list)  # lines this player can play in playoffs (2/3 rule)
    elo_rating: float = 1500.0  # Elo rating (higher = stronger)
    elo_rd: float = 350.0       # Rating deviation (uncertainty; shrinks with more matches)

@dataclass
class Pairing:
    player_a: str
    player_b: str
    checker_number: float = 0.0
    chemistry_score: float = 0.0
    estimated_win_prob: float = 0.5
    times_played_together: int = 0
    record_together: str = ""
    confidence: float = 0.0

@dataclass
class Lineup:
    pairings: list  # list of Pairing, index = line number (0-based)
    legality_score: float = 0.0
    performance_score: float = 0.0
    team_win_probability: float = 0.0
    confidence: float = 0.0
    notes: list = field(default_factory=list)
    decision_trace: list = field(default_factory=list)  # per-line scoring trace for explainability

# ─────────────────────────────────────────────────────────────────────
# Elo Rating Engine (Line-Adjusted for Recreational Doubles)
# ─────────────────────────────────────────────────────────────────────

class EloEngine:
    """
    Line-adjusted Elo rating system for ALTA doubles.

    Key design decisions:
      - High K-factor (K=40) for fast adaptation with small sample sizes
      - Line-adjusted opponent baseline: line 1 opponents are assumed stronger
        than line 5 opponents, preventing rating confounding
      - Prior season win% used as a weak initial rating adjustment
      - Rating deviation (RD) tracks uncertainty, shrinking with each match
      - Output is a RELATIVE ranking score, not a calibrated true win probability
    """

    BASE_RATING = 1500.0
    INITIAL_RD = 350.0
    K_FACTOR = 40          # High K for small samples (~5 matches/player)
    RD_DECAY_PER_MATCH = 0.8  # RD shrinks by this factor each match

    # Line-adjusted opponent baselines: stronger opponents at higher lines
    # Line 1 opponents ~ 1600, Line 5 opponents ~ 1400
    LINE_BASELINES = {1: 1600, 2: 1550, 3: 1500, 4: 1450, 5: 1400}

    @classmethod
    def initial_rating_from_prior(cls, last_win_pct: float = None,
                                   alta_value: float = None,
                                   num_lines: int = 5) -> tuple[float, float]:
        """
        Set initial Elo from prior season data (weak prior).
        Returns (rating, rd).
        """
        rating = cls.BASE_RATING
        rd = cls.INITIAL_RD

        if last_win_pct is not None and last_win_pct > 0:
            # Map win% to a capped rating adjustment: 50% -> 0, 90% -> +100, 10% -> -100
            adjustment = (last_win_pct - 50.0) * 2.5
            adjustment = max(-100, min(100, adjustment))
            rating += adjustment
            rd = 300.0  # Slightly lower uncertainty with prior data

        if alta_value is not None and alta_value > 0:
            # ALTA value is line-based (lower = stronger). Use as additional signal.
            # Map: value 1.0 -> +50, value 5.0 -> -50
            center = (num_lines + 1) / 2.0
            adjustment = (center - alta_value) * (100.0 / num_lines)
            adjustment = max(-75, min(75, adjustment))
            rating += adjustment * 0.3  # Weak blend
            rd = min(rd, 310.0)

        return rating, rd

    @classmethod
    def expected_score(cls, rating_a: float, rating_b: float) -> float:
        """Standard Elo expected score: P(A wins)."""
        return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))

    @classmethod
    def update_rating(cls, rating: float, rd: float, expected: float,
                      actual: float) -> tuple[float, float]:
        """Update a single player's rating after a match result."""
        # K scaled by uncertainty: more uncertain players adjust faster
        effective_k = cls.K_FACTOR * (rd / cls.INITIAL_RD)
        effective_k = max(cls.K_FACTOR * 0.5, min(cls.K_FACTOR * 1.5, effective_k))

        new_rating = rating + effective_k * (actual - expected)
        new_rd = rd * cls.RD_DECAY_PER_MATCH
        new_rd = max(50.0, new_rd)  # Floor: never fully certain

        return new_rating, new_rd

    @classmethod
    def compute_ratings(cls, players: dict, num_lines: int = 5):
        """
        Process all match records chronologically and compute Elo ratings.
        Modifies Player objects in-place.
        """
        # Step 1: Set initial ratings from prior data
        for name, player in players.items():
            last_wp = None
            alta_val = None

            # Try to extract prior info
            if player.win_rate > 0 and player.total_matches == 0:
                # Has historical win_rate but no current season matches
                last_wp = player.win_rate * 100.0
            if player.strength_number > 0:
                alta_val = player.strength_number

            rating, rd = cls.initial_rating_from_prior(last_wp, alta_val, num_lines)
            player.elo_rating = rating
            player.elo_rd = rd

        # Step 2: Collect all matches with dates, sort chronologically
        all_events = []
        for name, player in players.items():
            for match in player.matches:
                all_events.append({
                    "player": name,
                    "date": match.date,
                    "line": match.line,
                    "result": match.result,
                    "partner": match.partner,
                })

        all_events.sort(key=lambda e: (e["date"], e["line"]))

        # Step 3: Process matches chronologically
        # Group by (date, line) to find partners playing together
        from itertools import groupby
        processed = set()

        for event in all_events:
            key = (event["player"], event["date"], event["line"])
            if key in processed:
                continue
            processed.add(key)

            player = players[event["player"]]
            line = event["line"]

            # Line-adjusted opponent baseline
            opponent_rating = cls.LINE_BASELINES.get(line, cls.BASE_RATING)

            # For doubles: if partner is known, use combined team rating
            partner_name = event["partner"]
            if partner_name and partner_name in players:
                partner = players[partner_name]
                team_rating = (player.elo_rating + partner.elo_rating) / 2.0
                opponent_team_rating = opponent_rating  # Opponent pair assumed at baseline
            else:
                team_rating = player.elo_rating
                opponent_team_rating = opponent_rating

            expected = cls.expected_score(team_rating, opponent_team_rating)
            actual = 1.0 if event["result"] == "W" else 0.0

            # Update this player's rating
            player.elo_rating, player.elo_rd = cls.update_rating(
                player.elo_rating, player.elo_rd, expected, actual
            )

            # Also update partner if known
            if partner_name and partner_name in players:
                partner_key = (partner_name, event["date"], event["line"])
                if partner_key not in processed:
                    processed.add(partner_key)
                    partner = players[partner_name]
                    partner.elo_rating, partner.elo_rd = cls.update_rating(
                        partner.elo_rating, partner.elo_rd, expected, actual
                    )

        # Step 4: Derive confidence from RD
        for player in players.values():
            # Map RD to 0-1 confidence: RD=350 -> 0.1, RD=50 -> 1.0
            player.confidence = max(0.1, min(1.0, 1.0 - (player.elo_rd - 50) / 350.0))

    @classmethod
    def pair_win_probability(cls, player_a, player_b,
                             opponent_line: int = None,
                             num_lines: int = 5) -> float:
        """
        Estimate win probability for a doubles pair against expected opponents.
        Uses average of both players' Elo as team rating.
        """
        team_rating = (player_a.elo_rating + player_b.elo_rating) / 2.0

        if opponent_line is not None:
            opp_rating = cls.LINE_BASELINES.get(opponent_line, cls.BASE_RATING)
        else:
            opp_rating = cls.BASE_RATING

        return cls.expected_score(team_rating, opp_rating)


# ─────────────────────────────────────────────────────────────────────
# Goal-Seeking Agent
# ─────────────────────────────────────────────────────────────────────

class PlayoffLineupAgent:
    """
    Goal-Seeking Agent for ALTA Playoff Lineup Optimization.

    Goal: Generate the optimal legal lineup that maximizes probability
          of winning the team match (majority of lines).

    Success Criteria:
      - All lineups pass ALTA checker rules (ascending checker numbers)
      - Each player used exactly once
      - Only available players are used
      - Top lineup maximizes expected team match win probability

    Recovery Strategies:
      - If not enough players: flag and suggest minimum viable lineup
      - If all lineups illegal: relax constraints and explain
      - If data sparse: use conservative estimates with uncertainty flags
    """

    def __init__(self, data_path: str, top_n: int = 3, mode: str = "balanced"):
        self.data_path = data_path
        self.top_n = top_n
        self.mode = mode  # "conservative", "balanced", "aggressive"
        self.players: dict[str, Player] = {}
        self.num_lines: int = 5
        self.team_name: str = ""
        self.league_info: dict = {}
        self.captain_overrides: dict = {}
        self.opponent_info: dict = {}
        self.all_pairings: list[Pairing] = []
        self.legal_lineups: list[Lineup] = []
        self.warnings: list[str] = []
        self.phase_results: dict = {}
        self.is_alta_format: bool = False

    def execute(self):
        """Main goal-seeking execution loop."""
        print("=" * 70)
        print("  ALTA PLAYOFF LINEUP OPTIMIZER")
        print("  Goal-Seeking Agent Pattern")
        print("=" * 70)

        phases = [
            ("Phase 1", "Data Ingestion & Validation", self.phase1_data_ingestion),
            ("Phase 2", "Strength Analysis", self.phase2_strength_analysis),
            ("Phase 3", "Lineup Generation", self.phase3_lineup_generation),
            ("Phase 4", "Performance Optimization", self.phase4_optimization),
            ("Phase 5", "Validation & Reporting", self.phase5_reporting),
        ]

        for phase_id, phase_name, phase_fn in phases:
            print(f"\n{'─' * 70}")
            print(f"  {phase_id}: {phase_name}")
            print(f"{'─' * 70}")
            try:
                success = phase_fn()
                status = "✓ COMPLETE" if success else "⚠ PARTIAL"
                self.phase_results[phase_id] = {"name": phase_name, "status": status}
                print(f"\n  [{status}] {phase_name}")
                if not success:
                    print("  Agent adapting strategy...")
            except Exception as e:
                self.phase_results[phase_id] = {"name": phase_name, "status": "✗ FAILED"}
                print(f"\n  [✗ FAILED] {phase_name}: {e}")
                self.warnings.append(f"{phase_id} failed: {e}")
                # Goal-seeking: try to continue with remaining phases
                print("  Agent attempting recovery — continuing with available data...")

        self._print_execution_summary()

    # ─────────────────────────────────────────────────────────────────
    # PHASE 1: Data Ingestion & Validation
    # ─────────────────────────────────────────────────────────────────

    def phase1_data_ingestion(self) -> bool:
        """Load and validate team data from JSON file."""
        if not os.path.exists(self.data_path):
            print(f"  ✗ Data file not found: {self.data_path}")
            return False

        with open(self.data_path, "r") as f:
            data = json.load(f)

        # Detect format: ALTA scraped data has "format" key, template has "team.num_lines"
        self.is_alta_format = "format" in data and "estimated_strength" in str(data.get("players", [{}])[0])

        # Team info
        team = data.get("team", {})
        self.team_name = team.get("name", "Unknown Team")
        if self.is_alta_format:
            fmt = data.get("format", {})
            self.num_lines = fmt.get("lines", 5)
            league = team.get("league", "?")
            print(f"  Team: {self.team_name}")
            print(f"  League: {league}")
            print(f"  Rank: #{team.get('division_rank', '?')} | Record: {team.get('division_record', '?')}")
            print(f"  Sets Won: {team.get('sets_won_pct', '?')}% | Games Won: {team.get('games_won_pct', '?')}%")
            print(f"  Lines: {self.num_lines} | Playoff: {fmt.get('playoff_start', '?')}")
        else:
            self.num_lines = team.get("num_lines", 5)
            print(f"  Team: {self.team_name}")
            print(f"  League: {team.get('league', '?')} | Season: {team.get('season', '?')}")
            print(f"  Flight: {team.get('level_flight', '?')} | Lines: {self.num_lines}")

        self.league_info = team
        self.captain_overrides = data.get("captain_overrides", {})
        self.opponent_info = data.get("opponent", {})

        # Players
        player_data = data.get("players", [])
        if not player_data:
            print("  ✗ No players found in data file!")
            return False

        if self.is_alta_format:
            self._load_alta_players(player_data)
        else:
            self._load_template_players(player_data)

        available = [p for p in self.players.values() if p.available]
        unavailable = [p for p in self.players.values() if not p.available]

        print(f"\n  Roster: {len(self.players)} players total")
        print(f"  Available: {len(available)} | Unavailable: {len(unavailable)}")

        if unavailable:
            for p in unavailable:
                print(f"    ✗ {p.name}: {p.notes or 'unavailable'}")

        # Validation
        needed = self.num_lines * 2
        if len(available) < needed:
            self.warnings.append(
                f"Only {len(available)} available players but need {needed} "
                f"for {self.num_lines} lines. Some lines may need to default."
            )
            print(f"\n  ⚠ WARNING: Need {needed} players, only {len(available)} available!")
            return True

        print(f"  ✓ Sufficient players for {self.num_lines} lines")

        if self.captain_overrides.get("forced_pairs"):
            print(f"  Captain forced pairs: {self.captain_overrides['forced_pairs']}")
        if self.captain_overrides.get("excluded_pairs"):
            print(f"  Captain excluded pairs: {self.captain_overrides['excluded_pairs']}")

        if self.is_alta_format:
            notes = data.get("data_quality_notes", [])
            if notes:
                print(f"\n  Data Quality Notes:")
                for n in notes[:3]:
                    print(f"    ⚠ {n}")

        return True

    def _load_alta_players(self, player_data):
        """Load players from ALTA scraped data format."""
        for p in player_data:
            # Use real ALTA value (checker number) if available
            alta_value = p.get("alta_value")
            strength = alta_value if alta_value else p.get("estimated_strength", 5.5)

            # Build match records from regular_season if available
            matches = []
            for m in p.get("regular_season", []):
                matches.append(MatchRecord(
                    date=m.get("match_date", ""),
                    line=m.get("line", 0),
                    partner=m.get("partner", ""),
                    result=m.get("result", ""),
                    score=m.get("score", "")
                ))

            # Win rate: prefer current season data, fall back to historical
            season_wp = p.get("current_season_win_pct")
            hist_wp = p.get("last_win_pct")
            if season_wp is not None:
                win_rate = season_wp / 100.0
                confidence = min(0.9, 0.4 + len(matches) * 0.1)
            elif hist_wp is not None:
                win_rate = hist_wp / 100.0
                confidence = 0.5
            else:
                win_rate = 0.5
                confidence = 0.2

            # Eligible lines from 2/3 rule (pre-computed or default)
            eligible = p.get("eligible_playoff_lines", list(range(1, self.num_lines + 1)))

            player = Player(
                name=p["name"],
                available=p.get("available_for_playoffs", True),
                notes=p.get("notes", ""),
                matches=matches,
                strength_number=strength,
                win_rate=win_rate,
                avg_line=strength,
                total_matches=len(matches),
                confidence=confidence,
                eligible_lines=eligible,
            )
            self.players[player.name] = player

    def _load_template_players(self, player_data):
        """Load players from template format (with match records)."""
        for p in player_data:
            matches = [
                MatchRecord(
                    date=m.get("match_date", ""),
                    line=m.get("line", 0),
                    partner=m.get("partner", ""),
                    result=m.get("result", ""),
                    score=m.get("score", "")
                )
                for m in p.get("regular_season", [])
            ]
            player = Player(
                name=p["name"],
                available=p.get("available_for_playoffs", True),
                notes=p.get("notes", ""),
                matches=matches,
                total_matches=len(matches),
            )
            self.players[player.name] = player

    # ─────────────────────────────────────────────────────────────────
    # PHASE 2: Strength Analysis (Checker Numbers)
    # ─────────────────────────────────────────────────────────────────

    def phase2_strength_analysis(self) -> bool:
        """
        Calculate each player's strength number for ALTA lineup checker.

        ALTA Checker Model (legality):
          strength_number = weighted average of lines played
          Lower number = stronger player (played higher lines)

        Performance Model (separate from checker):
          win_rate, chemistry scores computed separately
        """
        print("  Computing player strength numbers...")
        print()

        if self.is_alta_format:
            # ALTA format: strengths already pre-computed from real ALTA values or flight data
            has_real_values = any(p.get("alta_value") for p in
                                  json.load(open(self.data_path, "r")).get("players", []))
            if has_real_values:
                print("  Using REAL ALTA checker values from Schedule & Lineup data")
            else:
                print("  Using estimated strength from ALTA flight data")
            print("  (Lower value = stronger player / higher line)")
            print()
        else:
            all_lines = []
            for p in self.players.values():
                if p.matches:
                    all_lines.extend([m.line for m in p.matches])

            # Fallback line for players with no matches
            default_line = max(all_lines) if all_lines else self.num_lines

            for name, player in self.players.items():
                if not player.matches:
                    player.strength_number = float(default_line)
                    player.win_rate = 0.0
                    player.confidence = 0.1
                    self.warnings.append(
                        f"{name}: no match data — assigned conservative strength {default_line}"
                    )
                    continue

                lines = [m.line for m in player.matches]
                n = len(lines)
                if n == 0:
                    player.strength_number = float(default_line)
                    player.confidence = 0.1
                    continue

                weights = [(1.0 + 0.1 * i) for i in range(n)]
                total_weight = sum(weights)
                player.strength_number = sum(l * w for l, w in zip(lines, weights)) / total_weight
                player.avg_line = sum(lines) / n

                wins = sum(1 for m in player.matches if m.result == "W")
                player.win_rate = wins / n if n > 0 else 0.0

                player.confidence = min(1.0, n / 10.0)

        # Print strength table
        sorted_players = sorted(self.players.values(), key=lambda p: p.strength_number)
        print(f"  {'Player':<22} {'Strength':>8} {'Win%':>6} {'Matches':>8} {'Conf':>6} {'Avail':>6}")
        print(f"  {'─' * 62}")
        for p in sorted_players:
            avail = "  ✓" if p.available else "  ✗"
            print(
                f"  {p.name:<22} {p.strength_number:>8.2f} "
                f"{p.win_rate * 100:>5.0f}% {p.total_matches:>8} "
                f"{p.confidence:>5.1f} {avail}"
            )

        # ALTA 2/3 Rule: A player can only play a playoff line if at least 2/3
        # of their regular season matches were at that line or higher (lower number).
        print(f"\n  Checking ALTA 2/3 playoff eligibility rule...")
        
        if self.is_alta_format:
            # Check if eligibility was pre-computed from real data
            has_real_eligibility = any(
                p.eligible_lines != list(range(1, self.num_lines + 1))
                for p in self.players.values()
            )
            if has_real_eligibility:
                print("  ✓ Using 2/3 rule eligibility computed from actual line assignments")
            else:
                print("  ⚠ No individual line data — cannot enforce 2/3 rule precisely")
                print("  → All players marked eligible for all lines")
                print("  → Captain should verify each player's eligibility before submitting")
                for name, player in self.players.items():
                    player.eligible_lines = list(range(1, self.num_lines + 1))
        else:
            for name, player in self.players.items():
                if not player.matches:
                    player.eligible_lines = list(range(1, self.num_lines + 1))
                    continue

                line_counts = defaultdict(int)
                for m in player.matches:
                    line_counts[m.line] += 1
                total = len(player.matches)

                eligible = []
                for target_line in range(1, self.num_lines + 1):
                    matches_at_or_above = sum(
                        count for line, count in line_counts.items() if line <= target_line
                    )
                    if total > 0 and matches_at_or_above / total >= 2 / 3:
                        eligible.append(target_line)
                    elif target_line > max(line_counts.keys()):
                        eligible.append(target_line)

                if not eligible:
                    eligible = [l for l in range(1, self.num_lines + 1)
                                if l >= int(player.avg_line)]
                    if not eligible:
                        eligible = list(range(1, self.num_lines + 1))
                    self.warnings.append(
                        f"{name}: 2/3 rule unclear — allowing lines {eligible}"
                    )

                player.eligible_lines = eligible

        # Print eligibility
        print(f"\n  {'Player':<22} {'Eligible Lines':>20}")
        print(f"  {'─' * 44}")
        for p in sorted_players:
            if p.available:
                lines_str = ", ".join(str(l) for l in p.eligible_lines)
                print(f"  {p.name:<22} {lines_str:>20}")

        # ── Elo Rating Computation ──
        print(f"\n  {'─' * 62}")
        print(f"  ELO PERFORMANCE RATINGS (Line-Adjusted)")
        print(f"  {'─' * 62}")
        print(f"  Using line-adjusted Elo with K={EloEngine.K_FACTOR} (high for small samples)")
        print(f"  Line baselines: {EloEngine.LINE_BASELINES}")

        EloEngine.compute_ratings(self.players, self.num_lines)

        elo_sorted = sorted(self.players.values(), key=lambda p: -p.elo_rating)
        print(f"\n  {'Player':<22} {'Elo':>6} {'±RD':>6} {'Conf':>6} {'Win%':>6} {'Matches':>8}")
        print(f"  {'─' * 62}")
        for p in elo_sorted:
            if p.available:
                print(
                    f"  {p.name:<22} {p.elo_rating:>6.0f} {p.elo_rd:>5.0f} "
                    f"{p.confidence:>5.0%} {p.win_rate * 100:>5.0f}% {p.total_matches:>8}"
                )

        return True

    # ─────────────────────────────────────────────────────────────────
    # PHASE 3: Constraint-Aware Lineup Generation
    # ─────────────────────────────────────────────────────────────────

    def phase3_lineup_generation(self) -> bool:
        """
        Generate all ALTA-legal lineups.

        Constraints:
          1. Only available players
          2. Each player used exactly once per lineup
          3. Exactly num_lines pairs
          4. Checker numbers (sum of partner strengths) in ascending order
          5. Captain overrides (forced/excluded pairs)
        """
        available = [p for p in self.players.values() if p.available]
        needed = self.num_lines * 2

        if len(available) < needed:
            # Recovery: reduce lines if possible
            max_lines = len(available) // 2
            self.warnings.append(
                f"Reducing to {max_lines} lines (only {len(available)} available players)"
            )
            self.num_lines = max_lines
            needed = self.num_lines * 2
            print(f"  ⚠ Adapting: reduced to {self.num_lines} lines")

        if self.num_lines == 0:
            print("  ✗ Cannot form any lines!")
            return False

        available_names = [p.name for p in available]

        # Step 1: Generate all possible pairs
        print(f"  Generating pairs from {len(available_names)} available players...")
        all_pairs = list(combinations(available_names, 2))

        # Apply captain overrides: exclude pairs
        excluded = set()
        for pair in self.captain_overrides.get("excluded_pairs", []):
            excluded.add(tuple(sorted(pair)))

        valid_pairs = []
        for a, b in all_pairs:
            key = tuple(sorted([a, b]))
            if key in excluded:
                print(f"  ✗ Excluded by captain: {a} + {b}")
                continue
            valid_pairs.append((a, b))

        # Step 2: Build pairing objects with checker numbers & chemistry
        pair_chemistry = self._compute_pair_chemistry()

        self.all_pairings = []
        for a, b in valid_pairs:
            sa = self.players[a].strength_number
            sb = self.players[b].strength_number
            checker = sa + sb

            chem_key = tuple(sorted([a, b]))
            chem = pair_chemistry.get(chem_key, {})

            pairing = Pairing(
                player_a=a,
                player_b=b,
                checker_number=checker,
                chemistry_score=chem.get("chemistry", 0.5),
                times_played_together=chem.get("times", 0),
                record_together=chem.get("record", "0-0"),
                confidence=chem.get("confidence", 0.3),
            )
            self.all_pairings.append(pairing)

        print(f"  Total possible pairs: {len(self.all_pairings)}")

        # Sort pairings by checker number ascending so strongest pairs are explored first
        self.all_pairings.sort(key=lambda p: p.checker_number)

        # Step 3: Generate valid lineups (all combinations of num_lines pairs
        #         where each player appears exactly once, checker numbers ascending)
        print(f"  Searching for legal {self.num_lines}-line lineups...")

        forced_pairs = self.captain_overrides.get("forced_pairs", [])
        forced_set = set()
        for pair in forced_pairs:
            forced_set.add(tuple(sorted(pair)))

        # Use recursive search with early pruning
        self.legal_lineups = []
        self._search_lineups([], set(), forced_set, 0.0)

        if not self.legal_lineups:
            # Recovery: try without forced pairs
            if forced_set:
                print("  ⚠ No legal lineups with forced pairs — trying without...")
                self.warnings.append("Captain's forced pairs produced no legal lineups — relaxed")
                self._search_lineups([], set(), set(), 0.0)

        print(f"  ✓ Found {len(self.legal_lineups)} legal lineups")
        return len(self.legal_lineups) > 0

    def _search_lineups(self, current: list, used_players: set,
                        forced_set: set, last_checker: float):
        """Recursive search for legal lineups with early pruning."""
        if len(current) == self.num_lines:
            # Check forced pairs are included
            if forced_set:
                lineup_pairs = set()
                for p in current:
                    lineup_pairs.add(tuple(sorted([p.player_a, p.player_b])))
                if not forced_set.issubset(lineup_pairs):
                    return
            lineup = Lineup(pairings=list(current))
            lineup.legality_score = 1.0
            self.legal_lineups.append(lineup)
            return

        # Cap search to avoid combinatorial explosion
        if len(self.legal_lineups) >= 10000:
            return

        # Filter eligible pairings for this position
        line_num = len(current) + 1
        candidates = []
        for pairing in self.all_pairings:
            if pairing.player_a in used_players or pairing.player_b in used_players:
                continue
            if pairing.checker_number < last_checker:
                continue
            pa = self.players[pairing.player_a]
            pb = self.players[pairing.player_b]
            if line_num not in pa.eligible_lines or line_num not in pb.eligible_lines:
                continue
            candidates.append(pairing)

        # Prioritize pairs with higher Elo-based win potential
        candidates.sort(key=lambda p: -(
            self.players[p.player_a].elo_rating + self.players[p.player_b].elo_rating
        ))

        # Beam limit: only explore top candidates per position to focus on quality
        beam_width = 50 if len(self.legal_lineups) < 1000 else 20
        for pairing in candidates[:beam_width]:
            new_used = used_players | {pairing.player_a, pairing.player_b}
            current.append(pairing)
            self._search_lineups(current, new_used, forced_set, pairing.checker_number)
            current.pop()

    def _compute_pair_chemistry(self) -> dict:
        """Compute chemistry scores for all player pairs based on match history."""
        chemistry = {}
        pair_records = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})

        for player in self.players.values():
            for match in player.matches:
                key = tuple(sorted([player.name, match.partner]))
                if match.result == "W":
                    pair_records[key]["wins"] += 1
                else:
                    pair_records[key]["losses"] += 1
                pair_records[key]["total"] += 1

        for key, record in pair_records.items():
            # Each match is counted twice (once per player), divide by 2
            wins = record["wins"] // 2
            losses = record["losses"] // 2
            total = record["total"] // 2

            if total > 0:
                win_rate = wins / total
                # Chemistry: pairs that win together have better chemistry
                chemistry[key] = {
                    "chemistry": 0.3 + 0.7 * win_rate,  # base 0.3, max 1.0
                    "times": total,
                    "record": f"{wins}-{losses}",
                    "confidence": min(1.0, total / 5.0),
                }

        return chemistry

    # ─────────────────────────────────────────────────────────────────
    # PHASE 4: Performance Scoring & Optimization
    # ─────────────────────────────────────────────────────────────────

    def phase4_optimization(self) -> bool:
        """
        Score each legal lineup and rank them.

        Performance Model: Elo-based win probability
          - Each pair's win probability computed from combined Elo vs
            line-adjusted opponent baseline
          - Team match win probability via DP over independent Bernoulli trials
          - Chemistry bonus from partnership history (reduced weight since
            Elo already captures some partnership signal)

        Optimization Modes:
          - conservative: maximize floor (worst-case lines)
          - balanced: maximize expected team match wins
          - aggressive: maximize ceiling (best-case upset potential)
        """
        if not self.legal_lineups:
            print("  ✗ No legal lineups to score!")
            return False

        print(f"  Scoring {len(self.legal_lineups)} lineups (mode: {self.mode})...")
        print(f"  Using Elo-based win probability model")

        for lineup in self.legal_lineups:
            self._score_lineup(lineup)

        # Sort by team win probability (primary), then performance score (secondary)
        self.legal_lineups.sort(
            key=lambda l: (l.team_win_probability, l.performance_score),
            reverse=True
        )

        # Show top lineups
        top = self.legal_lineups[:self.top_n]
        print(f"\n  Top {len(top)} lineups scored.")
        return True

    def _score_lineup(self, lineup: Lineup):
        """Score a single lineup using Elo-based win probability model."""
        line_win_probs = []
        lineup.decision_trace = []

        for i, pairing in enumerate(lineup.pairings):
            pa = self.players[pairing.player_a]
            pb = self.players[pairing.player_b]
            line_num = i + 1

            # Elo-based win probability against line-adjusted opponent
            team_elo = (pa.elo_rating + pb.elo_rating) / 2.0
            opp_baseline = EloEngine.LINE_BASELINES.get(line_num, 1500)
            base_prob = EloEngine.pair_win_probability(
                pa, pb, opponent_line=line_num, num_lines=self.num_lines
            )

            # Chemistry bonus (reduced weight; Elo captures some of this)
            chem_bonus = (pairing.chemistry_score - 0.5) * 0.08

            # Confidence-weighted regression toward 0.5
            pair_confidence = min(pa.confidence, pb.confidence)
            adjusted_prob = base_prob * pair_confidence + 0.5 * (1 - pair_confidence)
            adjusted_prob += chem_bonus

            pre_mode_prob = adjusted_prob

            # Mode adjustments
            if self.mode == "aggressive":
                deviation = adjusted_prob - 0.5
                adjusted_prob = 0.5 + deviation * 1.2
            elif self.mode == "conservative":
                deviation = adjusted_prob - 0.5
                adjusted_prob = 0.5 + deviation * 0.8

            # Clamp to [0.05, 0.95]
            adjusted_prob = max(0.05, min(0.95, adjusted_prob))

            pairing.estimated_win_prob = adjusted_prob
            line_win_probs.append(adjusted_prob)

            # Decision trace for this line
            lineup.decision_trace.append({
                "line": line_num,
                "player_a": pa.name,
                "player_b": pb.name,
                "elo_a": pa.elo_rating,
                "elo_b": pb.elo_rating,
                "rd_a": pa.elo_rd,
                "rd_b": pb.elo_rd,
                "team_elo": team_elo,
                "opp_baseline": opp_baseline,
                "elo_advantage": team_elo - opp_baseline,
                "base_prob": base_prob,
                "chemistry_score": pairing.chemistry_score,
                "chem_bonus": chem_bonus,
                "pair_confidence": pair_confidence,
                "confidence_adjusted": pre_mode_prob,
                "mode": self.mode,
                "mode_adjusted": adjusted_prob,
                "final_prob": adjusted_prob,
                "checker_number": pairing.checker_number,
                "times_together": pairing.times_played_together,
                "record_together": pairing.record_together,
                "win_rate_a": pa.win_rate,
                "win_rate_b": pb.win_rate,
                "matches_a": pa.total_matches,
                "matches_b": pb.total_matches,
            })

        # Performance score: sum of line win probabilities
        lineup.performance_score = sum(line_win_probs)

        # Team match win probability: need majority of lines
        n = len(line_win_probs)
        majority = n // 2 + 1
        lineup.team_win_probability = self._calc_team_win_prob(line_win_probs, majority)

        # Overall confidence from Elo RD
        lineup.confidence = sum(
            min(self.players[p.player_a].confidence,
                self.players[p.player_b].confidence)
            for p in lineup.pairings
        ) / len(lineup.pairings)

    def _calc_team_win_prob(self, probs: list[float], majority: int) -> float:
        """
        Calculate probability of winning at least 'majority' out of N lines.
        Uses dynamic programming over independent Bernoulli trials.
        """
        n = len(probs)
        # dp[j] = probability of winning exactly j lines after considering i lines
        dp = [0.0] * (n + 1)
        dp[0] = 1.0

        for p in probs:
            # Process in reverse to avoid using updated values
            new_dp = [0.0] * (n + 1)
            for j in range(n + 1):
                # Don't win this line
                new_dp[j] += dp[j] * (1 - p)
                # Win this line
                if j + 1 <= n:
                    new_dp[j + 1] += dp[j] * p
            dp = new_dp

        return sum(dp[j] for j in range(majority, n + 1))

    # ─────────────────────────────────────────────────────────────────
    # PHASE 5: Validation, Reporting & Recommendations
    # ─────────────────────────────────────────────────────────────────

    def phase5_reporting(self) -> bool:
        """Generate detailed report with recommendations."""
        if not self.legal_lineups:
            print("  No lineups to report!")
            self._print_warnings()
            return False

        top = self.legal_lineups[:self.top_n]

        for rank, lineup in enumerate(top, 1):
            print(f"\n  {'═' * 60}")
            print(f"  LINEUP #{rank}  |  Team Win Prob: {lineup.team_win_probability:.1%}  "
                  f"|  Confidence: {lineup.confidence:.0%}")
            print(f"  {'═' * 60}")

            for i, pairing in enumerate(lineup.pairings, 1):
                pa = self.players[pairing.player_a]
                pb = self.players[pairing.player_b]

                # Chemistry indicator
                if pairing.times_played_together > 0:
                    chem_str = f"({pairing.record_together}, {pairing.times_played_together}x together)"
                else:
                    chem_str = "(new pair)"

                print(f"\n  Line {i}: {pairing.player_a} + {pairing.player_b}")
                print(f"    Checker#: {pairing.checker_number:.2f}  |  "
                      f"Win Prob: {pairing.estimated_win_prob:.0%}  |  {chem_str}")
                print(f"    {pairing.player_a}: Elo {pa.elo_rating:.0f} (±{pa.elo_rd:.0f}), "
                      f"win rate {pa.win_rate:.0%}, {pa.total_matches} matches")
                print(f"    {pairing.player_b}: Elo {pb.elo_rating:.0f} (±{pb.elo_rd:.0f}), "
                      f"win rate {pb.win_rate:.0%}, {pb.total_matches} matches")

            # Strategic notes
            if lineup.notes:
                print(f"\n  Notes:")
                for note in lineup.notes:
                    print(f"    • {note}")

        # Comparison
        if len(top) > 1:
            print(f"\n  {'─' * 60}")
            print(f"  COMPARISON")
            print(f"  {'─' * 60}")
            print(f"  {'Rank':<6} {'Team Win%':>10} {'Perf Score':>11} {'Confidence':>11}")
            for rank, lineup in enumerate(top, 1):
                print(f"  #{rank:<5} {lineup.team_win_probability:>9.1%} "
                      f"{lineup.performance_score:>11.2f} {lineup.confidence:>10.0%}")

            # Why #1 beats #2
            if len(top) >= 2:
                l1, l2 = top[0], top[1]
                diff = l1.team_win_probability - l2.team_win_probability
                print(f"\n  Why #{1} over #{2}: +{diff:.1%} team win probability")

                # Identify key differences
                pairs1 = {tuple(sorted([p.player_a, p.player_b])) for p in l1.pairings}
                pairs2 = {tuple(sorted([p.player_a, p.player_b])) for p in l2.pairings}
                diff_pairs = pairs1.symmetric_difference(pairs2)
                if diff_pairs:
                    print(f"  Key differences: {len(diff_pairs)} pair(s) differ")

        self._print_warnings()

        # Summary
        best = top[0]
        print(f"\n  {'═' * 60}")
        print(f"  RECOMMENDATION")
        print(f"  {'═' * 60}")
        print(f"  Use Lineup #1 (Team Win Probability: {best.team_win_probability:.1%})")
        print(f"  Confidence: {best.confidence:.0%}")
        if best.confidence < 0.5:
            print(f"  ⚠ Low confidence — consider verifying with your captain/coordinator")
        print(f"\n  Remember to verify with the ALTA Lineup Checker before submitting!")

        return True

    def _print_warnings(self):
        """Print accumulated warnings."""
        if self.warnings:
            print(f"\n  {'─' * 60}")
            print(f"  WARNINGS & ASSUMPTIONS")
            print(f"  {'─' * 60}")
            for w in self.warnings:
                print(f"  ⚠ {w}")

    def _print_execution_summary(self):
        """Print overall agent execution summary."""
        print(f"\n{'═' * 70}")
        print(f"  AGENT EXECUTION SUMMARY")
        print(f"{'═' * 70}")
        for phase_id, result in self.phase_results.items():
            print(f"  {phase_id}: {result['name']} — {result['status']}")
        print(f"\n  Total legal lineups found: {len(self.legal_lineups)}")
        print(f"  Optimization mode: {self.mode}")
        print(f"  Warnings: {len(self.warnings)}")
        print(f"{'═' * 70}")


# ─────────────────────────────────────────────────────────────────────
# Multi-Round Playoff Planner (DP over rounds)
# ─────────────────────────────────────────────────────────────────────

class PlayoffPlanner:
    """
    Plans lineups across all playoff rounds using Dynamic Programming.

    ALTA Official Lineup Rules:
    1. VALUE: Each player has a value = weighted average of lines played
       during the regular season.
    2. ASCENDING CHECKER: Line 1 pair's combined value must be lower
       than Line 2's, and so on through Line 5.
    3. LINE MOVEMENT (±1): A pair (same two players staying together)
       can move at most 1 line up or down between consecutive weeks.
    4. PLAYER MOVEMENT (±2): A player (even with different partners)
       can move at most 2 lines between consecutive weeks.
    5. 2/3 ELIGIBILITY: A player can only play a playoff line if ≥2/3
       of their regular season matches were at that line or higher.
    6. CONTINUITY: Movement rules apply from the LAST regular season
       week into playoffs — Round 1 is constrained by final regular
       season positions.

    These rules prevent 田忌赛马 (Tian Ji's horse racing strategy —
    strategic sandbagging/mismatching).

    Approach:
    - Extract last regular season lineup as starting constraint
    - Generate all legal lineups per round (reusing single-round agent)
    - Filter Round 1 lineups by movement rules from last regular season
    - Build legal transitions between consecutive rounds
    - DP to find the 4-round path maximizing total team win probability
    """

    ROUND_NAMES = ["Rnd I", "Rnd II", "Rnd III", "Finals"]
    ROUND_DATES = ["May 2", "May 3", "May 10", "May 17"]

    def __init__(self, data_path: str, num_rounds: int = 4,
                 mode: str = "balanced", top_n: int = 3):
        self.data_path = data_path
        self.num_rounds = min(num_rounds, 4)
        self.mode = mode
        self.top_n = top_n
        self.per_round_availability = {}  # round_idx -> set of unavailable names
        self.round_lineups = []  # per round: list of scored Lineup objects
        self.best_paths = []  # top-N paths through rounds
        self.last_regular_season = {}  # player_name -> last line played

    def _extract_last_regular_season(self, data):
        """
        Extract each player's last regular season line from the data.
        Movement rules apply from the last regular season week into playoffs.
        """
        player_last = {}  # player_name -> (week, line)
        for p in data.get("players", []):
            rs = p.get("regular_season", [])
            if rs:
                last_match = max(rs, key=lambda x: x["week"])
                player_last[p["name"]] = {
                    "week": last_match["week"],
                    "line": last_match["line"],
                    "value": p.get("alta_value", 0),
                }
        return player_last

    def _filter_by_last_season(self, lineups, player_last):
        """
        Filter lineups to only those reachable from the last regular
        season positions under ALTA movement rules.
        Each player can move at most ±2 lines from their last regular
        season line.
        """
        filtered = []
        for lu in lineups:
            legal = True
            for i, pairing in enumerate(lu.pairings):
                line = i + 1
                for player in [pairing.player_a, pairing.player_b]:
                    if player in player_last:
                        last_line = player_last[player]["line"]
                        if abs(line - last_line) > 2:
                            legal = False
                            break
                if not legal:
                    break
            if legal:
                filtered.append(lu)
        return filtered

    def execute(self):
        """Run the multi-round playoff planner."""
        print("=" * 70)
        print("  ALTA PLAYOFF PLANNER — Multi-Round DP Optimization")
        print("  Goal-Seeking Agent Pattern")
        print("=" * 70)
        print(f"\n  Rounds: {self.num_rounds}")
        print(f"  Mode: {self.mode}")
        print()
        print(f"  ALTA Official Lineup Rules:")
        print(f"    1. VALUE: Player value = weighted avg of lines played")
        print(f"    2. ASCENDING: L1 pair value < L2 < L3 < L4 < L5")
        print(f"    3. LINE MOVE: Same pair ±1 line between consecutive weeks")
        print(f"    4. PLAYER MOVE: Any player ±2 lines between consecutive weeks")
        print(f"    5. 2/3 RULE: ≥2/3 of season matches at line or higher")
        print(f"    6. CONTINUITY: Rules apply from last regular season into playoffs")
        print()

        # Load data to extract last regular season lineup
        with open(self.data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        player_last = self._extract_last_regular_season(data)

        if player_last:
            print(f"  Last Regular Season Positions (movement baseline):")
            for name, info in sorted(player_last.items(), key=lambda x: x[1]["line"]):
                print(f"    {name:<25} Week {info['week']}  L{info['line']}  "
                      f"(val {info['value']:.3f})  → playoff L{max(1, info['line']-2)}-L{min(5, info['line']+2)}")
            print()

        # Step 1: Generate legal lineups for each round
        print(f"{'─' * 70}")
        print(f"  Step 1: Generate legal lineups per round")
        print(f"{'─' * 70}")

        for rnd in range(self.num_rounds):
            name = self.ROUND_NAMES[rnd] if rnd < len(self.ROUND_NAMES) else f"Round {rnd+1}"
            print(f"\n  {name} ({self.ROUND_DATES[rnd] if rnd < len(self.ROUND_DATES) else '?'}):")

            agent = PlayoffLineupAgent(self.data_path, top_n=9999, mode=self.mode)
            agent.phase1_data_ingestion()

            # Apply round-specific unavailability
            unavail = self.per_round_availability.get(rnd, set())
            if unavail:
                for name_u in unavail:
                    if name_u in agent.players:
                        agent.players[name_u].available = False
                        print(f"    ⚠ {name_u} unavailable for {name}")

            agent.phase2_strength_analysis()
            agent.phase3_lineup_generation()
            agent.phase4_optimization()

            all_lineups = agent.legal_lineups
            print(f"    Generated: {len(all_lineups)} legal lineups (checker + 2/3 rule)")

            # For Round 1: filter by last regular season movement constraint
            if rnd == 0 and player_last:
                filtered = self._filter_by_last_season(all_lineups, player_last)
                print(f"    After last-season movement filter: {len(filtered)} lineups")
                if not filtered:
                    print(f"    ⚠ No lineups reachable from last season! Using all.")
                    filtered = all_lineups
                self.round_lineups.append(filtered)
            else:
                self.round_lineups.append(all_lineups)

            print(f"    ✓ {len(self.round_lineups[-1])} valid lineups for {name}")

        # Step 2: Build transition graph and run DP
        print(f"\n{'─' * 70}")
        print(f"  Step 2: Multi-Round DP Optimization")
        print(f"{'─' * 70}")

        if self.num_rounds == 1:
            best = self.round_lineups[0][:self.top_n]
            self.best_paths = [([lu], lu.team_win_probability) for lu in best]
        else:
            self._run_dp()

        # Step 3: Report
        print(f"\n{'─' * 70}")
        print(f"  Step 3: Playoff Plan Report")
        print(f"{'─' * 70}")
        self._report(player_last)

    def _is_legal_transition(self, prev_lineup: Lineup, next_lineup: Lineup) -> bool:
        """
        Check if transitioning from prev_lineup to next_lineup is legal
        under ALTA movement rules:
        - Same pair staying together: ±1 line
        - Any individual player: ±2 lines
        """
        prev_player_lines = {}
        prev_pairs = {}
        for i, p in enumerate(prev_lineup.pairings):
            line = i + 1
            prev_player_lines[p.player_a] = line
            prev_player_lines[p.player_b] = line
            pair_key = tuple(sorted([p.player_a, p.player_b]))
            prev_pairs[pair_key] = line

        next_player_lines = {}
        next_pairs = {}
        for i, p in enumerate(next_lineup.pairings):
            line = i + 1
            next_player_lines[p.player_a] = line
            next_player_lines[p.player_b] = line
            pair_key = tuple(sorted([p.player_a, p.player_b]))
            next_pairs[pair_key] = line

        # ALTA Rule 3: Same pair ±1 line
        for pair_key, prev_line in prev_pairs.items():
            if pair_key in next_pairs:
                if abs(next_pairs[pair_key] - prev_line) > 1:
                    return False

        # ALTA Rule 4: Any player ±2 lines
        for player, prev_line in prev_player_lines.items():
            if player in next_player_lines:
                if abs(next_player_lines[player] - prev_line) > 2:
                    return False

        return True

    def _run_dp(self):
        """
        DP across rounds to find optimal playoff paths.
        State: lineup index in each round.
        Transition: legal if ALTA movement rules are satisfied.
        Value: sum of team_win_probability across all rounds.
        """
        num_rounds = len(self.round_lineups)

        MAX_PER_ROUND = 200
        for rnd in range(num_rounds):
            if len(self.round_lineups[rnd]) > MAX_PER_ROUND:
                self.round_lineups[rnd] = self.round_lineups[rnd][:MAX_PER_ROUND]

        sizes = [len(self.round_lineups[r]) for r in range(num_rounds)]
        print(f"  Lineup candidates per round: {sizes}")

        # Initialize DP with Round 0
        current_dp = {}
        for i, lu in enumerate(self.round_lineups[0]):
            current_dp[i] = (lu.team_win_probability, [i])

        # Forward DP
        for rnd in range(1, num_rounds):
            rnd_name = self.ROUND_NAMES[rnd] if rnd < len(self.ROUND_NAMES) else f"Round {rnd+1}"
            next_dp = {}
            transitions_checked = 0
            transitions_valid = 0

            for j, next_lu in enumerate(self.round_lineups[rnd]):
                best_score = -1
                best_path = None

                for i, (prev_score, prev_path) in current_dp.items():
                    prev_lu = self.round_lineups[rnd - 1][i]
                    transitions_checked += 1

                    if self._is_legal_transition(prev_lu, next_lu):
                        transitions_valid += 1
                        total = prev_score + next_lu.team_win_probability
                        if total > best_score:
                            best_score = total
                            best_path = prev_path + [j]

                if best_path is not None:
                    next_dp[j] = (best_score, best_path)

            print(f"  {rnd_name}: {transitions_checked} transitions checked, "
                  f"{transitions_valid} valid, {len(next_dp)} reachable lineups")

            if not next_dp:
                print(f"  ⚠ No legal transitions to {rnd_name}!")
                print(f"  → Movement rules are too constraining.")
                print(f"  → Falling back to independent per-round optimization.")
                self.best_paths = []
                for rnd_f in range(num_rounds):
                    if self.round_lineups[rnd_f]:
                        self.best_paths.append(
                            ([self.round_lineups[rnd_f][0]],
                             self.round_lineups[rnd_f][0].team_win_probability)
                        )
                return

            current_dp = next_dp

        all_paths = sorted(current_dp.values(), key=lambda x: x[0], reverse=True)
        self.best_paths = all_paths[:self.top_n]
        print(f"\n  ✓ Found {len(all_paths)} complete playoff paths")

    def _report(self, player_last=None):
        """Print the playoff plan report."""
        if not self.best_paths:
            print("  No valid playoff paths found!")
            return

        for rank, (total_score, path) in enumerate(self.best_paths, 1):
            avg_win = total_score / len(path) if path else 0
            print(f"\n  {'═' * 60}")
            print(f"  PLAYOFF PLAN #{rank}  |  Avg Win Prob: {avg_win:.1%}  "
                  f"|  Total: {total_score:.2f}")
            print(f"  {'═' * 60}")

            # Show last regular season as Round 0 context
            if rank == 1 and player_last:
                print(f"\n  ── Last Regular Season (baseline) ──")
                lines_last = defaultdict(list)
                for name, info in player_last.items():
                    lines_last[info["line"]].append(name)
                for line in sorted(lines_last.keys()):
                    players = lines_last[line]
                    print(f"    L{line}: {' + '.join(players)}")

            prev_player_lines = {}
            if player_last:
                prev_player_lines = {n: info["line"] for n, info in player_last.items()}

            for rnd_idx, lu_idx in enumerate(path):
                rnd_name = self.ROUND_NAMES[rnd_idx] if rnd_idx < len(self.ROUND_NAMES) else f"R{rnd_idx+1}"
                rnd_date = self.ROUND_DATES[rnd_idx] if rnd_idx < len(self.ROUND_DATES) else ""
                lineup = self.round_lineups[rnd_idx][lu_idx]

                print(f"\n  ── {rnd_name} ({rnd_date}) ── Win Prob: {lineup.team_win_probability:.1%}")

                curr_player_lines = {}
                for i, pairing in enumerate(lineup.pairings, 1):
                    curr_player_lines[pairing.player_a] = i
                    curr_player_lines[pairing.player_b] = i

                    # Movement annotations from previous round/last season
                    moves = []
                    for player in [pairing.player_a, pairing.player_b]:
                        if player in prev_player_lines:
                            prev_line = prev_player_lines[player]
                            delta = i - prev_line
                            if delta != 0:
                                arrow = "↓" if delta > 0 else "↑"
                                moves.append(f"{player.split(',')[0]}{arrow}{abs(delta)}")

                    move_str = f"  [{', '.join(moves)}]" if moves else ""
                    print(f"    L{i}: {pairing.player_a} + {pairing.player_b}  "
                          f"(ck {pairing.checker_number:.2f}, "
                          f"wp {pairing.estimated_win_prob:.0%}){move_str}")

                prev_player_lines = curr_player_lines

        # Flexibility analysis
        if self.best_paths:
            print(f"\n  {'─' * 60}")
            print(f"  FLEXIBILITY ANALYSIS")
            print(f"  {'─' * 60}")
            total_paths = len(self.best_paths)
            best_score = self.best_paths[0][0]
            if total_paths > 1:
                worst_top = self.best_paths[-1][0]
                print(f"  Top {total_paths} paths: {best_score:.2f} to {worst_top:.2f} total score")
            print(f"  Best avg win prob per round: {best_score / self.num_rounds:.1%}")

            # Show player line ranges across playoffs
            best_path = self.best_paths[0][1]
            player_line_ranges = defaultdict(list)
            for rnd_idx, lu_idx in enumerate(best_path):
                lineup = self.round_lineups[rnd_idx][lu_idx]
                for i, p in enumerate(lineup.pairings, 1):
                    player_line_ranges[p.player_a].append(i)
                    player_line_ranges[p.player_b].append(i)

            print(f"\n  Player line ranges across playoff:")
            for player, lines in sorted(player_line_ranges.items()):
                if len(lines) > 1:
                    lo, hi = min(lines), max(lines)
                    movement = hi - lo
                    lock_str = "LOCKED" if movement == 0 else f"moves {movement} line(s)"
                    # Show constraint from last season
                    season_note = ""
                    if player_last and player in player_last:
                        sl = player_last[player]["line"]
                        season_note = f" (was L{sl} in season)"
                    print(f"    {player:<25} L{lo}-L{hi}  ({lock_str}){season_note}")

        # Value adjustment analysis
        if player_last:
            print(f"\n  {'─' * 60}")
            print(f"  VALUE ADJUSTMENT ANALYSIS")
            print(f"  {'─' * 60}")
            print(f"  Coach note: Captains adjust player values at end of regular")
            print(f"  season to position for desired playoff lines. Players whose")
            print(f"  current value doesn't match their optimal playoff line may")
            print(f"  need value adjustment in remaining regular season matches.")
            if self.best_paths:
                best_path = self.best_paths[0][1]
                lineup_r1 = self.round_lineups[0][best_path[0]]
                mismatches = []
                for i, pairing in enumerate(lineup_r1.pairings, 1):
                    for player in [pairing.player_a, pairing.player_b]:
                        if player in player_last:
                            last_line = player_last[player]["line"]
                            if abs(i - last_line) > 0:
                                mismatches.append((player, last_line, i))
                if mismatches:
                    print(f"\n  Players moving from regular season positions:")
                    for player, from_line, to_line in mismatches:
                        delta = to_line - from_line
                        direction = "down" if delta > 0 else "up"
                        print(f"    {player:<25} L{from_line} → L{to_line} ({direction} {abs(delta)})")
                else:
                    print(f"\n  ✓ All players staying at their regular season positions")

        # Recommendation
        best_total, _ = self.best_paths[0]
        print(f"\n  {'═' * 60}")
        print(f"  RECOMMENDATION")
        print(f"  {'═' * 60}")
        print(f"  Use Playoff Plan #1")
        print(f"  Average team win probability: {best_total / self.num_rounds:.1%}")
        print(f"\n  ALTA movement rules mean:")
        print(f"    • Round 1 is constrained by last regular season positions")
        print(f"    • Each round constrains the next (pair ±1, player ±2)")
        print(f"    • Plan all rounds together — don't optimize one at a time!")
        print(f"\n  If player availability changes, re-run the planner.")
        print(f"  Verify with the ALTA Lineup Checker before submitting!")


# ─────────────────────────────────────────────────────────────────────
# SLM: Small Language Model (Rule-Based Knowledge Engine)
# ─────────────────────────────────────────────────────────────────────

class LineupExplainer:
    """
    Generates natural language explanations from optimizer decision traces.
    Not a neural model — renders structured traces into readable text.
    """

    @classmethod
    def explain_lineup(cls, lineup: Lineup, rank: int = 1) -> str:
        """Generate a full explanation for a lineup."""
        if not lineup.decision_trace:
            return "  No decision trace available."

        lines = []
        lines.append(f"  WHY LINEUP #{rank} (Team Win: {lineup.team_win_probability:.1%})")
        lines.append(f"  {'─' * 55}")

        # Overall summary
        best_line = max(lineup.decision_trace, key=lambda t: t["final_prob"])
        worst_line = min(lineup.decision_trace, key=lambda t: t["final_prob"])
        lines.append(f"  Strongest line: L{best_line['line']} ({best_line['final_prob']:.0%})")
        lines.append(f"  Weakest line:   L{worst_line['line']} ({worst_line['final_prob']:.0%})")
        lines.append(f"  Need 3 of 5 lines to win the match.")
        lines.append("")

        # Per-line explanation
        for t in lineup.decision_trace:
            lines.append(cls._explain_line(t))
            lines.append("")

        # Confidence assessment
        lines.append(cls._explain_confidence(lineup))

        return "\n".join(lines)

    @classmethod
    def _explain_line(cls, t: dict) -> str:
        """Explain one line's scoring decision."""
        parts = []
        ln = t["line"]
        pa, pb = t["player_a"], t["player_b"]

        parts.append(f"  Line {ln}: {pa} + {pb}")

        # Why these players are paired here
        adv = t["elo_advantage"]
        if adv > 30:
            parts.append(f"    → Strong pair: combined Elo {t['team_elo']:.0f} vs "
                        f"expected opponents {t['opp_baseline']:.0f} (+{adv:.0f} advantage)")
        elif adv > 0:
            parts.append(f"    → Slight edge: Elo {t['team_elo']:.0f} vs "
                        f"opponents {t['opp_baseline']:.0f} (+{adv:.0f})")
        elif adv > -30:
            parts.append(f"    → Close matchup: Elo {t['team_elo']:.0f} vs "
                        f"opponents {t['opp_baseline']:.0f} ({adv:.0f})")
        else:
            parts.append(f"    → Tough draw: Elo {t['team_elo']:.0f} vs "
                        f"opponents {t['opp_baseline']:.0f} ({adv:.0f})")

        # Player contributions
        parts.append(f"    → {pa}: Elo {t['elo_a']:.0f}, "
                    f"{t['win_rate_a']:.0%} win rate, {t['matches_a']} matches")
        parts.append(f"    → {pb}: Elo {t['elo_b']:.0f}, "
                    f"{t['win_rate_b']:.0%} win rate, {t['matches_b']} matches")

        # Chemistry
        if t["times_together"] > 0:
            parts.append(f"    → Chemistry: played together {t['times_together']}x "
                        f"({t['record_together']}), bonus +{t['chem_bonus']:.0%}")
        else:
            parts.append(f"    → New pair: no history together (chemistry neutral)")

        # Confidence impact
        if t["pair_confidence"] < 0.5:
            parts.append(f"    → ⚠ Low confidence ({t['pair_confidence']:.0%}): "
                        f"win prob pulled toward 50% due to limited data")

        # Mode impact
        if t["mode"] != "balanced":
            delta = t["mode_adjusted"] - t["confidence_adjusted"]
            if abs(delta) > 0.005:
                direction = "boosted" if delta > 0 else "reduced"
                parts.append(f"    → {t['mode'].title()} mode: {direction} by "
                            f"{abs(delta):.1%}")

        parts.append(f"    → Final win probability: {t['final_prob']:.0%}")

        return "\n".join(parts)

    @classmethod
    def _explain_confidence(cls, lineup: Lineup) -> str:
        parts = []
        conf = lineup.confidence
        if conf >= 0.8:
            parts.append(f"  Confidence: {conf:.0%} — HIGH")
            parts.append(f"  Most players have enough match data for reliable estimates.")
        elif conf >= 0.5:
            parts.append(f"  Confidence: {conf:.0%} — MODERATE")
            parts.append(f"  Some players have limited match history. Predictions are")
            parts.append(f"  reasonable but will improve with more regular season data.")
        else:
            parts.append(f"  Confidence: {conf:.0%} — LOW")
            parts.append(f"  ⚠ Many players have very few matches. Win probabilities")
            parts.append(f"  are heavily regressed toward 50%. Verify with your captain.")
        return "\n".join(parts)

    @classmethod
    def explain_comparison(cls, lineup_a: Lineup, lineup_b: Lineup,
                           rank_a: int = 1, rank_b: int = 2) -> str:
        """Explain why lineup A ranks above lineup B."""
        if not lineup_a.decision_trace or not lineup_b.decision_trace:
            return "  No decision traces for comparison."

        diff = lineup_a.team_win_probability - lineup_b.team_win_probability
        lines = []
        lines.append(f"  WHY #{rank_a} OVER #{rank_b} "
                     f"(+{diff:.1%} team win probability)")
        lines.append(f"  {'─' * 55}")

        # Find per-line differences
        for ta, tb in zip(lineup_a.decision_trace, lineup_b.decision_trace):
            ln = ta["line"]
            pair_a = f"{ta['player_a'][:12]}+{ta['player_b'][:12]}"
            pair_b = f"{tb['player_a'][:12]}+{tb['player_b'][:12]}"
            prob_diff = ta["final_prob"] - tb["final_prob"]

            if pair_a != pair_b:
                lines.append(f"  L{ln}: {pair_a} vs {pair_b}")
                if abs(prob_diff) > 0.01:
                    better = "A" if prob_diff > 0 else "B"
                    lines.append(f"      #{rank_a}: {ta['final_prob']:.0%}  "
                                f"#{rank_b}: {tb['final_prob']:.0%}  "
                                f"(Lineup {better} +{abs(prob_diff):.1%})")
                    # Why
                    elo_diff = ta["team_elo"] - tb["team_elo"]
                    if abs(elo_diff) > 5:
                        lines.append(f"      Reason: Elo difference "
                                    f"({ta['team_elo']:.0f} vs {tb['team_elo']:.0f})")
            else:
                lines.append(f"  L{ln}: Same pair ({pair_a})")

        return "\n".join(lines)


class ALTAHelpSystem:
    """
    Knowledge base for ALTA rules, tool usage, and strategy.
    Uses intent matching with synonym expansion.
    """

    KNOWLEDGE_BASE = [
        # ── ALTA Rules ──
        {
            "id": "checker_number",
            "intents": ["checker", "checker number", "what is checker",
                       "ascending order", "lineup order", "pair value"],
            "category": "ALTA Rules",
            "title": "Checker Number",
            "answer": (
                "A checker number is the sum of both partners' strength values.\n"
                "ALTA requires checker numbers in ASCENDING order from Line 1\n"
                "to Line 5. Your strongest pair (lowest checker) must play\n"
                "Line 1, weakest pair plays Line 5.\n\n"
                "Example: Player A (value 1.5) + Player B (value 2.0) = 3.5 checker"
            ),
        },
        {
            "id": "two_thirds_rule",
            "intents": ["2/3", "two thirds", "eligibility", "eligible",
                       "playoff eligibility", "can play", "which line"],
            "category": "ALTA Rules",
            "title": "2/3 Eligibility Rule",
            "answer": (
                "A player can only play a playoff line if at least 2/3 of\n"
                "their regular season matches were at that line or HIGHER\n"
                "(lower line number = higher/stronger).\n\n"
                "Example: If Sarah played 4 matches at L1 and 1 at L2,\n"
                "she's eligible for L1-L5 (4/5 = 80% at L1 or higher).\n"
                "But if she played 3 at L4 and 2 at L5, she can only play L4-L5."
            ),
        },
        {
            "id": "movement_rules",
            "intents": ["movement", "move", "line move", "pair move",
                       "player move", "±1", "±2", "transition",
                       "between rounds", "round to round"],
            "category": "ALTA Rules",
            "title": "Movement Rules (Between Rounds)",
            "answer": (
                "ALTA limits how much a lineup can change between consecutive weeks:\n\n"
                "  • PAIR MOVEMENT (±1): If the same two players stay paired,\n"
                "    they can move at most 1 line up or down.\n"
                "  • PLAYER MOVEMENT (±2): Any individual player can move\n"
                "    at most 2 lines between consecutive weeks.\n"
                "  • CONTINUITY: These rules apply from the last regular\n"
                "    season week into Round 1 of playoffs."
            ),
        },
        {
            "id": "strength_value",
            "intents": ["strength", "value", "player value",
                       "how is strength calculated", "alta value"],
            "category": "ALTA Rules",
            "title": "Player Strength Value",
            "answer": (
                "Each player's value = weighted average of the line numbers\n"
                "they played during the regular season.\n"
                "Lower value = stronger player (played higher lines).\n\n"
                "ALTA uses this for the checker/ascending order rule.\n"
                "It's separate from Elo rating — value is for LEGALITY,\n"
                "Elo is for PERFORMANCE prediction."
            ),
        },
        {
            "id": "playoffs_format",
            "intents": ["playoff", "playoffs", "format", "rounds",
                       "how many rounds", "bracket", "finals"],
            "category": "ALTA Rules",
            "title": "Playoff Format",
            "answer": (
                "ALTA playoffs have up to 4 rounds:\n"
                "  Rnd I  (May 2)  — Division playoffs\n"
                "  Rnd II (May 3)  — Division playoffs\n"
                "  Rnd III (May 10) — Flight playoffs\n"
                "  Finals (May 17) — City finals\n\n"
                "5 lines of doubles, 10 players per match.\n"
                "Win majority of lines (3 of 5) to advance."
            ),
        },
        # ── Tool Usage ──
        {
            "id": "how_to_start",
            "intents": ["how to start", "getting started", "quick start",
                       "how to use", "help", "tutorial", "guide"],
            "category": "Usage",
            "title": "Getting Started",
            "answer": (
                "Three ways to use this tool:\n\n"
                "  1. INTERACTIVE MODE (recommended):\n"
                "     python lineup_optimizer.py alta_team_data.json -i\n\n"
                "  2. SINGLE MATCH:\n"
                "     python lineup_optimizer.py alta_team_data.json\n\n"
                "  3. FULL PLAYOFF PLAN:\n"
                "     python lineup_optimizer.py alta_team_data.json --playoff-rounds 4\n\n"
                "  Options: --mode balanced|aggressive|conservative\n"
                "           --top N (show top N lineups)"
            ),
        },
        {
            "id": "availability",
            "intents": ["availability", "unavailable", "player out",
                       "missing player", "can't play", "absent",
                       "mark unavailable"],
            "category": "Usage",
            "title": "Managing Player Availability",
            "answer": (
                "In interactive mode (option 4):\n"
                "  1. Pick which playoff round\n"
                "  2. Toggle players available/unavailable by number\n"
                "  3. Type 'done' to save\n\n"
                "Availability is saved to a session file and persists\n"
                "between runs. Use 'all' to reset everyone to available.\n"
                "Each round can have different availability."
            ),
        },
        {
            "id": "what_if",
            "intents": ["what if", "what-if", "whatif", "scenario", "test", "simulate",
                       "without player", "remove player", "impact"],
            "category": "Usage",
            "title": "What-If Scenarios",
            "answer": (
                "The What-If feature lets you test 'what happens if certain\n"
                "players are unavailable?' without changing your actual data.\n\n"
                "How to use (Web UI):\n"
                "  1. Go to the What-If tab\n"
                "  2. Check the players to REMOVE from the scenario\n"
                "  3. Click 'Run Scenario'\n"
                "  4. See impact: baseline vs scenario win probability\n"
                "  5. See the best lineup under the scenario\n\n"
                "Changes are temporary — your base data is never modified.\n"
                "Great for planning: 'What if Sarah and Jane can't make playoffs?'"
            ),
        },
        {
            "id": "forced_excluded",
            "intents": ["force pair", "forced", "exclude pair",
                       "excluded", "pair override", "captain override",
                       "keep together", "split", "separate"],
            "category": "Usage",
            "title": "Forced & Excluded Pairs",
            "answer": (
                "Captain overrides let you control pairings:\n\n"
                "  FORCED PAIRS: These two players MUST be together.\n"
                "  → Set in Settings (option 7) or What-If (option 5)\n\n"
                "  EXCLUDED PAIRS: These two players must NOT be paired.\n"
                "  → Set in Settings (option 7) or What-If (option 5)\n\n"
                "If forced pairs make no legal lineup possible, the optimizer\n"
                "automatically relaxes them and warns you."
            ),
        },
        {
            "id": "modes",
            "intents": ["mode", "balanced", "aggressive", "conservative",
                       "strategy", "which mode", "when to use"],
            "category": "Usage",
            "title": "Optimization Modes",
            "answer": (
                "  BALANCED (default): Maximizes expected team match wins.\n"
                "  → Best for most matches.\n\n"
                "  AGGRESSIVE: Increases variance — bigger swings.\n"
                "  → Use when you're the underdog and need an upset.\n"
                "  → Boosts strong pairs more, but also boosts weak pairs.\n\n"
                "  CONSERVATIVE: Shrinks toward 50% — reduces risk.\n"
                "  → Use when you're the favorite and want to protect lead.\n"
                "  → More stable but less upside."
            ),
        },
        {
            "id": "data_refresh",
            "intents": ["refresh", "reload", "update data", "new data",
                       "scrape", "re-scrape", "after match"],
            "category": "Usage",
            "title": "Updating Data Between Rounds",
            "answer": (
                "After each regular season match or playoff round:\n\n"
                "  1. Re-run the scraper:\n"
                "     python scrape_alta.py\n\n"
                "  2. In interactive mode, use option 8 (Reload Data)\n"
                "     This re-reads the JSON without losing your\n"
                "     availability overrides or settings.\n\n"
                "  3. Generate new lineups — Elo ratings will update\n"
                "     with the latest match results."
            ),
        },
        # ── Strategy ──
        {
            "id": "elo_rating",
            "intents": ["elo", "rating", "how is elo calculated",
                       "what is elo", "player rating", "ranking"],
            "category": "Strategy",
            "title": "Elo Ratings",
            "answer": (
                "Each player has an Elo rating (higher = stronger).\n"
                "Base rating: 1500. Updated after each match using:\n"
                "  P(win) = 1 / (1 + 10^((Rb-Ra)/400))\n\n"
                "Key features:\n"
                "  • LINE-ADJUSTED: L1 opponents assumed stronger (1600)\n"
                "    vs L5 opponents weaker (1400), so a L1 player's\n"
                "    losses don't unfairly tank their rating.\n"
                "  • HIGH K-FACTOR (K=40): Adapts fast with few matches.\n"
                "  • UNCERTAINTY (±RD): Shrinks with more matches.\n"
                "    High RD → win probability pulled toward 50%."
            ),
        },
        {
            "id": "chemistry",
            "intents": ["chemistry", "pair history", "played together",
                       "partnership", "pair record"],
            "category": "Strategy",
            "title": "Pair Chemistry",
            "answer": (
                "Chemistry = how well two players perform as a pair,\n"
                "based on their win/loss record when playing together.\n\n"
                "  • Score ranges from 0.3 (all losses) to 1.0 (all wins)\n"
                "  • New pairs with no history get 0.5 (neutral)\n"
                "  • Chemistry adds a small bonus/penalty to win probability\n"
                "  • With limited data, the bonus is intentionally small\n"
                "    to avoid overfitting to few matches"
            ),
        },
        {
            "id": "win_probability",
            "intents": ["win probability", "how is probability calculated",
                       "team win", "match win", "scoring", "how scored"],
            "category": "Strategy",
            "title": "How Win Probability Works",
            "answer": (
                "Win probability is calculated in layers:\n\n"
                "  1. BASE: Elo formula for each pair vs line opponent\n"
                "  2. CHEMISTRY: Small bonus from partnership history\n"
                "  3. CONFIDENCE: Uncertain pairs pulled toward 50%\n"
                "  4. MODE: Aggressive boosts, conservative shrinks\n"
                "  5. TEAM WIN: Probability of winning 3+ of 5 lines\n"
                "     (computed via dynamic programming over all\n"
                "     independent line win probabilities)\n\n"
                "The team win% is what matters — it accounts for\n"
                "needing a MAJORITY of lines, not just sum of probs."
            ),
        },
        {
            "id": "sandbagging",
            "intents": ["sandbagging", "sandbag", "tian ji",
                       "stack", "stacking", "gaming"],
            "category": "Strategy",
            "title": "Anti-Sandbagging Rules",
            "answer": (
                "ALTA's checker rules prevent 田忌赛马 (Tian Ji's horse\n"
                "racing strategy) — intentionally putting strong players\n"
                "on lower lines to gain matchup advantages.\n\n"
                "Three rules work together:\n"
                "  1. ASCENDING CHECKER: Strongest pair must play L1\n"
                "  2. 2/3 RULE: Players locked to lines they've played\n"
                "  3. MOVEMENT ±1/±2: Can't suddenly shift lineup\n\n"
                "The optimizer respects all these — every generated\n"
                "lineup is ALTA-legal."
            ),
        },
    ]

    @classmethod
    def answer(cls, query: str) -> str:
        """Find the best matching answer for a user query."""
        query_lower = query.lower().strip()
        # Normalize: remove hyphens, strip "what is/are" prefix
        query_norm = query_lower.replace("-", " ").replace("  ", " ")
        query_core = query_norm
        for prefix in ["what is a ", "what is an ", "what is the ", "what is ", "what are ", "what does ", "how does ", "how do ", "tell me about "]:
            if query_core.startswith(prefix):
                query_core = query_core[len(prefix):]
                break

        if not query_lower:
            return cls._list_topics()

        # Score each knowledge entry by intent match
        best_score = 0
        best_entry = None

        for entry in cls.KNOWLEDGE_BASE:
            score = 0
            for intent in entry["intents"]:
                intent_lower = intent.lower()
                intent_norm = intent_lower.replace("-", " ").replace("  ", " ")
                # Exact match (original or normalized)
                if intent_lower == query_lower or intent_norm == query_norm or intent_norm == query_core:
                    score = max(score, 100)
                # Query core matches intent
                elif query_core == intent_norm:
                    score = max(score, 95)
                # Query contains intent (normalized)
                elif intent_norm in query_norm:
                    score = max(score, 70 + len(intent_norm))
                # Intent contains query (normalized)
                elif query_norm in intent_norm or query_core in intent_norm:
                    score = max(score, 50 + len(query_core))
                # Word overlap
                else:
                    query_words = set(query_norm.split()) - {"what", "is", "a", "an", "the", "how", "does", "do", "are", "tell", "me", "about"}
                    intent_words = set(intent_norm.split())
                    overlap = query_words & intent_words
                    if overlap:
                        score = max(score, 30 + len(overlap) * 10)

            if score > best_score:
                best_score = score
                best_entry = entry

        if best_score >= 30 and best_entry:
            result = []
            result.append(f"  [{best_entry['category']}] {best_entry['title']}")
            result.append(f"  {'─' * 50}")
            result.append(f"  {best_entry['answer']}")
            return "\n".join(result)
        else:
            return cls._no_match(query)

    @classmethod
    def _list_topics(cls) -> str:
        lines = []
        lines.append("  Available Topics:")
        lines.append(f"  {'─' * 50}")

        categories = {}
        for entry in cls.KNOWLEDGE_BASE:
            cat = entry["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(entry["title"])

        for cat, titles in categories.items():
            lines.append(f"\n  {cat}:")
            for title in titles:
                lines.append(f"    • {title}")

        lines.append(f"\n  Type a question or keyword to learn more.")
        return "\n".join(lines)

    @classmethod
    def _no_match(cls, query: str) -> str:
        lines = []
        lines.append(f"  I don't have a specific answer for '{query}'.")
        lines.append(f"  Try one of these topics:")
        lines.append(f"")
        for entry in cls.KNOWLEDGE_BASE[:5]:
            lines.append(f"    • {entry['title']}: {entry['intents'][0]}")
        lines.append(f"")
        lines.append(f"  Or type just Enter to see all topics.")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Interactive Playoff Manager
# ─────────────────────────────────────────────────────────────────────

class InteractiveManager:
    """
    Menu-driven interactive UX for playoff lineup management.

    State model:
      - base_data: loaded from JSON (immutable during session)
      - session_overrides: per-round availability, mode, top_n (persisted to file)
      - scenario_overrides: temporary what-if changes (discarded after each scenario)
      - saved_runs: results from previous runs for comparison
    """

    ROUND_NAMES = ["Rnd I", "Rnd II", "Rnd III", "Finals"]
    ROUND_DATES = ["May 2", "May 3", "May 10", "May 17"]
    SESSION_FILE_SUFFIX = ".session.json"

    def __init__(self, data_path: str, mode: str = "balanced", top_n: int = 3):
        self.data_path = data_path
        self.mode = mode
        self.top_n = top_n

        # Load base data
        with open(data_path, "r", encoding="utf-8") as f:
            self.base_data = json.load(f)

        self.team_name = self.base_data.get("team", {}).get("name", "Unknown")
        self.league = self.base_data.get("team", {}).get("league", "")
        self.num_lines = self.base_data.get("format", {}).get("lines", 5)

        # Build roster list
        self.roster = [p["name"] for p in self.base_data.get("players", [])]

        # Session overrides (persisted)
        self.round_availability: dict[int, set] = {}  # round_idx -> set of unavailable
        self.forced_pairs: list = []
        self.excluded_pairs: list = []
        self.saved_runs: list = []  # list of {label, lineup, round, mode}

        self._load_session()

    # ── Session Persistence ──

    def _session_path(self) -> str:
        base = os.path.splitext(self.data_path)[0]
        return base + self.SESSION_FILE_SUFFIX

    def _load_session(self):
        path = self._session_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.mode = data.get("mode", self.mode)
                self.top_n = data.get("top_n", self.top_n)
                self.forced_pairs = data.get("forced_pairs", [])
                self.excluded_pairs = data.get("excluded_pairs", [])
                for k, v in data.get("round_availability", {}).items():
                    self.round_availability[int(k)] = set(v)
            except Exception:
                pass

    def _save_session(self):
        data = {
            "mode": self.mode,
            "top_n": self.top_n,
            "forced_pairs": self.forced_pairs,
            "excluded_pairs": self.excluded_pairs,
            "round_availability": {
                str(k): list(v) for k, v in self.round_availability.items()
            },
        }
        with open(self._session_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # ── Build Agent with Overrides ──

    def _build_agent(self, round_idx: int = None,
                     extra_unavailable: set = None,
                     extra_forced: list = None,
                     extra_excluded: list = None) -> PlayoffLineupAgent:
        """Create a fresh agent with session + scenario overrides applied."""
        agent = PlayoffLineupAgent(self.data_path, top_n=self.top_n, mode=self.mode)

        # Suppress noisy output during interactive mode
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            agent.phase1_data_ingestion()
        finally:
            sys.stdout = old_stdout

        # Apply round-specific unavailability
        if round_idx is not None:
            unavail = self.round_availability.get(round_idx, set())
            for name in unavail:
                if name in agent.players:
                    agent.players[name].available = False

        # Apply extra scenario unavailability
        if extra_unavailable:
            for name in extra_unavailable:
                if name in agent.players:
                    agent.players[name].available = False

        # Apply forced/excluded pairs
        overrides = agent.captain_overrides
        all_forced = list(overrides.get("forced_pairs", [])) + self.forced_pairs
        all_excluded = list(overrides.get("excluded_pairs", [])) + self.excluded_pairs
        if extra_forced:
            all_forced += extra_forced
        if extra_excluded:
            all_excluded += extra_excluded
        overrides["forced_pairs"] = all_forced
        overrides["excluded_pairs"] = all_excluded

        return agent

    def _run_agent(self, agent: PlayoffLineupAgent, quiet: bool = False):
        """Run all phases on an agent. If quiet, suppress print output."""
        if quiet:
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()

        try:
            agent.phase2_strength_analysis()
            agent.phase3_lineup_generation()
            agent.phase4_optimization()
        finally:
            if quiet:
                sys.stdout = old_stdout

    # ── Menu ──

    def run(self):
        """Main interactive loop."""
        self._print_header()

        while True:
            choice = self._print_menu()

            if choice == "0":
                print("\n  Goodbye! Good luck in playoffs! 🎾")
                break
            elif choice == "1":
                self._view_roster()
            elif choice == "2":
                self._generate_lineup()
            elif choice == "3":
                self._plan_all_rounds()
            elif choice == "4":
                self._update_availability()
            elif choice == "5":
                self._what_if()
            elif choice == "6":
                self._compare_runs()
            elif choice == "7":
                self._settings()
            elif choice == "8":
                self._reload_data()
            elif choice == "9":
                self._ask_question()
            else:
                print("  Invalid choice. Try again.")

    def _print_header(self):
        rank = self.base_data.get("team", {}).get("division_rank", "?")
        record = self.base_data.get("team", {}).get("division_record", "?")
        print()
        print("  ╔══════════════════════════════════════════════════════════╗")
        print(f"  ║  ALTA PLAYOFF MANAGER — {self.team_name:<31}║")
        print(f"  ║  {self.league:<54}║")
        print(f"  ║  Rank #{rank} | Record: {record:<38}║")
        print("  ╚══════════════════════════════════════════════════════════╝")

    def _print_menu(self) -> str:
        avail_note = ""
        overrides_count = sum(len(v) for v in self.round_availability.values())
        if overrides_count > 0:
            avail_note = f" ({overrides_count} override(s))"
        saved_note = f" ({len(self.saved_runs)} saved)" if self.saved_runs else ""

        print(f"""
  ┌──────────────────────────────────────────────┐
  │  1.  View Roster & Elo Ratings               │
  │  2.  Generate Lineup (single round)          │
  │  3.  Plan All Playoff Rounds (DP)            │
  │  4.  Update Availability{avail_note:<21}│
  │  5.  What-If Scenario                        │
  │  6.  Compare Saved Runs{saved_note:<22}│
  │  7.  Settings (mode: {self.mode}, top: {self.top_n}){"":>{17 - len(self.mode)}}│
  │  8.  Reload Data                             │
  │  9.  Ask a Question (SLM Help)              │
  │  0.  Exit                                    │
  └──────────────────────────────────────────────┘""")

        try:
            return input("  Choose [0-9]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "0"

    # ── 1. View Roster ──

    def _view_roster(self):
        agent = self._build_agent()
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            agent.phase2_strength_analysis()
        finally:
            sys.stdout = old_stdout

        print(f"\n  {'═' * 58}")
        print(f"  ROSTER — {self.team_name} ({len(self.roster)} players)")
        print(f"  {'═' * 58}")

        sorted_players = sorted(agent.players.values(), key=lambda p: -p.elo_rating)
        print(f"\n  {'#':<4}{'Player':<22}{'Elo':>6}{'±RD':>5}{'Win%':>6}{'Mat':>5}{'Lines':>12}")
        print(f"  {'─' * 58}")
        for i, p in enumerate(sorted_players, 1):
            lines_str = ",".join(str(l) for l in p.eligible_lines[:5])
            avail = "✓" if p.available else "✗"
            print(f"  {i:<4}{p.name:<22}{p.elo_rating:>5.0f}{p.elo_rd:>5.0f}"
                  f"{p.win_rate*100:>5.0f}%{p.total_matches:>5} {lines_str:>11} {avail}")

        # Show per-round availability overrides
        if self.round_availability:
            print(f"\n  Availability Overrides:")
            for rnd, unavail in sorted(self.round_availability.items()):
                if unavail:
                    rnd_name = self.ROUND_NAMES[rnd] if rnd < len(self.ROUND_NAMES) else f"Rnd {rnd+1}"
                    print(f"    {rnd_name}: {', '.join(sorted(unavail))} — UNAVAILABLE")

    # ── 2. Generate Lineup ──

    def _generate_lineup(self):
        rnd = self._pick_round("Generate lineup for which round?")
        if rnd is None:
            return

        rnd_name = self.ROUND_NAMES[rnd] if rnd < len(self.ROUND_NAMES) else f"Rnd {rnd+1}"
        print(f"\n  Generating lineup for {rnd_name}...")

        agent = self._build_agent(round_idx=rnd)
        self._run_agent(agent, quiet=True)

        if not agent.legal_lineups:
            print("  ✗ No legal lineups found!")
            return

        top = agent.legal_lineups[:self.top_n]
        self._print_lineups(top, agent, rnd_name)

        # Save best
        self.saved_runs.append({
            "label": f"{rnd_name} ({self.mode})",
            "lineup": top[0],
            "round": rnd,
            "mode": self.mode,
            "win_prob": top[0].team_win_probability,
            "players": {n: {"elo": p.elo_rating, "rd": p.elo_rd}
                        for n, p in agent.players.items()},
        })
        print(f"  💾 Best lineup saved as run #{len(self.saved_runs)}")

    # ── 3. Plan All Rounds ──

    def _plan_all_rounds(self):
        num = self._pick_num_rounds()
        if num is None:
            return

        print(f"\n  Planning {num} playoff rounds with DP optimization...")
        print(f"  Mode: {self.mode} | Enforcing ALTA movement rules\n")

        planner = PlayoffPlanner(
            self.data_path, num_rounds=num, mode=self.mode, top_n=self.top_n
        )

        # Apply per-round availability
        for rnd, unavail in self.round_availability.items():
            planner.per_round_availability[rnd] = unavail

        planner.execute()

    # ── 4. Update Availability ──

    def _update_availability(self):
        rnd = self._pick_round("Update availability for which round?")
        if rnd is None:
            return

        rnd_name = self.ROUND_NAMES[rnd] if rnd < len(self.ROUND_NAMES) else f"Rnd {rnd+1}"
        unavail = self.round_availability.get(rnd, set())

        while True:
            print(f"\n  {rnd_name} — Player Availability")
            print(f"  {'─' * 45}")
            for i, name in enumerate(self.roster, 1):
                status = "✗ UNAVAILABLE" if name in unavail else "✓ Available"
                print(f"  {i:>3}. {name:<25} {status}")

            print(f"\n  Enter player # to toggle, 'done' to finish, 'all' to reset:")
            try:
                choice = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break

            if choice == "done" or choice == "":
                break
            elif choice == "all":
                unavail = set()
                print(f"  ✓ All players available for {rnd_name}")
                continue

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(self.roster):
                    name = self.roster[idx]
                    if name in unavail:
                        unavail.discard(name)
                        print(f"  ✓ {name} → AVAILABLE for {rnd_name}")
                    else:
                        unavail.add(name)
                        print(f"  ✗ {name} → UNAVAILABLE for {rnd_name}")
                else:
                    print("  Invalid number.")
            except ValueError:
                print("  Enter a number or 'done'.")

        self.round_availability[rnd] = unavail
        self._save_session()
        print(f"  💾 Availability saved.")

    # ── 5. What-If Scenario ──

    def _what_if(self):
        print(f"\n  ╔══════════════════════════════════════╗")
        print(f"  ║  WHAT-IF SCENARIO BUILDER            ║")
        print(f"  ╚══════════════════════════════════════╝")

        # Pick round
        rnd = self._pick_round("Scenario for which round?")
        if rnd is None:
            return
        rnd_name = self.ROUND_NAMES[rnd] if rnd < len(self.ROUND_NAMES) else f"Rnd {rnd+1}"

        extra_unavail = set()
        extra_forced = []
        extra_excluded = []

        while True:
            print(f"\n  Current scenario changes:")
            if extra_unavail:
                print(f"    Remove: {', '.join(sorted(extra_unavail))}")
            if extra_forced:
                print(f"    Force pairs: {extra_forced}")
            if extra_excluded:
                print(f"    Exclude pairs: {extra_excluded}")
            if not extra_unavail and not extra_forced and not extra_excluded:
                print(f"    (none yet)")

            print(f"""
  What to change?
    1. Remove a player
    2. Add player back
    3. Force a pair together
    4. Exclude a pair
    5. Run scenario
    0. Cancel""")

            try:
                choice = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                return

            if choice == "0":
                return
            elif choice == "1":
                name = self._pick_player("Remove which player?")
                if name:
                    extra_unavail.add(name)
                    print(f"  → {name} removed from scenario")
            elif choice == "2":
                if extra_unavail:
                    name = self._pick_from_list("Add back:", sorted(extra_unavail))
                    if name:
                        extra_unavail.discard(name)
                        print(f"  → {name} added back")
                else:
                    print("  No players removed yet.")
            elif choice == "3":
                p1 = self._pick_player("First player:")
                p2 = self._pick_player("Second player:")
                if p1 and p2 and p1 != p2:
                    extra_forced.append([p1, p2])
                    print(f"  → Forced: {p1} + {p2}")
            elif choice == "4":
                p1 = self._pick_player("First player:")
                p2 = self._pick_player("Second player:")
                if p1 and p2 and p1 != p2:
                    extra_excluded.append([p1, p2])
                    print(f"  → Excluded: {p1} + {p2}")
            elif choice == "5":
                break

        # Run baseline
        print(f"\n  Running baseline ({rnd_name})...")
        baseline = self._build_agent(round_idx=rnd)
        self._run_agent(baseline, quiet=True)
        base_prob = baseline.legal_lineups[0].team_win_probability if baseline.legal_lineups else 0

        # Run scenario
        print(f"  Running scenario...")
        scenario = self._build_agent(
            round_idx=rnd,
            extra_unavailable=extra_unavail,
            extra_forced=extra_forced,
            extra_excluded=extra_excluded,
        )
        self._run_agent(scenario, quiet=True)

        if not scenario.legal_lineups:
            print(f"\n  ✗ No legal lineups in this scenario!")
            print(f"  Baseline: {base_prob:.1%}")
            return

        scen_prob = scenario.legal_lineups[0].team_win_probability
        diff = scen_prob - base_prob

        print(f"\n  ╔══════════════════════════════════════╗")
        print(f"  ║  WHAT-IF RESULTS — {rnd_name:<18} ║")
        print(f"  ╚══════════════════════════════════════╝")
        print(f"  Baseline Team Win:  {base_prob:.1%}")
        print(f"  Scenario Team Win:  {scen_prob:.1%}")

        arrow = "▲" if diff > 0 else "▼" if diff < 0 else "═"
        color_diff = f"+{diff:.1%}" if diff > 0 else f"{diff:.1%}"
        print(f"  Impact:             {arrow} {color_diff}")
        print()

        # Show scenario's best lineup compactly
        best = scenario.legal_lineups[0]
        for i, p in enumerate(best.pairings, 1):
            print(f"  L{i}: {p.player_a} + {p.player_b}  "
                  f"(checker {p.checker_number:.2f}, win {p.estimated_win_prob:.0%})")

        # Save
        self.saved_runs.append({
            "label": f"What-if {rnd_name}: {', '.join(extra_unavail) if extra_unavail else 'custom'}",
            "lineup": best,
            "round": rnd,
            "mode": self.mode,
            "win_prob": scen_prob,
        })
        print(f"\n  💾 Scenario saved as run #{len(self.saved_runs)}")

    # ── 6. Compare Runs ──

    def _compare_runs(self):
        if len(self.saved_runs) < 2:
            print(f"\n  Need at least 2 saved runs to compare. (Have {len(self.saved_runs)})")
            print(f"  Generate lineups or run what-if scenarios first.")
            return

        print(f"\n  Saved Runs:")
        print(f"  {'#':<4}{'Label':<40}{'Win%':>8}{'Mode':>14}")
        print(f"  {'─' * 64}")
        for i, run in enumerate(self.saved_runs, 1):
            print(f"  {i:<4}{run['label']:<40}{run['win_prob']:>7.1%}{run['mode']:>14}")

        try:
            a = int(input(f"\n  Compare run #: ").strip()) - 1
            b = int(input(f"  Against run  #: ").strip()) - 1
        except (ValueError, EOFError, KeyboardInterrupt):
            return

        if not (0 <= a < len(self.saved_runs) and 0 <= b < len(self.saved_runs)):
            print("  Invalid run numbers.")
            return

        ra, rb = self.saved_runs[a], self.saved_runs[b]

        print(f"\n  ╔══════════════════════════════════════════════════════════╗")
        print(f"  ║  COMPARISON                                             ║")
        print(f"  ╚══════════════════════════════════════════════════════════╝")
        print(f"  {'':>6}{'Run A':<28}{'Run B':<28}")
        print(f"  {'':>6}{ra['label']:<28}{rb['label']:<28}")
        print(f"  {'─' * 62}")

        la, lb = ra["lineup"], rb["lineup"]
        max_lines = max(len(la.pairings), len(lb.pairings))

        for i in range(max_lines):
            if i < len(la.pairings):
                pa = la.pairings[i]
                a_str = f"{pa.player_a[:10]}+{pa.player_b[:10]} {pa.estimated_win_prob:.0%}"
            else:
                a_str = "—"
            if i < len(lb.pairings):
                pb = lb.pairings[i]
                b_str = f"{pb.player_a[:10]}+{pb.player_b[:10]} {pb.estimated_win_prob:.0%}"
            else:
                b_str = "—"
            print(f"  L{i+1}:  {a_str:<28}{b_str:<28}")

        diff = ra["win_prob"] - rb["win_prob"]
        print(f"  {'─' * 62}")
        print(f"  Win%: {ra['win_prob']:<27.1%}{rb['win_prob']:<27.1%}")
        arrow = "◄ A better" if diff > 0 else "► B better" if diff < 0 else "= Tied"
        print(f"  {arrow} (diff: {abs(diff):.1%})")

    # ── 7. Settings ──

    def _settings(self):
        print(f"\n  Current Settings:")
        print(f"    Mode: {self.mode}")
        print(f"    Top N: {self.top_n}")
        if self.forced_pairs:
            print(f"    Forced Pairs: {self.forced_pairs}")
        if self.excluded_pairs:
            print(f"    Excluded Pairs: {self.excluded_pairs}")

        print(f"""
  What to change?
    1. Mode (balanced / aggressive / conservative)
    2. Top N lineups to show
    3. Add forced pair
    4. Add excluded pair
    5. Clear pair overrides
    0. Back""")

        try:
            choice = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return

        if choice == "1":
            print("  Modes: [1] balanced  [2] aggressive  [3] conservative")
            try:
                m = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            modes = {"1": "balanced", "2": "aggressive", "3": "conservative"}
            if m in modes:
                self.mode = modes[m]
                print(f"  ✓ Mode → {self.mode}")
        elif choice == "2":
            try:
                n = int(input("  Top N (1-10): ").strip())
                self.top_n = max(1, min(10, n))
                print(f"  ✓ Top N → {self.top_n}")
            except (ValueError, EOFError, KeyboardInterrupt):
                pass
        elif choice == "3":
            p1 = self._pick_player("First player:")
            p2 = self._pick_player("Second player:")
            if p1 and p2 and p1 != p2:
                self.forced_pairs.append([p1, p2])
                print(f"  ✓ Forced: {p1} + {p2}")
        elif choice == "4":
            p1 = self._pick_player("First player:")
            p2 = self._pick_player("Second player:")
            if p1 and p2 and p1 != p2:
                self.excluded_pairs.append([p1, p2])
                print(f"  ✓ Excluded: {p1} + {p2}")
        elif choice == "5":
            self.forced_pairs = []
            self.excluded_pairs = []
            print("  ✓ All pair overrides cleared.")

        self._save_session()

    # ── 8. Reload Data ──

    def _reload_data(self):
        print(f"\n  Reloading {self.data_path}...")
        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                self.base_data = json.load(f)
            self.roster = [p["name"] for p in self.base_data.get("players", [])]
            print(f"  ✓ Reloaded. {len(self.roster)} players on roster.")
            print(f"  Session overrides (availability, pairs) preserved.")
            print(f"  Saved runs cleared (data may have changed).")
            self.saved_runs = []
        except Exception as e:
            print(f"  ✗ Error: {e}")

    # ── Helper: Print Lineups (compact) ──

    def _print_lineups(self, lineups: list, agent: PlayoffLineupAgent, label: str):
        for rank, lineup in enumerate(lineups, 1):
            print(f"\n  ╔═══════════════════════════════════════════════════════╗")
            print(f"  ║  #{rank} {label:<20} Win: {lineup.team_win_probability:.1%}"
                  f"  Conf: {lineup.confidence:.0%}{'':>8}║")
            print(f"  ╚═══════════════════════════════════════════════════════╝")

            for i, p in enumerate(lineup.pairings, 1):
                pa = agent.players[p.player_a]
                pb = agent.players[p.player_b]
                if p.times_played_together > 0:
                    chem = f"{p.record_together}"
                else:
                    chem = "new"
                print(f"  L{i}: {p.player_a:<20}+ {p.player_b:<20}"
                      f"chk:{p.checker_number:.1f} win:{p.estimated_win_prob:.0%} [{chem}]")

            # Auto-explain top lineup
            if rank == 1 and lineup.decision_trace:
                print()
                print(LineupExplainer.explain_lineup(lineup, rank))

            # Compare #1 vs #2
            if rank == 2 and len(lineups) >= 2 and lineups[0].decision_trace:
                print()
                print(LineupExplainer.explain_comparison(lineups[0], lineups[1], 1, 2))

    # ── Helper: Pick Round ──

    def _pick_round(self, prompt: str) -> int:
        print(f"\n  {prompt}")
        for i, (name, date) in enumerate(zip(self.ROUND_NAMES, self.ROUND_DATES)):
            unavail = self.round_availability.get(i, set())
            note = f" ({len(unavail)} out)" if unavail else ""
            print(f"    {i+1}. {name} ({date}){note}")
        try:
            choice = int(input("  Round [1-4]: ").strip()) - 1
            if 0 <= choice < 4:
                return choice
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        print("  Cancelled.")
        return None

    def _pick_num_rounds(self) -> int:
        print(f"\n  How many playoff rounds to plan?")
        print(f"    1-4 rounds (4 = full playoffs through Finals)")
        try:
            n = int(input("  Rounds [1-4]: ").strip())
            if 1 <= n <= 4:
                return n
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        print("  Cancelled.")
        return None

    def _pick_player(self, prompt: str) -> str:
        print(f"\n  {prompt}")
        for i, name in enumerate(self.roster, 1):
            print(f"    {i:>2}. {name}")
        try:
            choice = int(input("  Player #: ").strip()) - 1
            if 0 <= choice < len(self.roster):
                return self.roster[choice]
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        return None

    def _pick_from_list(self, prompt: str, items: list) -> str:
        print(f"\n  {prompt}")
        for i, item in enumerate(items, 1):
            print(f"    {i}. {item}")
        try:
            choice = int(input("  #: ").strip()) - 1
            if 0 <= choice < len(items):
                return items[choice]
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        return None

    # ── 9. Ask a Question (SLM Help) ──

    def _ask_question(self):
        print(f"\n  ┌──────────────────────────────────────────────┐")
        print(f"  │  ALTA Help — Ask anything about rules,       │")
        print(f"  │  strategy, or how to use this tool.          │")
        print(f"  │  Type 'back' to return to menu.              │")
        print(f"  └──────────────────────────────────────────────┘")

        while True:
            try:
                q = input("\n  Question: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if q.lower() in ("back", "exit", "quit", "q"):
                break

            print()
            print(ALTAHelpSystem.answer(q))


# ─────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ALTA Playoff Lineup Optimizer — Goal-Seeking Agent"
    )
    parser.add_argument(
        "data_file", nargs="?", default="sample_data.json",
        help="Path to your team data JSON file (default: sample_data.json)"
    )
    parser.add_argument(
        "--top", type=int, default=3,
        help="Number of top lineups to show (default: 3)"
    )
    parser.add_argument(
        "--mode", choices=["conservative", "balanced", "aggressive"],
        default="balanced",
        help="Optimization mode (default: balanced)"
    )
    parser.add_argument(
        "--playoff-rounds", type=int, default=0,
        help="Plan across N playoff rounds (0 = single match, 4 = full playoffs)"
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true",
        help="Launch interactive playoff manager"
    )
    parser.add_argument(
        "--explain", "-e", action="store_true",
        help="Show SLM explanations for generated lineups"
    )
    args = parser.parse_args()

    if args.interactive:
        mgr = InteractiveManager(args.data_file, mode=args.mode, top_n=args.top)
        mgr.run()
    elif args.playoff_rounds > 0:
        planner = PlayoffPlanner(
            args.data_file,
            num_rounds=args.playoff_rounds,
            mode=args.mode,
            top_n=args.top,
        )
        planner.execute()
    else:
        agent = PlayoffLineupAgent(args.data_file, top_n=args.top, mode=args.mode)
        agent.execute()

        # SLM explanations in CLI mode
        if args.explain and agent.legal_lineups:
            print("\n" + "=" * 60)
            print("  SLM LINEUP EXPLANATIONS")
            print("=" * 60)
            top = agent.legal_lineups[:args.top]
            for rank, lineup in enumerate(top, 1):
                print()
                print(LineupExplainer.explain_lineup(lineup, rank))
            if len(top) >= 2:
                print()
                print(LineupExplainer.explain_comparison(top[0], top[1], 1, 2))


if __name__ == "__main__":
    main()
