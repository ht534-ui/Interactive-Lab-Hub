"""Offline protocol tests: no credentials, audio hardware, or billable calls."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speech-scripts"))
from coach_greeting import OpeningGreeting, GREETING_INSTRUCTIONS


class GreetingTests(unittest.IsolatedAsyncioTestCase):
    def connection(self):
        return SimpleNamespace(session=SimpleNamespace(
            instructions=SimpleNamespace(append=AsyncMock())))

    def ack(self, greeting, event_id=None):
        return SimpleNamespace(type="session.instructions.appended",
                               client_event_id=event_id or greeting.event_id)

    async def test_request_is_native_instruction_and_ack_is_not_playback(self):
        greeting = OpeningGreeting()
        connection = self.connection()
        pending = asyncio.create_task(greeting.request(connection))
        await asyncio.sleep(0)
        self.assertTrue(greeting.requested)
        self.assertFalse(greeting.accepted.is_set())
        self.assertFalse(pending.done())
        connection.session.instructions.append.assert_awaited_once_with(
            event_id=greeting.event_id, delegation_id=None,
            content=GREETING_INSTRUCTIONS)
        self.assertTrue(greeting.handle(self.ack(greeting)))
        await pending
        self.assertTrue(greeting.accepted.is_set())

    async def test_concurrent_duplicate_request_does_not_repeat_opening(self):
        greeting = OpeningGreeting()
        connection = self.connection()
        pending = asyncio.create_task(greeting.request(connection))
        await asyncio.sleep(0)
        await greeting.request(connection)
        greeting.handle(self.ack(greeting))
        await pending
        await greeting.request(connection)
        connection.session.instructions.append.assert_awaited_once()

    async def test_unrelated_ack_does_not_mark_ready(self):
        greeting = OpeningGreeting(timeout=0.01)
        connection = self.connection()
        pending = asyncio.create_task(greeting.request(connection))
        await asyncio.sleep(0)
        self.assertFalse(greeting.handle(self.ack(greeting, "different_command")))
        with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
            await pending
        self.assertFalse(greeting.accepted.is_set())

    async def test_matching_rejection_raises_without_exposing_error_payload(self):
        greeting = OpeningGreeting()
        greeting.requested = True
        error = SimpleNamespace(type="error", error=SimpleNamespace(
            client_event_id=greeting.event_id, message="private payload"))
        with self.assertRaisesRegex(RuntimeError, "were rejected") as caught:
            greeting.handle(error)
        self.assertNotIn("private payload", str(caught.exception))
        self.assertFalse(greeting.accepted.is_set())
        error.error.client_event_id = "other_command"
        self.assertFalse(greeting.handle(error))

    async def test_failed_send_is_not_accepted_or_retried(self):
        greeting = OpeningGreeting()
        connection = self.connection()
        connection.session.instructions.append.side_effect = OSError("socket closed")
        with self.assertRaises(OSError):
            await greeting.request(connection)
        self.assertFalse(greeting.accepted.is_set())
        await greeting.request(connection)
        connection.session.instructions.append.assert_awaited_once()

    async def test_new_connection_can_open_again(self):
        first, second = OpeningGreeting(), OpeningGreeting()
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertFalse(second.requested)
        self.assertFalse(second.handle(self.ack(second)))
        self.assertFalse(second.accepted.is_set())

    async def test_audio_and_event_dispatch_continue_while_waiting_for_ack(self):
        greeting = OpeningGreeting()
        connection = self.connection()
        pending = asyncio.create_task(greeting.request(connection))
        audio_append = AsyncMock()
        for _ in range(4):
            await asyncio.sleep(0)
            await audio_append(audio="continuous microphone or silence")
        self.assertFalse(pending.done())
        self.assertEqual(audio_append.await_count, 4)
        greeting.handle(self.ack(greeting))
        await pending

    async def test_prompt_does_not_supply_a_fake_menu_or_request_bookkeeping(self):
        self.assertIn("Immediately greet the user in English", GREETING_INSTRUCTIONS)
        self.assertIn("What have you eaten today?", GREETING_INSTRUCTIONS)
        self.assertIn("Then pause and listen", GREETING_INSTRUCTIONS)
        self.assertIn("Do not wait for the user to speak first", GREETING_INSTRUCTIONS)
        self.assertLess(len(GREETING_INSTRUCTIONS.split()), 50)
        self.assertNotIn("log_food", GREETING_INSTRUCTIONS)
        for food in ("broccoli", "cake", "fried chicken"):
            self.assertNotIn(food, GREETING_INSTRUCTIONS.lower())


if __name__ == "__main__":
    unittest.main()
