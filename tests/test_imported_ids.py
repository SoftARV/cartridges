# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Regression tests for manually added game IDs."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cartridges import games, sources
from cartridges.collections import Collection
from cartridges.games import Game
from cartridges.sources import imported
from cartridges.ui.games import GameEditable


class ImportedIDTests(unittest.TestCase):
    """Allocate IDs without changing existing records."""

    def setUp(self):
        """Keep record discovery inside a temporary directory."""
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.games_dir = Path(directory)
        self.enterContext(patch.object(imported, "GAMES_DIR", self.games_dir))
        self.enterContext(patch.object(games, "GAMES_DIR", self.games_dir))

    def test_empty_library_starts_at_zero(self):
        """Start at zero without writing a record."""
        game = imported.new(())

        self.assertEqual(game.game_id, "imported_0")
        self.assertEqual(game.source, "imported")
        self.assertEqual(list(self.games_dir.iterdir()), [])

    def test_saved_filenames_reserve_ids_without_reading_json(self):
        """Reserve canonical filenames even when their records are invalid."""
        names = ("imported_0.json", "imported_2.json")
        for name in names:
            (self.games_dir / name).write_text("invalid JSON", encoding="utf-8")

        self.assertEqual(imported.new(()).game_id, "imported_1")
        self.assertEqual({path.name for path in self.games_dir.iterdir()}, set(names))
        for name in names:
            self.assertEqual(
                (self.games_dir / name).read_text(encoding="utf-8"), "invalid JSON"
            )

    def test_malformed_filenames_do_not_break_allocation(self):
        """Leave malformed filenames untouched and continue allocating."""
        names = ("imported_bad.json", "imported_.json", "imported_-1.json")
        for name in names:
            (self.games_dir / name).write_text("unchanged", encoding="utf-8")

        self.assertEqual(imported.new(()).game_id, "imported_0")
        self.assertEqual({path.name for path in self.games_dir.iterdir()}, set(names))
        for name in names:
            self.assertEqual(
                (self.games_dir / name).read_text(encoding="utf-8"), "unchanged"
            )

    def test_saved_additions_have_distinct_ids_and_collection_membership(self):
        """Exercise the real save/append flow and preserve independent membership."""
        source = sources.Source(imported, 1)
        with patch.object(sources, "get", return_value=source):
            first = GameEditable(name="First", executable="true", developer="One")
            first.apply()
            second = GameEditable(name="Second", executable="true")
            second.apply()

        self.assertEqual(
            [game.game_id for game in source], ["imported_0", "imported_1"]
        )
        self.assertEqual(first.game.name, "First")
        self.assertEqual(first.game.developer, "One")
        self.assertEqual(second.game.name, "Second")
        self.assertEqual(second.game.executable, "true")
        collection = Collection(game_ids=[first.game.game_id])
        self.assertIn(first.game.game_id, collection)
        self.assertNotIn(second.game.game_id, collection)
        self.assertEqual(
            {path.name for path in self.games_dir.iterdir()},
            {"imported_0.json", "imported_1.json"},
        )

    def test_disk_and_hidden_or_removed_games_reserve_ids(self):
        """Use all games in memory, even ones absent from filtered views."""
        (self.games_dir / "imported_0.json").write_text("invalid", encoding="utf-8")
        games = [
            Game(game_id="imported_1", hidden=True),
            Game(game_id="imported_2", removed=True),
        ]

        self.assertEqual(imported.new(iter(games)).game_id, "imported_3")

    def test_mixed_ids_leave_gaps_available(self):
        """Allocate the smallest free ID across disk and memory."""
        (self.games_dir / "imported_2.json").write_text("invalid", encoding="utf-8")
        games = [Game(game_id="imported_0"), Game(game_id="imported_3")]

        self.assertEqual(imported.new(games).game_id, "imported_1")
        self.assertEqual([game.game_id for game in games], ["imported_0", "imported_3"])
