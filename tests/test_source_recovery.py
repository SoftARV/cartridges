# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Keep valid games available when external source data is damaged."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cartridges import games, sources
from cartridges.games import Game
from cartridges.sources import heroic, imported, legendary


class SourceRecoveryTests(unittest.TestCase):
    """Exercise source failures without inspecting the user's library."""

    def setUp(self):
        """Create temporary record and cover roots."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(imported, "GAMES_DIR", self.root))
        self.enterContext(patch.object(games, "GAMES_DIR", self.root))
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

    def test_source_loading_continues_after_a_partial_failure(self):
        """Keep partial results and load a later healthy source."""
        first = Game(game_id="first")
        last = Game(game_id="last")

        def broken():
            yield first
            raise PermissionError

        modules = [
            SimpleNamespace(ID="broken", NAME="Broken", get_games=broken),
            SimpleNamespace(
                ID="healthy", NAME="Healthy", get_games=lambda: iter([last])
            ),
        ]
        model = sources.Gio.ListStore(item_type=sources.Source)
        with (
            patch.object(sources, "model", model),
            patch.object(
                sources.pkgutil,
                "iter_modules",
                return_value=[SimpleNamespace(name=module.ID) for module in modules],
            ),
            patch.object(sources.importlib, "import_module", side_effect=modules),
        ):
            sources.load()
        self.assertEqual(
            [[game.game_id for game in source] for source in model],
            [["first"], ["last"]],
        )

    def test_manual_unreadable_record_does_not_hide_later_records(self):
        """Continue after a missing record from a discovery snapshot."""
        missing = self.root / "imported_missing.json"
        valid = self.root / "imported_valid.json"
        valid.write_text(
            json.dumps({
                "game_id": "imported_valid",
                "source": "imported",
                "name": "Valid",
                "executable": "true",
            })
        )
        with patch.object(imported, "get_paths", return_value=iter([missing, valid])):
            games = list(imported.get_games())
        self.assertEqual([game.name for game in games], ["Valid"])

    def test_heroic_null_library_is_reported(self):
        """Do not confuse an existing null JSON library with an absent store."""
        (self.root / "sideload_apps").mkdir()
        (self.root / "sideload_apps" / "library.json").write_text("null")
        with (
            patch.object(heroic, "_CONFIG_PATHS", (self.root,)),
            self.assertLogs(heroic.__name__, level="WARNING"),
        ):
            self.assertEqual(list(heroic.get_games()), [])

    def test_manual_out_of_range_fields_do_not_hide_later_records(self):
        """Reject integer fields that cannot fit the model's GObject properties."""
        good = {
            "game_id": "imported_1",
            "source": "imported",
            "name": "Valid",
            "executable": "true",
        }
        bad = self.root / "imported_0.json"
        bad.write_text(json.dumps({**good, "added": 10**100}))
        valid = self.root / "imported_1.json"
        valid.write_text(json.dumps(good))
        with patch.object(imported, "get_paths", return_value=iter([bad, valid])):
            games = list(imported.get_games())
        self.assertEqual([game.name for game in games], ["Valid"])

    def test_heroic_store_indexes_preserve_installed_and_hidden_games(self):
        """Retain valid store entries around malformed membership metadata."""
        (self.root / "store_cache").mkdir()
        (self.root / "store").mkdir()
        (self.root / "store" / "config.json").write_text(
            json.dumps({
                "games": {"hidden": [{"appName": "first"}, None, {"appName": []}]}
            })
        )
        indexes = (
            (
                "legendary",
                "library",
                "legendaryConfig/legendary/installed.json",
                {"first": {}, "last": {}},
            ),
            (
                "gog",
                "games",
                "gog_store/installed.json",
                {
                    "installed": [
                        {"appName": "first"},
                        None,
                        {"appName": []},
                        {"appName": "last"},
                    ]
                },
            ),
            (
                "nile",
                "library",
                "nile_config/nile/installed.json",
                [{"id": "first"}, None, {"id": []}, {"id": "last"}],
            ),
        )
        for store, key, installed_path, installed in indexes:
            path = self.root / installed_path
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(installed))
            entries = [
                {"app_name": name, "runner": store, "title": name}
                for name in ("first", "uninstalled", "last")
            ]
            (self.root / "store_cache" / f"{store}_library.json").write_text(
                json.dumps({key: entries})
            )
        with patch.object(heroic, "_CONFIG_PATHS", (self.root,)):
            games = list(heroic.get_games())
        expected = {
            f"heroic_{store}_{name}"
            for store, *_ in indexes
            for name in ("first", "last")
        }
        self.assertEqual({game.game_id for game in games}, expected)
        self.assertTrue(all(game.hidden == (game.name == "first") for game in games))
