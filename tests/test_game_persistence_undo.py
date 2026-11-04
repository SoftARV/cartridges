# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Persist visibility actions through real toast and keyboard Undo dispatch."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cartridges import games
from cartridges.collections import Collection
from cartridges.games import Game
from cartridges.ui import games as actions
from cartridges.ui.window import Window


class VisibilityPersistenceTests(unittest.TestCase):
    """Exercise existing toast history without creating a desktop window."""

    def setUp(self):
        """Isolate record writes and capture real Adwaita toasts."""
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(games, "GAMES_DIR", self.directory))
        self.toasts = []
        self.window = SimpleNamespace(
            _history={}, toast_overlay=SimpleNamespace(add_toast=self.toasts.append)
        )
        self.window._undo = lambda toast=None: Window._undo(self.window, toast)  # noqa: SLF001 - Actual dispatch on an isolated window fixture.
        self.window.send_toast = lambda *args, **kwargs: Window.send_toast(
            self.window, *args, **kwargs
        )
        self.enterContext(patch.object(actions, "_window", return_value=self.window))
        self.game = Game(
            game_id="imported_0", source="imported", name="Before", executable="true"
        )
        self.game.save()
        self.path = self.directory / "imported_0.json"
        self.collection = Collection(game_ids=[self.game.game_id])

    def test_visibility_actions_and_undo_save_only_affected_property(self):
        """Keep membership and intervening edits through each inverse action."""
        for source in ("imported", "steam"):
            for action, prop, initial, result in (
                (actions.hide, "hidden", False, True),
                (actions.unhide, "hidden", True, False),
                (actions.remove, "removed", False, True),
            ):
                with self.subTest(source=source, action=action.__name__):
                    self.game.source = source
                    setattr(self.game, prop, initial)
                    self.game.save()
                    action(self.game)
                    self.assertEqual(json.loads(self.path.read_text())[prop], result)
                    self.game.save_changes(name="Intervening edit")
                    self.toasts[-1].emit("button-clicked")
                    saved = json.loads(self.path.read_text())
                    self.assertEqual(saved[prop], initial)
                    self.assertEqual(saved["name"], "Intervening edit")
                    self.assertIn(self.game.game_id, self.collection)
                    self.assertTrue(self.path.exists())

    def test_failed_visibility_change_keeps_state_without_success_undo(self):
        """Do not filter a game or offer a successful Undo before saving."""
        before = self.path.read_bytes()
        notifications = []
        self.game.connect("notify", lambda _game, prop: notifications.append(prop.name))
        for action in (actions.hide, actions.unhide, actions.remove):
            with (
                self.subTest(action=action.__name__),
                patch.object(Game, "save", side_effect=PermissionError),
            ):
                action(self.game)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(notifications, [])
                self.assertEqual(self.window._history, {})  # noqa: SLF001
                self.assertIsNone(self.toasts[-1].props.button_label)

    def test_failed_undo_offers_retry_for_toast_and_keyboard_paths(self):
        """Replace a consumed Undo with a retry when its save fails."""
        for keyboard in (False, True):
            with self.subTest(keyboard=keyboard):
                actions.hide(self.game)
                before = self.path.read_bytes()
                success_toast = self.toasts[-1]
                count = len(self.toasts)
                with patch.object(Game, "save", side_effect=PermissionError):
                    if keyboard:
                        self.window._undo()  # noqa: SLF001
                    else:
                        success_toast.emit("button-clicked")
                self.assertTrue(self.game.hidden)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(len(self.toasts), count + 1)
                retry = self.toasts[-1]
                self.assertEqual(retry.props.button_label, "Undo")
                self.assertIn(retry, self.window._history)  # noqa: SLF001
                self.window._undo()  # noqa: SLF001
                self.assertFalse(self.game.hidden)
                self.assertFalse(json.loads(self.path.read_text())["hidden"])
                self.assertEqual(self.window._history, {})  # noqa: SLF001

    def test_collection_style_none_returning_undo_still_works(self):
        """Leave existing callbacks that return None compatible."""
        called = []
        self.window.send_toast("Collection removed", undo=lambda: called.append(True))
        self.toasts[-1].emit("button-clicked")
        self.assertEqual(called, [True])
        self.assertEqual(self.window._history, {})  # noqa: SLF001
