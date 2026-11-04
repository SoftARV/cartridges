# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Keep source views in the same order as All Games."""

import locale
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib, Gtk

from cartridges import STATE_SETTINGS, games, sources
from cartridges.games import Game
from cartridges.ui import games as actions
from cartridges.ui.sources import SourceSidebarItem


class SourceSortingTests(unittest.TestCase):
    """Exercise real source, filter, and sorting models with isolated records."""

    def setUp(self):
        """Restore shared state and keep overrides away from the real library."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(games, "GAMES_DIR", root))
        previous_locale = locale.setlocale(locale.LC_COLLATE)
        self.addCleanup(locale.setlocale, locale.LC_COLLATE, previous_locale)
        locale.setlocale(locale.LC_COLLATE, "C")
        previous_mode = STATE_SETTINGS.get_string("sort-mode")
        self.addCleanup(STATE_SETTINGS.set_string, "sort-mode", previous_mode)
        STATE_SETTINGS.set_string("sort-mode", "a-z")
        previous_sources = list(sources.model)
        self.addCleanup(
            lambda: sources.model.splice(
                0, sources.model.get_n_items(), previous_sources
            )
        )
        sources.model.remove_all()
        self.zulu = Game(
            game_id="test_z", source="test", name="Zulu", added=30, last_played=10
        )
        self.alpha = Game(
            game_id="test_a", source="test", name="The alpha", added=10, last_played=50
        )
        self.beta = Game(
            game_id="test_b", source="test", name="beta", added=30, last_played=50
        )
        self.original = [self.zulu, self.alpha, self.beta]
        self.source = self.make_source("test", self.original)
        self.other = self.make_source(
            "other", [Game(game_id="other_1", source="other", name="Aardvark")]
        )
        sources.model.splice(0, 0, [self.source, self.other])
        self.item = SourceSidebarItem(source=self.source)

    @staticmethod
    def make_source(ident: str, entries: list[Game]) -> sources.Source:
        """Load a fixed source through the real override reader."""
        return sources.Source(
            SimpleNamespace(ID=ident, NAME=ident, get_games=lambda: iter(entries)), 1
        )

    def test_modes_update_existing_source_model_without_writes(self):
        """Keep explicit ordering, timestamp ties, and All Games agreement."""
        exposed = self.item.model
        orders = {
            "a-z": [self.alpha, self.beta, self.zulu],
            "z-a": [self.zulu, self.beta, self.alpha],
            "last_played": [self.alpha, self.beta, self.zulu],
            "newest": [self.beta, self.zulu, self.alpha],
            "oldest": [self.alpha, self.beta, self.zulu],
        }
        with patch.object(Game, "save") as save:
            for mode, expected in orders.items():
                with self.subTest(mode=mode):
                    STATE_SETTINGS.set_string("sort-mode", mode)
                    self.assertEqual(list(exposed), expected)
                    self.assertIs(self.item.model, exposed)
                    self.assertEqual(
                        list(exposed), [g for g in actions.model if g.source == "test"]
                    )
                    self.assertEqual(list(self.source), self.original)
            save.assert_not_called()

    def test_source_replacement_and_eligibility_update_visibility(self):
        """Rebinding and watched flags retain sorted membership and counts."""
        exposed = self.item.model
        self.item.source = None
        self.assertEqual(list(exposed), [])
        self.assertFalse(self.item.props.visible)
        empty = self.make_source("empty", [])
        self.item.source = empty
        empty.append(self.zulu)
        empty.append(self.alpha)
        self.assertEqual(list(exposed), [self.alpha, self.zulu])
        self.assertTrue(self.item.props.visible)
        self.alpha.removed = True
        self.zulu.blacklisted = True
        self.assertEqual(list(exposed), [])
        self.assertFalse(self.item.props.visible)
        self.item.source = self.other
        self.assertEqual(list(exposed), list(self.other))
        self.alpha.removed = False
        self.zulu.blacklisted = False
        self.assertEqual(list(exposed), list(self.other))
        self.assertTrue(self.item.props.visible)

    def test_saved_rename_reorders_names_and_timestamp_ties(self):
        """Only a committed Apply invalidates the already-open source model."""
        window = SimpleNamespace(send_toast=Mock())
        self.enterContext(patch.object(actions, "_window", return_value=window))
        for mode in ("a-z", "last_played"):
            with self.subTest(mode=mode):
                STATE_SETTINGS.set_string("sort-mode", mode)
                draft = actions.GameEditable(
                    game=self.beta, name="Aardvark", executable="true"
                )
                with patch.object(Game, "save", side_effect=PermissionError):
                    self.assertFalse(draft.apply())
                self.assertEqual(
                    list(self.item.model), [self.alpha, self.beta, self.zulu]
                )
                self.assertEqual(self.beta.name, "beta")
                self.assertTrue(draft.apply())
                self.assertEqual(
                    list(self.item.model), [self.beta, self.alpha, self.zulu]
                )
                draft.name = "beta"
                self.assertTrue(draft.apply())

    def test_launch_reorders_only_after_timestamp_commit(self):
        """Source models react to accepted timestamps without real commands."""
        STATE_SETTINGS.set_string("sort-mode", "last_played")
        app = SimpleNamespace(
            props=SimpleNamespace(active_window=SimpleNamespace(send_toast=Mock()))
        )
        self.enterContext(
            patch.object(Gio.Application, "get_default", return_value=app)
        )
        self.enterContext(patch("time.time", return_value=100))
        self.enterContext(
            patch.object(GLib, "timeout_add", side_effect=range(100, 110))
        )
        self.enterContext(patch.object(GLib, "source_remove"))
        self.addCleanup(actions.stop_launch_monitors)
        spawn = self.enterContext(patch.object(Game, "play", return_value=Mock()))
        spawn.side_effect = OSError
        play = actions.GameActions(game=self.zulu).lookup_action("play")
        play.activate(None)
        self.assertEqual(list(self.item.model), [self.alpha, self.beta, self.zulu])
        self.assertEqual(self.zulu.last_played, 10)
        spawn.side_effect = None
        with patch.object(Game, "save", side_effect=PermissionError):
            play.activate(None)
        self.assertEqual(list(self.item.model), [self.alpha, self.beta, self.zulu])
        self.assertEqual(self.zulu.last_played, 10)
        play.activate(None)
        self.assertEqual(list(self.item.model), [self.zulu, self.alpha, self.beta])
        self.assertEqual(self.zulu.last_played, 100)

    def test_search_hidden_and_undo_preserve_sorted_subsets(self):
        """Use the same GTK filter composition as the window's grid."""
        window = SimpleNamespace(send_toast=Mock())
        self.enterContext(patch.object(actions, "_window", return_value=window))
        names = Gtk.StringFilter(
            expression=Gtk.PropertyExpression.new(Game, None, "name")
        )
        developers = Gtk.StringFilter(
            expression=Gtk.PropertyExpression.new(Game, None, "developer")
        )
        search = Gtk.AnyFilter()
        search.append(names)
        search.append(developers)
        hidden = Gtk.BoolFilter(
            expression=Gtk.PropertyExpression.new(Game, None, "hidden"), invert=True
        )
        filters = Gtk.EveryFilter()
        filters.append(search)
        filters.append(hidden)
        grid = Gtk.FilterListModel(
            model=self.item.model, filter=filters, watch_items=True
        )
        self.assertEqual(list(grid), [self.alpha, self.beta, self.zulu])
        actions.hide(self.alpha)
        actions.hide(self.zulu)
        self.assertEqual(list(grid), [self.beta])
        hidden.props.invert = False
        self.assertEqual(list(grid), [self.alpha, self.zulu])
        STATE_SETTINGS.set_string("sort-mode", "z-a")
        self.assertEqual(list(grid), [self.zulu, self.alpha])
        self.zulu.developer = "Studio"
        for query, expected in (
            ("alpha", [self.alpha]),
            ("Studio", [self.zulu]),
            ("missing", []),
        ):
            names.props.search = developers.props.search = query
            self.assertEqual(list(grid), expected)
            self.assertTrue(self.item.props.visible)
        names.props.search = developers.props.search = ""
        actions.unhide(self.zulu)
        self.assertEqual(list(grid), [self.alpha])
        hidden.props.invert = True
        self.assertEqual(list(grid), [self.zulu, self.beta])
        actions.remove(self.zulu)
        self.assertEqual(list(grid), [self.beta])
        undo = window.send_toast.call_args.kwargs["undo"]
        self.assertTrue(undo())
        self.assertEqual(list(grid), [self.zulu, self.beta])
        self.source.append(Game(game_id="test_d", source="test", name="Delta"))
        self.assertEqual([g.name for g in grid], ["Zulu", "Delta", "beta"])
