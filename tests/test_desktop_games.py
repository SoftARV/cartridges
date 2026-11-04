# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Exclude Steam shortcuts without losing ordinary desktop games."""

import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gi.repository import GLib

from cartridges import games
from cartridges.sources import desktop


class DesktopGamesTests(unittest.TestCase):
    """Read real desktop files from isolated source directories."""

    def setUp(self):
        """Keep discovery away from user files, commands, and artwork."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.entries = self.root / "applications"
        self.entries.mkdir()
        self.records = self.root / "records"
        self.records.mkdir()
        self.enterContext(patch.object(desktop, "_DESKTOP_PATHS", (self.entries,)))
        self.enterContext(patch.object(games, "GAMES_DIR", self.records))
        self.theme = Mock()
        self.enterContext(patch.object(desktop, "_icon_theme", return_value=self.theme))
        self.enterContext(patch.object(desktop.cover, "from_icon", return_value=None))
        self.enterContext(patch.object(desktop, "_try_exec", return_value=True))

    def write_entry(self, name: str, command: str, **fields: str) -> Path:
        """Serialize Exec through the same KeyFile boundary used by discovery."""
        file = GLib.KeyFile()
        for key, value in {
            "Type": "Application",
            "Name": "Same title",
            "Categories": "Game;",
            "Exec": command,
            "Icon": "game-icon",
            **fields,
        }.items():
            file.set_string("Desktop Entry", key, value)
        path = self.entries / name
        path.write_text(file.to_data()[0])
        return path

    def test_steam_shortcuts_are_excluded_before_icon_loading(self):
        """Handle native Steam arguments while preserving the bare URI rule."""
        for command in (
            "steam steam://rungameid/3321460",
            "/usr/bin/steam steam://rungameid/2344520",
            '"/usr/bin/steam" "steam://rungameid/2344520"',
            '"/opt/Steam Client/steam" "steam://rungameid/2344520"',
            "  steam   steam://rungameid/2344520  ",
            "steam -silent steam://rungameid/2344520 %U",
            "steam://rungameid/2344520",
        ):
            with self.subTest(command=command):
                self.write_entry("steam.desktop", command)
                self.theme.reset_mock()
                self.assertEqual(list(desktop.get_games()), [])
                self.theme.lookup_icon.assert_not_called()

    def test_unrelated_commands_and_near_matches_remain(self):
        """Do not match by title, substring, or a similar executable name."""
        for command in (
            "other-game --note steam://rungameid/2344520",
            "steam-helper steam://rungameid/2344520",
            "./steam steam://rungameid/2344520",
            "steam",
            "steam steam://rungameid/abc",
            "steam steam://rungameid/\uff11\uff12\uff13",
            "steam prefixsteam://rungameid/123",
            "steam steam://rungameid/123suffix",
            "steam steam://rungameid/123/",
            "steam STEAM://rungameid/123",
            "Steam steam://rungameid/123",
            "env steam steam://rungameid/123",
            'sh -c "steam steam://rungameid/123"',
        ):
            with self.subTest(command=command):
                path = self.write_entry("ordinary game.desktop", command)
                loaded = list(desktop.get_games())
                self.assertEqual(len(loaded), 1)
                game = loaded[0]
                self.assertEqual(
                    (game.game_id, game.source, game.name),
                    ("desktop_ordinary game", "desktop", "Same title"),
                )
                self.assertEqual(
                    game.executable, f"gio launch {shlex.quote(str(path))}"
                )

    def test_malformed_quoting_does_not_hide_healthy_peer(self):
        """Skip one invalid entry without stopping the reader."""
        self.write_entry("broken.desktop", 'steam "unfinished')
        self.write_entry("healthy.desktop", "ordinary-game")
        self.assertEqual([g.game_id for g in desktop.get_games()], ["desktop_healthy"])

    def test_existing_exclusions_and_filename_precedence_remain(self):
        """Retain filters and the first accepted entry with a given filename."""
        for index, fields in enumerate((
            {"Categories": "Utility;"},
            {"Hidden": "true"},
            {"NoDisplay": "true"},
            {"X-Flatpak": "com.valvesoftware.Steam"},
        )):
            self.write_entry(f"excluded-{index}.desktop", "ordinary-game", **fields)
        for name in ("net.lutris.game.desktop", "page.kramo.Cartridges.Devel.desktop"):
            self.write_entry(name, "ordinary-game")
        self.write_entry("heroic.desktop", "heroic://launch/game")
        self.write_entry("bottles.desktop", "bottles-cli run game")
        first = self.write_entry("healthy.desktop", "ordinary-game")
        second = self.root / "other"
        second.mkdir()
        (second / first.name).write_text(
            first.read_text().replace("Same title", "Later")
        )
        with patch.object(desktop, "_DESKTOP_PATHS", (self.entries, second)):
            loaded = list(desktop.get_games())
        self.assertEqual(
            [(g.game_id, g.name) for g in loaded], [("desktop_healthy", "Same title")]
        )
