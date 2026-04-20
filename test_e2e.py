"""
ALTA Playoff Optimizer — Playwright E2E Tests
Tests all 5 panels: Generate, Roster, Availability, What-If, Help

Run: python -m pytest test_e2e.py -v --headed   (to watch)
     python -m pytest test_e2e.py -v             (headless)

Requires: pip install pytest-playwright && python -m playwright install chromium
Server must be running: python app.py alta_team_data.json
"""

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5000"


@pytest.fixture(autouse=True)
def navigate(page: Page):
    """Navigate to app and wait for header to load before each test."""
    page.goto(BASE_URL)
    page.wait_for_function(
        "document.getElementById('team-name').textContent !== 'Loading...'",
        timeout=15000,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Header & Team Info
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHeader:
    def test_page_title(self, page: Page):
        expect(page).to_have_title("ALTA Playoff Optimizer")

    def test_team_name_loads(self, page: Page):
        expect(page.locator("#team-name")).to_have_text("PENHURST")

    def test_team_league_loads(self, page: Page):
        expect(page.locator("#team-league")).to_contain_text("C-8")

    def test_team_rank(self, page: Page):
        expect(page.locator("#team-rank")).to_have_text("#1")

    def test_team_record(self, page: Page):
        expect(page.locator("#team-record")).to_contain_text("22/25")

    def test_roster_size(self, page: Page):
        expect(page.locator("#team-roster-size")).to_have_text("19")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Navigation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNavigation:
    def test_generate_panel_active_by_default(self, page: Page):
        expect(page.locator("#panel-generate")).to_have_class(re.compile(r"active"))
        expect(page.locator('[data-panel="generate"]')).to_have_class(re.compile(r"active"))

    def test_switch_to_roster(self, page: Page):
        page.click('[data-panel="roster"]')
        expect(page.locator("#panel-roster")).to_have_class(re.compile(r"active"))
        expect(page.locator("#panel-generate")).not_to_have_class(re.compile(r"active"))

    def test_switch_to_availability(self, page: Page):
        page.click('[data-panel="availability"]')
        expect(page.locator("#panel-availability")).to_have_class(re.compile(r"active"))

    def test_switch_to_whatif(self, page: Page):
        page.click('[data-panel="whatif"]')
        expect(page.locator("#panel-whatif")).to_have_class(re.compile(r"active"))

    def test_switch_to_help(self, page: Page):
        page.click('[data-panel="help"]')
        expect(page.locator("#panel-help")).to_have_class(re.compile(r"active"))

    def test_nav_highlights_active_tab(self, page: Page):
        page.click('[data-panel="help"]')
        expect(page.locator('[data-panel="help"]')).to_have_class(re.compile(r"active"))
        expect(page.locator('[data-panel="generate"]')).not_to_have_class(re.compile(r"active"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Generate Lineup Panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestGenerate:
    def test_generate_controls_visible(self, page: Page):
        expect(page.locator("#sel-mode")).to_be_visible()
        expect(page.locator("#sel-topn")).to_be_visible()
        expect(page.locator("#btn-generate")).to_be_visible()

    def test_default_mode_is_balanced(self, page: Page):
        expect(page.locator("#sel-mode")).to_have_value("balanced")

    def test_default_top_n_is_3(self, page: Page):
        expect(page.locator("#sel-topn")).to_have_value("3")

    def test_generate_shows_lineups(self, page: Page):
        page.select_option("#sel-topn", "2")
        page.click("#btn-generate")
        # Wait for results (optimizer takes a few seconds)
        page.wait_for_selector(".lineup-card", timeout=30000)

        cards = page.locator(".lineup-card")
        assert cards.count() >= 2

    def test_lineup_has_5_lines(self, page: Page):
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        lines = page.locator(".lineup-card >> .line-row")
        assert lines.count() == 5

    def test_lineup_shows_win_probability(self, page: Page):
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        win = page.locator(".lineup-win").first
        text = win.text_content()
        # Should be a percentage like "59.3%"
        assert "%" in text
        pct = float(text.replace("%", ""))
        assert 10 < pct < 100

    def test_lineup_has_player_names(self, page: Page):
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        players = page.locator(".line-player")
        assert players.count() >= 10  # 5 lines × 2 players

    def test_lineup_checkers_ascending(self, page: Page):
        """ALTA rule: checker numbers must be ascending L1→L5."""
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        checkers = page.locator(".line-checker")
        vals = []
        for i in range(checkers.count()):
            text = checkers.nth(i).text_content()
            # Extract number from "chk 2.5 · new pair"
            match = re.search(r"chk\s+([\d.]+)", text)
            if match:
                vals.append(float(match.group(1)))

        assert len(vals) == 5
        for i in range(len(vals) - 1):
            assert vals[i] <= vals[i + 1], f"Checker {vals[i]} > {vals[i+1]} — not ascending!"

    def test_explanation_toggle(self, page: Page):
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        # Explanation should be collapsed initially
        explain = page.locator(".explain-content").first
        expect(explain).not_to_have_class(re.compile(r"open"))

        # Click toggle to expand
        page.locator(".explain-toggle").first.click()
        expect(explain).to_have_class(re.compile(r"open"))

        # Should contain Elo-related content
        text = explain.text_content()
        assert "Elo" in text or "elo" in text.lower() or "Line" in text

    def test_aggressive_mode(self, page: Page):
        page.select_option("#sel-mode", "aggressive")
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        # Results should load and mention the mode
        results_text = page.locator("#lineup-results").text_content()
        assert "aggressive" in results_text.lower()

    def test_conservative_mode(self, page: Page):
        page.select_option("#sel-mode", "conservative")
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        results_text = page.locator("#lineup-results").text_content()
        assert "conservative" in results_text.lower()

    def test_comparison_shown_with_top2(self, page: Page):
        page.select_option("#sel-topn", "2")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        # Should show "Why #1 Over #2" comparison
        expect(page.get_by_role("heading", name="Why #1 Over #")).to_be_visible()

    def test_total_found_shown(self, page: Page):
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        text = page.locator("#lineup-results").text_content()
        assert "Found" in text
        assert "legal lineups" in text

    def test_button_disables_during_generation(self, page: Page):
        page.click("#btn-generate")
        # Button should be disabled immediately
        expect(page.locator("#btn-generate")).to_be_disabled()
        # Wait for it to re-enable
        page.wait_for_selector("#btn-generate:not([disabled])", timeout=30000)
        expect(page.locator("#btn-generate")).to_be_enabled()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Roster Panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRoster:
    def test_roster_loads_on_click(self, page: Page):
        page.click('[data-panel="roster"]')
        # Wait for roster data to load (triggers API call)
        page.wait_for_selector("#roster-body tr td strong", timeout=15000)

        rows = page.locator("#roster-body tr")
        assert rows.count() == 19

    def test_roster_shows_player_names(self, page: Page):
        page.click('[data-panel="roster"]')
        page.wait_for_selector("#roster-body tr td strong", timeout=15000)

        text = page.locator("#roster-body").text_content()
        assert "BEISHER, SARAH" in text
        assert "GONG, MICHELLE" in text

    def test_roster_shows_elo_ratings(self, page: Page):
        page.click('[data-panel="roster"]')
        page.wait_for_selector("#roster-body tr td strong", timeout=15000)

        # First player's Elo should be a number around 1500-1600
        first_row = page.locator("#roster-body tr").first
        cells = first_row.locator("td")
        elo_text = cells.nth(2).text_content().strip()
        elo = int(elo_text)
        assert 1400 < elo < 1700

    def test_roster_shows_confidence_badges(self, page: Page):
        page.click('[data-panel="roster"]')
        page.wait_for_selector("#roster-body tr td strong", timeout=15000)

        badges = page.locator("#roster-body .badge")
        assert badges.count() == 19

    def test_roster_shows_eligible_lines(self, page: Page):
        page.click('[data-panel="roster"]')
        page.wait_for_selector("#roster-body tr td strong", timeout=15000)

        text = page.locator("#roster-body").text_content()
        # Some players should be eligible for specific lines
        assert "L1" in text
        assert "L5" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. Availability Panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAvailability:
    def test_availability_shows_all_players(self, page: Page):
        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        toggles = page.locator("#avail-grid .avail-toggle")
        assert toggles.count() == 19

    def test_toggle_player_unavailable(self, page: Page):
        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        first = page.locator("#avail-grid .avail-toggle").first
        expect(first).not_to_have_class(re.compile(r"unavailable"))

        first.click()
        expect(first).to_have_class(re.compile(r"unavailable"))

    def test_toggle_back_to_available(self, page: Page):
        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        first = page.locator("#avail-grid .avail-toggle").first
        first.click()  # → unavailable
        expect(first).to_have_class(re.compile(r"unavailable"))
        first.click()  # → available again
        expect(first).not_to_have_class(re.compile(r"unavailable"))

    def test_availability_counter(self, page: Page):
        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        expect(page.locator("#avail-count")).to_contain_text("19 available, 0 out")

        page.locator("#avail-grid .avail-toggle").first.click()
        expect(page.locator("#avail-count")).to_contain_text("18 available, 1 out")

    def test_reset_all_available(self, page: Page):
        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        # Toggle 2 players off
        page.locator("#avail-grid .avail-toggle").nth(0).click()
        page.locator("#avail-grid .avail-toggle").nth(1).click()
        expect(page.locator("#avail-count")).to_contain_text("17 available, 2 out")

        # Reset
        page.click("text=Reset All Available")
        expect(page.locator("#avail-count")).to_contain_text("19 available, 0 out")

        unavail = page.locator("#avail-grid .avail-toggle.unavailable")
        assert unavail.count() == 0

    def test_unavailable_affects_generate(self, page: Page):
        """Marking players unavailable should affect lineup generation."""
        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        # Remove last 3 players (least critical, won't break eligibility)
        toggles = page.locator("#avail-grid .avail-toggle")
        total = toggles.count()
        names_to_remove = []
        for i in range(total - 3, total):
            names_to_remove.append(toggles.nth(i).text_content().strip())
            toggles.nth(i).click()

        # Switch to generate and run
        page.click('[data-panel="generate"]')
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        # Check that unavailable players don't appear in the LINEUP PAIRINGS
        lineup_players = page.locator(".line-player")
        pairing_names = set()
        for i in range(lineup_players.count()):
            pairing_names.add(lineup_players.nth(i).text_content().strip())

        for name in names_to_remove:
            assert name not in pairing_names, f"{name} is unavailable but appears in lineup pairings!"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. What-If Panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestWhatIf:
    def test_whatif_shows_player_checkboxes(self, page: Page):
        page.click('[data-panel="whatif"]')
        page.wait_for_selector("#whatif-players .avail-toggle", timeout=15000)

        checkboxes = page.locator("#whatif-players input[type='checkbox']")
        assert checkboxes.count() == 19

    def test_whatif_run_no_changes(self, page: Page):
        """Running with no removals should show ~0% impact."""
        page.click('[data-panel="whatif"]')
        page.wait_for_selector("#whatif-players .avail-toggle", timeout=15000)

        page.click("#btn-whatif")
        page.wait_for_selector(".impact-box", timeout=30000)

        impact_text = page.locator(".impact-value").text_content()
        # Should be +0.0% or very close to 0
        val = float(impact_text.replace("%", "").replace("+", ""))
        assert abs(val) < 1.0, f"Impact should be ~0% with no changes, got {val}"

    def test_whatif_removing_player_shows_impact(self, page: Page):
        page.click('[data-panel="whatif"]')
        page.wait_for_selector("#whatif-players .avail-toggle", timeout=15000)

        # Check first 3 players to remove
        for i in range(3):
            page.locator("#whatif-players input[type='checkbox']").nth(i).check()

        page.click("#btn-whatif")
        page.wait_for_selector(".impact-box", timeout=30000)

        # Should show some impact
        expect(page.locator(".impact-box")).to_be_visible()
        expect(page.locator(".impact-value")).to_be_visible()

        # Should show baseline and scenario
        text = page.locator(".impact-box").text_content()
        assert "Baseline" in text
        assert "Scenario" in text

    def test_whatif_shows_scenario_lineup(self, page: Page):
        page.click('[data-panel="whatif"]')
        page.wait_for_selector("#whatif-players .avail-toggle", timeout=15000)

        # Check last player (least impactful) to avoid breaking lineup generation
        page.locator("#whatif-players input[type='checkbox']").last.check()
        page.click("#btn-whatif")
        page.wait_for_selector(".impact-box", timeout=30000)

        expect(page.locator("text=Best Lineup Under Scenario")).to_be_visible()

    def test_whatif_button_disables_during_run(self, page: Page):
        page.click('[data-panel="whatif"]')
        page.wait_for_selector("#whatif-players .avail-toggle", timeout=15000)

        page.click("#btn-whatif")
        expect(page.locator("#btn-whatif")).to_be_disabled()
        page.wait_for_selector("#btn-whatif:not([disabled])", timeout=30000)
        expect(page.locator("#btn-whatif")).to_be_enabled()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. Help / Ask Panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHelp:
    def _open_chat(self, page: Page):
        """Open the floating chat widget."""
        page.click("#chat-fab")
        page.wait_for_selector(".chat-widget.open", timeout=5000)

    def test_help_welcome_message(self, page: Page):
        self._open_chat(page)
        expect(page.locator(".help-msg.answer")).to_be_visible()
        text = page.locator(".help-msg.answer").first.text_content()
        assert "Welcome" in text

    def test_ask_about_checker(self, page: Page):
        self._open_chat(page)
        page.fill("#chat-input", "checker number")
        page.click(".chat-send-btn")

        # Wait for answer to appear (question + answer renders new .help-msg elements)
        page.wait_for_function(
            "document.querySelectorAll('.help-msg').length >= 3",
            timeout=10000,
        )
        msgs = page.locator(".help-msg")
        assert msgs.count() >= 3

        answer_text = msgs.last.text_content()
        assert "checker" in answer_text.lower() or "ascending" in answer_text.lower()

    def test_ask_about_elo(self, page: Page):
        self._open_chat(page)
        page.fill("#chat-input", "elo")
        page.click(".chat-send-btn")

        page.wait_for_function(
            "document.querySelectorAll('.help-msg').length >= 3",
            timeout=10000,
        )
        last_answer = page.locator(".help-msg.answer").last
        text = last_answer.text_content()
        assert "elo" in text.lower() or "rating" in text.lower()

    def test_ask_with_enter_key(self, page: Page):
        self._open_chat(page)
        page.fill("#chat-input", "modes")
        page.press("#chat-input", "Enter")

        page.wait_for_function(
            "document.querySelectorAll('.help-msg').length >= 3",
            timeout=10000,
        )
        answer = page.locator(".help-msg.answer").last
        text = answer.text_content()
        assert "balanced" in text.lower() or "aggressive" in text.lower() or "conservative" in text.lower()

    def test_ask_clears_input(self, page: Page):
        self._open_chat(page)
        page.fill("#chat-input", "elo")
        page.click(".chat-send-btn")
        page.wait_for_function(
            "document.querySelectorAll('.help-msg').length >= 3",
            timeout=10000,
        )

        expect(page.locator("#chat-input")).to_have_value("")

    def test_question_shown_in_chat(self, page: Page):
        self._open_chat(page)
        page.fill("#chat-input", "what is sandbagging")
        page.click(".chat-send-btn")

        page.wait_for_function(
            "document.querySelectorAll('.help-msg.question').length >= 1",
            timeout=10000,
        )
        question_msg = page.locator(".help-msg.question").first
        expect(question_msg).to_contain_text("what is sandbagging")

    def test_multiple_questions(self, page: Page):
        self._open_chat(page)

        page.fill("#chat-input", "checker")
        page.click(".chat-send-btn")
        page.wait_for_function(
            "document.querySelectorAll('.help-msg').length >= 3",
            timeout=10000,
        )

        page.fill("#chat-input", "elo")
        page.click(".chat-send-btn")
        page.wait_for_function(
            "document.querySelectorAll('.help-msg.question').length >= 2",
            timeout=10000,
        )

        questions = page.locator(".help-msg.question")
        assert questions.count() >= 2

    def test_unknown_question_still_responds(self, page: Page):
        self._open_chat(page)
        page.fill("#chat-input", "xyzzy nonsense query")
        page.click(".chat-send-btn")

        # Wait for answer to appear: welcome(1) + question(2) + answer(3)
        page.wait_for_function(
            "document.querySelectorAll('.help-msg').length >= 3",
            timeout=15000,
        )

        msgs = page.locator(".help-msg")
        assert msgs.count() >= 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. Cross-panel Integration Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIntegration:
    def test_mode_persists_across_panels(self, page: Page):
        """Mode selection should persist when switching panels."""
        page.select_option("#sel-mode", "aggressive")

        # Switch to roster and back
        page.click('[data-panel="roster"]')
        page.click('[data-panel="generate"]')

        expect(page.locator("#sel-mode")).to_have_value("aggressive")

    def test_generate_then_view_roster(self, page: Page):
        """Can generate lineup then view roster without errors."""
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        page.click('[data-panel="roster"]')
        page.wait_for_selector("#roster-body tr td strong", timeout=30000)
        rows = page.locator("#roster-body tr")
        assert rows.count() == 19

    def test_full_workflow(self, page: Page):
        """End-to-end: check availability → generate → view explanation → ask question."""
        # 1. Set availability
        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        # 2. Generate
        page.click('[data-panel="generate"]')
        page.select_option("#sel-topn", "1")
        page.click("#btn-generate")
        page.wait_for_selector(".lineup-card", timeout=30000)

        # 3. Expand explanation
        page.locator(".explain-toggle").first.click()
        explain = page.locator(".explain-content").first
        expect(explain).to_have_class(re.compile(r"open"))

        # 4. Ask a question via floating chat
        page.click("#chat-fab")
        page.wait_for_selector(".chat-widget.open", timeout=5000)
        page.fill("#chat-input", "win probability")
        page.click(".chat-send-btn")
        # Wait for answer: welcome(1) + question(2) + answer(3)
        page.wait_for_function(
            "document.querySelectorAll('.help-msg').length >= 3",
            timeout=15000,
        )
        answer = page.locator(".help-msg.answer").last
        text = answer.text_content()
        assert "win" in text.lower() or "probability" in text.lower() or "prob" in text.lower()

    def test_no_console_errors(self, page: Page):
        """No JS errors should appear during normal usage."""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))

        page.click('[data-panel="roster"]')
        page.wait_for_selector("#roster-body tr td strong", timeout=15000)

        page.click('[data-panel="availability"]')
        page.wait_for_selector("#avail-grid .avail-toggle", timeout=15000)

        page.click("#chat-fab")
        page.wait_for_selector(".chat-widget.open", timeout=5000)
        page.fill("#chat-input", "elo")
        page.click(".chat-send-btn")
        page.wait_for_timeout(2000)

        assert len(errors) == 0, f"JS console errors: {errors}"
