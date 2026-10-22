"""Fuzzy/normalized keyword matching for voice transcripts."""
import unittest
from matching import fuzzy_match, normalize


class MatchingTests(unittest.TestCase):
    def test_case_accent_punctuation(self):
        self.assertTrue(fuzzy_match("cafe", "Meet at the CAFÉ!!"))

    def test_small_misspelling_matches(self):
        self.assertTrue(fuzzy_match("emergency", "this is an emergancy bhai"))

    def test_bangla_variation_matches(self):
        self.assertTrue(fuzzy_match("জরুরী", "ভাই এটা খুব জরুরি কথা"))

    def test_merged_phrase_matches(self):
        self.assertTrue(fuzzy_match("call koro", "ekhuni callkoro please"))

    def test_unrelated_does_not_match(self):
        self.assertFalse(fuzzy_match("emergency", "amra kal bazar e jabo"))

    def test_short_keyword_needs_exact_word(self):
        self.assertFalse(fuzzy_match("gun", "fun time"))
        self.assertTrue(fuzzy_match("gun", "he has a GUN."))

    def test_normalize_keeps_bangla_signs(self):
        self.assertEqual(normalize("খবরে!"), "খবরে")


if __name__ == "__main__":
    unittest.main()
