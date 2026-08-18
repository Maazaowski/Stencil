"""Unit tests for the supplier-profile watcher path and DB-driven registry sync."""

from pathlib import Path

from stencil.intake.watcher import _sync_profile_watches, resolve_profile_watch_dir
from stencil.profiles.loader import save_profile
from stencil.profiles.schema import SupplierProfile


def _profile(*, inbound_path, output_path="/data/Test/xls", status="active",
             profile_id="test.standard.v1") -> SupplierProfile:
    return SupplierProfile(
        profile_id=profile_id,
        version=1,
        status=status,
        delivery={"inbound_path": inbound_path, "output_path": output_path},
        identity={"canonical_name": "Test", "aliases": []},
        classification={"output_type": "standard"},
    )


class TestResolveProfileWatchDir:
    def test_static_path(self):
        profile = _profile(inbound_path="/data/GTT/T171404/pdf")
        target = resolve_profile_watch_dir(profile)
        assert target == Path("/data/GTT/T171404/pdf")

    def test_missing_inbound_returns_none(self):
        profile = _profile(inbound_path=None)
        assert resolve_profile_watch_dir(profile) is None

    def test_unresolved_token_returns_none(self):
        profile = _profile(inbound_path="/data/{account_number}/pdf")
        assert resolve_profile_watch_dir(profile) is None


class _FakeWatch:
    def __init__(self, path):
        self.path = path


class _FakeObserver:
    """Minimal stand-in for a watchdog observer for sync tests."""

    def __init__(self):
        self.watched: dict[str, _FakeWatch] = {}

    def schedule(self, handler, path, recursive=False):
        watch = _FakeWatch(path)
        self.watched[path] = watch
        return watch

    def unschedule(self, watch):
        self.watched.pop(watch.path, None)


class TestProfileRegistrySync:
    """The watcher re-reads active profiles from the DB so changes are picked up live."""

    def _save(self, tmp_path, profile_id, name, *, status="active", with_output=True):
        out = str(tmp_path / name / "xls") if with_output else None
        save_profile(_profile(
            profile_id=profile_id, status=status,
            inbound_path=str(tmp_path / name / "pdf"), output_path=out,
        ))

    def test_new_profile_is_picked_up_without_restart(self, isolated_db, tmp_path):
        obs = _FakeObserver()
        handlers: list = []
        scheduled: dict = {}

        self._save(tmp_path, "test.aaa.v1", "AAA")
        added, removed = _sync_profile_watches(obs, lambda p, a: None, scheduled, handlers, "__global__")
        assert added == 1 and removed == 0
        assert len(scheduled) == 1

        self._save(tmp_path, "test.bbb.v1", "BBB")
        added, removed = _sync_profile_watches(obs, lambda p, a: None, scheduled, handlers, "__global__")
        assert added == 1 and removed == 0
        assert len(scheduled) == 2

    def test_paused_profile_is_unwatched(self, isolated_db, tmp_path):
        obs = _FakeObserver()
        handlers: list = []
        scheduled: dict = {}

        self._save(tmp_path, "test.aaa.v1", "AAA")
        _sync_profile_watches(obs, lambda p, a: None, scheduled, handlers, "__global__")
        assert len(scheduled) == 1

        self._save(tmp_path, "test.aaa.v1", "AAA", status="retired")
        added, removed = _sync_profile_watches(obs, lambda p, a: None, scheduled, handlers, "__global__")
        assert added == 0 and removed == 1
        assert len(scheduled) == 0

    def test_missing_output_path_is_skipped(self, isolated_db, tmp_path):
        obs = _FakeObserver()
        handlers: list = []
        scheduled: dict = {}

        self._save(tmp_path, "test.aaa.v1", "AAA", with_output=False)
        added, removed = _sync_profile_watches(obs, lambda p, a: None, scheduled, handlers, "__global__")
        assert added == 0 and removed == 0
        assert len(scheduled) == 0

    def test_two_profiles_on_same_folder_watch_once_and_log(self, isolated_db, tmp_path, capsys):
        obs = _FakeObserver()
        handlers: list = []
        scheduled: dict = {}

        shared = str(tmp_path / "SHARED" / "pdf")
        save_profile(_profile(profile_id="test.aaa.v1", inbound_path=shared,
                              output_path=str(tmp_path / "SHARED" / "xls")))
        save_profile(_profile(profile_id="test.bbb.v1", inbound_path=shared,
                              output_path=str(tmp_path / "SHARED" / "xls")))

        added, _ = _sync_profile_watches(obs, lambda p, a: None, scheduled, handlers, "__global__")

        # The folder is watched exactly once; the second claim is logged, not silent.
        assert added == 1
        assert len(scheduled) == 1
        assert "watcher.account_conflict" in capsys.readouterr().out
