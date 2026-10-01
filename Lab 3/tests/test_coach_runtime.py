"""Fault injection for audio/tool independence, bounded buffering and finalization."""
import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speech-scripts"))
from coach_backend import Backend
from coach_runtime import Metrics, PCMOutput, SerialWorker, StatusWriter, off_thread


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_live_pcm_does_not_mark_coach_speaking(self):
        audio = PCMOutput(io.BytesIO(), Metrics())
        try:
            self.assertFalse(audio.offer(bytes(4800)))
            self.assertFalse(audio.playing)
            await audio.worker.drain()
            self.assertFalse(audio.playing)
            self.assertTrue(audio.offer(b"\x00\x10" * 2400))
            self.assertTrue(audio.playing)
            await audio.worker.drain()
            self.assertTrue(audio.playing)
        finally:
            await audio.worker.stop()

    async def test_slow_ledger_and_tool_return_do_not_hold_audio_receiver(self):
        entered, release = threading.Event(), threading.Event()
        send_entered, send_release = asyncio.Event(), asyncio.Event()
        def execute(*args):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test failed to release ledger")
            return {"ok": True}
        async def return_tool(**kwargs):
            send_entered.set()
            await send_release.wait()
        connection = SimpleNamespace(response=SimpleNamespace(item=SimpleNamespace(create=return_tool), create=AsyncMock()))
        metrics = Metrics()
        backend = Backend(connection, SimpleNamespace(execute=execute), metrics=metrics)
        worker = SerialWorker(backend.handle)
        sink = io.BytesIO()
        audio = PCMOutput(sink, metrics)
        try:
            for event in [
                {"type": "response.created", "response": {"id": "r"}},
                {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "c", "name": "get_food_log", "arguments": "{}"}},
                {"type": "response.completed", "response": {"id": "r", "status": "completed"}},
            ]:
                worker.submit(event)
            await until(entered.is_set)
            audio.offer(b"\x01\x00" * 2400)
            await asyncio.wait_for(audio.worker.drain(), 1)
            self.assertEqual(len(sink.getvalue()), 4800)
            self.assertTrue(worker.busy)
            release.set()
            await asyncio.wait_for(send_entered.wait(), 1)
            audio.offer(b"\x02\x00" * 2400)
            await asyncio.wait_for(audio.worker.drain(), 1)
            self.assertEqual(len(sink.getvalue()), 9600)
            send_release.set()
            await worker.drain()
            connection.response.create.assert_awaited_once()
            self.assertEqual(metrics.values["tool_calls"], 1)
        finally:
            release.set()
            send_release.set()
            await worker.stop()
            await audio.worker.stop()

    async def test_blocked_speaker_does_not_block_loop_and_preserves_pcm_order(self):
        entered, release = threading.Event(), threading.Event()
        class SlowSink(io.BytesIO):
            def write(self, data):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("Test failed to release speaker")
                return super().write(data)
        sink = SlowSink()
        audio = PCMOutput(sink, Metrics())
        try:
            audio.offer(b"aa")
            await until(entered.is_set)
            for _ in range(10):
                await asyncio.sleep(0.005)
            audio.offer(b"bb")
            self.assertEqual(audio.buffered, 4)
            release.set()
            await audio.recap(b"cc" * 5000)
            self.assertEqual(sink.getvalue(), b"aabb" + b"cc" * 5000)
            self.assertEqual(audio.buffered, 0)
        finally:
            release.set()
            await audio.worker.stop()

    async def test_output_limit_fails_explicitly_without_dropping_accepted_audio(self):
        sink = io.BytesIO()
        metrics = Metrics()
        audio = PCMOutput(sink, metrics, max_seconds=0.1)
        try:
            audio.offer(bytes(4800))
            with self.assertRaisesRegex(RuntimeError, "backlog"):
                audio.offer(b"xx")
            await audio.worker.drain()
            self.assertEqual(sink.getvalue(), bytes(4800))
            self.assertEqual(metrics.values["pcm_overflows"], 1)
        finally:
            await audio.worker.stop()

    async def test_small_audio_deltas_are_bounded_by_bytes_not_packet_count(self):
        sink = io.BytesIO()
        audio = PCMOutput(sink, Metrics(), max_seconds=0.1)
        try:
            for _ in range(100):
                audio.offer(bytes(48))
            await audio.worker.drain()
            self.assertEqual(len(sink.getvalue()), 4800)
        finally:
            await audio.worker.stop()

    async def test_worker_failure_surfaces_to_drain_instead_of_hanging(self):
        async def broken(item):
            raise OSError("broken pipe")
        worker = SerialWorker(broken)
        worker.submit(1)
        worker.submit(2)
        with self.assertRaisesRegex(OSError, "broken pipe"):
            await asyncio.wait_for(worker.drain(), 1)
        with self.assertRaises(OSError):
            await worker.stop()

    async def test_cancellation_joins_ledger_write_before_releasing_lock(self):
        entered, release = threading.Event(), threading.Event()
        finished = []
        def save():
            entered.set()
            release.wait(3)
            finished.append(True)
        task = asyncio.create_task(off_thread(save))
        await until(entered.is_set)
        task.cancel()
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(finished, [True])

    async def test_status_writes_coalesce_and_flush_latest_on_close(self):
        entered, release = threading.Event(), threading.Event()
        writes = []
        def slow_write(path, state):
            entered.set()
            release.wait(3)
            writes.append(state)
        with patch("coach_runtime.atomic_json", slow_write):
            writer = StatusWriter(Path("unused"), Metrics())
            try:
                writer.publish(phase="listening", backend_busy=False)
                await until(entered.is_set)
                for i in range(100):
                    writer.publish(phase="speaking", score=i, backend_busy=True)
                release.set()
                await writer.close()
            finally:
                release.set()
            self.assertEqual(writes[-1]["score"], 99)
            self.assertTrue(writes[-1]["backend_busy"])
            self.assertLessEqual(len(writes), 3)

    async def test_final_status_file_has_private_metrics_and_no_transcript(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            metrics = Metrics()
            metrics.count("output_chunks", 3)
            writer = StatusWriter(path, metrics)
            writer.publish(phase="done")
            await writer.close()
            value = json.loads(path.read_text())
            self.assertEqual(value["audio_diagnostics"]["output_chunks"], 3)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("transcript", value)
