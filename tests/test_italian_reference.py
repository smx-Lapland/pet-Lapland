import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from pet.italian_reference import shape_syllable_stress, write_dialogue
from pet.neural_worker import trim_phrase_join


@unittest.skipUnless(importlib.util.find_spec("scipy") and importlib.util.find_spec("soundfile"),
                     "requires the optional Italian reference voice environment")
class ItalianDeliveryTest(unittest.TestCase):
    def test_preserves_timing_and_accents_only_flat_phrases(self):
        import soundfile as sf

        rate = 24000
        t = np.arange(rate // 5) / rate
        vowel = (.3 * np.sin(2 * np.pi * 250 * t) * np.sin(np.pi * t / .2) ** 2).astype(np.float32)
        flat = np.concatenate([vowel * v for v in (.7, .79, .7, .79, .7)])
        clear = np.concatenate([vowel * v for v in (.35, .95, .35, .95, .35)])
        pause = np.zeros(rate // 2, dtype=np.float32)
        sample = np.concatenate([flat, pause, clear])
        shaped = shape_syllable_stress(sample, rate)
        def contrast(x):
            peaks = np.max(np.abs(x.reshape(5, -1)), axis=1)
            return 20 * np.log10(peaks[1] / peaks[2])
        self.assertGreater(contrast(shaped[:len(flat)]), contrast(flat) + .4)
        self.assertAlmostEqual(contrast(shaped[-len(clear):]), contrast(clear), places=4)
        self.assertFalse(shaped[len(flat):len(flat)+len(pause)].any())
        np.testing.assert_array_equal(np.sign(shaped), np.sign(sample))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav.pending"
            with patch("audiotsm.wsola", side_effect=AssertionError("default rate must use native audio")):
                write_dialogue(path, clear, rate, 0)
            data, sr = sf.read(str(path), dtype="float32")
            self.assertEqual(sr, rate)
            np.testing.assert_allclose(data, trim_phrase_join(clear, rate, True, True), atol=1 / 32768)
            write_dialogue(path, clear, rate, 2)
            self.assertAlmostEqual(sf.info(str(path)).frames / len(data), 2 ** (-.2), places=3)
