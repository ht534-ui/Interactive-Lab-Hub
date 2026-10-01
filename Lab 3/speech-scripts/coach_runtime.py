"""Bounded, ordered workers for PCM, tool events and coalesced screen state."""
import asyncio
from array import array
import sys
import copy
import time

from food_coach import atomic_json


async def off_thread(function, *args):
    """Join an in-flight write even on cancellation; never leave a ledger writer behind."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class Metrics:
    def __init__(self):
        self.values = {}
        self.last = {}

    def count(self, key, amount=1):
        self.values[key] = self.values.get(key, 0) + amount

    def maximum(self, key, value):
        self.values[key] = round(max(self.values.get(key, 0), value), 2)

    def interval(self, key):
        now = time.monotonic()
        if key in self.last:
            self.maximum(key + "_max_ms", (now - self.last[key]) * 1000)
        previous = self.last.get(key)
        self.last[key] = now
        return (now - previous) * 1000 if previous is not None else 0


class SerialWorker:
    """One ordered consumer. A full queue or failed consumer is an explicit error."""
    def __init__(self, handle, capacity=128):
        self.handle = handle
        self.queue = asyncio.Queue(capacity)
        self.in_flight = False
        self.task = asyncio.create_task(self._run())

    @property
    def busy(self):
        return self.in_flight or not self.queue.empty()

    def check(self):
        if self.task.done():
            self.task.result()
            raise RuntimeError("Worker stopped")

    def submit(self, item):
        self.check()
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            raise RuntimeError("Worker backlog exceeded limit") from None

    async def _run(self):
        while True:
            item = await self.queue.get()
            self.in_flight = True
            try:
                await self.handle(item)
            finally:
                self.in_flight = False
                self.queue.task_done()

    async def drain(self):
        self.check()
        join = asyncio.create_task(self.queue.join())
        try:
            await asyncio.wait((join, self.task), return_when=asyncio.FIRST_COMPLETED)
            self.check()
            await join
        finally:
            join.cancel()
            await asyncio.gather(join, return_exceptions=True)

    async def stop(self):
        self.task.cancel()
        results = await asyncio.gather(self.task, return_exceptions=True)
        if isinstance(results[0], Exception):
            raise results[0]


def pcm_has_signal(pcm, rms_floor=80):
    """Output activity cue only: continuous Live PCM also contains silence.

    This is not microphone VAD, echo cancellation, or a speech-completion signal.
    """
    samples = array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return bool(samples) and sum(sample * sample for sample in samples) > len(samples) * rms_floor ** 2


class PCMOutput:
    """No extra prebuffer: preserve sample order, isolate pipe backpressure, bound latency."""
    def __init__(self, stream, metrics, max_seconds=4):
        self.stream, self.metrics = stream, metrics
        self.limit = int(max_seconds * 48000)
        self.buffered = 0
        self.playout_until = 0.0
        self.voiced_until = 0.0
        self.voiced_buffered = 0
        self.last_write = 0.0
        self.worker = SerialWorker(self._write, capacity=max(1, self.limit // 2))

    @property
    def playing(self):
        # Conservative local estimate, not a device playback-completion signal.
        return self.voiced_buffered > 0 or time.monotonic() < max(self.voiced_until, self.last_write + 0.7)

    async def _write(self, item):
        pcm, audible = item
        start = time.monotonic()
        try:
            await off_thread(self._write_blocking, pcm)
            if audible:
                self.last_write = time.monotonic()
        finally:
            self.buffered -= len(pcm)
            if audible:
                self.voiced_buffered -= len(pcm)
            self.metrics.maximum("pcm_write_max_ms", (time.monotonic() - start) * 1000)

    def _write_blocking(self, pcm):
        written = self.stream.write(pcm)
        if written != len(pcm):
            raise OSError("Short PCM write")
        self.stream.flush()

    def offer(self, pcm):
        if len(pcm) % 2:
            raise ValueError("PCM must contain whole samples")
        if self.buffered + len(pcm) > self.limit:
            self.metrics.count("pcm_overflows")
            raise RuntimeError("Speaker backlog exceeded configured limit")
        audible = False
        for pos in range(0, len(pcm), 4800):
            chunk = pcm[pos:pos + 4800]
            active = pcm_has_signal(chunk)
            self.worker.submit((chunk, active))
            self.buffered += len(chunk)
            self.playout_until = max(time.monotonic(), self.playout_until) + len(chunk) / 48000
            if active:
                audible = True
                self.voiced_buffered += len(chunk)
                self.voiced_until = self.playout_until
        self.metrics.maximum("pcm_buffered_max_ms", self.buffered / 48)
        return audible

    async def recap(self, pcm):
        # Finish accepted conversational audio before the finite ending, with one writer.
        await self.worker.drain()
        for pos in range(0, len(pcm), 4800):
            self.offer(pcm[pos:pos + 4800])
            await self.worker.drain()


class StatusWriter:
    """Latest state wins; at most ten fsyncs/second, all away from the audio loop."""
    def __init__(self, path, metrics):
        self.path, self.metrics = path, metrics
        self.state = {}
        self.closed = False
        self.task = asyncio.create_task(self._run())

    def publish(self, **updates):
        self.state.update(updates)
        self.state["updated_at"] = time.time()

    async def _save(self):
        state = copy.deepcopy(self.state)
        state["audio_diagnostics"] = copy.deepcopy(self.metrics.values)
        await off_thread(atomic_json, self.path, state)

    async def _run(self):
        while not self.closed:
            start = time.monotonic()
            await asyncio.sleep(0.1)
            lag = max(0, time.monotonic() - start - 0.1) * 1000
            self.metrics.values["event_loop_lag_latest_ms"] = round(lag, 2)
            self.metrics.maximum("event_loop_lag_max_ms", lag)
            if self.state:
                await self._save()

    async def close(self):
        self.closed = True
        await self.task
        if self.state:
            await self._save()
