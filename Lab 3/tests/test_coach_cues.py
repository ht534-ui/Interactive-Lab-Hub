import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'speech-scripts'))
from roast_button import StatusTiming
from coach_cues import ProcessingCue, processing_tone
from coach_display import STATE_COLORS


class CueTests(unittest.TestCase):
    def test_short_save_stays_silent(self):
        cue = ProcessingCue()
        self.assertFalse(cue.update({'phase': 'listening', 'backend_busy': True}, 0))
        self.assertFalse(cue.update({'phase': 'listening'}, 1))

    def test_sustained_save_once_per_episode(self):
        cue = ProcessingCue()
        state = {'phase': 'listening', 'backend_busy': True}
        self.assertFalse(cue.update(state, 0))
        self.assertTrue(cue.update(state, 1.2))
        self.assertFalse(cue.update(state, 20))
        cue.update({'phase': 'listening'}, 21)
        self.assertFalse(cue.update(state, 22))
        self.assertTrue(cue.update(state, 23.21))

    def test_speech_guard_and_cooldown(self):
        cue = ProcessingCue()
        speaking = {'phase': 'speaking', 'backend_busy': True}
        cue.update(speaking, 0)
        self.assertFalse(cue.update(speaking, 2))
        self.assertTrue(cue.update({'phase': 'listening', 'backend_busy': True}, 3))
        cue.update({'phase': 'listening'}, 3.1)
        cue.update({'phase': 'thinking'}, 4)
        self.assertFalse(cue.update({'phase': 'thinking'}, 5.3))
        self.assertTrue(cue.update({'phase': 'thinking'}, 8))

    def test_pcm_audio_guard(self):
        cue = ProcessingCue()
        state = {'phase': 'listening', 'backend_busy': True, 'audio_playing': True}
        cue.update(state, 0)
        self.assertFalse(cue.update(state, 3))

    def test_foreground_and_background_independent(self):
        clock = StatusTiming()
        state = clock.update({'phase': 'listening', 'backend_busy': True}, 10)
        state = clock.update({'phase': 'speaking', 'backend_busy': True}, 12)
        self.assertEqual(state['phase'], 'speaking')
        self.assertEqual(state['phase_elapsed'], 0)
        self.assertEqual(state['backend_elapsed'], 2)
        self.assertEqual(clock.update({'phase': 'speaking'}, 13)['backend_elapsed'], 0)

    def test_activity_colors_are_distinct(self):
        self.assertEqual(len({STATE_COLORS[p] for p in ('listening', 'thinking', 'speaking')}), 3)
        self.assertEqual(STATE_COLORS['thinking'], STATE_COLORS['finalizing'])

    def test_tone_format_and_fades(self):
        pcm = processing_tone()
        self.assertEqual(len(pcm), 4320)
        self.assertEqual(pcm[:2], b'\x00\x00')
        self.assertEqual(pcm[-2:], b'\x00\x00')


if __name__ == '__main__':
    unittest.main()
