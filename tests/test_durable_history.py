from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_premarket_history.sh"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def history_workspace(tmp_path: Path) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    seed.mkdir()
    _git("init", "-b", "main", cwd=seed)
    _git("config", "user.name", "test", cwd=seed)
    _git("config", "user.email", "test@example.invalid", cwd=seed)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=seed)
    _git("commit", "-m", "seed", cwd=seed)
    _git("init", "--bare", str(remote), cwd=tmp_path)
    _git("remote", "add", "origin", str(remote), cwd=seed)
    _git("push", "origin", "main", cwd=seed)
    _git("clone", "--branch", "main", str(remote), str(work), cwd=tmp_path)
    return work, remote


def _prepare(work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(work), "history"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_first_run_creates_missing_remote_history_branch(history_workspace):
    work, remote = history_workspace
    result = _prepare(work)
    assert result.returncode == 0
    assert _git(
        "show-ref", "--verify", "refs/heads/premarket-history", cwd=remote
    ).returncode == 0


def test_existing_remote_history_branch_is_fetched_and_checked_out(history_workspace):
    work, _remote = history_workspace
    assert _prepare(work).returncode == 0
    fresh = work.parent / "fresh"
    _git("clone", "--branch", "main", str(work.parent / "remote.git"), str(fresh), cwd=work.parent)
    result = _prepare(fresh)
    assert result.returncode == 0
    assert _git("branch", "--show-current", cwd=fresh).stdout.strip() == "premarket-history"
    assert _git("rev-parse", "HEAD", cwd=fresh).stdout == _git(
        "rev-parse", "origin/premarket-history", cwd=fresh
    ).stdout


def test_empty_orphan_initialization_has_no_pathspec_failure(history_workspace):
    work, _remote = history_workspace
    result = _prepare(work)
    assert result.returncode == 0
    assert "pathspec" not in result.stderr.lower()


def test_initialization_creates_history_gitkeep(history_workspace):
    work, _remote = history_workspace
    assert _prepare(work).returncode == 0
    assert (work / "history" / ".gitkeep").is_file()
    assert _git("ls-files", "history/.gitkeep", cwd=work).stdout.strip() == "history/.gitkeep"


def test_initialization_is_idempotent_without_duplicate_commit(history_workspace):
    work, _remote = history_workspace
    first = _prepare(work)
    first_head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    second = _prepare(work)
    second_head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    assert first.returncode == second.returncode == 0
    assert first_head == second_head
    assert int(_git("rev-list", "--count", "HEAD", cwd=work).stdout.strip()) == 1


def test_coach_and_collector_share_safe_full_history_checkout():
    for name in ("coach-app.yml", "premarket-collector.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "contents: write" in text
        assert text.count("fetch-depth: 0") >= 2
        assert (
            "bash scripts/prepare_premarket_history.sh "
            "premarket-history-store history"
        ) in text
        assert "git rm -rf ." not in text


def test_prepare_script_does_not_enable_shell_trace_or_name_secrets():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -x" not in text
    for secret_name in (
        "KRX_AUTH_KEY",
        "FRED_API_KEY",
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "NETLIFY_AUTH_TOKEN",
        "NETLIFY_SITE_ID",
    ):
        assert secret_name not in text
