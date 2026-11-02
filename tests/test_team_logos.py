"""Portable logo tests generate tiny plain images, never use private artwork."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ui_components as ui


def make_test_logos(root):
    root = Path(root)
    root.mkdir()
    for filename in ui.APPROVED_TEAM_LOGOS.values():
        # Plain synthetic pixels test decoding/MIME handling, not NBA artwork.
        Image.new("RGB", (8, 6), (120, 80, 160)).save(root / filename)
    return root


class LocalLogoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = make_test_logos(Path(self.temp.name) / "assets")
        self.enterContext(patch("ui_components.ASSETS", self.assets))

    def test_all_30_canonical_teams_render_locally(self):
        expected = set("ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS".split())
        self.assertEqual(len(expected), 30)
        self.assertEqual(set(ui.APPROVED_TEAM_LOGOS), expected)
        self.assertEqual(len(set(ui.APPROVED_TEAM_LOGOS.values())), 30)
        with patch("requests.get") as network:
            for team in expected:
                with self.subTest(team=team):
                    badge = ui.team_badge(team)
                    self.assertIn(f'alt="{team} logo"', badge)
                    self.assertIn("data:image/", badge)
            network.assert_not_called()

    def test_aliases_and_case_normalization(self):
        for alias, canonical in ui.TEAM_ALIASES.items():
            self.assertEqual(ui.team_badge(alias), ui.team_badge(canonical))
        self.assertEqual(ui.team_badge(" chi "), ui.team_badge("CHI"))

    def test_jpeg_and_png_have_correct_mime_types(self):
        self.assertIn("data:image/jpeg;base64,", ui.team_badge("MIL"))
        self.assertIn("data:image/jpeg;base64,", ui.team_badge("NYK"))
        self.assertIn("data:image/jpeg;base64,", ui.team_badge("ORL"))
        self.assertIn("data:image/jpeg;base64,", ui.team_badge("SAS"))
        self.assertIn("data:image/png;base64,", ui.team_badge("CHI"))

    def test_missing_corrupt_or_misidentified_files_fall_back(self):
        (self.assets / "bulls.png").unlink()
        self.assertNotIn("<img", ui.team_badge("CHI"))
        (self.assets / "bulls.png").write_bytes(b"invalid image")
        self.assertNotIn("<img", ui.team_badge("CHI"))
        Image.new("RGB", (8, 6)).save(self.assets / "bulls.png", format="JPEG")
        self.assertNotIn("<img", ui.team_badge("CHI"))
        Image.new("RGBA", (8, 6)).save(self.assets / "bulls.png")
        self.assertIn("<img", ui.team_badge("CHI"))
        self.assertNotIn("<img", ui.team_badge("UNKNOWN"))

    def test_remote_traversal_and_svg_paths_are_rejected(self):
        self.assertIsNone(ui.image_uri("https://example.invalid/logo.png"))
        outside = self.assets.parent / "outside.png"
        Image.new("RGB", (8, 6)).save(outside)
        self.assertIsNone(ui.image_uri(str(outside)))
        self.assertIsNone(ui.image_uri(str(self.assets / "logo.svg")))

    def test_valid_image_bytes_are_read_once_and_reused(self):
        original = Path.read_bytes
        with patch.object(Path, "read_bytes", autospec=True, side_effect=original) as reader:
            first = ui.team_badge("GSW")
            self.assertEqual(ui.team_badge("GS"), first)
            self.assertEqual(ui.team_badge("GSW"), first)
            self.assertEqual(reader.call_count, 1)

    def test_pick_and_matchup_cards_include_teams_and_safe_fallbacks(self):
        game = {"home": "CHI", "away": "MIA", "date": "2026-11-02"}
        self.assertEqual(ui.matchup_card(game).count("<img"), 2)
        for status in ("open", "won", "lost", "void"):
            card = ui.pending_pick_card({"pick": "CHI", "matchup": "MIA @ CHI",
                                         "stake": "25", "status": status})
            self.assertEqual(card.count("<img"), 3)
        unsafe = ui.pending_pick_card({"pick": "<script>", "matchup": "<script>"})
        self.assertNotIn("<script>", unsafe)
        self.assertIn("&lt;script&gt;", unsafe)
