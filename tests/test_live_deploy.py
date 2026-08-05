from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_coach_workflow_has_netlify_production_deploy():
    path = ROOT / ".github" / "workflows" / "coach-app.yml"
    text = path.read_text(encoding="utf-8")
    assert "NETLIFY_AUTH_TOKEN" in text
    assert "NETLIFY_SITE_ID" in text
    assert "netlify-cli@latest deploy" in text
    assert "--prod" in text
    assert "--dir=site" in text
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


def test_v52_app_shell_is_decision_first_and_research_is_collapsed():
    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    assert "Decision Coach v5.2" in html
    assert 'data-tab="today"' in html
    assert 'data-tab="candidates"' in html
    assert 'data-tab="research"' in html
    assert 'id="marketGateReasons"' in html
    assert 'id="marketGateConditions"' in html
    assert 'id="candidateGateNotice"' in html
    assert '<details id="kospi-model-lab"' in html
    assert '<details id="data-lab"' in html
    assert 'class="decision-dock"' in html


def test_v52_refresh_reloads_the_app_shell_and_rotates_static_cache():
    javascript = (ROOT / "app" / "app.js").read_text(encoding="utf-8")
    worker = (ROOT / "app" / "sw.js").read_text(encoding="utf-8")
    assert 'const APP_SHELL_VERSION = "5.2.0"' in javascript
    assert "reloadAppShell" in javascript
    assert "window.location.reload()" in javascript
    assert 'registration.update()' in javascript
    assert 'kospi-shadow-decision-coach-v5-2-static' in worker
    assert 'client.navigate(client.url)' in worker
    assert 'SKIP_WAITING' in worker


def test_candidate_ui_is_explicitly_subordinate_to_market_gate():
    javascript = (ROOT / "app" / "app.js").read_text(encoding="utf-8")
    assert "gate.stock_entries_allowed" in javascript
    assert "signalGate.stock_signal_enabled" in javascript
    assert 'candidateGateNotice' in javascript
    assert '관찰만 · 진입 잠금' in javascript
    assert 'cards.map((card, index) => renderDecisionCard(card, gateLocked, index))' in javascript
