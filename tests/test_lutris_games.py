# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Read each Lutris game once while preserving category visibility."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import call, patch

from cartridges import games, sources
from cartridges.sources import OPEN, lutris


class LutrisGamesTests(unittest.TestCase):
    """Use real category relationships in a temporary launcher database."""

    def setUp(self):
        """Isolate the database and replace unrelated artwork loading."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "pga.db"
        self.records = self.root / "records"
        self.records.mkdir()
        self.enterContext(patch.object(games, "GAMES_DIR", self.records))
        self.enterContext(patch.object(lutris, "_DATA_PATHS", (self.root,)))
        self.artwork = self.enterContext(
            patch.object(lutris.cover, "at_path", return_value=None)
        )
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("""
                CREATE TABLE games (id, name, slug, runner, configPath, installed);
                CREATE TABLE games_categories (game_id, category_id);
                CREATE TABLE categories (id, name);
                INSERT INTO categories VALUES
                    (1, '.hidden'), (2, 'Favorites'), (3, 'Completed'),
                    (4, 'hidden'), (5, '.Hidden');
                INSERT INTO games VALUES (1, 'Same name', 'first', 'wine', 'c', 1);
                INSERT INTO games VALUES (2, 'Same name', 'second', 'linux', 'c', 1);
            """)

    def test_category_memberships_do_not_duplicate_games(self):
        """Any hidden membership wins, regardless of order or repetition."""
        for categories in (
            (),
            (2,),
            (2, 3),
            (1,),
            (1, 2),
            (2, 1),
            (1, 1, 2),
            (2, 2),
            (999,),
            (999, 1),
            (4, 5),
        ):
            with self.subTest(categories=categories):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute("DELETE FROM games_categories")
                    connection.executemany(
                        "INSERT INTO games_categories VALUES (1, ?)",
                        [(category,) for category in categories],
                    )
                before = self.path.read_bytes()
                self.artwork.reset_mock()
                loaded = list(lutris.get_games())
                self.assertCountEqual(
                    [(game.game_id, game.hidden) for game in loaded],
                    [("lutris_wine_1", 1 in categories), ("lutris_linux_2", False)],
                )
                self.assertCountEqual(
                    [(game.name, game.source, game.executable) for game in loaded],
                    [
                        ("Same name", "lutris_wine", f"{OPEN} lutris:rungameid/1"),
                        ("Same name", "lutris_linux", f"{OPEN} lutris:rungameid/2"),
                    ],
                )
                self.assertCountEqual(
                    self.artwork.call_args_list,
                    [
                        call(self.root / "coverart/first.jpg"),
                        call(self.root / "coverart/second.jpg"),
                    ],
                )
                self.assertEqual(self.path.read_bytes(), before)

    def test_ineligible_and_invalid_rows_do_not_remove_healthy_games(self):
        """Preserve runner/install filters and per-row validation."""
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("""
                INSERT INTO games VALUES
                    (3, 'Steam', 's', 'steam', 'c', 1),
                    (4, 'Flatpak', 's', 'flatpak', 'c', 1),
                    (5, 'Uninstalled', 's', 'wine', 'c', 0),
                    (6, 'No config', 's', 'wine', NULL, 1),
                    (7, NULL, 's', 'wine', 'c', 1),
                    (8, 'No slug', NULL, 'wine', 'c', 1),
                    (9, '', 's', 'wine', 'c', 1),
                    (10, 'Empty slug', '', 'wine', 'c', 1),
                    (11, 'No runner', 's', NULL, 'c', 1),
                    ('bad', 'Bad ID', 's', 'wine', 'c', 1);
            """)
        before = self.path.read_bytes()
        with self.assertLogs(lutris.__name__, level="WARNING"):
            loaded = list(lutris.get_games())
        self.assertCountEqual(
            [game.game_id for game in loaded], ["lutris_wine_1", "lutris_linux_2"]
        )
        self.assertEqual(self.path.read_bytes(), before)

    def test_source_loading_preserves_saved_visibility_without_writes(self):
        """Apply real subsource overrides after deriving category visibility."""
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("""
                INSERT INTO games_categories VALUES (1, 1), (1, 2), (2, 2);
            """)
        before = self.path.read_bytes()
        loaded = list(sources.Source(lutris, 100))
        self.assertCountEqual(
            [(game.game_id, game.hidden) for game in loaded],
            [("lutris_wine_1", True), ("lutris_linux_2", False)],
        )
        self.assertEqual(list(self.records.iterdir()), [])
        for game in loaded:
            game.hidden = not game.hidden
            game.save()
        records_before = {
            path.name: path.read_bytes() for path in self.records.iterdir()
        }

        with patch.object(games.Game, "save", side_effect=AssertionError):
            restored = list(sources.Source(lutris, 200))
        self.assertCountEqual(
            [(game.game_id, game.source, game.hidden) for game in restored],
            [
                ("lutris_wine_1", "lutris_wine", False),
                ("lutris_linux_2", "lutris_linux", True),
            ],
        )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.records.iterdir()},
            records_before,
        )
