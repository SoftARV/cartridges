# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Persist accepted launch requests without confusing them with game startup."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib, Gtk

from cartridges import STATE_SETTINGS, games
from cartridges.games import Game
from cartridges.ui import games as actions


class LaunchPersistenceTests(unittest.TestCase):
    """Use controlled clocks and processes with the real atomic writer."""

    def setUp(self):
        """Redirect writes and timer callbacks without launching commands."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(games, "GAMES_DIR", self.root))
        self.game = Game(
            game_id="imported_0",
            source="imported",
            name="Zulu",
            executable="true",
            last_played=10,
        )
        self.game.save()
        self.window = SimpleNamespace(send_toast=Mock())
        app = SimpleNamespace(props=SimpleNamespace(active_window=self.window))
        self.enterContext(
            patch.object(Gio.Application, "get_default", return_value=app)
        )
        self.clock = self.enterContext(patch("time.time", return_value=100))
        self.process = Mock()
        self.process.poll.return_value = None
        self.spawn = self.enterContext(
            patch.object(Game, "play", return_value=self.process)
        )
        self.timer = self.enterContext(
            patch.object(GLib, "timeout_add", side_effect=range(100, 120))
        )
        self.enterContext(patch.object(GLib, "source_remove"))
        self.addCleanup(actions.stop_launch_monitors)
        previous = STATE_SETTINGS.get_string("sort-mode")
        self.addCleanup(STATE_SETTINGS.set_string, "sort-mode", previous)

    def test_success_publishes_saved_timestamp_and_updates_sorting(self):
        """All Games' shared sorter reacts only to committed timestamp changes."""
        other = Game(name="Alpha", last_played=50)
        store = Gio.ListStore(item_type=Game)
        store.splice(0, 0, [self.game, other])
        model = Gtk.SortListModel(model=store, sorter=actions.sorter)
        STATE_SETTINGS.set_string("sort-mode", "last_played")
        self.assertEqual(list(model), [other, self.game])
        observed = []
        path = self.root / "imported_0.json"
        self.game.connect(
            "notify::last-played",
            lambda *_: observed.append(json.loads(path.read_text())["last_played"]),
        )
        actions.play(self.game)
        self.assertEqual(observed, [100])
        self.assertEqual(list(model), [self.game, other])
        STATE_SETTINGS.set_string("sort-mode", "a-z")
        self.clock.return_value = 200
        actions.play(self.game)
        self.assertEqual(list(model), [other, self.game])
        self.assertEqual(self.game.last_played, 200)
        self.window.send_toast.assert_not_called()

    def test_save_failure_does_not_undo_or_repeat_launch(self):
        """Keep old data while monitoring the already-started command."""
        path = self.root / "imported_0.json"
        before = path.read_bytes()
        with patch.object(Game, "save", side_effect=PermissionError):
            actions.play(self.game)
        self.assertEqual(self.game.last_played, 10)
        self.assertEqual(path.read_bytes(), before)
        self.spawn.assert_called_once()
        self.window.send_toast.assert_called_once()
        self.assertIn("Last Played", self.window.send_toast.call_args.args[0])
        callback = self.timer.call_args.args[1]
        self.process.poll.return_value = 127
        self.assertFalse(callback())
        self.assertEqual(self.window.send_toast.call_count, 2)
        self.process.kill.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_repeated_requests_and_late_failures_preserve_current_state(self):
        """Completion never restores stale snapshots or rolls back timestamps."""
        actions.play(self.game)
        first = self.timer.call_args.args[1]
        self.game.save_changes(name="Edited", hidden=True)
        self.clock.return_value = 200
        actions.play(self.game)
        second = self.timer.call_args.args[1]
        self.process.poll.return_value = 1
        self.assertFalse(second())
        self.assertFalse(first())
        self.clock.return_value = 50
        actions.play(self.game)
        self.assertEqual(
            (self.game.last_played, self.game.name, self.game.hidden),
            (200, "Edited", True),
        )
        self.assertEqual(self.spawn.call_count, 3)
        self.assertEqual(
            json.loads((self.root / "imported_0.json").read_text())["last_played"], 200
        )

    def test_timestamp_survives_fresh_process_for_manual_and_detected_games(self):
        """Restore records through the existing readers with controlled discovery."""
        actions.play(self.game)
        detected = Game(
            game_id="steam_42", source="steam", name="Detected", executable="true"
        )
        actions.play(detected)
        before = {p.name: p.read_bytes() for p in self.root.glob("*.json")}
        script = """
import json, os
from pathlib import Path
from types import SimpleNamespace
from cartridges import games, sources
from cartridges.sources import imported
games.GAMES_DIR = imported.GAMES_DIR = Path(os.environ['LAUNCH_TEST_RECORDS'])
imported.COVERS_DIR = games.GAMES_DIR / 'covers'
manual = list(imported.get_games())
module = SimpleNamespace(ID='steam', NAME='Steam', get_games=lambda: iter([
    games.Game(game_id='steam_42', source='steam', name='Fresh', executable='true',
               last_played=80)]))
restored = list(sources.Source(module, 1))
print(json.dumps({g.game_id:g.last_played for g in manual + restored}))
"""
        env = dict(
            os.environ, LAUNCH_TEST_RECORDS=str(self.root), GSETTINGS_BACKEND="memory"
        )
        for name in ("DATA", "CONFIG", "CACHE", "STATE"):
            env[f"XDG_{name}_HOME"] = str(self.root / name.lower())
        result = subprocess.run(  # noqa: S603 - Fixed interpreter and isolated reader script.
            [sys.executable, "-P", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"imported_0": 100, "steam_42": 100}
        )
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.root.glob("*.json")}, before
        )

    def test_equal_or_older_request_time_saves_without_false_notifications(self):
        """Retain discovered future timestamps and existing tie ordering."""
        self.game.last_played = 200
        notifications = []
        self.game.connect("notify::last-played", lambda *_: notifications.append(True))
        for now in (200, 100):
            with self.subTest(now=now):
                self.clock.return_value = now
                actions.play(self.game)
                self.assertEqual(self.game.last_played, 200)
                self.assertEqual(
                    json.loads((self.root / "imported_0.json").read_text())[
                        "last_played"
                    ],
                    200,
                )
        self.assertEqual(notifications, [])
