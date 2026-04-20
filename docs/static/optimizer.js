/* ALTA Playoff Lineup Optimizer — Pure JS Port of lineup_optimizer.py */

// ─── Data Models ───

class Player {
  constructor({ name, available = true, notes = '', matches = [], strength_number = 0,
    win_rate = 0, avg_line = 0, total_matches = 0, confidence = 0,
    eligible_lines = [], elo_rating = 1500, elo_rd = 350 }) {
    this.name = name;
    this.available = available;
    this.notes = notes;
    this.matches = matches;
    this.strength_number = strength_number;
    this.win_rate = win_rate;
    this.avg_line = avg_line;
    this.total_matches = total_matches;
    this.confidence = confidence;
    this.eligible_lines = eligible_lines;
    this.elo_rating = elo_rating;
    this.elo_rd = elo_rd;
  }
}

class Pairing {
  constructor({ player_a, player_b, checker_number = 0, chemistry_score = 0.5,
    estimated_win_prob = 0.5, times_played_together = 0, record_together = '',
    confidence = 0 }) {
    this.player_a = player_a;
    this.player_b = player_b;
    this.checker_number = checker_number;
    this.chemistry_score = chemistry_score;
    this.estimated_win_prob = estimated_win_prob;
    this.times_played_together = times_played_together;
    this.record_together = record_together;
    this.confidence = confidence;
  }
}

class Lineup {
  constructor(pairings) {
    this.pairings = pairings;
    this.legality_score = 0;
    this.performance_score = 0;
    this.team_win_probability = 0;
    this.confidence = 0;
    this.notes = [];
    this.decision_trace = [];
  }
}

// ─── Elo Rating Engine ───

const EloEngine = {
  BASE_RATING: 1500.0,
  INITIAL_RD: 350.0,
  K_FACTOR: 40,
  RD_DECAY_PER_MATCH: 0.8,
  LINE_BASELINES: { 1: 1600, 2: 1550, 3: 1500, 4: 1450, 5: 1400 },

  initialRatingFromPrior(lastWinPct, altaValue, numLines) {
    let rating = this.BASE_RATING;
    let rd = this.INITIAL_RD;

    if (lastWinPct != null && lastWinPct > 0) {
      let adj = (lastWinPct - 50.0) * 2.5;
      adj = Math.max(-100, Math.min(100, adj));
      rating += adj;
      rd = 300.0;
    }

    if (altaValue != null && altaValue > 0) {
      const center = (numLines + 1) / 2.0;
      let adj = (center - altaValue) * (100.0 / numLines);
      adj = Math.max(-75, Math.min(75, adj));
      rating += adj * 0.3;
      rd = Math.min(rd, 310.0);
    }

    return [rating, rd];
  },

  expectedScore(ratingA, ratingB) {
    return 1.0 / (1.0 + Math.pow(10.0, (ratingB - ratingA) / 400.0));
  },

  updateRating(rating, rd, expected, actual) {
    let effectiveK = this.K_FACTOR * (rd / this.INITIAL_RD);
    effectiveK = Math.max(this.K_FACTOR * 0.5, Math.min(this.K_FACTOR * 1.5, effectiveK));
    const newRating = rating + effectiveK * (actual - expected);
    let newRd = rd * this.RD_DECAY_PER_MATCH;
    newRd = Math.max(50.0, newRd);
    return [newRating, newRd];
  },

  computeRatings(players, numLines) {
    numLines = numLines || 5;

    // Step 1: initial ratings
    for (const player of Object.values(players)) {
      let lastWp = null;
      let altaVal = null;
      if (player.win_rate > 0 && player.total_matches === 0) {
        lastWp = player.win_rate * 100.0;
      }
      if (player.strength_number > 0) {
        altaVal = player.strength_number;
      }
      const [r, rd] = this.initialRatingFromPrior(lastWp, altaVal, numLines);
      player.elo_rating = r;
      player.elo_rd = rd;
    }

    // Step 2: collect all match events
    const allEvents = [];
    for (const [name, player] of Object.entries(players)) {
      for (const match of player.matches) {
        allEvents.push({
          player: name,
          date: match.date || match.match_date || '',
          line: match.line,
          result: match.result,
          partner: match.partner,
        });
      }
    }
    allEvents.sort((a, b) => {
      if (a.date < b.date) return -1;
      if (a.date > b.date) return 1;
      return a.line - b.line;
    });

    // Step 3: process chronologically
    const processed = new Set();
    for (const event of allEvents) {
      const key = `${event.player}|${event.date}|${event.line}`;
      if (processed.has(key)) continue;
      processed.add(key);

      const player = players[event.player];
      const line = event.line;
      const oppRating = this.LINE_BASELINES[line] || this.BASE_RATING;
      const partnerName = event.partner;

      let teamRating, oppTeamRating;
      if (partnerName && players[partnerName]) {
        teamRating = (player.elo_rating + players[partnerName].elo_rating) / 2.0;
        oppTeamRating = oppRating;
      } else {
        teamRating = player.elo_rating;
        oppTeamRating = oppRating;
      }

      const expected = this.expectedScore(teamRating, oppTeamRating);
      const actual = event.result === 'W' ? 1.0 : 0.0;

      [player.elo_rating, player.elo_rd] = this.updateRating(
        player.elo_rating, player.elo_rd, expected, actual
      );

      if (partnerName && players[partnerName]) {
        const partnerKey = `${partnerName}|${event.date}|${event.line}`;
        if (!processed.has(partnerKey)) {
          processed.add(partnerKey);
          const partner = players[partnerName];
          [partner.elo_rating, partner.elo_rd] = this.updateRating(
            partner.elo_rating, partner.elo_rd, expected, actual
          );
        }
      }
    }

    // Step 4: confidence from RD
    for (const player of Object.values(players)) {
      player.confidence = Math.max(0.1, Math.min(1.0, 1.0 - (player.elo_rd - 50) / 350.0));
    }
  },

  pairWinProbability(playerA, playerB, opponentLine, numLines) {
    const teamRating = (playerA.elo_rating + playerB.elo_rating) / 2.0;
    const oppRating = opponentLine != null
      ? (this.LINE_BASELINES[opponentLine] || this.BASE_RATING)
      : this.BASE_RATING;
    return this.expectedScore(teamRating, oppRating);
  },
};

// ─── Playoff Lineup Agent ───

class PlayoffLineupAgent {
  constructor(data, { topN = 3, mode = 'balanced' } = {}) {
    this.rawData = data;
    this.topN = topN;
    this.mode = mode;
    this.players = {};
    this.numLines = 5;
    this.teamName = '';
    this.leagueInfo = {};
    this.captainOverrides = {};
    this.allPairings = [];
    this.legalLineups = [];
    this.warnings = [];
    this.isAltaFormat = false;
  }

  run() {
    this.phase1DataIngestion();
    this.phase2StrengthAnalysis();
    this.phase3LineupGeneration();
    this.phase4Optimization();
    return this;
  }

  // Phase 1
  phase1DataIngestion() {
    const data = this.rawData;
    this.isAltaFormat = !!data.format &&
      data.players && data.players.length > 0 &&
      JSON.stringify(data.players[0]).includes('estimated_strength');

    const team = data.team || {};
    this.teamName = team.name || 'Unknown Team';
    this.leagueInfo = team;

    if (this.isAltaFormat) {
      const fmt = data.format || {};
      this.numLines = fmt.lines || 5;
    } else {
      this.numLines = team.num_lines || 5;
    }

    this.captainOverrides = data.captain_overrides || {};

    const playerData = data.players || [];
    if (this.isAltaFormat) {
      this._loadAltaPlayers(playerData);
    } else {
      this._loadTemplatePlayers(playerData);
    }
  }

  _loadAltaPlayers(playerData) {
    for (const p of playerData) {
      const altaValue = p.alta_value;
      const strength = altaValue || p.estimated_strength || 5.5;

      const matches = (p.regular_season || []).map(m => ({
        date: m.match_date || '',
        line: m.line || 0,
        partner: m.partner || '',
        result: m.result || '',
        score: m.score || '',
      }));

      let winRate, confidence;
      const seasonWp = p.current_season_win_pct;
      const histWp = p.last_win_pct;
      if (seasonWp != null) {
        winRate = seasonWp / 100.0;
        confidence = Math.min(0.9, 0.4 + matches.length * 0.1);
      } else if (histWp != null) {
        winRate = histWp / 100.0;
        confidence = 0.5;
      } else {
        winRate = 0.5;
        confidence = 0.2;
      }

      const eligible = p.eligible_playoff_lines ||
        Array.from({ length: this.numLines }, (_, i) => i + 1);

      this.players[p.name] = new Player({
        name: p.name,
        available: p.available_for_playoffs !== false,
        notes: p.notes || '',
        matches,
        strength_number: strength,
        win_rate: winRate,
        avg_line: strength,
        total_matches: matches.length,
        confidence,
        eligible_lines: eligible,
      });
    }
  }

  _loadTemplatePlayers(playerData) {
    for (const p of playerData) {
      const matches = (p.regular_season || []).map(m => ({
        date: m.match_date || '',
        line: m.line || 0,
        partner: m.partner || '',
        result: m.result || '',
        score: m.score || '',
      }));
      this.players[p.name] = new Player({
        name: p.name,
        available: p.available_for_playoffs !== false,
        notes: p.notes || '',
        matches,
        total_matches: matches.length,
      });
    }
  }

  // Phase 2
  phase2StrengthAnalysis() {
    if (this.isAltaFormat) {
      // Strengths pre-computed; check eligibility
      const hasRealEligibility = Object.values(this.players).some(p => {
        const allLines = Array.from({ length: this.numLines }, (_, i) => i + 1);
        return JSON.stringify(p.eligible_lines) !== JSON.stringify(allLines);
      });
      if (!hasRealEligibility) {
        for (const player of Object.values(this.players)) {
          player.eligible_lines = Array.from({ length: this.numLines }, (_, i) => i + 1);
        }
      }
    } else {
      const allLines = [];
      for (const p of Object.values(this.players)) {
        for (const m of p.matches) allLines.push(m.line);
      }
      const defaultLine = allLines.length > 0 ? Math.max(...allLines) : this.numLines;

      for (const player of Object.values(this.players)) {
        if (!player.matches.length) {
          player.strength_number = defaultLine;
          player.win_rate = 0;
          player.confidence = 0.1;
          continue;
        }

        const lines = player.matches.map(m => m.line);
        const n = lines.length;
        if (n === 0) {
          player.strength_number = defaultLine;
          player.confidence = 0.1;
          continue;
        }

        const weights = lines.map((_, i) => 1.0 + 0.1 * i);
        const totalWeight = weights.reduce((a, b) => a + b, 0);
        player.strength_number = lines.reduce((s, l, i) => s + l * weights[i], 0) / totalWeight;
        player.avg_line = lines.reduce((a, b) => a + b, 0) / n;

        const wins = player.matches.filter(m => m.result === 'W').length;
        player.win_rate = n > 0 ? wins / n : 0;
        player.confidence = Math.min(1.0, n / 10.0);
      }

      // 2/3 eligibility rule
      for (const player of Object.values(this.players)) {
        if (!player.matches.length) {
          player.eligible_lines = Array.from({ length: this.numLines }, (_, i) => i + 1);
          continue;
        }

        const lineCounts = {};
        for (const m of player.matches) {
          lineCounts[m.line] = (lineCounts[m.line] || 0) + 1;
        }
        const total = player.matches.length;
        const maxLine = Math.max(...Object.keys(lineCounts).map(Number));

        const eligible = [];
        for (let targetLine = 1; targetLine <= this.numLines; targetLine++) {
          let matchesAtOrAbove = 0;
          for (const [line, count] of Object.entries(lineCounts)) {
            if (Number(line) <= targetLine) matchesAtOrAbove += count;
          }
          if (total > 0 && matchesAtOrAbove / total >= 2 / 3) {
            eligible.push(targetLine);
          } else if (targetLine > maxLine) {
            eligible.push(targetLine);
          }
        }

        if (eligible.length === 0) {
          const fallback = [];
          for (let l = 1; l <= this.numLines; l++) {
            if (l >= Math.floor(player.avg_line)) fallback.push(l);
          }
          player.eligible_lines = fallback.length > 0 ? fallback :
            Array.from({ length: this.numLines }, (_, i) => i + 1);
        } else {
          player.eligible_lines = eligible;
        }
      }
    }

    // Elo computation
    EloEngine.computeRatings(this.players, this.numLines);
  }

  // Phase 3
  phase3LineupGeneration() {
    let available = Object.values(this.players).filter(p => p.available);
    let needed = this.numLines * 2;

    if (available.length < needed) {
      this.numLines = Math.floor(available.length / 2);
      needed = this.numLines * 2;
    }
    if (this.numLines === 0) return;

    const availableNames = available.map(p => p.name);

    // Generate all pairs
    const allPairs = [];
    for (let i = 0; i < availableNames.length; i++) {
      for (let j = i + 1; j < availableNames.length; j++) {
        allPairs.push([availableNames[i], availableNames[j]]);
      }
    }

    // Excluded pairs
    const excluded = new Set();
    for (const pair of (this.captainOverrides.excluded_pairs || [])) {
      excluded.add([...pair].sort().join('|'));
    }

    const validPairs = allPairs.filter(([a, b]) => !excluded.has([a, b].sort().join('|')));

    // Chemistry
    const pairChemistry = this._computePairChemistry();

    this.allPairings = validPairs.map(([a, b]) => {
      const sa = this.players[a].strength_number;
      const sb = this.players[b].strength_number;
      const chemKey = [a, b].sort().join('|');
      const chem = pairChemistry[chemKey] || {};
      return new Pairing({
        player_a: a,
        player_b: b,
        checker_number: sa + sb,
        chemistry_score: chem.chemistry || 0.5,
        times_played_together: chem.times || 0,
        record_together: chem.record || '0-0',
        confidence: chem.confidence || 0.3,
      });
    });

    this.allPairings.sort((a, b) => a.checker_number - b.checker_number);

    // Forced pairs
    const forcedPairs = (this.captainOverrides.forced_pairs || []);
    const forcedSet = new Set(forcedPairs.map(p => [...p].sort().join('|')));

    this.legalLineups = [];
    this._searchLineups([], new Set(), forcedSet, 0.0);

    if (this.legalLineups.length === 0 && forcedSet.size > 0) {
      this._searchLineups([], new Set(), new Set(), 0.0);
    }
  }

  _searchLineups(current, usedPlayers, forcedSet, lastChecker) {
    if (current.length === this.numLines) {
      if (forcedSet.size > 0) {
        const lineupPairs = new Set(current.map(p => [p.player_a, p.player_b].sort().join('|')));
        for (const fp of forcedSet) {
          if (!lineupPairs.has(fp)) return;
        }
      }
      const lineup = new Lineup([...current]);
      lineup.legality_score = 1.0;
      this.legalLineups.push(lineup);
      return;
    }

    if (this.legalLineups.length >= 10000) return;

    const lineNum = current.length + 1;
    const candidates = [];
    for (const pairing of this.allPairings) {
      if (usedPlayers.has(pairing.player_a) || usedPlayers.has(pairing.player_b)) continue;
      if (pairing.checker_number < lastChecker) continue;
      const pa = this.players[pairing.player_a];
      const pb = this.players[pairing.player_b];
      if (!pa.eligible_lines.includes(lineNum) || !pb.eligible_lines.includes(lineNum)) continue;
      candidates.push(pairing);
    }

    candidates.sort((a, b) => {
      const eloA = this.players[a.player_a].elo_rating + this.players[a.player_b].elo_rating;
      const eloB = this.players[b.player_a].elo_rating + this.players[b.player_b].elo_rating;
      return eloB - eloA;
    });

    const beamWidth = this.legalLineups.length < 1000 ? 50 : 20;
    for (let k = 0; k < Math.min(candidates.length, beamWidth); k++) {
      const pairing = candidates[k];
      const newUsed = new Set(usedPlayers);
      newUsed.add(pairing.player_a);
      newUsed.add(pairing.player_b);
      current.push(pairing);
      this._searchLineups(current, newUsed, forcedSet, pairing.checker_number);
      current.pop();
    }
  }

  _computePairChemistry() {
    const pairRecords = {};
    for (const player of Object.values(this.players)) {
      for (const match of player.matches) {
        const key = [player.name, match.partner].sort().join('|');
        if (!pairRecords[key]) pairRecords[key] = { wins: 0, losses: 0, total: 0 };
        if (match.result === 'W') pairRecords[key].wins++;
        else pairRecords[key].losses++;
        pairRecords[key].total++;
      }
    }

    const chemistry = {};
    for (const [key, record] of Object.entries(pairRecords)) {
      // Each match counted twice (once per player)
      const wins = Math.floor(record.wins / 2);
      const losses = Math.floor(record.losses / 2);
      const total = Math.floor(record.total / 2);
      if (total > 0) {
        const winRate = wins / total;
        chemistry[key] = {
          chemistry: 0.3 + 0.7 * winRate,
          times: total,
          record: `${wins}-${losses}`,
          confidence: Math.min(1.0, total / 5.0),
        };
      }
    }
    return chemistry;
  }

  // Phase 4
  phase4Optimization() {
    if (!this.legalLineups.length) return;

    for (const lineup of this.legalLineups) {
      this._scoreLineup(lineup);
    }

    this.legalLineups.sort((a, b) => {
      if (b.team_win_probability !== a.team_win_probability)
        return b.team_win_probability - a.team_win_probability;
      return b.performance_score - a.performance_score;
    });
  }

  _scoreLineup(lineup) {
    const lineWinProbs = [];
    lineup.decision_trace = [];

    for (let i = 0; i < lineup.pairings.length; i++) {
      const pairing = lineup.pairings[i];
      const pa = this.players[pairing.player_a];
      const pb = this.players[pairing.player_b];
      const lineNum = i + 1;

      const teamElo = (pa.elo_rating + pb.elo_rating) / 2.0;
      const oppBaseline = EloEngine.LINE_BASELINES[lineNum] || 1500;
      const baseProb = EloEngine.pairWinProbability(pa, pb, lineNum, this.numLines);

      const chemBonus = (pairing.chemistry_score - 0.5) * 0.08;

      const pairConfidence = Math.min(pa.confidence, pb.confidence);
      let adjustedProb = baseProb * pairConfidence + 0.5 * (1 - pairConfidence);
      adjustedProb += chemBonus;

      const preModeProb = adjustedProb;

      if (this.mode === 'aggressive') {
        const deviation = adjustedProb - 0.5;
        adjustedProb = 0.5 + deviation * 1.2;
      } else if (this.mode === 'conservative') {
        const deviation = adjustedProb - 0.5;
        adjustedProb = 0.5 + deviation * 0.8;
      }

      adjustedProb = Math.max(0.05, Math.min(0.95, adjustedProb));

      pairing.estimated_win_prob = adjustedProb;
      lineWinProbs.push(adjustedProb);

      lineup.decision_trace.push({
        line: lineNum,
        player_a: pa.name,
        player_b: pb.name,
        elo_a: pa.elo_rating,
        elo_b: pb.elo_rating,
        rd_a: pa.elo_rd,
        rd_b: pb.elo_rd,
        team_elo: teamElo,
        opp_baseline: oppBaseline,
        elo_advantage: teamElo - oppBaseline,
        base_prob: baseProb,
        chemistry_score: pairing.chemistry_score,
        chem_bonus: chemBonus,
        pair_confidence: pairConfidence,
        confidence_adjusted: preModeProb,
        mode: this.mode,
        mode_adjusted: adjustedProb,
        final_prob: adjustedProb,
        checker_number: pairing.checker_number,
        times_together: pairing.times_played_together,
        record_together: pairing.record_together,
        win_rate_a: pa.win_rate,
        win_rate_b: pb.win_rate,
        matches_a: pa.total_matches,
        matches_b: pb.total_matches,
      });
    }

    lineup.performance_score = lineWinProbs.reduce((a, b) => a + b, 0);

    const n = lineWinProbs.length;
    const majority = Math.floor(n / 2) + 1;
    lineup.team_win_probability = this._calcTeamWinProb(lineWinProbs, majority);

    lineup.confidence = lineup.pairings.reduce((sum, p) => {
      return sum + Math.min(this.players[p.player_a].confidence,
        this.players[p.player_b].confidence);
    }, 0) / lineup.pairings.length;
  }

  _calcTeamWinProb(probs, majority) {
    const n = probs.length;
    let dp = new Array(n + 1).fill(0);
    dp[0] = 1.0;

    for (const p of probs) {
      const newDp = new Array(n + 1).fill(0);
      for (let j = 0; j <= n; j++) {
        newDp[j] += dp[j] * (1 - p);
        if (j + 1 <= n) newDp[j + 1] += dp[j] * p;
      }
      dp = newDp;
    }

    let total = 0;
    for (let j = majority; j <= n; j++) total += dp[j];
    return total;
  }
}

// ─── Lineup Explainer ───

const LineupExplainer = {
  explainLineup(lineup, rank) {
    if (!lineup.decision_trace || !lineup.decision_trace.length) {
      return '  No decision trace available.';
    }

    const lines = [];
    lines.push(`  WHY LINEUP #${rank} (Team Win: ${(lineup.team_win_probability * 100).toFixed(1)}%)`);
    lines.push(`  ${'─'.repeat(55)}`);

    const best = lineup.decision_trace.reduce((a, b) => a.final_prob > b.final_prob ? a : b);
    const worst = lineup.decision_trace.reduce((a, b) => a.final_prob < b.final_prob ? a : b);
    lines.push(`  Strongest line: L${best.line} (${(best.final_prob * 100).toFixed(0)}%)`);
    lines.push(`  Weakest line:   L${worst.line} (${(worst.final_prob * 100).toFixed(0)}%)`);
    lines.push(`  Need 3 of 5 lines to win the match.`);
    lines.push('');

    for (const t of lineup.decision_trace) {
      lines.push(this._explainLine(t));
      lines.push('');
    }

    lines.push(this._explainConfidence(lineup));
    return lines.join('\n');
  },

  _explainLine(t) {
    const parts = [];
    parts.push(`  Line ${t.line}: ${t.player_a} + ${t.player_b}`);

    const adv = t.elo_advantage;
    if (adv > 30) {
      parts.push(`    → Strong pair: combined Elo ${t.team_elo.toFixed(0)} vs expected opponents ${t.opp_baseline.toFixed(0)} (+${adv.toFixed(0)} advantage)`);
    } else if (adv > 0) {
      parts.push(`    → Slight edge: Elo ${t.team_elo.toFixed(0)} vs opponents ${t.opp_baseline.toFixed(0)} (+${adv.toFixed(0)})`);
    } else if (adv > -30) {
      parts.push(`    → Close matchup: Elo ${t.team_elo.toFixed(0)} vs opponents ${t.opp_baseline.toFixed(0)} (${adv.toFixed(0)})`);
    } else {
      parts.push(`    → Tough draw: Elo ${t.team_elo.toFixed(0)} vs opponents ${t.opp_baseline.toFixed(0)} (${adv.toFixed(0)})`);
    }

    parts.push(`    → ${t.player_a}: Elo ${t.elo_a.toFixed(0)}, ${(t.win_rate_a * 100).toFixed(0)}% win rate, ${t.matches_a} matches`);
    parts.push(`    → ${t.player_b}: Elo ${t.elo_b.toFixed(0)}, ${(t.win_rate_b * 100).toFixed(0)}% win rate, ${t.matches_b} matches`);

    if (t.times_together > 0) {
      parts.push(`    → Chemistry: played together ${t.times_together}x (${t.record_together}), bonus +${(t.chem_bonus * 100).toFixed(0)}%`);
    } else {
      parts.push(`    → New pair: no history together (chemistry neutral)`);
    }

    if (t.pair_confidence < 0.5) {
      parts.push(`    → ⚠ Low confidence (${(t.pair_confidence * 100).toFixed(0)}%): win prob pulled toward 50% due to limited data`);
    }

    if (t.mode !== 'balanced') {
      const delta = t.mode_adjusted - t.confidence_adjusted;
      if (Math.abs(delta) > 0.005) {
        const direction = delta > 0 ? 'boosted' : 'reduced';
        parts.push(`    → ${t.mode.charAt(0).toUpperCase() + t.mode.slice(1)} mode: ${direction} by ${(Math.abs(delta) * 100).toFixed(1)}%`);
      }
    }

    parts.push(`    → Final win probability: ${(t.final_prob * 100).toFixed(0)}%`);
    return parts.join('\n');
  },

  _explainConfidence(lineup) {
    const conf = lineup.confidence;
    const parts = [];
    if (conf >= 0.8) {
      parts.push(`  Confidence: ${(conf * 100).toFixed(0)}% — HIGH`);
      parts.push(`  Most players have enough match data for reliable estimates.`);
    } else if (conf >= 0.5) {
      parts.push(`  Confidence: ${(conf * 100).toFixed(0)}% — MODERATE`);
      parts.push(`  Some players have limited match history. Predictions are`);
      parts.push(`  reasonable but will improve with more regular season data.`);
    } else {
      parts.push(`  Confidence: ${(conf * 100).toFixed(0)}% — LOW`);
      parts.push(`  ⚠ Many players have very few matches. Win probabilities`);
      parts.push(`  are heavily regressed toward 50%. Verify with your captain.`);
    }
    return parts.join('\n');
  },

  explainComparison(lineupA, lineupB, rankA, rankB) {
    if (!lineupA.decision_trace || !lineupB.decision_trace) {
      return '  No decision traces for comparison.';
    }
    const diff = lineupA.team_win_probability - lineupB.team_win_probability;
    const lines = [];
    lines.push(`  WHY #${rankA} OVER #${rankB} (+${(diff * 100).toFixed(1)}% team win probability)`);
    lines.push(`  ${'─'.repeat(55)}`);

    for (let i = 0; i < lineupA.decision_trace.length; i++) {
      const ta = lineupA.decision_trace[i];
      const tb = lineupB.decision_trace[i];
      const ln = ta.line;
      const pairA = `${ta.player_a.substring(0, 12)}+${ta.player_b.substring(0, 12)}`;
      const pairB = `${tb.player_a.substring(0, 12)}+${tb.player_b.substring(0, 12)}`;
      const probDiff = ta.final_prob - tb.final_prob;

      if (pairA !== pairB) {
        lines.push(`  L${ln}: ${pairA} vs ${pairB}`);
        if (Math.abs(probDiff) > 0.01) {
          const better = probDiff > 0 ? 'A' : 'B';
          lines.push(`      #${rankA}: ${(ta.final_prob * 100).toFixed(0)}%  #${rankB}: ${(tb.final_prob * 100).toFixed(0)}%  (Lineup ${better} +${(Math.abs(probDiff) * 100).toFixed(1)}%)`);
          const eloDiff = ta.team_elo - tb.team_elo;
          if (Math.abs(eloDiff) > 5) {
            lines.push(`      Reason: Elo difference (${ta.team_elo.toFixed(0)} vs ${tb.team_elo.toFixed(0)})`);
          }
        }
      } else {
        lines.push(`  L${ln}: Same pair (${pairA})`);
      }
    }
    return lines.join('\n');
  },
};

// ─── ALTA Help System ───

const ALTAHelpSystem = {
  KNOWLEDGE_BASE: [
    {
      id: 'checker_number',
      intents: ['checker', 'checker number', 'what is checker', 'ascending order', 'lineup order', 'pair value'],
      category: 'ALTA Rules',
      title: 'Checker Number',
      answer: "A checker number is the sum of both partners' strength values.\nALTA requires checker numbers in ASCENDING order from Line 1\nto Line 5. Your strongest pair (lowest checker) must play\nLine 1, weakest pair plays Line 5.\n\nExample: Player A (value 1.5) + Player B (value 2.0) = 3.5 checker",
    },
    {
      id: 'two_thirds_rule',
      intents: ['2/3', 'two thirds', 'eligibility', 'eligible', 'playoff eligibility', 'can play', 'which line'],
      category: 'ALTA Rules',
      title: '2/3 Eligibility Rule',
      answer: "A player can only play a playoff line if at least 2/3 of\ntheir regular season matches were at that line or HIGHER\n(lower line number = higher/stronger).\n\nExample: If Sarah played 4 matches at L1 and 1 at L2,\nshe's eligible for L1-L5 (4/5 = 80% at L1 or higher).\nBut if she played 3 at L4 and 2 at L5, she can only play L4-L5.",
    },
    {
      id: 'movement_rules',
      intents: ['movement', 'move', 'line move', 'pair move', 'player move', '±1', '±2', 'transition', 'between rounds', 'round to round'],
      category: 'ALTA Rules',
      title: 'Movement Rules (Between Rounds)',
      answer: "ALTA limits how much a lineup can change between consecutive weeks:\n\n  • PAIR MOVEMENT (±1): If the same two players stay paired,\n    they can move at most 1 line up or down.\n  • PLAYER MOVEMENT (±2): Any individual player can move\n    at most 2 lines between consecutive weeks.\n  • CONTINUITY: These rules apply from the last regular\n    season week into Round 1 of playoffs.",
    },
    {
      id: 'strength_value',
      intents: ['strength', 'value', 'player value', 'how is strength calculated', 'alta value'],
      category: 'ALTA Rules',
      title: 'Player Strength Value',
      answer: "Each player's value = weighted average of the line numbers\nthey played during the regular season.\nLower value = stronger player (played higher lines).\n\nALTA uses this for the checker/ascending order rule.\nIt's separate from Elo rating — value is for LEGALITY,\nElo is for PERFORMANCE prediction.",
    },
    {
      id: 'playoffs_format',
      intents: ['playoff', 'playoffs', 'format', 'rounds', 'how many rounds', 'bracket', 'finals'],
      category: 'ALTA Rules',
      title: 'Playoff Format',
      answer: "ALTA playoffs have up to 4 rounds:\n  Rnd I  (May 2)  — Division playoffs\n  Rnd II (May 3)  — Division playoffs\n  Rnd III (May 10) — Flight playoffs\n  Finals (May 17) — City finals\n\n5 lines of doubles, 10 players per match.\nWin majority of lines (3 of 5) to advance.",
    },
    {
      id: 'how_to_start',
      intents: ['how to start', 'getting started', 'quick start', 'how to use', 'help', 'tutorial', 'guide'],
      category: 'Usage',
      title: 'Getting Started',
      answer: "How to use this tool:\n\n  1. GENERATE: Click Generate to create optimal lineups\n  2. ROSTER: View all players with Elo ratings\n  3. AVAILABILITY: Toggle players available/unavailable\n  4. WHAT-IF: Test scenarios with missing players\n  5. CHAT: Ask about rules, strategy, or how to use the tool\n\n  Options: Mode (balanced/aggressive/conservative)\n           Show Top N lineups",
    },
    {
      id: 'availability',
      intents: ['availability', 'unavailable', 'player out', 'missing player', "can't play", 'absent', 'mark unavailable'],
      category: 'Usage',
      title: 'Managing Player Availability',
      answer: "Use the Availability tab to toggle players:\n  1. Click a player to mark them unavailable\n  2. Unavailable players won't appear in lineups\n  3. Click again to make them available\n  4. Use 'Reset All' to make everyone available\n\nAvailability persists during your session.\nGenerate will respect your availability settings.",
    },
    {
      id: 'what_if',
      intents: ['what if', 'what-if', 'whatif', 'scenario', 'test', 'simulate', 'without player', 'remove player', 'impact'],
      category: 'Usage',
      title: 'What-If Scenarios',
      answer: "The What-If feature lets you test 'what happens if certain\nplayers are unavailable?' without changing your actual data.\n\nHow to use:\n  1. Go to the What-If tab\n  2. Check the players to REMOVE from the scenario\n  3. Click 'Run Scenario'\n  4. See impact: baseline vs scenario win probability\n  5. See the best lineup under the scenario\n\nChanges are temporary — your base data is never modified.",
    },
    {
      id: 'forced_excluded',
      intents: ['force pair', 'forced', 'exclude pair', 'excluded', 'pair override', 'captain override', 'keep together', 'split', 'separate'],
      category: 'Usage',
      title: 'Forced & Excluded Pairs',
      answer: "Captain overrides let you control pairings:\n\n  FORCED PAIRS: These two players MUST be together.\n  EXCLUDED PAIRS: These two players must NOT be paired.\n\nIf forced pairs make no legal lineup possible, the optimizer\nautomatically relaxes them and warns you.",
    },
    {
      id: 'modes',
      intents: ['mode', 'balanced', 'aggressive', 'conservative', 'strategy', 'which mode', 'when to use'],
      category: 'Usage',
      title: 'Optimization Modes',
      answer: "  BALANCED (default): Maximizes expected team match wins.\n  → Best for most matches.\n\n  AGGRESSIVE: Increases variance — bigger swings.\n  → Use when you're the underdog and need an upset.\n  → Boosts strong pairs more, but also boosts weak pairs.\n\n  CONSERVATIVE: Shrinks toward 50% — reduces risk.\n  → Use when you're the favorite and want to protect lead.\n  → More stable but less upside.",
    },
    {
      id: 'data_refresh',
      intents: ['refresh', 'reload', 'update data', 'new data', 'scrape', 're-scrape', 'after match'],
      category: 'Usage',
      title: 'Updating Data',
      answer: "This static version loads data from data.json.\nTo update with new match results, replace the data.json file\nand reload the page.",
    },
    {
      id: 'elo_rating',
      intents: ['elo', 'rating', 'how is elo calculated', 'what is elo', 'player rating', 'ranking'],
      category: 'Strategy',
      title: 'Elo Ratings',
      answer: "Each player has an Elo rating (higher = stronger).\nBase rating: 1500. Updated after each match using:\n  P(win) = 1 / (1 + 10^((Rb-Ra)/400))\n\nKey features:\n  • LINE-ADJUSTED: L1 opponents assumed stronger (1600)\n    vs L5 opponents weaker (1400), so a L1 player's\n    losses don't unfairly tank their rating.\n  • HIGH K-FACTOR (K=40): Adapts fast with few matches.\n  • UNCERTAINTY (±RD): Shrinks with more matches.\n    High RD → win probability pulled toward 50%.",
    },
    {
      id: 'chemistry',
      intents: ['chemistry', 'pair history', 'played together', 'partnership', 'pair record'],
      category: 'Strategy',
      title: 'Pair Chemistry',
      answer: "Chemistry = how well two players perform as a pair,\nbased on their win/loss record when playing together.\n\n  • Score ranges from 0.3 (all losses) to 1.0 (all wins)\n  • New pairs with no history get 0.5 (neutral)\n  • Chemistry adds a small bonus/penalty to win probability\n  • With limited data, the bonus is intentionally small\n    to avoid overfitting to few matches",
    },
    {
      id: 'win_probability',
      intents: ['win probability', 'how is probability calculated', 'team win', 'match win', 'scoring', 'how scored'],
      category: 'Strategy',
      title: 'How Win Probability Works',
      answer: "Win probability is calculated in layers:\n\n  1. BASE: Elo formula for each pair vs line opponent\n  2. CHEMISTRY: Small bonus from partnership history\n  3. CONFIDENCE: Uncertain pairs pulled toward 50%\n  4. MODE: Aggressive boosts, conservative shrinks\n  5. TEAM WIN: Probability of winning 3+ of 5 lines\n     (computed via dynamic programming over all\n     independent line win probabilities)\n\nThe team win% is what matters — it accounts for\nneeding a MAJORITY of lines, not just sum of probs.",
    },
    {
      id: 'sandbagging',
      intents: ['sandbagging', 'sandbag', 'tian ji', 'stack', 'stacking', 'gaming'],
      category: 'Strategy',
      title: 'Anti-Sandbagging Rules',
      answer: "ALTA's checker rules prevent 田忌赛马 (Tian Ji's horse\nracing strategy) — intentionally putting strong players\non lower lines to gain matchup advantages.\n\nThree rules work together:\n  1. ASCENDING CHECKER: Strongest pair must play L1\n  2. 2/3 RULE: Players locked to lines they've played\n  3. MOVEMENT ±1/±2: Can't suddenly shift lineup\n\nThe optimizer respects all these — every generated\nlineup is ALTA-legal.",
    },
  ],

  answer(query) {
    const queryLower = (query || '').toLowerCase().trim();
    let queryNorm = queryLower.replace(/-/g, ' ').replace(/  +/g, ' ');
    let queryCore = queryNorm;
    for (const prefix of ['what is a ', 'what is an ', 'what is the ', 'what is ', 'what are ', 'what does ', 'how does ', 'how do ', 'tell me about ']) {
      if (queryCore.startsWith(prefix)) {
        queryCore = queryCore.substring(prefix.length);
        break;
      }
    }

    if (!queryLower) return this._listTopics();

    let bestScore = 0;
    let bestEntry = null;

    for (const entry of this.KNOWLEDGE_BASE) {
      let score = 0;
      for (const intent of entry.intents) {
        const intentLower = intent.toLowerCase();
        const intentNorm = intentLower.replace(/-/g, ' ').replace(/  +/g, ' ');
        if (intentLower === queryLower || intentNorm === queryNorm || intentNorm === queryCore) {
          score = Math.max(score, 100);
        } else if (queryCore === intentNorm) {
          score = Math.max(score, 95);
        } else if (queryNorm.includes(intentNorm)) {
          score = Math.max(score, 70 + intentNorm.length);
        } else if (intentNorm.includes(queryNorm) || intentNorm.includes(queryCore)) {
          score = Math.max(score, 50 + queryCore.length);
        } else {
          const stopWords = new Set(['what', 'is', 'a', 'an', 'the', 'how', 'does', 'do', 'are', 'tell', 'me', 'about']);
          const queryWords = new Set(queryNorm.split(' ').filter(w => !stopWords.has(w)));
          const intentWords = new Set(intentNorm.split(' '));
          let overlap = 0;
          for (const w of queryWords) {
            if (intentWords.has(w)) overlap++;
          }
          if (overlap > 0) score = Math.max(score, 30 + overlap * 10);
        }
      }
      if (score > bestScore) {
        bestScore = score;
        bestEntry = entry;
      }
    }

    if (bestScore >= 30 && bestEntry) {
      return `  [${bestEntry.category}] ${bestEntry.title}\n  ${'─'.repeat(50)}\n  ${bestEntry.answer}`;
    }
    return this._noMatch(query);
  },

  _listTopics() {
    const lines = ['  Available Topics:', `  ${'─'.repeat(50)}`];
    const categories = {};
    for (const entry of this.KNOWLEDGE_BASE) {
      if (!categories[entry.category]) categories[entry.category] = [];
      categories[entry.category].push(entry.title);
    }
    for (const [cat, titles] of Object.entries(categories)) {
      lines.push(`\n  ${cat}:`);
      for (const title of titles) lines.push(`    • ${title}`);
    }
    lines.push('\n  Type a question or keyword to learn more.');
    return lines.join('\n');
  },

  _noMatch(query) {
    const lines = [`  I don't have a specific answer for '${query}'.`, '  Try one of these topics:', ''];
    for (const entry of this.KNOWLEDGE_BASE.slice(0, 5)) {
      lines.push(`    • ${entry.title}: ${entry.intents[0]}`);
    }
    lines.push('', '  Or type just Enter to see all topics.');
    return lines.join('\n');
  },
};
