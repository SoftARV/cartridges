# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Verify game records and collection settings across fresh processes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def run_stage(stage: str, target: str):
    """Run only controlled sources in the child process's isolated XDG roots."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from gi.repository import Gio

    from cartridges import collections, sources
    from cartridges.collections import Collection
    from cartridges.games import Game
    from cartridges.sources import imported
    from cartridges.ui import games as actions

    detected = Game(
        game_id="steam_42",
        source="steam",
        name="Launcher refresh",
        executable="launcher changed",
        developer="Launcher studio",
        last_played=50,
    )
    module = SimpleNamespace(
        ID="steam", NAME="Steam", get_games=lambda: iter([detected])
    )
    with patch.object(
        sources,
        "_get_sources",
        lambda: iter([sources.Source(imported, 100), sources.Source(module, 100)]),
    ):
        sources.load()
    collections.load()
    notifications = []
    window = SimpleNamespace(
        send_toast=lambda title, **kwargs: notifications.append((title, kwargs))
    )
    with patch.object(actions, "_window", return_value=window):
        if stage == "setup":
            for name in ("First", "Second"):
                editor = actions.GameEditable(name=name, executable="true")
                if not editor.apply():
                    raise RuntimeError
            first = next(iter(sources.get("imported")))
            actions.GameEditable(
                game=first,
                name="Edited manual",
                executable="true",
                developer="Manual studio",
            ).apply()
            actions.GameEditable(
                game=detected,
                name="Custom detected",
                executable="true",
                developer="Local studio",
            ).apply()
            collections.model.append(
                Collection(
                    name="Saved collection",
                    game_ids=[
                        game.game_id for source in sources.model for game in source
                    ],
                )
            )
            collections.save()
        elif stage != "read":
            game = next(
                game
                for source in sources.model
                for game in source
                if game.game_id == target
            )
            action = stage.removesuffix("-undo")
            game.save_changes(hidden=action == "unhide", removed=False)
            getattr(actions, action)(game)
            if stage.endswith("-undo"):
                notifications[-1][1]["undo"]()
    Gio.Settings.sync()
    sys.stdout.write(
        json.dumps({
            "games": {
                game.game_id: {
                    key: getattr(game, key)
                    for key in (
                        "name",
                        "executable",
                        "developer",
                        "hidden",
                        "removed",
                        "last_played",
                    )
                }
                for source in sources.model
                for game in source
            },
            "collections": {
                collection.name: sorted(collection) for collection in collections.model
            },
        })
    )


class RestartPersistenceTests(unittest.TestCase):
    """Require persistence on disk, not shared objects or a memory backend."""

    def setUp(self):
        """Create independent XDG roots for this test's child processes."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.environment = dict(
            os.environ, GSETTINGS_BACKEND="keyfile", PYTHONDONTWRITEBYTECODE="1"
        )
        for name in ("data", "config", "cache", "state"):
            path = self.root / name
            path.mkdir()
            self.environment[f"XDG_{name.upper()}_HOME"] = str(path)

    def run_child(self, stage: str, target: str = "imported_0") -> dict:
        """Bound process lifetime and report child failures with their logs."""
        result = subprocess.run(  # noqa: S603 - Fixed fixture script and controlled arguments.
            [sys.executable, "-P", str(Path(__file__).resolve()), stage, target],
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_add_edit_and_membership_survive_restart(self):
        """Read records and persistent settings in a separate interpreter."""
        self.run_child("setup")
        records = self.root / "data" / "cartridges" / "games"
        before = {path.name: path.read_bytes() for path in records.iterdir()}
        restored = self.run_child("read")
        self.assertEqual(
            set(restored["games"]), {"imported_0", "imported_1", "steam_42"}
        )
        self.assertEqual(restored["games"]["imported_0"]["name"], "Edited manual")
        self.assertEqual(restored["games"]["steam_42"]["name"], "Custom detected")
        self.assertEqual(restored["games"]["steam_42"]["developer"], "Local studio")
        self.assertEqual(restored["games"]["steam_42"]["last_played"], 50)
        self.assertEqual(
            restored["collections"]["Saved collection"],
            ["imported_0", "imported_1", "steam_42"],
        )
        self.assertTrue(
            any(path.is_file() for path in (self.root / "config").rglob("*"))
        )
        self.assertEqual(
            {path.name: path.read_bytes() for path in records.iterdir()}, before
        )

    def test_visibility_and_undo_results_survive_restart(self):
        """Restart after each manual and detected visibility action or inverse."""
        self.run_child("setup")
        for target in ("imported_0", "steam_42"):
            for action in ("hide", "unhide", "remove"):
                for undo in (False, True):
                    stage = action + ("-undo" if undo else "")
                    with self.subTest(target=target, stage=stage):
                        self.run_child(stage, target)
                        restored = self.run_child("read")
                        prop = "removed" if action == "remove" else "hidden"
                        expected = action == "unhide" if undo else action != "unhide"
                        self.assertEqual(restored["games"][target][prop], expected)
                        self.assertIn(
                            target, restored["collections"]["Saved collection"]
                        )
                        self.assertTrue(
                            (
                                self.root
                                / "data"
                                / "cartridges"
                                / "games"
                                / (target + ".json")
                            ).exists()
                        )


if __name__ == "__main__":
    run_stage(sys.argv[1], sys.argv[2])
