"""One Live-native opening per connection, without a synthetic user food report.

Protocol: https://developers.openai.com/api/docs/guides/live-conversations
Keep input PCM flowing while requesting and acknowledging the greeting. An
acknowledgment means accepted instructions, not heard or completed playback.
"""
import asyncio
from uuid import uuid4


# Keep this a short, concrete speech request. Persona, interruption, and food
# bookkeeping rules already belong to the session prompt.
GREETING_INSTRUCTIONS = """Immediately greet the user in English. Say: "Hey, I'm Orange. What have you eaten today? Give me the menu; I'll bring the attitude." Then pause and listen. Do not wait for the user to speak first."""


class OpeningGreeting:
    """Send once and correlate acceptance/errors without blocking event dispatch."""

    def __init__(self, timeout=8.0):
        self.event_id = "coach_greeting_" + uuid4().hex
        self.timeout = timeout
        self.requested = False
        self.accepted = asyncio.Event()

    async def request(self, connection):
        if self.requested:
            return
        # Mark before the await; an uncertain send must never be retried blindly.
        self.requested = True
        try:
            async with asyncio.timeout(self.timeout):
                await connection.session.instructions.append(
                    event_id=self.event_id,
                    delegation_id=None,
                    content=GREETING_INSTRUCTIONS,
                )
                await self.accepted.wait()
        except TimeoutError:
            raise RuntimeError("Opening greeting instructions were not acknowledged") from None

    def handle(self, event):
        """Return True only for our accepted append; reject a correlated error."""
        if not self.requested:
            return False
        if event.type == "session.instructions.appended":
            if getattr(event, "client_event_id", None) != self.event_id:
                return False
            self.accepted.set()
            return True
        if event.type in ("error", "session.error"):
            error = getattr(event, "error", None)
            if getattr(error, "client_event_id", None) == self.event_id:
                raise RuntimeError("Opening greeting instructions were rejected")
        return False
