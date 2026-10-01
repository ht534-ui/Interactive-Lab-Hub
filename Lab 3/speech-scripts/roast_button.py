#!/usr/bin/env python3
"""Use Orange's upper Mini PiTFT button (GPIO23) to start/recap GPT-Live.

Run through run_roast_button.sh, which manages the competing boot display.
This controller uses the existing Lab 2 GPIO environment. The voice client
runs in Lab 3's independent environment.
"""

import argparse
import math
import json
import os
from uuid import uuid4
import signal
import subprocess
import sys
import time
from pathlib import Path

from food_coach import atomic_json


LAB_DIR = Path(__file__).resolve().parent.parent
CLIENT = LAB_DIR / "speech-scripts" / "roast_master_live.py"
PYTHON = Path(os.environ.get("COACH_PYTHON", LAB_DIR / ".venv" / "bin" / "python"))


class StatusTiming:
    """Monotonic elapsed activity labels are honest wait cues, not progress."""
    def __init__(self):
        self.phase = None
        self.phase_since = None
        self.busy_since = None

    def update(self, state, now):
        result = dict(state)
        phase = state.get("phase", "idle")
        if phase != self.phase:
            self.phase, self.phase_since = phase, now
        busy = bool(state.get("backend_busy"))
        if busy and self.busy_since is None:
            self.busy_since = now
        elif not busy:
            self.busy_since = None
        result["phase_elapsed"] = max(0, int(now - self.phase_since))
        result["backend_elapsed"] = max(0, int(now - self.busy_since)) if busy else 0
        return result


def beep(frequency: int, count: int = 1) -> None:
    """A short audible acknowledgment before audio starts or after it stops."""
    rate = 24000
    tone = bytearray()
    for i in range(int(rate * 0.12)):
        value = int(6000 * math.sin(2 * math.pi * frequency * i / rate))
        tone.extend(value.to_bytes(2, "little", signed=True))
    try:
        for _ in range(count):
            subprocess.run(
                ["aplay", "-q", "-D", "default", "-t", "raw", "-f", "S16_LE",
                 "-r", "24000", "-c", "1"],
                input=tone, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2, check=False,
            )
            if count > 1:
                time.sleep(0.08)
    except (OSError, subprocess.TimeoutExpired):
        pass


class VoiceSession:
    def __init__(self):
        self.recorder = self.agent = self.player = None
        self.ending = False
        self.folder = None
        self.last_state = {"phase": "idle"}

    @property
    def running(self):
        return self.agent is not None and self.agent.poll() is None

    def state(self):
        if self.folder and (self.folder / "status.json").exists():
            self.last_state = json.loads((self.folder / "status.json").read_text(encoding="utf-8"))
        result = dict(self.last_state)
        if result.get("phase") == "speaking" and time.time() - result.get("updated_at", 0) > 2:
            result["phase"] = "listening"
        return result

    def start(self, max_seconds):
        if not PYTHON.is_file():
            raise RuntimeError(f"Missing Lab 3 environment: {PYTHON}")
        self.folder = LAB_DIR / ".food-coach" / uuid4().hex
        self.folder.mkdir(parents=True, mode=0o700)
        self.ending = False
        self.last_state = {"phase": "connecting"}
        atomic_json(self.folder / "status.json", self.last_state)
        beep(880)
        self.recorder = subprocess.Popen(
            ["arecord", "-q", "-D", "default", "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1"],
            stdout=subprocess.PIPE, start_new_session=True)
        try:
            self.agent = subprocess.Popen([str(PYTHON), "-u", str(CLIENT),
                "--record", str(self.folder / "record.json"), "--status", str(self.folder / "status.json"),
                "--playback-ack", str(self.folder / "playback.json"), "--max-seconds", str(max_seconds)],
                stdin=self.recorder.stdout, stdout=subprocess.PIPE, start_new_session=True)
            self.recorder.stdout.close()
            self.player = subprocess.Popen(
                ["aplay", "-q", "-D", "default", "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1"],
                stdin=self.agent.stdout, start_new_session=True)
            self.agent.stdout.close()
        except BaseException:
            self.cleanup()
            raise
        print(f"A: recap then finish. Check-in limit {max_seconds}s. Record: {self.folder}", flush=True)

    def finish(self):
        if not self.ending and self.running:
            self.ending = True
            print("END_BUTTON: preparing saved food recap", flush=True)
            # EOF requests reconciliation, NOT session.close. Keep speaker alive.
            if self.recorder and self.recorder.poll() is None:
                self.recorder.terminate()

    def poll(self):
        state = self.state()
        if state.get("phase") in ("finalizing", "synthesizing", "summary", "draining", "closing"):
            self.finish()
        if self.player and self.player.poll() is not None:
            if state.get("phase") == "draining":
                atomic_json(self.folder / "playback.json", {"returncode": self.player.returncode})
            elif self.running and not self.ending:
                self.finish()
        if self.recorder and self.recorder.poll() is not None and self.running and not self.ending:
            self.finish()
        return state

    def cleanup(self):
        for proc in (self.recorder, self.agent, self.player):
            if proc is None:
                continue
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.recorder = self.agent = self.player = None


def main():
    import board
    import digitalio
    from coach_display import Display
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=int, default=180)
    parser.add_argument("--run-seconds", type=int, default=0, help="Exit controller after a bounded hardware test")
    args = parser.parse_args()
    if not 10 <= args.max_seconds <= 180:
        parser.error("--max-seconds must be between 10 and 180")
    session = VoiceSession()
    status_timing = StatusTiming()
    button = digitalio.DigitalInOut(board.D23)
    display = None
    exit_requested = False
    def request_exit(*_):
        nonlocal exit_requested
        exit_requested = True
    signal.signal(signal.SIGINT, request_exit)
    signal.signal(signal.SIGTERM, request_exit)
    try:
        button.switch_to_input(pull=digitalio.Pull.UP)
        display = Display()
        raw = stable = button.value
        changed_at = began = time.monotonic()
        last_draw = 0
        end_deadline = None
        print("READY: upper A starts; A again recaps then closes. Ctrl+C exits.", flush=True)
        while True:
            now = time.monotonic()
            if args.run_seconds and now - began >= args.run_seconds:
                exit_requested = True
            if exit_requested:
                session.finish()
                if not session.running:
                    break
            current = button.value
            if current != raw:
                raw, changed_at = current, now
            if current != stable and now - changed_at >= 0.06:
                stable = current
                if not stable and not exit_requested:
                    print("BUTTON_A", flush=True)
                    if session.running:
                        session.finish()
                    else:
                        session.cleanup()
                        session.start(args.max_seconds)
                        end_deadline = None
            state = session.poll()
            if session.ending and end_deadline is None:
                end_deadline = now + 120
            if end_deadline and now > end_deadline and session.running:
                print("Ending timed out; recap delivery unconfirmed", flush=True)
                session.cleanup()
                session.last_state = {"phase": "error"}
                atomic_json(session.folder / "status.json", session.last_state)
            if session.agent is not None and not session.running:
                print(f"CLIENT_EXIT {session.agent.returncode}", flush=True)
                session.cleanup()
                beep(440, 2)
            if now - last_draw > 0.1:
                display.show(status_timing.update(state, now))
                last_draw = now
            time.sleep(0.02)
    finally:
        session.cleanup()
        button.deinit()
        if display:
            display.close()


if __name__ == "__main__":
    main()
