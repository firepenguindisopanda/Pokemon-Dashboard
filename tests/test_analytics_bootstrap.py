"""Two fixes the maintainer ruled on after T24.

**Issue 6 — a cold cache hung the dashboard and the ML playground forever.**
Both pages call `waitForAnalytics()`, which polls
`/api/pokemon-analytics/status` until `ready` is true. But `status` was the one
analytics route *without* `@with_analytics`, so polling it never triggered the
lazy training that would make it ready, and nothing else on either page fires
first. On a freshly started worker with no warm cache, the spinner (now the
skeleton) ran indefinitely. Found during T21's browser pass.

The fix is not `@with_analytics` on `status`: that would make the poll itself
block for the length of a full training run (~28s measured in T15), which is
exactly what the async status contract exists to avoid. `status` instead
*starts* the work and answers immediately, so the next poll sees
`initializing`, then `ready`.

**The orphaned helper.** T21 deleted `/pokemon-stats-v1`, which was the only
caller of `get_combined_type_distribution()`. It was kept pending a ruling;
the ruling is to delete it.
"""

import re
import threading
import time
from pathlib import Path

import pytest

ANALYTICS = Path("App/blueprints/analytics.py")


def read(path):
    return Path(path).read_text(encoding="utf8")


@pytest.fixture
def cold_analytics():
    """Force the untrained state and restore whatever was there afterwards.

    `state` is a module-level singleton shared by the whole session, so a test
    that leaves it cold makes every later analytics test pay for a retrain.
    """
    from App.blueprints.analytics import state

    saved = (state.instance, state.ready, state.error, dict(state.metrics))
    state.reset()
    yield state
    state.instance, state.ready, state.error, state.metrics = saved


class TestStatusPollTriggersInitialisation:
    """Issue 6: polling must make progress, not wait for something else to."""

    def test_status_starts_initialisation_when_cold(self, auth_client, cold_analytics):
        """The whole bug in one assertion.

        Before the fix, any number of polls left `ready` false forever because
        nothing in the poll path ever called `ensure_analytics()`.

        Training is stubbed. The defect was that nothing *called* it — real
        training already has its own coverage in tests/test_training.py, and a
        genuine run costs ~28s here because conftest gives every test a fresh
        MODEL_CACHE_DIR.
        """
        from App.blueprints import analytics

        assert cold_analytics.ready is False

        real = analytics.initialize_pokemon_analytics

        def fake():
            cold_analytics.mark_ready(object(), {})
            return True

        analytics.initialize_pokemon_analytics = fake
        try:
            auth_client.get("/api/pokemon-analytics/status")
            deadline = time.time() + 10
            while time.time() < deadline and not cold_analytics.ready:
                time.sleep(0.02)
        finally:
            analytics.initialize_pokemon_analytics = real

        assert cold_analytics.ready, (
            "polling /status never made analytics ready — the page would spin "
            "forever, which is the bug this fixes"
        )

    def test_status_answers_immediately_and_does_not_block(
        self, auth_client, cold_analytics
    ):
        """A poll must stay a poll.

        Decorating `status` with `@with_analytics` would also make the page
        eventually work, while turning a 2-second polling interval into a
        ~28-second stall on the very first request. This pins the difference.
        """
        start = time.time()
        response = auth_client.get("/api/pokemon-analytics/status")
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 2.0, (
            f"/status took {elapsed:.1f}s — it is blocking on training instead "
            "of kicking it off and answering"
        )

    def test_status_contract_is_unchanged(self, auth_client, cold_analytics):
        """T16 pinned this payload shape; the fix must not alter it."""
        payload = auth_client.get("/api/pokemon-analytics/status").get_json()
        assert set(payload) == {"ready", "error", "initializing"}, (
            f"the polled contract changed shape: {sorted(payload)}"
        )

    def test_only_one_training_run_is_started(self, auth_client, cold_analytics):
        """`waitForAnalytics` polls every 2s, and two pages may poll at once.

        Without a guard, every poll would spawn another training thread and a
        cold start would fork a dozen of them.
        """
        from App.blueprints import analytics

        started = []
        real = analytics.initialize_pokemon_analytics

        def counting():
            started.append(threading.current_thread().name)
            # Slow enough that a second poll lands while this one is running,
            # which is the race the guard exists for.
            time.sleep(0.4)
            cold_analytics.mark_ready(object(), {})
            return True

        analytics.initialize_pokemon_analytics = counting
        try:
            for _ in range(5):
                auth_client.get("/api/pokemon-analytics/status")
                time.sleep(0.05)
            deadline = time.time() + 10
            while time.time() < deadline and not cold_analytics.ready:
                time.sleep(0.02)
        finally:
            analytics.initialize_pokemon_analytics = real

        assert len(started) <= 1, (
            f"{len(started)} training runs were started by 5 polls; the "
            "bootstrap is not guarded"
        )

    def test_the_background_thread_is_a_daemon(self):
        """T15 removed a non-daemon training thread for a concrete reason.

        It kept the process alive after `flask init` finished and made focused
        pytest runs hang after the summary line. Reintroducing one here would
        bring both back.
        """
        source = read(ANALYTICS)
        spawn = re.search(r"threading\.Thread\((?:[^)]|\n)*?\)", source)
        assert spawn, "no background thread is started; how does /status bootstrap?"
        assert "daemon=True" in spawn.group(0), (
            f"the bootstrap thread is not a daemon: {spawn.group(0)[:160]}"
        )

    def test_auto_initialize_false_is_still_honoured(self, auth_client, cold_analytics):
        """`ANALYTICS_AUTO_INITIALIZE=false` means "never train on a request".

        T15 made that setting real after it had sat unreferenced. A bootstrap
        that ignored it would quietly undo that.
        """
        from App.blueprints import analytics

        calls = []
        real = analytics.initialize_pokemon_analytics
        analytics.initialize_pokemon_analytics = lambda *a, **k: calls.append(1)

        settings = analytics.get_settings()
        original = settings.analytics_auto_initialize
        object.__setattr__(settings, "analytics_auto_initialize", False)
        try:
            auth_client.get("/api/pokemon-analytics/status")
            time.sleep(0.5)
        finally:
            object.__setattr__(settings, "analytics_auto_initialize", original)
            analytics.initialize_pokemon_analytics = real

        assert not calls, (
            "/status started training even though auto-initialisation is off"
        )


class TestOrphanedHelperIsGone:
    """`get_combined_type_distribution()` lost its only caller in T21.

    It was kept then because T18 had rewritten it as a UNION ALL, verified it
    against live Neon and mutation-tested it — deleting proven code is a
    separate decision from deleting a broken route. The maintainer has now
    made it.
    """

    def test_the_helper_is_deleted(self):
        source = read(ANALYTICS)
        assert "def get_combined_type_distribution" not in source, (
            "the helper is still defined but has no callers"
        )

    def test_nothing_calls_or_imports_it(self):
        """AST, not substring search.

        The comments and docstrings recording *why* it went are wanted, so a
        text search fails on its own documentation — it even matches the regex
        literal in a test written to do the searching. This project already
        uses AST guards for the same reason (the eval scan in T2, the error
        hygiene scan in T14, the force_retrain scan in T17).
        """
        import ast

        name = "get_combined_type_distribution"
        hits = []
        for path in list(Path("App").rglob("*.py")) + list(Path("tests").rglob("*.py")):
            tree = ast.parse(read(path), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == name:
                    hits.append(f"{path}:{node.lineno} (reference)")
                elif isinstance(node, ast.Attribute) and node.attr == name:
                    hits.append(f"{path}:{node.lineno} (attribute)")
                elif isinstance(node, ast.ImportFrom):
                    if any(alias.name == name for alias in node.names):
                        hits.append(f"{path}:{node.lineno} (import)")
                elif isinstance(node, ast.FunctionDef) and node.name == name:
                    hits.append(f"{path}:{node.lineno} (definition)")
        assert not hits, f"still referenced in code at: {hits}"

    def test_the_sql_pushdown_suite_keeps_its_other_half(self):
        """T18 covered two pushdowns. Only one of them is being removed.

        The quiz's `COUNT + OFFSET/LIMIT` row selection has a live caller and
        its tests must survive the deletion.
        """
        suite = Path("tests/test_sql_pushdown.py")
        assert suite.exists(), "the whole pushdown suite was deleted, not just the helper's half"
        text = read(suite)
        assert "class TestQuizPicksOneRowInTheDatabase" in text, (
            "the quiz row-selection tests went with the type-distribution ones"
        )
