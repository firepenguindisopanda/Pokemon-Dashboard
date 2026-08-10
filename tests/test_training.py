"""Analytics lifecycle tests.

C2/C7/C8 — model training ran at *import* time in a non-daemon thread. Three
consequences, all observed rather than theorised:

* every `flask` command against a seeded database completed its work and then
  hung forever, which blocks `flask db upgrade` in the Render build step;
* under gunicorn each worker retrained independently, racing on shared globals;
* the manifest recorded absolute developer-machine paths, so the committed
  artifacts could never load anywhere else.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys
import threading

import pytest

from App.app import app as flask_app


def run_python(code, timeout, env_extra=None):
    """Run a snippet in a fresh interpreter and report whether it exited.

    Returns:
        (exited_cleanly, returncode, combined_output)
    """
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("REDIS_URL", None)
    env.update(env_extra or {})
    try:
        finished = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return True, finished.returncode, finished.stdout + finished.stderr
    except subprocess.TimeoutExpired as expired:
        output = (expired.stdout or b"") + (expired.stderr or b"")
        if isinstance(output, bytes):
            output = output.decode("utf8", "replace")
        return False, None, output


class TestImportIsSideEffectFree:
    """Importing the app must not train, and must not keep the process alive."""

    def test_importing_the_app_exits_promptly(self, client, sqlite_db):
        """The bug that hangs `flask db upgrade` in the Render build step."""
        exited, code, output = run_python(
            "from App.app import app; print('IMPORTED')",
            timeout=60,
            env_extra={"SQLALCHEMY_DATABASE_URI": sqlite_db},
        )
        assert exited, (
            "importing App never exited — a non-daemon background thread is "
            f"still keeping the process alive. Output tail: {output[-300:]}"
        )
        assert code == 0, output[-500:]
        assert "IMPORTED" in output

    def test_importing_the_app_does_not_train(self, client, sqlite_db):
        exited, _code, output = run_python(
            "from App.app import app\n"
            "from App.blueprints import analytics\n"
            "print('READY:', analytics.analytics_ready)\n"
            "print('INSTANCE:', analytics.pokemon_analytics is not None)",
            timeout=60,
            env_extra={"SQLALCHEMY_DATABASE_URI": sqlite_db},
        )
        assert exited, "import hung"
        assert "READY: False" in output, output[-400:]
        assert "INSTANCE: False" in output, output[-400:]


class TestTrainingIsAnExplicitOperation:
    """Training belongs to a command, not to whoever imports a module."""

    def test_train_cli_command_is_registered(self):
        import wsgi  # noqa: F401 — registers CLI commands

        assert "train" in flask_app.cli.commands

    def test_analytics_can_be_disabled(self):
        """ANALYTICS_AUTO_INITIALIZE existed in config but was never referenced."""
        from App.config import Settings

        settings = Settings(_env_file=None, analytics_auto_initialize=False)
        assert settings.analytics_auto_initialize is False


class TestLazyInitialisationIsGuarded:
    """A first request may trigger training, but only ever one at a time."""

    def test_concurrent_requests_trigger_at_most_one_initialisation(self, monkeypatch):
        from App.blueprints import analytics as analytics_module

        calls = []
        entered = threading.Event()
        release = threading.Event()

        def slow_init():
            calls.append(1)
            entered.set()
            release.wait(timeout=5)
            analytics_module.analytics_ready = True
            return True

        monkeypatch.setattr(analytics_module, "pokemon_analytics", None)
        monkeypatch.setattr(analytics_module, "analytics_ready", False)
        monkeypatch.setattr(analytics_module, "initialize_pokemon_analytics", slow_init)

        threads = [
            threading.Thread(target=analytics_module.ensure_analytics) for _ in range(5)
        ]
        for thread in threads:
            thread.start()

        assert entered.wait(timeout=5), "no thread started initialisation"
        release.set()
        for thread in threads:
            thread.join(timeout=10)

        assert len(calls) == 1, (
            f"{len(calls)} concurrent initialisations ran — training is not "
            "mutex-guarded, so requests can pile up on it"
        )


class TestStatusContractUnchanged:
    """The frontend polls this shape; Phase 6 must not alter it."""

    def test_status_returns_the_same_keys(self, auth_client):
        payload = auth_client.get("/api/pokemon-analytics/status").get_json()
        assert set(payload) == {"ready", "error", "initializing"}
        assert isinstance(payload["ready"], bool)
        assert isinstance(payload["initializing"], bool)


class TestModelArtifactsAreBuiltNotCommitted:
    """Artifacts are environment-specific, so the repository must not carry them.

    The cache key is a hash of the seeded data. A committed model only loads if
    the target database hashes identically, which it does not in practice —
    three distinct hashes were observed for nominally identical seed data. The
    build runs `flask train` instead, so the cache always matches the data
    actually being served.
    """

    @staticmethod
    def _tracked_cache_files():
        return [
            line
            for line in subprocess.check_output(
                ["git", "ls-files", "App/models_cache"], text=True
            ).split()
            if line.strip()
        ]

    def test_no_model_artifacts_are_committed(self):
        artifacts = [f for f in self._tracked_cache_files() if f.endswith(".joblib")]
        assert artifacts == [], (
            "model artifacts are committed again. They are environment-specific "
            f"and will not load on the deployment target: {artifacts}"
        )

    def test_the_manifest_is_not_committed(self):
        """A tracked manifest gets rewritten by any training run and goes stale."""
        tracked = [
            f for f in self._tracked_cache_files() if f.endswith("manifest.json")
        ]
        assert tracked == [], (
            "manifest.json is tracked again — training rewrites it, so it drifts "
            "out of sync and ends up referencing artifacts that were never added"
        )

    def test_the_cache_directory_is_gitignored(self):
        ignore = pathlib.Path(".gitignore").read_text()
        assert "App/models_cache/*.joblib" in ignore
        assert "App/models_cache/manifest.json" in ignore

    def test_saved_artifacts_are_recorded_by_filename_not_absolute_path(self, tmp_path):
        """C8 — absolute paths from the build machine cannot resolve at runtime."""
        from App import ml_utils

        original_dir, original_meta = ml_utils.MODEL_DIR, ml_utils.META_FILE
        ml_utils.MODEL_DIR = tmp_path
        ml_utils.META_FILE = tmp_path / "manifest.json"
        try:
            ml_utils.save_model({"fake": "model"}, "probe", "hash123")
            manifest = json.loads(ml_utils.META_FILE.read_text())
            stored = manifest["probe"]["path"]
            assert not os.path.isabs(stored), f"absolute path recorded: {stored}"
            assert ml_utils.resolve_artifact_path(stored).exists()
        finally:
            ml_utils.MODEL_DIR, ml_utils.META_FILE = original_dir, original_meta

    def test_legacy_absolute_paths_still_resolve(self):
        """Older manifests recorded absolute paths; loading must not break."""
        from App.ml_utils import MODEL_DIR, resolve_artifact_path

        legacy = "/some/other/machine/App/models_cache/thing.joblib"
        assert resolve_artifact_path(legacy) == MODEL_DIR / "thing.joblib"


class TestBuildTrainsTheModels:
    """The deploy must produce artifacts rather than assume they are present."""

    def test_render_build_runs_training(self):
        render = pathlib.Path("render.yaml")
        if not render.exists():
            pytest.skip("no render.yaml")
        build = render.read_text()
        assert "flask db upgrade" in build
        assert "flask train" in build, (
            "the build does not train models, and none are committed — every "
            "cold start would train on a user's first request"
        )

    def test_build_does_not_seed(self):
        """`flask init` replaces seed data; running it per deploy wipes trainers."""
        render = pathlib.Path("render.yaml")
        if not render.exists():
            pytest.skip("no render.yaml")
        build_line = next(
            (ln for ln in render.read_text().splitlines() if "buildCommand" in ln), ""
        )
        assert "flask init" not in build_line, (
            "the build runs `flask init`, which deletes every registered trainer"
        )


class TestNoDeprecatedDatetimeUsage:
    """utcnow() is deprecated in 3.12 and scheduled for removal."""

    def test_ml_utils_does_not_call_utcnow(self):
        with open("App/ml_utils.py", encoding="utf8") as handle:
            tree = ast.parse(handle.read())
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "utcnow"
        ]
        assert offenders == [], f"datetime.utcnow() called at lines {offenders}"
