"""The CLI surface and the packaging contract.

The packaging checks exist because PyInstaller runs its entry script as a
top-level module: pointing it at ``autotutor/__main__.py`` (relative imports)
produces an executable that dies immediately with an ImportError.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from autotutor.app import _build_parser, main
from autotutor.version import APP_NAME, __version__

ROOT = Path(__file__).resolve().parent.parent


class TestCliParser:
    def test_defaults(self):
        args = _build_parser().parse_args([])
        assert args.cli is False
        assert args.level is None and args.topic is None

    def test_full_invocation(self):
        args = _build_parser().parse_args(
            ["--cli", "--level", "N3", "--topic", "anime", "--length", "long",
             "--source", "online", "--seed", "7", "--no-audio"]
        )
        assert (args.level, args.topic, args.length) == ("N3", "anime", "long")
        assert args.source == "online" and args.seed == 7 and args.no_audio

    def test_rejects_unknown_level(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--level", "N9"])

    def test_version_flag_exits_cleanly(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert __version__ in capsys.readouterr().out


class TestCliRun:
    def test_generates_files(self, tmp_path):
        code = main([
            "--cli", "--level", "N5", "--topic", "daily_life",
            "--length", "short", "--no-audio", "--seed", "3",
            "--out", str(tmp_path),
        ])
        assert code == 0
        assert {p.suffix for p in tmp_path.iterdir()} == {".txt", ".html"}

    def test_free_text_topic_falls_back_offline(self, tmp_path):
        code = main([
            "--cli", "--level", "N4", "--topic", "医院",
            "--length", "short", "--no-audio", "--out", str(tmp_path),
        ])
        assert code == 0
        assert any(p.suffix == ".txt" for p in tmp_path.iterdir())


class TestPackaging:
    def test_launcher_exists_and_uses_absolute_imports(self):
        launcher = ROOT / "launcher.py"
        assert launcher.is_file()
        source = launcher.read_text(encoding="utf-8")
        tree = ast.parse(source)
        relative = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0
        ]
        assert not relative, "the frozen entry point cannot use relative imports"
        assert "from autotutor.app import main" in source

    def test_spec_points_at_the_launcher(self):
        spec = (ROOT / "AutoTutor.spec").read_text(encoding="utf-8")
        assert '"launcher.py"' in spec
        assert '"__main__.py"' not in spec

    def test_spec_bundles_the_corpus_and_the_voice(self):
        spec = (ROOT / "AutoTutor.spec").read_text(encoding="utf-8")
        assert 'collect_data_files("pyopenjtalk"' in spec
        assert '"autotutor/data"' in spec

    def test_launcher_runs_as_a_script(self, tmp_path):
        """The exact code path PyInstaller will take."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "launcher.py"), "--version"],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert APP_NAME in result.stdout

    def test_package_is_runnable_as_a_module(self):
        result = subprocess.run(
            [sys.executable, "-m", "autotutor", "--version"],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
        assert result.returncode == 0, result.stderr

    def test_requirements_pin_the_offline_stack(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for package in ("pyopenjtalk-plus", "lameenc", "numpy", "pykakasi"):
            assert package in requirements
