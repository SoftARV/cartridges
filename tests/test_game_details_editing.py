# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Discard ended editing sessions without losing retryable drafts."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import Adw, GLib, GObject, Gtk

from cartridges import games, sources
from cartridges.games import Game
from cartridges.sources import imported
from cartridges.ui import games as actions
from cartridges.ui.game_details import GameDetails


def entries(widget: Gtk.Widget) -> list[Adw.EntryRow]:
    """Find the real entry rows without depending on exported template children."""
    result = [widget] if isinstance(widget, Adw.EntryRow) else []
    child = widget.get_first_child()
    while child:
        result.extend(entries(child))
        child = child.get_next_sibling()
    return result


class GameDetailsEditingTests(unittest.TestCase):
    """Host compiled GameDetails in a navigation view with isolated records."""

    def setUp(self):
        """Initialize widgets without application startup or launcher discovery."""
        Adw.init()
        GObject.type_ensure(GObject.SignalGroup)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for module in (games, imported):
            self.enterContext(patch.object(module, "GAMES_DIR", self.root))
        self.enterContext(patch.object(imported, "COVERS_DIR", self.root / "covers"))
        self.source = sources.Source(imported, 1)
        self.enterContext(patch.object(sources, "get", return_value=self.source))
        self.details = GameDetails()
        self.navigation = Adw.NavigationView(animate_transitions=False)
        self.home = Adw.NavigationPage.new(Gtk.Box(), "Library")
        self.navigation.add(self.home)
        self.navigation.add(self.details)
        self.host = Gtk.Window(child=self.navigation)
        self.addCleanup(self.host.destroy)
        self.window = SimpleNamespace(
            details=self.details, navigation_view=self.navigation, send_toast=Mock()
        )
        self.enterContext(patch.object(actions, "_window", return_value=self.window))
        self.rows = entries(self.details)
        self.assertEqual(len(self.rows), 3)
        self.game = Game(
            game_id="imported_0",
            source="imported",
            name="Saved",
            developer="",
            executable="true",
        )
        self.game.save()
        self.source.append(self.game)
        self.host.present()
        self.drain()

    @staticmethod
    def drain():
        """Process pending widget events with a bounded loop."""
        context = GLib.MainContext.default()
        for _ in range(100):
            if not context.pending():
                break
            context.iteration(False)

    def draft(self):
        """Edit each actual entry, allowing bindings to update GameEditable."""
        for row, value in zip(
            self.rows, ("Draft", "Draft studio", "false"), strict=True
        ):
            row.props.text = value

    def values(self) -> tuple[str, ...]:
        """Return visible field values in template order."""
        return tuple(row.props.text for row in self.rows)

    def test_cancel_reopen_restores_all_fields_without_saving(self):
        """Reopening the same object must discard the cancelled draft."""
        actions.edit(self.game)
        before = (self.root / "imported_0.json").read_bytes()
        notifications = []
        self.game.connect("notify", lambda _game, prop: notifications.append(prop.name))
        self.draft()
        self.details.activate_action("details.cancel")
        self.assertFalse(self.details.editing)
        self.assertIs(self.navigation.get_visible_page(), self.details)
        self.details.activate_action("details.edit")
        self.assertEqual(self.values(), ("Saved", "", "true"))
        self.assertIs(self.details.game, self.game)
        self.assertEqual(notifications, [])
        self.assertEqual((self.root / "imported_0.json").read_bytes(), before)

    def test_cancelled_add_restarts_blank_without_allocating(self):
        """Consecutive Add sessions reset even when game remains None."""
        with patch.object(imported, "new", side_effect=AssertionError):
            actions.add()
            self.draft()
            self.details.activate_action("details.cancel")
            self.drain()
            self.assertIs(self.navigation.get_visible_page(), self.home)
            actions.add()
            self.assertEqual(self.values(), ("", "", ""))
            self.assertFalse(self.details.game_editable.valid)
            self.details.activate_action("details.apply")
        self.assertEqual(list(self.source), [self.game])
        self.assertEqual([p.name for p in self.root.iterdir()], ["imported_0.json"])

    def test_repeated_edit_preserves_draft_and_successful_apply(self):
        """Repeated Edit is not a new session and Apply persists those fields."""
        actions.edit(self.game)
        self.draft()
        self.details.edit()
        self.assertEqual(self.values(), ("Draft", "Draft studio", "false"))
        self.details.activate_action("details.apply")
        self.assertFalse(self.details.editing)
        self.details.edit()
        self.assertEqual(self.values(), ("Draft", "Draft studio", "false"))
        self.assertEqual(
            (self.game.name, self.game.developer, self.game.executable), self.values()
        )

    def test_navigation_and_game_switching_discard_old_drafts(self):
        """The hidden-page callback ends editing; other games retain their values."""
        other = Game(
            game_id="imported_1",
            source="imported",
            name="Other",
            developer="Other studio",
            executable="other-command",
        )
        actions.edit(self.game)
        self.draft()
        self.navigation.pop()
        self.drain()
        self.assertFalse(self.details.editing)
        actions.edit(self.game)
        self.assertEqual(self.values(), ("Saved", "", "true"))
        self.draft()
        self.details.game = other
        self.details.edit()
        self.assertEqual(self.values(), ("Other", "Other studio", "other-command"))
        self.draft()
        self.details.game = self.game
        self.details.edit()
        self.assertEqual(self.values(), ("Saved", "", "true"))
        self.details.activate_action("details.cancel")
        self.details.activate_action("details.cancel")
        self.drain()
        self.assertIs(self.navigation.get_visible_page(), self.home)

    def test_failed_apply_retains_draft_for_retry_or_cancel(self):
        """Exercise actual entries and Apply actions for existing and new games."""
        for adding in (False, True):
            for retry in (False, True):
                with self.subTest(adding=adding, retry=retry):
                    if adding:
                        actions.add()
                    else:
                        actions.edit(self.game)
                    committed = (
                        self.game.name,
                        self.game.developer,
                        self.game.executable,
                    )
                    before = {p.name: p.read_bytes() for p in self.root.glob("*.json")}
                    count = len(list(self.source))
                    self.window.send_toast.reset_mock()
                    self.draft()
                    with patch.object(Game, "save", side_effect=PermissionError):
                        self.details.activate_action("details.apply")
                    self.assertTrue(self.details.editing)
                    self.details.edit()
                    self.assertEqual(self.values(), ("Draft", "Draft studio", "false"))
                    self.assertEqual(
                        (self.game.name, self.game.developer, self.game.executable),
                        committed,
                    )
                    self.assertEqual(
                        {p.name: p.read_bytes() for p in self.root.glob("*.json")},
                        before,
                    )
                    self.assertEqual(len(list(self.source)), count)
                    self.window.send_toast.assert_called_once()
                    if retry:
                        self.details.activate_action("details.apply")
                        self.assertFalse(self.details.editing)
                        self.assertEqual(len(list(self.source)), count + int(adding))
                        saved = self.details.game
                        self.assertTrue((self.root / f"{saved.game_id}.json").exists())
                        self.details.edit()
                        self.assertEqual(
                            self.values(), ("Draft", "Draft studio", "false")
                        )
                        self.details.activate_action("details.cancel")
                    else:
                        self.details.activate_action("details.cancel")
                        if adding:
                            actions.add()
                            self.assertEqual(self.values(), ("", "", ""))
                        else:
                            self.details.edit()
                            self.assertEqual(self.values(), committed)
                        self.details.activate_action("details.cancel")
