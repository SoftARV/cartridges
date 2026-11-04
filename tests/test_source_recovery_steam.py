# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2026 Cartridges contributors

"""Discover Steam games even when optional caches are damaged."""

import io
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cartridges.sources import steam


class SteamRecoveryTests(unittest.TestCase):
    """Use a miniature Steam installation without touching the real one."""

    def setUp(self):
        """Create a valid library and two installed game manifests."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(steam, "_DATA_PATHS", (self.root,)))
        self.apps = self.root / "steamapps"
        self.apps.mkdir()
        (self.apps / "libraryfolders.vdf").write_text(f'"path" "{self.root}"\n')
        for appid in (10, 20):
            (self.apps / f"appmanifest_{appid}.acf").write_text(
                f'"name" "Game {appid}"\n"appid" "{appid}"\n"StateFlags" "4"\n'
            )
        self.cache = self.root / "appcache"
        self.cache.mkdir()
        self.appinfo = self.cache / "appinfo.vdf"

    def test_missing_and_truncated_appinfo_keep_manifest_games(self):
        """Fall back to manifests for missing or unusable optional metadata."""
        for payload in (
            None,
            b"",
            b")DV\x07",
            b"bad format",
            b")DV\x07" + bytes(4) + struct.pack("q", -1),
        ):
            with self.subTest(payload=payload):
                if payload is not None:
                    self.appinfo.write_bytes(payload)
                games = list(steam.get_games())
                self.assertEqual(
                    {game.game_id for game in games}, {"steam_10", "steam_20"}
                )
                self.assertEqual({game.name for game in games}, {"Game 10", "Game 20"})
                if payload is not None:
                    self.assertEqual(self.appinfo.read_bytes(), payload)

    def test_bad_manifest_and_unreadable_cover_keep_valid_games(self):
        """Skip one invalid manifest and retain games without artwork."""
        (self.apps / "appmanifest_bad.acf").write_text('"name" "Incomplete"\n')
        cover = self.cache / "librarycache" / "10" / "library_600x900.jpg"
        cover.parent.mkdir(parents=True)
        cover.write_bytes(b"bad artwork")
        with patch.object(steam.cover, "at_path", side_effect=PermissionError):
            games = list(steam.get_games())
        self.assertEqual({game.game_id for game in games}, {"steam_10", "steam_20"})
        self.assertTrue(all(game.cover is None for game in games))

    def test_manifest_read_failure_does_not_hide_later_games(self):
        """Continue after an unreadable individual manifest."""
        original = Path.read_text

        def read(path: Path, *args: Any, **kwargs: Any) -> str:
            if path.name == "appmanifest_10.acf":
                raise PermissionError
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            games = list(steam.get_games())
        self.assertEqual([game.game_id for game in games], ["steam_20"])

    def test_valid_metadata_keeps_type_filtering(self):
        """Retain existing filtering when metadata is available."""
        self.appinfo.write_bytes(b"fixture")
        entries = [
            ("10", steam._AppInfo(type="tool")),  # noqa: SLF001 - Parser fixture.
            ("20", steam._AppInfo(type="game", developer="Studio")),  # noqa: SLF001
        ]
        with patch.object(steam, "_parse_appinfo_vdf", return_value=iter(entries)):
            games = list(steam.get_games())
        self.assertEqual([game.game_id for game in games], ["steam_20"])
        self.assertEqual(games[0].developer, "Studio")

    def test_wrong_shaped_appinfo_fields_use_empty_metadata(self):
        """Keep malformed optional fields out of game construction."""
        for common in (
            None,
            [],
            {"type": [], "associations": [], "library_assets_full": []},
        ):
            with (
                self.subTest(common=common),
                patch.object(
                    steam,
                    "_load_binary_vdf",
                    return_value=iter([("appinfo", {"common": common})]),
                ),
            ):
                metadata = steam._AppInfo.from_vdf(io.BytesIO(), ())  # noqa: SLF001 - Binary reader boundary.
                self.assertIsNone(metadata.type)
                self.assertIsNone(metadata.developer)
                self.assertIsNone(metadata.capsule)
