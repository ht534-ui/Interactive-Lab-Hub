"""Pure processing-sound policy; playback belongs to the existing PCM writer."""
import math

PROCESSING_PHASES = {"thinking", "finalizing", "synthesizing", "connecting", "closing"}


class ProcessingCue:
    """Once per sustained processing episode, with a cooldown and speech guard.

    The live client plays the returned cue through its existing PCM writer;
    this policy never opens another player or sleeps in the button loop.
    """
    def __init__(self, delay=1.2, cooldown=5.0):
        self.delay, self.cooldown = delay, cooldown
        self.started = None
        self.emitted = False
        self.last_cue = float("-inf")

    def update(self, state, now):
        busy = bool(state.get("backend_busy")) or state.get("phase") in PROCESSING_PHASES
        if not busy:
            self.started, self.emitted = None, False
            return False
        if self.started is None:
            self.started = now
        speaking = state.get("audio_playing", False) or state.get("phase") in {"speaking", "summary", "draining"}
        if (not self.emitted and not speaking and now - self.started >= self.delay
                and now - self.last_cue >= self.cooldown):
            self.emitted, self.last_cue = True, now
            return True
        return False


def processing_tone():
    """A quiet 90 ms two-note PCM cue, with fades to avoid boundary clicks."""
    rate, duration = 24000, 0.09
    frames = int(rate * duration)
    pcm = bytearray()
    for i in range(frames):
        frequency = 660 if i < frames // 2 else 880
        local = i % (frames // 2)
        envelope = min(1.0, local / 120, (frames // 2 - local - 1) / 120)
        value = int(2400 * envelope * math.sin(2 * math.pi * frequency * local / rate))
        pcm.extend(value.to_bytes(2, "little", signed=True))
    return bytes(pcm)


