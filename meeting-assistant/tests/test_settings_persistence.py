"""Tests for atomic settings-save logic in app.py.

Covers:
  - POSIX branch of _atomic_replace (single os.replace, no retries)
  - Windows backup-swap branch of _atomic_replace:
      backup created (dst → dst.bak)
      source renamed into place (src → dst)
      backup deleted on success
      original restored from backup on failure (failure-restore)
  - _save_persisted_settings: data written correctly, temp file gone on success
  - _save_persisted_settings: temp file cleaned up when write fails
  - _save_persisted_settings: existing settings file intact after crash
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Load the two functions under test without triggering heavy app.py imports
# ---------------------------------------------------------------------------

def _load_app_module():
    stubs = {}
    for name in ("dotenv", "fastapi", "fastapi.responses", "fastapi.staticfiles",
                 "uvicorn", "backend", "backend.audio", "backend.config",
                 "backend.context", "backend.document_parser", "backend.entities",
                 "backend.hub", "backend.salesforce_client", "backend.topic_state",
                 "backend.transcribe"):
        mod = types.ModuleType(name)
        for attr in ("load_dotenv", "FastAPI", "File", "HTTPException", "UploadFile",
                     "WebSocket", "WebSocketDisconnect", "JSONResponse", "StaticFiles",
                     "run", "microphone_chunks", "Config", "ConfigError",
                     "ContextManager", "DEFAULT_SENSITIVITY", "SENSITIVITY_LEVELS",
                     "parse_document", "EntityExtractor", "ConnectionHub",
                     "SalesforceClient", "TopicState", "Transcriber"):
            setattr(mod, attr, MagicMock())
        stubs[name] = mod

    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    sys.modules.pop("app", None)

    spec = importlib.util.spec_from_file_location(
        "app", Path(__file__).parent.parent / "app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

    return mod


_app = _load_app_module()
_atomic_replace = _app._atomic_replace
_save_persisted_settings = _app._save_persisted_settings


@pytest.fixture()
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "user_settings.json"
    monkeypatch.setattr(_app, "SETTINGS_FILE", path)
    return path


# ---------------------------------------------------------------------------
# _atomic_replace – POSIX
# ---------------------------------------------------------------------------

class TestAtomicReplacePosix:
    def test_calls_os_replace_once(self, tmp_path):
        src, dst = str(tmp_path / "src"), str(tmp_path / "dst")
        Path(src).write_text("x")
        with patch.object(sys, "platform", "linux"), patch("os.replace") as m:
            _atomic_replace(src, dst)
        m.assert_called_once_with(src, dst)

    def test_propagates_os_error(self, tmp_path):
        src, dst = str(tmp_path / "src"), str(tmp_path / "dst")
        with patch.object(sys, "platform", "linux"), \
             patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                _atomic_replace(src, dst)

    def test_does_not_create_bak_file(self, tmp_path):
        src, dst = str(tmp_path / "src"), str(tmp_path / "dst")
        Path(src).write_text("x")
        Path(dst).write_text("old")
        with patch.object(sys, "platform", "linux"):
            _atomic_replace(src, dst)
        assert not Path(dst + ".bak").exists()


# ---------------------------------------------------------------------------
# _atomic_replace – Windows backup-swap
# ---------------------------------------------------------------------------

class TestAtomicReplaceWindows:
    """Tests for the Windows backup-swap sequence.

    Sequence on success:
      1. os.replace(dst, dst+".bak")  – backup existing dst
      2. os.replace(src, dst)          – promote new content
      3. os.unlink(dst+".bak")         – delete backup

    On failure at step 2 the backup is restored: os.replace(dst+".bak", dst).
    """

    def _run(self, src, dst, replace_side_effects=None, unlink_side_effect=None):
        """Helper: run _atomic_replace on win32 recording all calls."""
        replace_calls: list = []
        unlink_calls: list = []
        effects = list(replace_side_effects or [])
        real_replace = os.replace
        real_unlink = os.unlink

        def fake_replace(s, d):
            replace_calls.append((s, d))
            if effects:
                effect = effects.pop(0)
                if isinstance(effect, Exception):
                    raise effect

        def fake_unlink(p):
            unlink_calls.append(p)
            if unlink_side_effect and isinstance(unlink_side_effect, Exception):
                raise unlink_side_effect

        with patch.object(sys, "platform", "win32"), \
             patch("os.replace", side_effect=fake_replace), \
             patch("os.unlink", side_effect=fake_unlink):
            _atomic_replace(src, dst)

        return replace_calls, unlink_calls

    def test_backup_created_and_src_renamed(self, tmp_path):
        src = str(tmp_path / "src.tmp")
        dst = str(tmp_path / "dst.json")
        dst_bak = dst + ".bak"
        Path(src).write_text("new")
        Path(dst).write_text("old")

        calls, _ = self._run(src, dst)

        assert calls[0] == (dst, dst_bak), "Step 1: dst must be renamed to dst.bak"
        assert calls[1] == (src, dst), "Step 2: src must be renamed to dst"

    def test_backup_deleted_after_success(self, tmp_path):
        src = str(tmp_path / "src.tmp")
        dst = str(tmp_path / "dst.json")
        dst_bak = dst + ".bak"
        Path(src).write_text("new")
        Path(dst).write_text("old")

        _, unlink_calls = self._run(src, dst)

        assert dst_bak in unlink_calls, "dst.bak must be deleted after success"

    def test_no_backup_when_dst_absent(self, tmp_path):
        """First-ever save: dst does not exist, no backup step."""
        src = str(tmp_path / "src.tmp")
        dst = str(tmp_path / "dst.json")
        Path(src).write_text("new")

        replace_calls: list = []
        real_replace = os.replace

        def fake_replace(s, d):
            replace_calls.append((s, d))
            if s == dst:
                raise FileNotFoundError("no existing dst")

        with patch.object(sys, "platform", "win32"), \
             patch("os.replace", side_effect=fake_replace), \
             patch("os.unlink"):
            _atomic_replace(src, dst)

        assert replace_calls[0] == (dst, dst + ".bak"), "Backup attempt should occur first"
        assert (src, dst) in replace_calls, "src→dst rename must still happen"

    def test_failure_restore_from_backup(self, tmp_path):
        """If step 2 (src→dst) fails, dst is restored from dst.bak."""
        src = str(tmp_path / "src.tmp")
        dst = str(tmp_path / "dst.json")
        dst_bak = dst + ".bak"
        Path(src).write_text("new")
        Path(dst).write_text("old")

        replace_calls: list = []
        effects = [None, PermissionError("locked"), None]

        def fake_replace(s, d):
            replace_calls.append((s, d))
            effect = effects.pop(0) if effects else None
            if isinstance(effect, Exception):
                raise effect

        with patch.object(sys, "platform", "win32"), \
             patch("os.replace", side_effect=fake_replace), \
             patch("os.unlink"):
            with pytest.raises(PermissionError):
                _atomic_replace(src, dst)

        assert replace_calls[0] == (dst, dst_bak), "Backup must be created first"
        assert replace_calls[1] == (src, dst), "Swap must be attempted"
        assert replace_calls[2] == (dst_bak, dst), "Backup must be restored on failure"

    def test_failure_reraises_original_error(self, tmp_path):
        src = str(tmp_path / "src.tmp")
        dst = str(tmp_path / "dst.json")
        Path(src).write_text("new")

        effects = [None, PermissionError("antivirus")]

        def fake_replace(s, d):
            effect = effects.pop(0) if effects else None
            if isinstance(effect, Exception):
                raise effect

        with patch.object(sys, "platform", "win32"), \
             patch("os.replace", side_effect=fake_replace), \
             patch("os.unlink"):
            with pytest.raises(PermissionError, match="antivirus"):
                _atomic_replace(src, dst)

    def test_no_restore_when_dst_never_existed(self, tmp_path):
        """If dst didn't exist, a failed swap must not attempt a restore."""
        src = str(tmp_path / "src.tmp")
        dst = str(tmp_path / "dst.json")
        Path(src).write_text("new")

        replace_calls: list = []
        call_n = [0]

        def fake_replace(s, d):
            replace_calls.append((s, d))
            call_n[0] += 1
            if call_n[0] == 1 and s == dst:
                raise FileNotFoundError("no dst")
            if call_n[0] == 2:
                raise PermissionError("locked")

        with patch.object(sys, "platform", "win32"), \
             patch("os.replace", side_effect=fake_replace), \
             patch("os.unlink"):
            with pytest.raises(PermissionError):
                _atomic_replace(src, dst)

        restore_calls = [(s, d) for s, d in replace_calls
                         if s == dst + ".bak" and d == dst]
        assert restore_calls == [], "No restore attempt when there was no backup"


# ---------------------------------------------------------------------------
# _save_persisted_settings – success
# ---------------------------------------------------------------------------

class TestSavePersistedSettingsSuccess:
    def test_data_written_correctly_posix(self, settings_path):
        data = {"sensitivity": "high", "audio_chunk_seconds": 5}
        with patch.object(sys, "platform", "linux"):
            _save_persisted_settings(data)
        assert json.loads(settings_path.read_text()) == data

    def test_temp_file_removed_after_save_posix(self, settings_path):
        with patch.object(sys, "platform", "linux"):
            _save_persisted_settings({"k": "v"})
        assert list(settings_path.parent.glob(".settings_tmp_*")) == []

    def test_data_written_correctly_windows(self, settings_path):
        data = {"sensitivity": "medium", "audio_chunk_seconds": 10}
        with patch.object(sys, "platform", "win32"):
            _save_persisted_settings(data)
        assert json.loads(settings_path.read_text()) == data

    def test_temp_file_removed_after_save_windows(self, settings_path):
        with patch.object(sys, "platform", "win32"):
            _save_persisted_settings({"k": "v"})
        assert list(settings_path.parent.glob(".settings_tmp_*")) == []

    def test_bak_file_removed_after_save_windows(self, settings_path):
        """dst.bak created during backup step must be deleted on success."""
        settings_path.write_text('{"sensitivity":"low"}')
        with patch.object(sys, "platform", "win32"):
            _save_persisted_settings({"sensitivity": "high"})
        assert not Path(str(settings_path) + ".bak").exists()


# ---------------------------------------------------------------------------
# _save_persisted_settings – failure / crash
# ---------------------------------------------------------------------------

class TestSavePersistedSettingsFailure:
    @staticmethod
    def _exploding_fdopen():
        original = os.fdopen

        def fake(fd, mode):
            original(fd, mode).close()

            class _Exploding:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    pass

                def write(self, _data):
                    raise OSError("disk full")

            return _Exploding()

        return fake

    def test_temp_file_cleaned_up_on_write_failure(self, settings_path):
        with patch("os.fdopen", side_effect=self._exploding_fdopen()), \
             patch.object(sys, "platform", "linux"):
            _save_persisted_settings({"k": "v"})
        assert list(settings_path.parent.glob(".settings_tmp_*")) == []

    def test_existing_settings_intact_after_write_failure(self, settings_path):
        original = {"sensitivity": "low"}
        with patch.object(sys, "platform", "linux"):
            _save_persisted_settings(original)
        with patch("os.fdopen", side_effect=self._exploding_fdopen()), \
             patch.object(sys, "platform", "linux"):
            _save_persisted_settings({"sensitivity": "high"})
        assert json.loads(settings_path.read_text()) == original

    def test_windows_bak_file_cleaned_up_after_swap_failure(self, settings_path):
        """When the final swap fails on Windows, dst.bak must not remain."""
        settings_path.write_text('{"sensitivity":"low"}')
        bak = Path(str(settings_path) + ".bak")

        call_n = [0]
        real_replace = os.replace

        def fake_replace(s, d):
            call_n[0] += 1
            if call_n[0] == 2:
                raise PermissionError("locked")
            real_replace(s, d)

        with patch.object(sys, "platform", "win32"), \
             patch("os.replace", side_effect=fake_replace):
            _save_persisted_settings({"sensitivity": "high"})

        assert not bak.exists(), "dst.bak must not remain after a failed swap"

    def test_windows_existing_settings_restored_after_swap_failure(self, settings_path):
        """Original settings survive a failed Windows swap (failure-restore)."""
        original = {"sensitivity": "low", "audio_chunk_seconds": 3}
        with patch.object(sys, "platform", "linux"):
            _save_persisted_settings(original)

        call_n = [0]
        real_replace = os.replace

        def fake_replace(s, d):
            call_n[0] += 1
            if call_n[0] == 2:
                raise PermissionError("locked")
            real_replace(s, d)

        with patch.object(sys, "platform", "win32"), \
             patch("os.replace", side_effect=fake_replace):
            _save_persisted_settings({"sensitivity": "high"})

        assert json.loads(settings_path.read_text()) == original
