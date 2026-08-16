import unittest
from renderer import _build_karaoke_captions, WordTiming

class TestRenderer(unittest.TestCase):
    def test_build_karaoke_captions_cache(self):
        # Create a list of words with duplicates
        words = [
            WordTiming(word="hello", start=0.0, end=1.0),
            WordTiming(word="world", start=1.0, end=2.0),
            WordTiming(word="hello", start=2.0, end=3.0),
            WordTiming(word="again", start=3.0, end=4.0),
        ]

        # Build captions
        clips = _build_karaoke_captions(words, 1080, 1920)

        # Verify the correct number of clips were generated
        self.assertEqual(len(clips), 4)

        # Verify properties were set correctly for each clip instance
        self.assertEqual(clips[0].start, 0.0)
        self.assertEqual(clips[0].duration, 1.0)

        self.assertEqual(clips[1].start, 1.0)
        self.assertEqual(clips[1].duration, 1.0)

        self.assertEqual(clips[2].start, 2.0)
        self.assertEqual(clips[2].duration, 1.0)

        self.assertEqual(clips[3].start, 3.0)
        self.assertEqual(clips[3].duration, 1.0)

if __name__ == '__main__':
    unittest.main()
