# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Regression tests for manually added game IDs."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cartridges.sources import imported


class ImportedIDTests(unittest.TestCase):
    """Allocate IDs without changing existing records."""

    def setUp(self):
        """Keep record discovery inside a temporary directory."""
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.games_dir = Path(directory)
        self.enterContext(patch.object(imported, "GAMES_DIR", self.games_dir))

    def test_empty_library_starts_at_zero(self):
        """Start at zero without writing a record."""
        game = imported.new()

        self.assertEqual(game.game_id, "imported_0")
        self.assertEqual(game.source, "imported")
        self.assertEqual(list(self.games_dir.iterdir()), [])

    def test_saved_filenames_reserve_ids_without_reading_json(self):
        """Reserve canonical filenames even when their records are invalid."""
        names = ("imported_0.json", "imported_2.json")
        for name in names:
            (self.games_dir / name).write_text("invalid JSON", encoding="utf-8")

        self.assertEqual(imported.new().game_id, "imported_1")
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

        self.assertEqual(imported.new().game_id, "imported_0")
        self.assertEqual({path.name for path in self.games_dir.iterdir()}, set(names))
        for name in names:
            self.assertEqual(
                (self.games_dir / name).read_text(encoding="utf-8"), "unchanged"
            )
