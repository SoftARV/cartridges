# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Regression tests for list-format game commands."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cartridges.games import Game


class GameCommandTests(unittest.TestCase):
    """Preserve command arguments through the model and POSIX shell."""

    def setUp(self):
        """Create a controlled argument-reporting executable."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.python = self.root / "Python with spaces"
        self.python.symlink_to(sys.executable)
        self.arguments = [
            "two words",
            "",
            "juego_ñ",
            "single'quote",
            'double"quote',
            "back\\slash",
            "tab\there",
            "line\nbreak",
            "$HOME",
            "~",
            "*",
            "$(touch substitution)",
            "`touch backtick`",
            "; touch semicolon",
            "| touch pipe",
            "> redirect",
        ]
        self.command = [
            str(self.python),
            "-c",
            "import json, sys; print(json.dumps(sys.argv[1:]))",
            *self.arguments,
        ]
        self.data: dict[str, Any] = {
            "game_id": "imported_0",
            "source": "imported",
            "name": "Fixture",
            "executable": self.command,
        }

    def check_shell_arguments(self, command: str):
        """Run only the test fixture and inspect its actual arguments."""
        result = subprocess.run(  # noqa: S603 - Test-authored command in a temporary directory.
            ["/bin/sh", "-c", command],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(json.loads(result.stdout), self.arguments)
        self.assertEqual(set(self.root.iterdir()), {self.python})

    def test_list_preserves_arguments_and_input(self):
        """Load a list without mutating it or letting the shell expand arguments."""
        original = copy.deepcopy(self.data)
        game = Game.from_data(self.data)
        self.check_shell_arguments(game.executable)
        self.assertEqual(self.data, original)

    def test_single_executable_loads_without_existence_check(self):
        """Accept a command that may only become available at launch time."""
        self.data["executable"] = ["unavailable-game-command"]
        game = Game.from_data(self.data)
        self.assertEqual(game.executable, "unavailable-game-command")

    def test_strings_remain_unchanged(self):
        """Keep whitespace and existing shell syntax in string commands."""
        for command in (
            "",
            "  game --name 'two words'  ",
            "VAR=value game | cat",
            "$HOME/game && echo done",
        ):
            with self.subTest(command=command):
                self.data["executable"] = command
                self.assertEqual(Game.from_data(self.data).executable, command)

    def test_invalid_executables_raise_type_error(self):
        """Reject invalid shapes and list elements before GObject assignment."""
        for value in (
            None,
            42,
            True,
            {},
            ("game",),
            [],
            [""],
            ["", "arg"],
            ["game", 1],
            ["game", None],
            ["game", []],
            ["game\0suffix"],
            ["game", "arg\0suffix"],
        ):
            with self.subTest(value=value):
                self.data["executable"] = value
                with self.assertRaises(TypeError):
                    Game.from_data(self.data)

    def test_required_fields_and_version_checks_remain(self):
        """Keep missing required fields and future versions invalid."""
        for field in ("game_id", "source", "name", "executable"):
            with self.subTest(field=field):
                data = self.data.copy()
                del data[field]
                with self.assertRaises(TypeError):
                    Game.from_data(data)
        self.data["version"] = 999.0
        with self.assertRaises(TypeError):
            Game.from_data(self.data)
