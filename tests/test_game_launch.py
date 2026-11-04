# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Observe launch requests without executing game commands."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import GLib

from cartridges import games
from cartridges.games import Game
from cartridges.ui import games as actions


class GameLaunchTests(unittest.TestCase):
    """Control process completion and isolate launch notifications."""

    def setUp(self):
        """Replace process creation and timer scheduling before using Play."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(games, "GAMES_DIR", self.root))
        self.game = Game(
            game_id="imported_0",
            source="imported",
            name="Game",
            executable="private-command --private-argument",
        )
        self.process = Mock()
        self.process.poll.return_value = None
        self.spawn = self.enterContext(
            patch.object(games.subprocess, "Popen", return_value=self.process)
        )
        self.window = SimpleNamespace(send_toast=Mock())
        self.app = SimpleNamespace(props=SimpleNamespace(active_window=self.window))
        self.enterContext(
            patch.object(actions.Gio.Application, "get_default", return_value=self.app)
        )
        self.timer = self.enterContext(
            patch.object(GLib, "timeout_add", return_value=123)
        )
        self.remove = self.enterContext(patch.object(GLib, "source_remove"))
        self.addCleanup(actions.stop_launch_monitors)

    def start(self):
        """Use the same Play action as cards and details."""
        actions.GameActions(game=self.game).lookup_action("play").activate(None)

    def test_launch_returns_handle_and_preserves_process_options(self):
        """The model keeps shell and detachment compatibility."""
        with patch.object(
            games, "format_executable", return_value="formatted"
        ) as format_:
            self.assertIs(self.game.play(), self.process)
        format_.assert_called_once_with(self.game.executable)
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.spawn.call_args.args, ("formatted",))
        self.assertEqual(
            self.spawn.call_args.kwargs,
            {
                "cwd": Path.home(),
                "shell": True,
                "start_new_session": True,
                "creationflags": games.subprocess.CREATE_NEW_PROCESS_GROUP
                if os.name == "nt"
                else 0,
            },
        )

    def test_pending_then_nonzero_exit_notifies_once(self):
        """Schedule polling instead of waiting and report shell command failure."""
        self.start()
        self.spawn.assert_called_once()
        self.timer.assert_called_once()
        callback = self.timer.call_args.args[1]
        self.assertTrue(callback())
        self.window.send_toast.assert_not_called()
        self.process.poll.return_value = 127
        self.assertFalse(callback())
        self.window.send_toast.assert_called_once()
        self.assertIn("command", self.window.send_toast.call_args.args[0].lower())
        self.process.wait.assert_not_called()
        self.process.communicate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_zero_exit_and_closed_window_are_safe(self):
        """Successful handoff is silent; absent windows cannot receive toasts."""
        for status in (0, 1):
            with self.subTest(status=status):
                self.start()
                callback = self.timer.call_args.args[1]
                self.app.props.active_window = None
                self.process.poll.return_value = status
                self.assertFalse(callback())
        self.window.send_toast.assert_not_called()

    def test_spawn_errors_do_not_start_monitoring(self):
        """Expected errors stay out of GTK callbacks and do not expose commands."""
        for error in (OSError("private path"), ValueError("embedded null byte")):
            with self.subTest(error=type(error).__name__):
                self.spawn.side_effect = error
                self.window.send_toast.reset_mock()
                with self.assertLogs(actions.__name__, level="WARNING") as logs:
                    self.start()
                self.timer.assert_not_called()
                self.window.send_toast.assert_called_once()
                self.assertNotIn("private", str(logs.output))
                self.assertNotIn("private", str(self.window.send_toast.call_args))
                self.assertEqual(self.game.last_played, 0)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_shutdown_removes_pending_monitor_without_stopping_process(self):
        """Stopping observation must not stop or wait for a launched game."""
        self.start()
        actions.stop_launch_monitors()
        self.remove.assert_called_once_with(123)
        self.process.wait.assert_not_called()
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()
