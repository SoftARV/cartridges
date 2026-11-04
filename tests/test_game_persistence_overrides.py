# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Restore saved values without replacing discovery identity or artwork."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gi.repository import Gdk, GLib

from cartridges import games, sources
from cartridges.games import PROPERTIES, Game


class OverrideTests(unittest.TestCase):
    """Use one controlled detected game and an isolated record directory."""

    def setUp(self):
        """Prepare discovery metadata and a different saved snapshot."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory = self.root / "games"
        self.directory.mkdir()
        self.enterContext(patch.object(games, "GAMES_DIR", self.directory))
        self.game = Game(
            game_id="heroic_gog_1",
            source="heroic_gog",
            name="Discovered",
            executable="launcher new",
            developer="New studio",
            added=20,
            last_played=30,
        )
        self.game.cover = Gdk.MemoryTexture.new(
            1, 1, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(4)), 4
        )
        self.module = SimpleNamespace(
            ID="heroic", NAME="Heroic", get_games=lambda: iter([self.game])
        )
        self.path = self.directory / "heroic_gog_1.json"
        self.data = {prop.name: getattr(self.game, prop.name) for prop in PROPERTIES}
        self.data.update(
            name="Saved",
            executable=["saved", "two words"],
            developer="",
            added=10,
            last_played=25,
            hidden=True,
            removed=True,
        )

    def load(self) -> Game:
        """Read through the real shared source boundary."""
        return next(iter(sources.Source(self.module, 100)))

    def test_saved_snapshot_preserves_identity_artwork_and_newer_play_time(self):
        """Restore user choices before the game reaches source consumers."""
        self.path.write_text(json.dumps(self.data))
        before = self.path.read_bytes()
        artwork = self.game.cover
        with patch.object(Game, "save", side_effect=AssertionError):
            loaded = self.load()
        self.assertIs(loaded, self.game)
        self.assertIs(loaded.cover, artwork)
        self.assertEqual(
            (loaded.name, loaded.developer, loaded.added, loaded.last_played),
            ("Saved", "", 10, 30),
        )
        self.assertEqual(loaded.executable, "saved 'two words'")
        self.assertTrue(loaded.hidden)
        self.assertTrue(loaded.removed)
        self.assertEqual(self.path.read_bytes(), before)

    def test_absent_optional_fields_keep_discovery_values(self):
        """Do not apply defaults introduced while validating an older record."""
        for field in ("developer", "added", "hidden", "removed", "blacklisted"):
            del self.data[field]
        self.data["last_played"] = 40
        self.path.write_text(json.dumps(self.data))
        loaded = self.load()
        self.assertEqual(
            (loaded.developer, loaded.added, loaded.last_played), ("New studio", 20, 40)
        )
        self.assertFalse(loaded.hidden)

    def test_invalid_overrides_leave_discovery_and_bytes_unchanged(self):
        """Isolate malformed records without rewriting or partially applying them."""
        for value in (
            b"{",
            b"\xff",
            b"[]",
            b"null",
            json.dumps({**self.data, "source": "steam"}).encode(),
            json.dumps({**self.data, "game_id": "different"}).encode(),
            json.dumps({**self.data, "version": 999.0}).encode(),
            json.dumps({**self.data, "hidden": "yes"}).encode(),
        ):
            with self.subTest(value=value):
                self.path.write_bytes(value)
                self.assertIs(self.load(), self.game)
                self.assertEqual(self.game.name, "Discovered")
                self.assertEqual(self.path.read_bytes(), value)

    def test_missing_or_unreadable_overrides_keep_discovery(self):
        """Treat absence as normal and contain an override read failure."""
        self.assertEqual(self.load().name, "Discovered")
        with patch.object(Path, "open", side_effect=PermissionError):
            self.assertEqual(self.load().name, "Discovered")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_unsafe_ids_and_symlinks_are_not_read(self):
        """Use the writer's filename boundary for override reads too."""
        target = self.root / "outside.json"
        target.write_text(json.dumps(self.data))
        self.path.symlink_to(target)
        self.assertEqual(self.load().name, "Discovered")
        self.path.unlink()
        for game_id in ("../outside", "C:outside", "nested\\outside", ""):
            with self.subTest(game_id=game_id):
                self.game.game_id = game_id
                with patch.object(Path, "open", side_effect=AssertionError):
                    self.assertEqual(self.load().name, "Discovered")

    def test_orphan_override_is_not_a_new_game(self):
        """Publish only the games produced by discovery."""
        self.path.write_text(json.dumps(self.data))
        self.module.get_games = lambda: iter(())
        self.assertEqual(list(sources.Source(self.module, 100)), [])
        self.assertTrue(self.path.exists())
