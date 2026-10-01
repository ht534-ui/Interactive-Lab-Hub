"""Bridge nested GPT-Live Responses events to the local ledger."""
import asyncio
import json
import time

from coach_runtime import off_thread


class Backend:
    def __init__(self, connection, ledger, changed=lambda result: None, metrics=None):
        self.connection, self.ledger, self.changed = connection, ledger, changed
        self.metrics = metrics
        self.ledger_lock = asyncio.Lock()
        self.calls = {}
        self.response_id = None
        self.active = False
        self.completed = 0
        self.failed = False
        self.seen_completed = set()

    async def handle(self, event):
        kind = event["type"]
        if kind == "response.created":
            self.response_id = event["response"]["id"]
            self.active = True
            self.calls.setdefault(self.response_id, {})
        elif kind == "response.output_item.done" and event["item"].get("type") == "function_call":
            if self.response_id is None:
                raise RuntimeError("Function call before response.created")
            item = event["item"]
            self.calls[self.response_id][item["call_id"]] = item
        elif kind in ("response.failed", "response.incomplete", "response.cancelled"):
            self.active = False
            self.failed = True
        elif kind == "response.completed":
            rid = event["response"]["id"]
            if rid in self.seen_completed:
                return
            self.seen_completed.add(rid)
            # response.output is deliberately empty in Live lifecycle envelopes.
            calls = self.calls.pop(rid, {})
            if event["response"].get("status") in ("failed", "incomplete", "cancelled"):
                self.active, self.failed = False, True
                return
            for item in calls.values():
                try:
                    args = json.loads(item["arguments"])
                    started = time.monotonic()
                    async with self.ledger_lock:
                        result = await off_thread(self.ledger.execute, item["call_id"], item["name"], args)
                    if self.metrics:
                        self.metrics.count("tool_calls")
                        self.metrics.maximum("tool_execute_max_ms", (time.monotonic() - started) * 1000)
                except (ValueError, TypeError):
                    result = {"ok": False, "error": "Invalid tool arguments"}
                if self.metrics and not result.get("ok"):
                    self.metrics.count("tool_errors")
                self.changed(result)
                started = time.monotonic()
                await self.connection.response.item.create(item={"type": "function_call_output",
                    "call_id": item["call_id"], "output": json.dumps(result, ensure_ascii=False)})
                if self.metrics:
                    self.metrics.maximum("tool_return_max_ms", (time.monotonic() - started) * 1000)
            if calls:
                await self.connection.response.create()
            else:
                self.active = False
                self.completed += 1


class TranscriptSync:
    """Debounce transcript activity; a pause schedules reconciliation, not a claimed turn boundary."""
    def __init__(self, pause=1.2):
        self.pause = pause
        self.revision = self.sent = 0
        self.last_input = 0.0

    def heard(self, now):
        self.revision += 1
        self.last_input = now

    def due(self, now, busy=False, ending=False):
        return not busy and not ending and self.revision > self.sent and now - self.last_input >= self.pause

    def dispatched(self):
        self.sent = self.revision
