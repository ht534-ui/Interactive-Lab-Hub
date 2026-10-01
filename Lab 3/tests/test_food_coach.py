import asyncio
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speech-scripts"))
from food_coach import Ledger
from coach_backend import Backend, TranscriptSync
from roast_button import VoiceSession
from coach_language import fill_template, localize_recap


def food(entry="broccoli", **changes):
    args = dict(entry_id=entry, action="add", food="西兰花", portion="一碗", category="vegetable", separate_serving=False)
    args.update(changes)
    return args


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "record.json"
        self.ledger = Ledger(self.path)

    def test_empty_is_not_fifty(self):
        self.assertIsNone(self.ledger.snapshot()["score"])
        self.assertIn("没有保存", self.ledger.summary())

    def test_sequence_and_persistence(self):
        for i, args in enumerate([food(), food("cake", food="蛋糕", category="dessert", portion="半块"), food("chicken", food="炸鸡", category="fried", portion="两块")]):
            self.ledger.execute(str(i), "log_food", args)
        self.assertEqual(Ledger(self.path).snapshot()["score"], 45)
        self.assertEqual(len(Ledger(self.path).snapshot()["entries"]), 3)

    def test_unknown_portion_then_correction(self):
        self.ledger.execute("a", "log_food", food(portion=None))
        self.assertIsNone(self.ledger.snapshot()["score"])
        self.assertEqual(self.ledger.snapshot()["pending_portions"], 1)
        self.ledger.execute("b", "log_food", food(action="correct", portion="半碗"))
        self.assertEqual(self.ledger.snapshot()["score"], 60)
        self.assertEqual(len(self.ledger.snapshot()["entries"]), 1)

    def test_same_call_replayed_after_restart(self):
        result = self.ledger.execute("a", "log_food", food())
        self.assertEqual(Ledger(self.path).execute("a", "log_food", food()), result)
        self.assertEqual(len(self.ledger.snapshot()["entries"]), 1)

    def test_call_id_conflict(self):
        self.ledger.execute("a", "log_food", food())
        self.assertFalse(self.ledger.execute("a", "log_food", food(portion="两碗"))["ok"])
        self.assertEqual(self.ledger.snapshot()["entries"][0]["portion"], "一碗")

    def test_same_id_new_call_is_idempotent(self):
        self.ledger.execute("a", "log_food", food())
        result = self.ledger.execute("b", "log_food", food())
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["score"], 60)

    def test_same_food_new_id_requires_explicit_additional_serving(self):
        self.ledger.execute("a", "log_food", food())
        self.assertFalse(self.ledger.execute("b", "log_food", food("new"))["ok"])
        self.assertTrue(self.ledger.execute("c", "log_food", food("new", separate_serving=True))["ok"])

    def test_replace_category_changes_score(self):
        self.ledger.execute("a", "log_food", food())
        self.ledger.execute("b", "log_food", food(action="correct", food="蛋糕", category="dessert"))
        self.assertEqual(self.ledger.snapshot()["score"], 45)

    def test_remove(self):
        self.ledger.execute("a", "log_food", food())
        result = self.ledger.execute("b", "log_food", food(action="remove"))
        self.assertIsNone(result["score"])

    def test_unknown_correction_rejected(self):
        self.assertFalse(self.ledger.execute("a", "log_food", food(action="correct"))["ok"])

    def test_invalid_arguments_no_mutation(self):
        for args in [[], food(category="magic"), food(portion=100), food(food=""), food(entry_id="")]:
            self.assertFalse(self.ledger.execute("a", "log_food", args)["ok"])
        self.assertEqual(self.ledger.snapshot()["entries"], [])

    def test_category_cap(self):
        for i in range(6):
            self.ledger.execute(str(i), "log_food", food(str(i), separate_serving=True))
        self.assertEqual(self.ledger.snapshot()["score"], 70)

    def test_frozen_record_and_failed_final_reconciliation(self):
        self.ledger.execute("a", "log_food", food(portion=None))
        summary = self.ledger.freeze(False)
        self.assertIn("未计分", summary)
        self.assertIn("没核对完", summary)
        self.assertFalse(self.ledger.execute("b", "log_food", food(action="correct"))["ok"])
        self.assertEqual(Ledger(self.path).data["summary"], summary)

    def test_private_file_permissions(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


    def test_size_adjective_is_not_a_portion(self):
        self.assertFalse(self.ledger.execute("a", "log_food", food(portion="大牛排"))["ok"])
        self.assertFalse(self.ledger.execute("b", "log_food", food(portion="some"))["ok"])
        self.assertTrue(self.ledger.execute("c", "log_food", food(portion="超大一块"))["ok"])


class TranscriptSyncTests(unittest.TestCase):
    def test_pause_schedules_once_and_waits_for_backend(self):
        sync = TranscriptSync()
        sync.heard(10)
        self.assertFalse(sync.due(11))
        self.assertFalse(sync.due(12, busy=True))
        self.assertFalse(sync.due(12, ending=True))
        self.assertTrue(sync.due(12))
        sync.dispatched()
        self.assertFalse(sync.due(13))
        sync.heard(13)
        self.assertFalse(sync.due(14, busy=True))
        self.assertTrue(sync.due(15))


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name) / "record.json")
        self.connection = SimpleNamespace(response=SimpleNamespace(item=SimpleNamespace(create=AsyncMock()), create=AsyncMock()))
        self.backend = Backend(self.connection, self.ledger)

    async def start(self, rid="r1"):
        await self.backend.handle({"type": "response.created", "response": {"id": rid}})

    async def item(self, call_id="c1", arguments=None):
        await self.backend.handle({"type": "response.output_item.done", "item": {"type": "function_call", "name": "log_food", "call_id": call_id, "arguments": json.dumps(food()) if arguments is None else arguments}})

    async def test_empty_lifecycle_output_still_executes_collected_calls(self):
        await self.start()
        await self.item()
        done = {"type": "response.completed", "response": {"id": "r1", "output": []}}
        await self.backend.handle(done)
        self.assertEqual(self.ledger.snapshot()["score"], 60)
        self.connection.response.item.create.assert_awaited_once()
        self.connection.response.create.assert_awaited_once()
        await self.backend.handle(done)
        self.connection.response.create.assert_awaited_once()
        await self.start("r2")
        await self.backend.handle({"type": "response.completed", "response": {"id": "r2", "output": []}})
        self.assertFalse(self.backend.active)
        self.assertEqual(self.backend.completed, 1)

    async def test_all_results_before_continue(self):
        await self.start()
        await self.item("c1")
        await self.item("c2")
        await self.backend.handle({"type": "response.completed", "response": {"id": "r1"}})
        self.assertEqual(self.connection.response.item.create.await_count, 2)
        self.connection.response.create.assert_awaited_once()
        self.assertEqual(len(self.ledger.snapshot()["entries"]), 1)

    async def test_invalid_json_returns_tool_error(self):
        await self.start()
        await self.item(arguments="bad")
        await self.backend.handle({"type": "response.completed", "response": {"id": "r1"}})
        result = self.connection.response.item.create.call_args.kwargs["item"]
        self.assertFalse(json.loads(result["output"])["ok"])

    async def test_failed_response_unblocks_finish(self):
        await self.start()
        await self.backend.handle({"type": "response.failed"})
        self.assertFalse(self.backend.active)
        self.assertTrue(self.backend.failed)


class ButtonTests(unittest.TestCase):
    def test_end_does_not_terminate_agent_or_player(self):
        s = VoiceSession()
        s.recorder, s.agent, s.player = Mock(), Mock(), Mock()
        for proc in (s.recorder, s.agent, s.player):
            proc.poll.return_value = None
        s.finish()
        s.finish()
        s.recorder.terminate.assert_called_once()
        s.agent.terminate.assert_not_called()
        s.player.terminate.assert_not_called()

    def test_player_ack_only_after_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = VoiceSession()
            s.folder = Path(tmp)
            s.last_state = {"phase": "draining"}
            s.player = Mock()
            s.player.poll.return_value = None
            s.poll()
            self.assertFalse((s.folder / "playback.json").exists())
            s.player.poll.return_value = 0
            s.player.returncode = 0
            s.poll()
            self.assertEqual(json.loads((s.folder / "playback.json").read_text(encoding="utf-8"))["returncode"], 0)


class LanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_english_recap_preserves_saved_food_and_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "record.json")
            ledger.execute("a", "log_food", food(food="broccoli"))
            result = SimpleNamespace(output_text=json.dumps({"template": "[[FOOD_0]] — [[SCORE]] points. See you next meal!"}))
            client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=result)))
            recap = await localize_recap(client, "backend", ledger, "en", True)
            self.assertEqual(recap, "broccoli — 60 points. See you next meal!")
            self.assertEqual(json.loads(client.responses.create.call_args.kwargs["input"])["language"], "en")

    async def test_translation_cannot_drop_food_or_invent_numeric_score(self):
        source = "[[FOOD_0]]——[[SCORE]]分。"
        slots = {"[[FOOD_0]]": "broccoli", "[[SCORE]]": 60}
        self.assertEqual(fill_template("[[FOOD_0]]: [[SCORE]] points. See you!", source, slots), "broccoli: 60 points. See you!")
        for invalid in ("[[SCORE]] points", "[[FOOD_0]]: 99 points, [[SCORE]]", "[[FOOD_0]] [[SCORE]] [[OTHER]]"):
            with self.assertRaises(ValueError):
                fill_template(invalid, source, slots)

    async def test_chinese_recap_needs_no_translation_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "record.json")
            ledger.execute("a", "log_food", food())
            client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
            recap = await localize_recap(client, "backend", ledger, "zh", True)
            self.assertIn("西兰花", recap)
            self.assertIn("60", recap)
            self.assertNotIn("[[", recap)
            client.responses.create.assert_not_awaited()


class PipeTests(unittest.TestCase):
    def test_pcm_eof_before_process_exit(self):
        script_dir = str(Path(__file__).resolve().parents[1] / "speech-scripts")
        code = "import sys,time; sys.path.insert(0," + repr(script_dir) + "); from roast_master_live import close_pcm_output; sys.stdout.buffer.write(b'pcm'); sys.stdout.flush(); close_pcm_output(); time.sleep(10)"
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
        try:
            import selectors
            selector = selectors.DefaultSelector()
            self.addCleanup(selector.close)
            selector.register(child.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(3))
            self.assertEqual(child.stdout.read(3), b"pcm")
            self.assertTrue(selector.select(3), "PCM pipe should reach EOF while API process stays alive")
            self.assertEqual(child.stdout.read(1), b"")
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait()
            child.stdout.close()


if __name__ == "__main__":
    unittest.main()
