# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Regression tests for saving game records without losing data."""

import json
import selectors
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TextIO
from unittest.mock import PropertyMock, patch

from cartridges import games
from cartridges.games import PROPERTIES, Game
from cartridges.sources import imported


class GameSaveTests(unittest.TestCase):
    """Keep all save operations inside temporary fixtures."""

    def setUp(self):
        """Redirect game records to an isolated library."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory = self.root / "games"
        self.enterContext(patch.object(games, "GAMES_DIR", self.directory))
        self.game = Game(
            game_id="imported_0", source="imported", name="Game", executable="true"
        )

    def test_invalid_ids_do_not_create_files(self):
        """Reject path syntax before creating the library directory."""
        outside = self.root / "outside.json"
        outside.write_bytes(b"unchanged")
        for game_id in (
            "",
            ".",
            "..",
            "../outside",
            "nested/game",
            "nested\\game",
            str(outside.with_suffix("")),
            "C:game",
            "C:\\game",
            "\0",
        ):
            with self.subTest(game_id=game_id):
                self.game.game_id = game_id
                with self.assertRaises(ValueError):
                    self.game.save()
                self.assertFalse(self.directory.exists())
                self.assertEqual(outside.read_bytes(), b"unchanged")

    def test_valid_names_preserve_format_and_round_trip(self):
        """Append the JSON suffix without changing valid IDs or properties."""
        names = (
            "imported_0",
            "steam_42",
            "has.dots",
            "with-hyphens",
            "with spaces",
            "juego_ñ",
        )
        for game_id in names:
            with self.subTest(game_id=game_id):
                self.game.game_id = game_id
                self.game.save()
                path = self.directory / (game_id + ".json")
                expected = {
                    prop.name: getattr(self.game, prop.name) for prop in PROPERTIES
                }
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    json.dumps(expected, indent=4, sort_keys=True),
                )
                loaded = Game.from_data(json.loads(path.read_text(encoding="utf-8")))
                self.assertEqual(
                    {prop.name: getattr(loaded, prop.name) for prop in PROPERTIES},
                    expected,
                )

        self.assertEqual(
            {path.name for path in self.directory.iterdir()},
            {name + ".json" for name in names},
        )

    def test_destination_symlinks_are_rejected(self):
        """Leave both existing and missing external targets unchanged."""
        self.directory.mkdir()
        path = self.directory / "imported_0.json"
        outside = self.root / "outside.json"
        for exists in (True, False):
            with self.subTest(exists=exists):
                if exists:
                    outside.write_bytes(b"unchanged")
                path.symlink_to(outside)
                with self.assertRaises(OSError):
                    self.game.save()
                self.assertTrue(path.is_symlink())
                self.assertEqual(outside.exists(), exists)
                if exists:
                    self.assertEqual(outside.read_bytes(), b"unchanged")
                    outside.unlink()
                path.unlink()

    def test_symlinked_library_is_supported(self):
        """Allow the configured library itself to point to another directory."""
        target = self.root / "target"
        target.mkdir()
        self.directory.symlink_to(target, target_is_directory=True)
        self.game.save()
        self.assertTrue((target / "imported_0.json").is_file())

    def test_destination_directory_is_rejected(self):
        """Leave a directory at the destination untouched."""
        path = self.directory / "imported_0.json"
        path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.game.save()
        self.assertTrue(path.is_dir())
        self.assertEqual(list(path.iterdir()), [])

    def test_serialization_failure_preserves_records(self):
        """Keep old bytes or an absent destination after partial serialization."""
        self.directory.mkdir()
        path = self.directory / "imported_0.json"
        other = self.directory / "imported_1.json"
        other.write_bytes(b"other record")
        failure = TypeError("cannot serialize")

        def fail_after_prefix(_properties, stream: TextIO, **_kwargs):
            stream.write('{"name":')
            raise failure

        for exists in (False, True):
            with self.subTest(exists=exists):
                if exists:
                    path.write_bytes(b"original record")
                before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
                with (
                    patch.object(games.json, "dump", side_effect=fail_after_prefix),
                    self.assertRaises(TypeError) as caught,
                ):
                    self.game.save()
                self.assertIs(caught.exception, failure)
                self.assertEqual(
                    {p.name: p.read_bytes() for p in self.directory.iterdir()}, before
                )

    def test_embedded_nul_is_rejected_before_directory_creation(self):
        """Check the save boundary without GObject truncating the input string."""
        with (
            patch.object(
                Game, "game_id", new_callable=PropertyMock, return_value="game\0suffix"
            ),
            self.assertRaises(ValueError),
        ):
            self.game.save()
        self.assertFalse(self.directory.exists())

    def test_io_failures_preserve_records(self):
        """Preserve existing or absent records at each filesystem failure point."""
        self.directory.mkdir()
        other = self.directory / "imported_1.json"
        other.write_bytes(b"other record")
        for stage in ("create", "write", "close", "replace"):
            for exists in (False, True):
                with self.subTest(stage=stage, exists=exists):
                    self.check_io_failure(stage, exists)

    def check_io_failure(self, stage: str, exists: bool):
        """Inject one failure and compare the full library with its prior state."""
        path = self.directory / "imported_0.json"
        path.unlink(missing_ok=True)
        if exists:
            path.write_bytes(b"original record")
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        failure = PermissionError(stage)
        with self.assertRaises(PermissionError) as caught:
            if stage == "create":
                with patch.object(
                    games.tempfile,
                    "NamedTemporaryFile",
                    side_effect=failure,
                ):
                    self.game.save()
            elif stage == "replace":
                with patch.object(Path, "replace", side_effect=failure):
                    self.game.save()
            else:
                temporary = self.enterContext(
                    tempfile.NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        dir=self.directory,
                        delete=False,
                    )
                )
                with patch.object(
                    games.tempfile,
                    "NamedTemporaryFile",
                    return_value=temporary,
                ):
                    if stage == "write":
                        write = temporary.write

                        def fail_write(text: str):
                            write(text[:1])
                            raise failure

                        with patch.object(temporary, "write", side_effect=fail_write):
                            self.game.save()
                    else:

                        def fail_close(*_args):
                            temporary.close()
                            raise failure

                        with patch.object(
                            type(temporary),
                            "__exit__",
                            side_effect=fail_close,
                        ):
                            self.game.save()
        self.assertIs(caught.exception, failure)
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.directory.iterdir()},
            before,
        )

    def test_cleanup_failure_keeps_original_error(self):
        """Expose the save error even if removing the temporary file fails."""
        self.directory.mkdir()
        path = self.directory / "imported_0.json"
        path.write_bytes(b"original record")
        failure = TypeError("cannot serialize")
        with (
            patch.object(games.json, "dump", side_effect=failure),
            patch.object(
                Path, "unlink", side_effect=PermissionError("cannot clean up")
            ),
            self.assertRaises(TypeError) as caught,
        ):
            self.game.save()
        self.assertIs(caught.exception, failure)
        self.assertEqual(path.read_bytes(), b"original record")
        with patch.object(imported, "GAMES_DIR", self.directory):
            self.assertEqual(list(imported.get_paths()), [path])

    def test_replacement_exposes_only_complete_records(self):
        """Keep old bytes until the closed temporary record replaces them."""
        self.directory.mkdir()
        path = self.directory / "imported_0.json"
        path.write_bytes(b"original record")
        other = self.directory / "imported_1.json"
        other.write_bytes(b"other record")
        replace = Path.replace
        temporary = self.enterContext(
            tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                delete=False,
            )
        )

        def inspect_replace(source: Path, destination: Path):
            self.assertTrue(temporary.closed)
            self.assertEqual(path.read_bytes(), b"original record")
            self.assertEqual(
                json.loads(Path(source).read_text(encoding="utf-8"))["name"], "Updated"
            )
            replace(source, destination)

        self.game.name = "Updated"
        with (
            patch.object(games.tempfile, "NamedTemporaryFile", return_value=temporary),
            patch.object(Path, "replace", autospec=True, side_effect=inspect_replace),
        ):
            self.game.save()
        loaded = Game.from_data(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(loaded.name, "Updated")
        self.assertEqual(other.read_bytes(), b"other record")
        self.assertEqual(set(self.directory.iterdir()), {path, other})
        with patch.object(imported, "GAMES_DIR", self.directory):
            self.assertEqual(set(imported.get_paths()), {path, other})

    def test_terminated_writer_preserves_old_record(self):
        """Kill a writer at the replacement boundary without timing assumptions."""
        self.directory.mkdir()
        path = self.directory / "imported_0.json"
        path.write_bytes(b"original record")
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
from cartridges import games

games.GAMES_DIR = Path(sys.argv[1])
def wait_before_replace(*args):
    print("ready", flush=True)
    sys.stdin.buffer.read(1)
    raise RuntimeError("parent should terminate the writer")
with patch.object(Path, "replace", side_effect=wait_before_replace):
    game = games.Game(
        game_id="imported_0", source="imported", name="New", executable="true"
    )
    game.save()
"""
        process = subprocess.Popen(  # noqa: S603 - Fixed script and temporary fixture path.
            [sys.executable, "-P", "-c", script, str(self.directory)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(
                    selector.select(timeout=10), "Writer did not reach replacement"
                )
                self.assertEqual(process.stdout.readline(), b"ready\n")
            process.kill()
            process.communicate(timeout=10)
            self.assertLess(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.assertEqual(path.read_bytes(), b"original record")
        orphans = set(self.directory.iterdir()) - {path}
        self.assertEqual(len(orphans), 1)
        self.assertEqual(
            json.loads(orphans.pop().read_text(encoding="utf-8"))["name"], "New"
        )
        with patch.object(imported, "GAMES_DIR", self.directory):
            self.assertEqual(list(imported.get_paths()), [path])
