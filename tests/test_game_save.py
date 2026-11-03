# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Regression tests for saving game records without losing data."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cartridges import games
from cartridges.games import PROPERTIES, Game


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
        for game_id in (
            "imported_0",
            "steam_42",
            "has.dots",
            "with-hyphens",
            "with spaces",
            "juego_ñ",
        ):
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
