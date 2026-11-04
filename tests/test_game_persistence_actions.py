# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Save confirmed edits before publishing changes to the UI."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cartridges import games, sources
from cartridges.games import Game
from cartridges.sources import imported
from cartridges.ui import games as actions
from cartridges.ui.game_details import GameDetails


class PersistenceActionTests(unittest.TestCase):
    """Keep record writes and user notifications inside fixtures."""

    def setUp(self):
        """Prepare an empty Added source and a notification recorder."""
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(games, "GAMES_DIR", self.directory))
        self.enterContext(patch.object(imported, "GAMES_DIR", self.directory))
        self.enterContext(
            patch.object(imported, "COVERS_DIR", self.directory / "covers")
        )
        self.source = sources.Source(imported, 1)
        self.enterContext(patch.object(sources, "get", return_value=self.source))
        self.window = SimpleNamespace(send_toast=Mock())
        self.enterContext(patch.object(actions, "_window", return_value=self.window))

    def test_apply_saves_before_inserting_new_game(self):
        """Publish two distinct records only after they exist on disk."""
        observed = []

        def changed(*_args):
            observed.append(
                all(
                    (self.directory / (game.game_id + ".json")).exists()
                    for game in self.source
                )
            )

        self.source.connect("items-changed", changed)
        for name in ("First", "Second"):
            editor = actions.GameEditable(name=name, executable="true")
            self.assertTrue(editor.apply())
        self.assertEqual(observed, [True, True])
        self.assertEqual(
            {p.name for p in self.directory.iterdir()},
            {"imported_0.json", "imported_1.json"},
        )
        self.assertEqual(
            sorted(game.name for game in imported.get_games()), ["First", "Second"]
        )

    def test_failed_add_keeps_draft_and_retries_without_duplicates(self):
        """Do not publish an unsaved game or close its editor."""
        editor = actions.GameEditable(
            name="Draft", executable="true", developer="Studio"
        )
        details = SimpleNamespace(game_editable=editor, game=None, editing=True)
        with patch.object(Game, "save", side_effect=PermissionError):
            GameDetails._apply(details)  # noqa: SLF001 - Exercise the real Apply callback.
        self.assertTrue(details.editing)
        self.assertIsNone(editor.game)
        self.assertEqual(editor.name, "Draft")
        self.assertEqual(list(self.source), [])
        self.assertEqual(list(self.directory.iterdir()), [])
        self.window.send_toast.assert_called_once()
        GameDetails._apply(details)  # noqa: SLF001
        self.assertFalse(details.editing)
        self.assertEqual(len(list(self.source)), 1)
        self.assertIs(details.game, editor.game)

    def test_failed_edit_has_no_live_notifications(self):
        """Keep saved bytes, object identity, and live fields intact on failure."""
        editor = actions.GameEditable(name="Before", executable="true")
        editor.apply()
        game = editor.game
        path = self.directory / (game.game_id + ".json")
        before = path.read_bytes()
        notifications = []
        game.connect("notify", lambda _game, prop: notifications.append(prop.name))
        editor.name = "After"
        with patch.object(Game, "save", side_effect=PermissionError):
            self.assertFalse(editor.apply())
        self.assertEqual(notifications, [])
        self.assertEqual(game.name, "Before")
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(editor.apply())
        self.assertIs(editor.game, game)
        self.assertEqual(game.name, "After")
        self.assertEqual(json.loads(path.read_text())["name"], "After")

    def test_invalid_draft_creates_nothing(self):
        """Leave an incomplete editor draft unsaved."""
        editor = actions.GameEditable(name="No command")
        self.assertFalse(editor.apply())
        self.assertEqual(list(self.source), [])
        self.assertEqual(list(self.directory.iterdir()), [])
