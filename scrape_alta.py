#!/usr/bin/env python3
"""
ALTA Team Data Scraper
======================
Uses your existing Edge browser cookies to access ALTA without re-login.

Usage:
    python scrape_alta.py

The script will:
1. Extract ALTA cookies from your Edge browser
2. Open a Playwright browser with those cookies
3. Navigate to your team pages and scrape all data
4. Save results to team_data.json (ready for lineup_optimizer.py)
"""

import asyncio
import json
import re
import sys
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright, Page, BrowserContext

ALTA_BASE = "https://www.altatennis.org"
ALTA_LOGIN = f"{ALTA_BASE}/Member/Dashboard.aspx?clear=1&sl=1"
ALTA_DASHBOARD = f"{ALTA_BASE}/Member/Dashboard.aspx"
ALTA_TEAM_DASHBOARD = f"{ALTA_BASE}/Member/Teams/Dashboard.aspx"

OUTPUT_FILE = "team_data.json"


def extract_edge_cookies(domain_filter="altatennis.org"):
    """Extract cookies from Edge's cookie database for the given domain."""
    edge_cookie_path = os.path.join(
        os.environ["LOCALAPPDATA"], "Microsoft", "Edge", "User Data", "Default", "Network", "Cookies"
    )
    if not os.path.exists(edge_cookie_path):
        # Try alternate path (older Edge versions)
        edge_cookie_path = os.path.join(
            os.environ["LOCALAPPDATA"], "Microsoft", "Edge", "User Data", "Default", "Cookies"
        )

    if not os.path.exists(edge_cookie_path):
        print(f"  ✗ Edge cookie file not found")
        return []

    # Copy the cookie file — Edge locks it, so use raw file read as fallback
    tmp_cookie = os.path.join(tempfile.gettempdir(), "edge_cookies_copy.db")
    try:
        # Try raw binary copy to bypass lock
        with open(edge_cookie_path, "rb") as src:
            data = src.read()
        with open(tmp_cookie, "wb") as dst:
            dst.write(data)
    except PermissionError:
        print(f"  ⚠ Cookie file locked by Edge — will use Edge profile directly")
        return []

    cookies = []
    try:
        conn = sqlite3.connect(tmp_cookie)
        cursor = conn.cursor()

        # Query cookies for ALTA domain
        cursor.execute("""
            SELECT name, value, host_key, path, expires_utc, is_secure, is_httponly
            FROM cookies
            WHERE host_key LIKE ?
        """, (f"%{domain_filter}%",))

        for row in cursor.fetchall():
            name, value, host, path, expires, secure, httponly = row
            # Skip encrypted cookies with empty value (Edge encrypts them)
            cookie = {
                "name": name,
                "value": value,
                "domain": host,
                "path": path,
                "secure": bool(secure),
                "httpOnly": bool(httponly),
            }
            if expires:
                # Chrome/Edge epoch is 1601-01-01, convert to unix
                chrome_epoch = 11644473600
                unix_expires = (expires / 1000000) - chrome_epoch
                if unix_expires > 0:
                    cookie["expires"] = unix_expires
            cookies.append(cookie)

        conn.close()
    except Exception as e:
        print(f"  ✗ Error reading cookies: {e}")
    finally:
        try:
            os.remove(tmp_cookie)
        except:
            pass

    return cookies


async def check_logged_in(page: Page) -> bool:
    """Check if we're logged in (not on login page)."""
    url = page.url.lower()
    if "login.aspx" in url:
        return False
    # Check for login form
    login_btn = await page.query_selector("#ctl00_ctl00_CPHolder_CPHolder_btnSingin")
    return login_btn is None


async def scrape_page(page: Page, url: str, label: str) -> dict:
    """Navigate to a URL and return text + HTML."""
    print(f"  Scraping: {label} ...")
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        text = await page.inner_text("body")
        html = await page.content()
        print(f"    ✓ Got {len(text)} chars")
        return {"text": text, "html": html, "url": url}
    except Exception as e:
        print(f"    ✗ Error: {e}")
        return {"text": "", "html": "", "url": url, "error": str(e)}


async def find_all_links(page: Page, keywords: list) -> list:
    """Find all links on page matching keywords."""
    links = await page.query_selector_all("a[href]")
    results = []
    seen = set()

    for link in links:
        try:
            href = await link.get_attribute("href") or ""
            text = (await link.inner_text()).strip()
            combined = f"{href} {text}".lower()

            if any(kw in combined for kw in keywords) and href not in seen:
                full_url = href if href.startswith("http") else f"{ALTA_BASE}{href}"
                results.append({"url": full_url, "text": text, "href": href})
                seen.add(href)
        except:
            continue

    return results


async def scrape_tables(page: Page) -> list:
    """Extract all tables from the current page."""
    tables = []
    table_els = await page.query_selector_all("table")
    for idx, table in enumerate(table_els):
        try:
            text = await table.inner_text()
            if len(text.strip()) > 30:
                # Also get structured data via rows
                rows = await table.query_selector_all("tr")
                row_data = []
                for row in rows:
                    cells = await row.query_selector_all("td, th")
                    cell_texts = []
                    for cell in cells:
                        cell_texts.append((await cell.inner_text()).strip())
                    if cell_texts:
                        row_data.append(cell_texts)
                tables.append({"index": idx, "text": text, "rows": row_data})
        except:
            continue
    return tables


async def scrape_dropdowns(page: Page) -> dict:
    """Extract all dropdown options from the current page."""
    dropdowns = {}
    selects = await page.query_selector_all("select")
    for sel in selects:
        try:
            sel_id = await sel.get_attribute("id") or await sel.get_attribute("name") or "unknown"
            options = await sel.query_selector_all("option")
            opts = []
            for opt in options:
                text = (await opt.inner_text()).strip()
                val = await opt.get_attribute("value") or ""
                selected = await opt.get_attribute("selected")
                opts.append({"text": text, "value": val, "selected": selected is not None})
            dropdowns[sel_id] = opts
        except:
            continue
    return dropdowns


async def parse_team_view_table(tables: list) -> list:
    """Parse the Team View table into structured player data.
    
    Expected columns: Player, Value, Week1, Week2, ..., W, L, Win%
    Each week cell contains line number and W/L result.
    """
    players = []
    for table in tables:
        rows = table.get("rows", [])
        if not rows:
            continue
        
        # Find the header row with "Value" column
        header_idx = -1
        for i, row in enumerate(rows):
            if any("value" in cell.lower() for cell in row if cell):
                header_idx = i
                break
        
        if header_idx < 0:
            continue
        
        headers = rows[header_idx]
        # Find column indices
        val_col = next((i for i, h in enumerate(headers) if "value" in h.lower()), -1)
        name_col = next((i for i, h in enumerate(headers) 
                        if any(kw in h.lower() for kw in ["player", "name", "member"])), 0)
        
        if val_col < 0:
            continue
        
        # Find W, L, Win% columns (usually at the end)
        w_col = next((i for i, h in enumerate(headers) if h.strip() == "W"), -1)
        l_col = next((i for i, h in enumerate(headers) if h.strip() == "L"), -1)
        
        # Week columns are between name/value and W/L
        week_start = val_col + 1
        week_end = w_col if w_col > 0 else len(headers) - 3
        
        for row in rows[header_idx + 1:]:
            if len(row) <= val_col:
                continue
            name = row[name_col].strip() if name_col < len(row) else ""
            if not name or name.lower() in ["total", "team", ""]:
                continue
            
            try:
                value = float(row[val_col]) if row[val_col].strip() else None
            except (ValueError, IndexError):
                value = None
            
            # Parse weekly results
            weekly = []
            for wi in range(week_start, min(week_end, len(row))):
                cell = row[wi].strip() if wi < len(row) else ""
                if cell:
                    weekly.append(cell)
            
            wins = 0
            losses = 0
            if w_col >= 0 and w_col < len(row):
                try:
                    wins = int(row[w_col])
                except (ValueError, IndexError):
                    pass
            if l_col >= 0 and l_col < len(row):
                try:
                    losses = int(row[l_col])
                except (ValueError, IndexError):
                    pass
            
            players.append({
                "name": name,
                "alta_value": value,
                "weekly_results": weekly,
                "wins": wins,
                "losses": losses,
            })
        
        if players:
            break  # Found the right table
    
    return players
    dropdowns = {}
    selects = await page.query_selector_all("select")
    for sel in selects:
        try:
            sel_id = await sel.get_attribute("id") or await sel.get_attribute("name") or "unknown"
            options = await sel.query_selector_all("option")
            opts = []
            for opt in options:
                text = (await opt.inner_text()).strip()
                val = await opt.get_attribute("value") or ""
                selected = await opt.get_attribute("selected")
                opts.append({"text": text, "value": val, "selected": selected is not None})
            dropdowns[sel_id] = opts
        except:
            continue
    return dropdowns


async def click_and_scrape(page: Page, selector: str, label: str) -> dict:
    """Click an element and scrape the resulting page."""
    try:
        el = await page.query_selector(selector)
        if el:
            await el.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)
            text = await page.inner_text("body")
            tables = await scrape_tables(page)
            print(f"    ✓ Clicked {label}, got {len(text)} chars, {len(tables)} tables")
            return {"text": text, "tables": tables}
    except Exception as e:
        print(f"    ✗ Click error on {label}: {e}")
    return {"text": "", "tables": []}


async def main():
    print("=" * 60)
    print("  ALTA TEAM DATA SCRAPER (Edge Session)")
    print("  Using your existing Edge login")
    print("=" * 60)

    # Step 1: Extract cookies from Edge
    print("\n  Phase 0: Extracting Edge cookies for altatennis.org...")
    cookies = extract_edge_cookies("altatennis.org")
    print(f"  Found {len(cookies)} ALTA cookies")

    if not cookies:
        print("\n  ⚠ No cookies found. Possible reasons:")
        print("    - Edge encrypts cookies (DPAPI) - will try launching Edge directly")
        print("    - Falling back to Edge channel launch with user profile copy...")

    async with async_playwright() as p:
        browser = None
        context = None
        page = None

        # Strategy 1: Try connecting to running Edge via CDP
        # Strategy 2: Launch Edge with persistent context (copies profile)
        # Strategy 3: Launch Edge fresh and let user log in

        # Strategy 1: Try CDP connection to running Edge
        print("\n  Trying to connect to running Edge via CDP...")
        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                pages = context.pages
                page = pages[0] if pages else await context.new_page()
                print("  ✓ Connected to running Edge!")
            else:
                print("  ⚠ Connected but no contexts found")
                browser = None
        except Exception as e:
            print(f"  CDP not available (Edge not started with --remote-debugging-port)")

        # Strategy 2: Launch Edge with user's profile (persistent context)
        if browser is None:
            print("\n  Launching Edge with your profile...")
            edge_profile = os.path.join(
                os.environ["LOCALAPPDATA"], "Microsoft", "Edge", "User Data"
            )
            # We need a temp copy since Edge is using the profile
            temp_profile = os.path.join(tempfile.gettempdir(), "alta_edge_profile")

            # Copy just the essential session files
            try:
                if os.path.exists(temp_profile):
                    shutil.rmtree(temp_profile, ignore_errors=True)

                # Copy the profile directory (minimal)
                os.makedirs(temp_profile, exist_ok=True)
                default_src = os.path.join(edge_profile, "Default")
                default_dst = os.path.join(temp_profile, "Default")
                os.makedirs(default_dst, exist_ok=True)

                # Copy essential files for session persistence
                for fname in ["Preferences", "Secure Preferences", "Login Data",
                              "Web Data", "Favicons"]:
                    src = os.path.join(default_src, fname)
                    if os.path.exists(src):
                        try:
                            with open(src, "rb") as f:
                                data = f.read()
                            with open(os.path.join(default_dst, fname), "wb") as f:
                                f.write(data)
                        except:
                            pass

                # Copy cookies
                for cookie_path in [
                    os.path.join(default_src, "Network", "Cookies"),
                    os.path.join(default_src, "Cookies")
                ]:
                    if os.path.exists(cookie_path):
                        dst_dir = os.path.dirname(cookie_path).replace(default_src, default_dst)
                        os.makedirs(dst_dir, exist_ok=True)
                        try:
                            with open(cookie_path, "rb") as f:
                                data = f.read()
                            dst_path = cookie_path.replace(default_src, default_dst)
                            with open(dst_path, "wb") as f:
                                f.write(data)
                            print(f"  ✓ Copied cookie database")
                        except:
                            pass

                # Copy Local State (needed for cookie decryption key)
                local_state = os.path.join(edge_profile, "Local State")
                if os.path.exists(local_state):
                    try:
                        with open(local_state, "rb") as f:
                            data = f.read()
                        with open(os.path.join(temp_profile, "Local State"), "wb") as f:
                            f.write(data)
                        print(f"  ✓ Copied Local State (encryption keys)")
                    except:
                        pass

                context = await p.chromium.launch_persistent_context(
                    temp_profile,
                    channel="msedge",
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                print("  ✓ Edge launched with profile copy")

            except Exception as e:
                print(f"  ⚠ Profile copy failed: {e}")

        # Strategy 3: Fresh Edge, user logs in manually
        if page is None:
            print("\n  Launching fresh Edge browser...")
            browser = await p.chromium.launch(
                channel="msedge",
                headless=False,
                args=["--start-maximized"]
            )
            context = await browser.new_context(viewport={"width": 1280, "height": 900})

            # Inject cookies if we got any
            if cookies:
                valid_cookies = []
                for c in cookies:
                    pc = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c["path"],
                    }
                    if c.get("expires") and c["expires"] > 0:
                        pc["expires"] = c["expires"]
                    if c.get("secure"):
                        pc["secure"] = True
                    if c.get("httpOnly"):
                        pc["httpOnly"] = True
                    valid_cookies.append(pc)
                try:
                    await context.add_cookies(valid_cookies)
                    print(f"  ✓ Injected {len(valid_cookies)} cookies")
                except Exception as e:
                    print(f"  ⚠ Cookie injection partial: {e}")

            page = await context.new_page()

        page = await context.new_page()

        # Navigate to ALTA dashboard
        print("\n  Phase 1: Checking login status...")
        await page.goto(ALTA_DASHBOARD, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        logged_in = await check_logged_in(page)

        if not logged_in:
            print("\n  ⚠ Not logged in (cookies may be encrypted by Edge).")
            print("  Please log in manually in the browser that just opened.")
            print("  The script will continue automatically after login.\n")

            # Wait for manual login
            for _ in range(120):  # 4 minute timeout
                await asyncio.sleep(2)
                logged_in = await check_logged_in(page)
                if logged_in:
                    break

            if not logged_in:
                print("  ✗ Login timeout. Please try again.")
                await browser.close()
                return

        print("  ✓ Logged in to ALTA!\n")

        # ─── SCRAPING BEGINS ─────────────────────────────────
        all_data = {
            "scraped_at": datetime.now().isoformat(),
            "league": "Sunday Women",
            "level_flight": "C-8",
            "season": "Spring 2026",
            "pages": {},
            "tables": {},
            "links": {},
            "dropdowns": {}
        }

        # Phase 2: Team Dashboard
        print("  Phase 2: Scraping Team Dashboard...")
        dash = await scrape_page(page, ALTA_TEAM_DASHBOARD, "Team Dashboard")
        all_data["pages"]["team_dashboard"] = dash["text"]
        
        # Get all tables and dropdowns
        tables = await scrape_tables(page)
        all_data["tables"]["dashboard"] = tables
        
        dropdowns = await scrape_dropdowns(page)
        all_data["dropdowns"]["dashboard"] = dropdowns

        # Find team-related links
        team_links = await find_all_links(page, [
            "sunday", "women", "c-8", "schedule", "roster",
            "match", "lineup", "results", "score", "team",
            "view", "select", "detail"
        ])
        all_data["links"]["dashboard"] = [
            {"text": l["text"][:80], "url": l["url"]} for l in team_links
        ]
        print(f"  Found {len(team_links)} team links, {len(tables)} tables, {len(dropdowns)} dropdowns")

        # Phase 3: Follow each team link
        print("\n  Phase 3: Following team links...")
        for i, link in enumerate(team_links[:15]):  # limit to 15
            label = link["text"][:40] or f"link_{i}"
            result = await scrape_page(page, link["url"], label)
            if result["text"] and len(result["text"]) > 100:
                all_data["pages"][f"link_{i}_{label}"] = result["text"]
                
                # Also get tables from linked pages
                link_tables = await scrape_tables(page)
                if link_tables:
                    all_data["tables"][f"link_{i}_{label}"] = link_tables

                # Look for match cards or result details
                detail_links = await find_all_links(page, [
                    "match", "card", "result", "score", "detail", "view"
                ])
                for j, dl in enumerate(detail_links[:5]):
                    dl_label = dl["text"][:30] or f"detail_{j}"
                    dl_result = await scrape_page(page, dl["url"], f"  → {dl_label}")
                    if dl_result["text"] and len(dl_result["text"]) > 100:
                        all_data["pages"][f"detail_{i}_{j}_{dl_label}"] = dl_result["text"]
                        dl_tables = await scrape_tables(page)
                        if dl_tables:
                            all_data["tables"][f"detail_{i}_{j}_{dl_label}"] = dl_tables

        # Phase 4: Try common ALTA URLs
        print("\n  Phase 4: Trying common ALTA member pages...")
        common_urls = [
            (f"{ALTA_BASE}/Member/Teams/Roster.aspx", "Roster"),
            (f"{ALTA_BASE}/Member/Teams/Schedule.aspx", "Schedule"),
            (f"{ALTA_BASE}/Member/Teams/MatchResults.aspx", "Match Results"),
            (f"{ALTA_BASE}/Schedules/TeamSchedule.aspx", "Team Schedule Public"),
            (f"{ALTA_BASE}/StandingsPostSeason/WeeklyStandingsList.aspx", "Standings"),
            (f"{ALTA_BASE}/Member/Teams/MatchCard.aspx", "Match Card"),
            (f"{ALTA_BASE}/Member/Teams/LineupChecker.aspx", "Lineup Checker"),
        ]

        for url, label in common_urls:
            result = await scrape_page(page, url, label)
            if result["text"] and len(result["text"]) > 100:
                all_data["pages"][label] = result["text"]
                url_tables = await scrape_tables(page)
                if url_tables:
                    all_data["tables"][label] = url_tables
                url_dropdowns = await scrape_dropdowns(page)
                if url_dropdowns:
                    all_data["dropdowns"][label] = url_dropdowns

        # Phase 5: Schedule & Lineup — Team View and LineUp details (postback-based)
        # These use javascript:__doPostBack() or WebForm_DoPostBackWithOptions() links.
        # We must navigate to the correct page first, then CLICK the postback links.
        print("\n  Phase 5: Scraping Schedule & Lineup views (postback clicks)...")

        # Step 1: Navigate to the team-specific Schedule & Lineup page
        schedule_url = f"{ALTA_BASE}/Member/Schedules/Schedule.aspx?sl=1"
        await page.goto(schedule_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # Scrape the default view (Player View) first
        sched_text = await page.inner_text("body")
        sched_tables = await scrape_tables(page)
        all_data["pages"]["schedule_player_view"] = sched_text
        if sched_tables:
            all_data["tables"]["schedule_player_view"] = sched_tables
        print(f"    Player View: {len(sched_text)} chars, {len(sched_tables)} tables")

        # Step 2: Click "Team View" tab
        # The Team View link may be on the Schedule page OR the Dashboard page.
        team_view_clicked = False

        # Try on current page (Schedule) first
        for selector_attempt in [
            "a:text-is('Team View')",
            "a:has-text('Team View')",
            "[id*='TeamView']",
            "[id*='lnkTeamView']",
        ]:
            try:
                el = await page.query_selector(selector_attempt)
                if el:
                    text = (await el.inner_text()).strip()
                    print(f"    Found '{text}' on Schedule page, clicking...")
                    await el.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    await asyncio.sleep(3)
                    team_view_clicked = True
                    break
            except Exception as e:
                continue

        # If not found on Schedule page, try from Dashboard
        if not team_view_clicked:
            print("    Team View not found on Schedule page, trying Dashboard...")
            await page.goto(ALTA_TEAM_DASHBOARD, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)

            # Try selectors on Dashboard
            for selector_attempt in [
                "a:text-is('Team View')",
                "a:has-text('Team View')",
                "[id*='TeamView']",
                "[id*='lnkTeamView']",
            ]:
                try:
                    el = await page.query_selector(selector_attempt)
                    if el:
                        text = (await el.inner_text()).strip()
                        print(f"    Found '{text}' on Dashboard, clicking...")
                        await el.click()
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        await asyncio.sleep(3)
                        team_view_clicked = True
                        break
                except Exception as e:
                    continue

            # Fallback: scan ALL links on Dashboard for "Team View" text
            if not team_view_clicked:
                all_links = await page.query_selector_all("a")
                for link in all_links:
                    try:
                        link_text = (await link.inner_text()).strip()
                        if link_text == "Team View":
                            print(f"    Found 'Team View' via text scan on Dashboard, clicking...")
                            await link.click()
                            await page.wait_for_load_state("networkidle", timeout=15000)
                            await asyncio.sleep(3)
                            team_view_clicked = True
                            break
                    except:
                        continue

        if team_view_clicked:
            tv_text = await page.inner_text("body")
            tv_tables = await scrape_tables(page)
            all_data["pages"]["schedule_team_view"] = tv_text
            all_data["tables"]["schedule_team_view"] = tv_tables
            print(f"    ✓ Team View: {len(tv_text)} chars, {len(tv_tables)} tables")

            # Parse structured player data from Team View table
            parsed_players = await parse_team_view_table(tv_tables)
            if parsed_players:
                all_data["team_view_players"] = parsed_players
                print(f"    ✓ Parsed {len(parsed_players)} players from Team View")
                for pp in parsed_players:
                    v = pp.get('alta_value', '?')
                    print(f"      {pp['name']}: Value={v}, {pp['wins']}W-{pp['losses']}L")
        else:
            print("    ⚠ Could not find Team View link")

        # Step 3: Click "League View" tab
        league_view_clicked = False
        for selector_attempt in [
            "a:text-is('League View')",
            "a:has-text('League View')",
        ]:
            try:
                el = await page.query_selector(selector_attempt)
                if el:
                    text = (await el.inner_text()).strip()
                    print(f"    Found '{text}', clicking...")
                    await el.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    await asyncio.sleep(3)
                    league_view_clicked = True
                    break
            except:
                continue

        if league_view_clicked:
            lv_text = await page.inner_text("body")
            lv_tables = await scrape_tables(page)
            all_data["pages"]["schedule_league_view"] = lv_text
            all_data["tables"]["schedule_league_view"] = lv_tables
            print(f"    ✓ League View: {len(lv_text)} chars, {len(lv_tables)} tables")

        # Step 4: Go back to Schedule page and try clicking individual LineUp links
        await page.goto(schedule_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        # Find and click "LineUp" or "View Lineup" postback links for each match week
        print("    Looking for individual match LineUp links...")

        # Count how many lineup links exist
        lineup_count = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a')).filter(
                a => (a.textContent.trim().toLowerCase().includes('lineup') ||
                      a.textContent.trim().toLowerCase().includes('view lineup')) &&
                     a.href && a.href.includes('javascript:')
            ).length;
        }""")
        print(f"    Found {lineup_count} LineUp postback links")

        for idx in range(min(lineup_count, 7)):
            try:
                await page.goto(schedule_url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)
                # Click the nth visible lineup link using JS evaluation
                clicked = await page.evaluate(f"""() => {{
                    const links = Array.from(document.querySelectorAll('a')).filter(
                        a => (a.textContent.trim().toLowerCase().includes('lineup') ||
                              a.textContent.trim().toLowerCase().includes('view lineup')) &&
                             a.href && a.href.includes('javascript:') &&
                             a.offsetParent !== null
                    );
                    if (links[{idx}]) {{ links[{idx}].click(); return true; }}
                    return false;
                }}""")
                if not clicked:
                    print(f"      LineUp #{idx+1}: not found (skipping)")
                    continue
                await page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(2)
                lu_text = await page.inner_text("body")
                lu_tables = await scrape_tables(page)
                key = f"lineup_week_{idx+1}"
                all_data["pages"][key] = lu_text
                all_data["tables"][key] = lu_tables
                print(f"      LineUp #{idx+1}: ✓ {len(lu_text)} chars, {len(lu_tables)} tables")
            except Exception as e:
                print(f"      LineUp #{idx+1}: ✗ {e}")

        # Step 5: Click Scorecard links from the Scorecards page
        scorecards_url = f"{ALTA_BASE}/Member/Teams/TeamScorecards.aspx?sl=1"
        await page.goto(scorecards_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        print("    Looking for Scorecard postback links...")

        scorecard_count = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a')).filter(
                a => a.textContent.trim().toLowerCase().includes('scorecard') &&
                     a.href && a.href.includes('javascript:') &&
                     a.offsetParent !== null
            ).length;
        }""")
        print(f"    Found {scorecard_count} Scorecard postback links")

        for idx in range(min(scorecard_count, 7)):
            try:
                await page.goto(scorecards_url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)
                clicked = await page.evaluate(f"""() => {{
                    const links = Array.from(document.querySelectorAll('a')).filter(
                        a => a.textContent.trim().toLowerCase().includes('scorecard') &&
                             a.href && a.href.includes('javascript:') &&
                             a.offsetParent !== null
                    );
                    if (links[{idx}]) {{ links[{idx}].click(); return true; }}
                    return false;
                }}""")
                if not clicked:
                    print(f"      Scorecard #{idx+1}: not found")
                    continue
                await page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(2)
                sc_text = await page.inner_text("body")
                sc_tables = await scrape_tables(page)
                key = f"scorecard_week_{idx+1}"
                all_data["pages"][key] = sc_text
                all_data["tables"][key] = sc_tables
                print(f"      Scorecard #{idx+1}: ✓ {len(sc_text)} chars, {len(sc_tables)} tables")
            except Exception as e:
                print(f"      Scorecard #{idx+1}: ✗ {e}")

        # ─── SAVE RESULTS ─────────────────────────────────
        raw_file = "alta_raw_data.json"
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)

        total_pages = len(all_data["pages"])
        total_tables = sum(len(v) if isinstance(v, list) else 0 for v in all_data["tables"].values())

        print(f"\n  {'=' * 50}")
        print(f"  SCRAPING COMPLETE")
        print(f"  {'=' * 50}")
        print(f"  Pages scraped: {total_pages}")
        print(f"  Tables found:  {total_tables}")
        print(f"  Data saved to: {raw_file}")
        print(f"  {'=' * 50}")

        # Keep browser open briefly
        print("\n  Browser will close in 10 seconds...")
        print("  (Navigate to any page you want me to see and tell me in chat)")
        await asyncio.sleep(10)

        if browser:
            await browser.close()
        elif context:
            await context.close()

    print(f"\n  ✓ Done! Raw data saved to {raw_file}")
    print(f"  Next: the optimizer will parse this data automatically.")


if __name__ == "__main__":
    asyncio.run(main())
