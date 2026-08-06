from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_coach_workflow_has_github_pages_production_deploy():
    path = ROOT / ".github" / "workflows" / "coach-app.yml"
    text = path.read_text(encoding="utf-8")
    assert "pages: write" in text
    assert "id-token: write" in text
    assert "actions/upload-pages-artifact@v4" in text
    assert "actions/deploy-pages@v4" in text
    assert "Deploy fresh market data to GitHub Pages" in text
    yaml.safe_load(text)


def test_coach_workflow_covers_post_close_refresh():
    text = (ROOT / ".github" / "workflows" / "coach-app.yml").read_text(encoding="utf-8")
    assert 'cron: "35 15 * * 1-5"' in text
    assert 'cron: "45 15 * * 1-5"' in text
    assert 'cron: "0 18 * * 1-5"' in text
    assert 'cron: "5 20 * * 1-5"' in text
    assert 'timezone: "Asia/Seoul"' in text


def test_coach_workflow_publishes_market_gate_checkpoints():
    text = (ROOT / ".github" / "workflows" / "coach-app.yml").read_text(encoding="utf-8")
    for cron in ('"30 7 * * 1-5"', '"0 8 * * 1-5"', '"50 8 * * 1-5"', '"5 9 * * 1-5"'):
        assert f"cron: {cron}" in text


def test_aftermarket_collector_and_coach_use_shared_serialization():
    collector = (ROOT / ".github" / "workflows" / "premarket-collector.yml").read_text(encoding="utf-8")
    coach = (ROOT / ".github" / "workflows" / "coach-app.yml").read_text(encoding="utf-8")
    assert 'cron: "42 15 * * 1-5"' in collector
    assert 'cron: "0 17,19,20 * * 1-5"' in collector
    assert "group: kospi-shadow-live-data" in collector
    assert "group: kospi-shadow-live-data" in coach
    assert "DART_API_KEY: ${{ secrets.DART_API_KEY }}" in coach


def test_service_worker_never_caches_live_dashboard():
    text = (ROOT / "app" / "sw.js").read_text(encoding="utf-8")
    assert "data/dashboard.json" in text
    assert 'cache: "no-store"' in text
    static_block = text.split("const STATIC_ASSETS", 1)[1].split("];", 1)[0]
    assert "dashboard.json" not in static_block


def test_netlify_headers_disable_browser_cache_for_live_data():
    text = (ROOT / "app" / "_headers").read_text(encoding="utf-8")
    assert "/data/*" in text
    assert "no-store" in text
    assert "/sw.js" in text


def test_app_renders_probability_explanation_panel():
    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "app.js").read_text(encoding="utf-8")
    assert "상승확률이 이렇게 나온 이유" in html
    assert 'id="positiveDrivers"' in html
    assert 'id="negativeDrivers"' in html
    assert "training_prior_probability" in javascript
    assert "effect_probability_points" in javascript


def test_v53_app_shell_is_decision_first_and_research_is_collapsed():
    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    assert "Decision Coach v5.3" in html
    assert 'data-tab="today"' in html
    assert 'data-tab="candidates"' in html
    assert 'data-tab="research"' in html
    assert 'id="marketGateReasons"' in html
    assert 'id="marketGateConditions"' in html
    assert 'id="candidateGateNotice"' in html
    assert '<details id="kospi-model-lab"' in html
    assert '<details id="data-lab"' in html
    assert 'class="decision-dock"' in html


def test_v54_refresh_rotates_cache_and_loads_operational_trust_guard():
    javascript = (ROOT / "app" / "app.js").read_text(encoding="utf-8")
    worker = (ROOT / "app" / "sw.js").read_text(encoding="utf-8")
    trust = (ROOT / "app" / "operational-trust.js").read_text(encoding="utf-8")
    assert 'const APP_SHELL_VERSION = "5.3.1"' in javascript
    assert "reloadAppShell" in javascript
    assert "window.location.reload()" in javascript
    assert 'registration.update()' in javascript
    assert 'kospi-shadow-decision-coach-v5-4-0-r1-static' in worker
    assert 'operational-trust.js' in worker
    assert 'appWithOperationalTrust' in worker
    assert 'client.navigate(client.url)' in worker
    assert 'SKIP_WAITING' in worker
    assert 'const VERSION = "5.4.0"' in trust
    assert 'MARKET_STALE_AFTER_MINUTES = 15' in trust
    assert 'DATA_STALE · 판단 잠금' in trust
    assert 'hoykeem7-design.github.io' in trust


def test_fast_market_snapshot_publishes_operational_trust_metadata():
    source = (ROOT / "scripts" / "fast_live_market.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "5.4.0"' in source
    assert '"operational_trust"' in source
    assert '"market_stale_after_minutes"' in source
    assert '"trade_lock_policy"' in source


def test_candidate_ui_is_explicitly_subordinate_to_market_gate():
    javascript = (ROOT / "app" / "app.js").read_text(encoding="utf-8")
    assert "gate.stock_entries_allowed" in javascript
    assert "signalGate.stock_signal_enabled" in javascript
    assert 'candidateGateNotice' in javascript
    assert '관찰만 · 진입 잠금' in javascript
    assert 'cards.map((card, index) => renderDecisionCard(card, gateLocked, index))' in javascript


def test_theme_supply_radar_is_shadow_only_and_exposes_no_entry_unlock():
    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "app.js").read_text(encoding="utf-8")
    source = (ROOT / "src" / "kospi_shadow" / "theme_radar.py").read_text(encoding="utf-8")
    assert 'id="theme-supply-radar"' in html
    assert 'id="themeRadarList"' in html
    assert 'id="marketAttentionList"' in html
    assert 'id="marketAttentionStatus"' in html
    assert "renderThemeSupplyRadar" in javascript
    assert "renderMarketAttention" in javascript
    assert "direct_query_rank_available" in source
    assert '"entry_signal_enabled": False' in source
    assert '"can_override_kospi_gate": False' in source
