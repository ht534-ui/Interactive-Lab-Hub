# Food coach activity cues

The screen separates the coach's mood face from activity: cyan LISTENING, violet SPEAKING/RECAP, amber SAVING/CHECKING, green SAVED and red CHECK LOG. Labels remain readable without interpreting color. `backend_busy` adds a second SAVING label while keeping the foreground listening or speaking label; “You can keep talking” appears during an open conversation. The displayed seconds count time in the current activity, not completion progress. A moving activity marker indicates continuing processing.

`StatusTiming` in the button controller uses a monotonic clock to supply `phase_elapsed` and `backend_elapsed` to the display. Older status producers remain compatible with the existing phase labels. The display accepts these additional fields but defaults to zero if a caller omits them.

`coach_cues.ProcessingCue(delay=1.2, cooldown=5.0).update(state, now)` returns true at most once per continuous processing episode, after 1.2 seconds. A new episode cannot sound within five seconds of the previous cue. Short saves stay silent; `audio_playing`, SPEAKING, SUMMARY and DRAINING suppress the cue while speech is active. The live client's PCM writer owns playback of `processing_tone()`, a 90 ms, two-note 24 kHz mono S16_LE cue with fades. No processing cue opens a competing ALSA player or blocks the button loop. Existing session start/end acknowledgment sounds remain in the controller.

Local verification covers duration/cooldown/speech guards, tone format, separate foreground/background timers and distinct activity colors. The preview is a rendered screen image, not a physical screen or listening test. Hardware legibility, speaker volume, audibility during actual conversation and microphone echo still require a device test.

```bash
python3 -m unittest discover -s 'Lab 3/tests' -p test_coach_cues.py -v
```
