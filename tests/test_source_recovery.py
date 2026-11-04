# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Keep valid games available when external source data is damaged."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cartridges import sources
from cartridges.games import Game
from cartridges.sources import heroic, imported, legendary


class SourceRecoveryTests(unittest.TestCase):
    """Exercise source failures without inspecting the user's library."""

    def setUp(self):
        """Create temporary record and cover roots."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(imported, "GAMES_DIR", self.root))
        self.enterContext(patch.object(imported, "COVERS_DIR", self.root / "covers"))

    def test_source_keeps_games_before_io_failure(self):
        """Keep yielded games and avoid leaking error paths into logs."""
        game = Game(game_id="fixture_0")

        def broken_reader():
            yield game
            message = "private path and command"
            raise PermissionError(message)

        module = SimpleNamespace(ID="fixture", NAME="Fixture", get_games=broken_reader)
        with self.assertLogs("cartridges.sources", level="WARNING") as messages:
            source = sources.Source(module, 1)
        self.assertEqual(list(source), [game])
        self.assertIn("fixture", " ".join(messages.output))
        self.assertNotIn("private path", " ".join(messages.output))

    def test_missing_source_is_quiet(self):
        """Treat an uninstalled launcher as normal."""
        module = SimpleNamespace(ID="absent", NAME="Absent")
        module.get_games = lambda: (_ for _ in ()).throw(FileNotFoundError())
        with self.assertNoLogs("cartridges.sources", level="WARNING"):
            self.assertEqual(list(sources.Source(module, 1)), [])

    def test_programming_errors_remain_visible(self):
        """Do not hide implementation failures as bad user data."""
        module = SimpleNamespace(ID="broken", NAME="Broken")
        module.get_games = lambda: (_ for _ in ()).throw(RuntimeError())
        with self.assertRaises(RuntimeError):
            sources.Source(module, 1)

    def test_manual_bad_records_do_not_hide_valid_peers(self):
        """Skip invalid JSON, shapes, encoding, and properties without writes."""
        good = {
            "game_id": "imported_0",
            "source": "imported",
            "name": "Good",
            "executable": "true",
        }
        values = [
            json.dumps(good).encode(),
            b"[]",
            b"null",
            b"{",
            b"\xff",
            b"{}",
            json.dumps({**good, "game_id": "imported_1"}).encode(),
        ]
        paths = []
        for number, value in enumerate(values):
            path = self.root / f"imported_{number}.json"
            path.write_bytes(value)
            paths.append(path)
        with patch.object(imported, "get_paths", return_value=iter(paths)):
            games = list(imported.get_games())
        self.assertEqual([game.game_id for game in games], ["imported_0", "imported_1"])
        self.assertEqual([path.read_bytes() for path in paths], values)

    def test_heroic_wrong_shape_does_not_stop_other_stores(self):
        """Load valid sideload games after a malformed store library."""
        (self.root / "store_cache").mkdir()
        (self.root / "store_cache" / "legendary_library.json").write_text("[]")
        (self.root / "sideload_apps").mkdir()
        records = [
            {"app_name": "first", "runner": "sideload", "title": "First"},
            None,
            {"app_name": [], "runner": "bad", "title": "Bad"},
            {"app_name": "last", "runner": "sideload", "title": "Last"},
        ]
        (self.root / "sideload_apps" / "library.json").write_text(
            json.dumps({"games": records})
        )
        with patch.object(heroic, "_CONFIG_PATHS", (self.root,)):
            games = list(heroic.get_games())
        self.assertEqual([game.name for game in games], ["First", "Last"])

    def test_legendary_wrong_entries_and_metadata_keep_valid_games(self):
        """Treat malformed optional metadata as absent."""
        installed = {
            "first": {"app_name": "first", "title": "First"},
            "bad": None,
            "last": {"app_name": "last", "title": "Last"},
        }
        (self.root / "installed.json").write_text(json.dumps(installed))
        (self.root / "metadata").mkdir()
        (self.root / "metadata" / "first.json").write_text("[]")
        (self.root / "metadata" / "last.json").write_text(
            '{"metadata": {"developer": [], "keyImages": [null]}}'
        )
        with patch.object(legendary, "_CONFIG_PATH", self.root):
            games = list(legendary.get_games())
        self.assertEqual([game.name for game in games], ["First", "Last"])
