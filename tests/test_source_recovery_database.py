# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Read launcher databases without losing games or creating missing files."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cartridges.sources import itch, lutris


class DatabaseRecoveryTests(unittest.TestCase):
    """Use real SQLite fixtures for expected reader failures."""

    def setUp(self):
        """Redirect both database readers into a temporary configuration."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "db").mkdir()
        self.enterContext(patch.object(lutris, "_DATA_PATHS", (self.root,)))
        self.enterContext(patch.object(itch, "_CONFIG_PATHS", (self.root,)))
        self.enterContext(patch.object(lutris.cover, "at_path", return_value=None))
        app = SimpleNamespace(create_asyncio_task=lambda coroutine: coroutine.close())
        self.enterContext(
            patch.object(itch.Gio.Application, "get_default", return_value=app)
        )

    def test_missing_and_corrupt_databases_are_contained(self):
        """Log source failures without creating or rewriting launcher data."""
        for module, path in (
            (lutris, self.root / "pga.db"),
            (itch, self.root / "db" / "butler.db"),
        ):
            for payload in (None, b"not a database"):
                with self.subTest(source=module.ID, payload=payload):
                    if payload is not None:
                        path.write_bytes(payload)
                    with self.assertLogs(module.__name__, level="WARNING") as logs:
                        self.assertEqual(list(module.get_games()), [])
                    self.assertIn(module.ID.lower(), " ".join(logs.output).lower())
                    self.assertNotIn(str(self.root), " ".join(logs.output))
                    if payload is None:
                        self.assertFalse(path.exists())
                    else:
                        self.assertEqual(path.read_bytes(), payload)
                        path.unlink()

    def test_missing_tables_are_contained(self):
        """Treat unsupported launcher schemas as source-level failures."""
        for module, path in (
            (lutris, self.root / "pga.db"),
            (itch, self.root / "db" / "butler.db"),
        ):
            with self.subTest(source=module.ID):
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute("CREATE TABLE unrelated (id INTEGER)")
                before = path.read_bytes()
                with self.assertLogs(module.__name__, level="WARNING"):
                    self.assertEqual(list(module.get_games()), [])
                self.assertEqual(path.read_bytes(), before)

    def test_lutris_skips_invalid_rows_without_changing_database(self):
        """Keep valid peers around an invalid game row."""
        path = self.root / "pga.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript("""
                CREATE TABLE games (id, name, slug, runner, configPath, installed);
                CREATE TABLE games_categories (game_id, category_id);
                CREATE TABLE categories (id, name);
                INSERT INTO games VALUES (1, 'First', 'first', 'wine', 'config', 1);
                INSERT INTO games VALUES (2, 42, 'bad', 'wine', 'config', 1);
                INSERT INTO games VALUES (3, 'Last', 'last', 'wine', 'config', 1);
            """)
        before = path.read_bytes()
        games = list(lutris.get_games())
        self.assertEqual([game.name for game in games], ["First", "Last"])
        self.assertEqual(path.read_bytes(), before)

    def test_itch_skips_invalid_rows_without_changing_database(self):
        """Keep installed itch games despite a malformed row."""
        path = self.root / "db" / "butler.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript("""
                CREATE TABLE games (id, title, cover_url, still_cover_url);
                CREATE TABLE caves (id, game_id);
                INSERT INTO games VALUES (1, 'First', NULL, NULL);
                INSERT INTO games VALUES (2, 42, NULL, NULL);
                INSERT INTO games VALUES (3, 'Last', NULL, NULL);
                INSERT INTO caves VALUES (10, 1), (20, 2), (30, 3);
            """)
        before = path.read_bytes()
        games = list(itch.get_games())
        self.assertEqual([game.name for game in games], ["First", "Last"])
        self.assertEqual(path.read_bytes(), before)
