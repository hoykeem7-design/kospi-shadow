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
    assert 'timezone: "Asia/Seoul"' in text


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
