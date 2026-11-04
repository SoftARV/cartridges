# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Keep source views in the same order as All Games."""

import locale
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
