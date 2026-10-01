#!/usr/bin/env python3
"""GPT-Live PCM transport with application-owned food tools and a drained ending."""
import argparse
import asyncio
import base64
import json
import errno
import os
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4

from food_coach import BACKEND_PROMPT, TOOLS, Ledger, atomic_json
from coach_backend import Backend, TranscriptSync
from coach_language import localize_recap
from coach_cues import ProcessingCue, processing_tone
from coach_runtime import Metrics, PCMOutput, SerialWorker, StatusWriter, off_thread
from coach_greeting import OpeningGreeting

LAB_DIR = Path(__file__).resolve().parent.parent
PROMPT = """You are Orange, the user's outspoken food coach: an affectionate ally with a viciously sharp wit and strong opinions about the menu. Sound like a person with a point of view, not a customer-service agent, food clerk, or nutrition announcer.

LANGUAGE: Always speak English, from the first response through praise, roasts, questions, and goodbye. Understand other languages without switching your output language.

GROUNDING: The user's actual reports in this check-in are the only source of what they ate. Start with no assumed foods. Never invent a food, portion, meal, sequence, or prior conversation to set up a joke. Hypotheticals, quoted examples, suggestions, and your own jokes are not evidence of eating. If a report is unclear, ask briefly rather than completing it yourself. Backend results establish what was saved; a new user correction takes precedence over an older report for conversation, even while the backend catches up. Drop callbacks to a retracted or corrected claim. Do not suggest a food and later remember it as something the user ate.

CHARACTER: Be unmistakably supportive AND judgemental. Give sincere, specific, energetic credit when the reported choice gives you a reason; acknowledge honesty or a useful correction without condescension. Let praise stand on its own. When the actual menu supplies comic material, roast it hard: incisive observations, audacious metaphors, dry disbelief, and a sharp punchline. Aim the joke at what was reported, not an imagined nutritional failure. Do not reflexively deflate every roast with a reassurance disclaimer. Confidence and warmth can coexist; neither requires blandness.

DEVELOP THE CONVERSATION: Respond to the latest utterance first. A callback may use an earlier food only if the user actually reported it and has not retracted or corrected it. Let tone follow the conversation; there is no required praise-to-roast progression, escalating sequence, or set of foods to perform. A single report can earn a complete reaction. Choose the attitude and rhythm that fit, without forcing a praise/criticism/encouragement sandwich. Do not recycle a stock punchline when the context changes.

BOUNDARIES: Roast the menu, never the user's body, weight, appearance, personality, or worth. No starvation, compensatory exercise, or extreme restriction. Do not equate eating a food with personal failure. If the user is genuinely distressed, meet that feeling supportively. Soften immediately on request, and stop roasting when asked.

TURN-TAKING: Usually give one or two short sentences, then leave room. Use timing, a brief pause, or a pointed question when it fits; no staged beat is mandatory. Ask a motivated question, not a repeated intake checklist. Clarify an unknown portion at a natural opening, one question at a time, and do not ask again after it is answered. Never guess a portion. If the user interrupts, stop and listen. Let them finish the new report or correction, then respond to that; do not restart the interrupted line. Backchannel only where it helps, without talking over an unfinished report.

BOOKKEEPING: The application schedules background food updates. Converse naturally while it works; do not announce delegation, saving, or progress, and do not issue a spoken receipt after each food. Backend updates are factual context, not lines to read aloud. Never claim a save succeeded before a successful tool result. Delegate a requested record check or unresolved correction when needed. This is a fictional menu game, not nutrition or calorie assessment. Numbers must come from the authoritative backend; do not volunteer routine scores or invent calories. The application owns the brief humorous closing when the check-in ends; do not manufacture an extra recap or rehearse an ending during ordinary turns."""


def load_key():
    if os.environ.get("OPENAI_API_KEY"):
        return
    env_path = Path(os.environ.get("COACH_ENV_FILE", LAB_DIR / ".env"))
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENAI_API_KEY="):
                value = line.partition("=")[2].strip().strip('"').strip("'")
                if value:
                    os.environ["OPENAI_API_KEY"] = value
                break
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Missing OPENAI_API_KEY in the Lab environment")


def close_pcm_output():
    # CPython's standard stream may own a wrapper with closefd=False.
    fd = sys.stdout.fileno()
    sys.stdout.close()
    try:
        os.close(fd)
    except OSError as exc:
        if exc.errno != errno.EBADF:
            raise


async def run(args):
    metrics = Metrics()
    screen = StatusWriter(args.status, metrics)
    audio = PCMOutput(sys.stdout.buffer, metrics)
    transcript_trace = []
    try:
        await run_session(args, metrics, screen, audio, transcript_trace)
    finally:
        try:
            await audio.worker.stop()
        finally:
            await screen.close()
            if args.transcript_log:
                await off_thread(atomic_json, args.transcript_log, {"events": transcript_trace})


async def run_session(args, metrics, screen, pcm_output, transcript_trace):
    from openai import AsyncOpenAI
    load_key()
    ledger = Ledger(args.record)
    loop = asyncio.get_running_loop()
    chunks = asyncio.Queue(maxsize=20)
    closed = asyncio.Event()
    ready = asyncio.Event()
    finish_requested = asyncio.Event()
    stopping = False
    ending = False
    reader_added = False
    pending_byte = b""
    language = "en"
    ledger.data["language"] = language
    ledger.save()
    backend_model = os.environ.get("COACH_BACKEND_MODEL", "gpt-5.6-luna")
    last_input = time.monotonic()
    input_bytes = output_bytes = 0
    publish = screen.publish
    publish(phase="connecting", mood="neutral", score=None, language=language, backend_busy=False)

    def read_audio():
        nonlocal reader_added, input_bytes
        chunk = os.read(sys.stdin.fileno(), 4800)
        if not chunk:
            loop.remove_reader(sys.stdin.fileno())
            reader_added = False
            finish_requested.set()
        else:
            input_bytes += len(chunk)
            if chunks.full():
                dropped = chunks.get_nowait()
                metrics.count("input_dropped_bytes", len(dropped))
            chunks.put_nowait(chunk)
            metrics.maximum("input_queued_chunks_max", chunks.qsize())

    def changed(result):
        if result.get("ok"):
            entries = result.get("entries", [])
            category = entries[-1]["category"] if entries else "other"
            mood = "glare" if category == "fried" else "wry" if category == "dessert" else "smile" if category in ("vegetable", "fruit") else "neutral"
            publish(mood=mood, score=result.get("score"), pending=result.get("pending_portions", 0))
        # Food details are already persisted in the private ledger. Avoid terminal I/O
        # in the receiver or tool callback; a slow terminal must not stall speech.

    publish()
    async with AsyncOpenAI(timeout=25, max_retries=1) as client:
        async with client.live.connect(max_retries=0) as connection:
            backend = Backend(connection, ledger, changed, metrics)

            async def handle_backend(event):
                await backend.handle(event)
                if not ending:
                    publish(backend_busy=backend.active)

            def backend_busy():
                return backend.active or backend_worker.busy
            transcript_sync = TranscriptSync()
            greeting = OpeningGreeting()

            async def greet():
                await ready.wait()
                if not finish_requested.is_set():
                    greeting_started = time.monotonic()
                    metrics.last["greeting_requested"] = greeting_started
                    await greeting.request(connection)
                    metrics.maximum("greeting_ack_ms", (time.monotonic() - greeting_started) * 1000)

            async def send_audio():
                nonlocal pending_byte
                await ready.wait()
                # Live's timeline needs continuous PCM, including after microphone EOF.
                while not stopping:
                    try:
                        chunk = await asyncio.wait_for(chunks.get(), 0.1)
                    except asyncio.TimeoutError:
                        chunk = bytes(4800)
                    chunk = pending_byte + chunk
                    size = len(chunk) - len(chunk) % 2
                    pending_byte = chunk[size:]
                    if size:
                        metrics.interval("input_send_gap")
                        started = time.monotonic()
                        await connection.session.input_audio.append(audio=base64.b64encode(chunk[:size]).decode())
                        metrics.maximum("input_send_max_ms", (time.monotonic() - started) * 1000)

            async def activity_cues():
                await ready.wait()
                cue = ProcessingCue()
                tone = processing_tone()
                while not stopping:
                    playing = pcm_output.playing
                    publish(audio_playing=playing)
                    if not ending:
                        publish(phase="speaking" if playing else "listening")
                    state = dict(screen.state)
                    # No cue may follow PCM EOF or compete with the finite recap.
                    if state.get("phase") not in {"summary", "draining", "closing", "done", "error"}:
                        if cue.update(state, time.monotonic()):
                            pcm_output.offer(tone)
                            metrics.count("processing_cues")
                    await asyncio.sleep(0.1)

            async def sync_food_reports():
                await ready.wait()
                while not stopping:
                    await asyncio.sleep(0.1)
                    if transcript_sync.due(time.monotonic(), backend_busy(), ending or finish_requested.is_set()):
                        transcript_sync.dispatched()
                        backend.active = True
                        publish(backend_busy=True)
                        await connection.response.item.create(item={"type": "message", "role": "user", "content": [
                            {"type": "input_text", "text": "[APPLICATION BACKGROUND SYNC] Check recent USER speech in the conversation and maintain the food log now. A pause may be mid-sentence: do not invent missing details or treat jokes/hypotheticals as food. Reuse existing IDs; save new food once, apply corrections/removals, and keep unclear portions null. This is background bookkeeping, not a request to repeat a confirmation or score aloud. If nothing changed, finish without changing records."}]})
                        await connection.response.create()

            async def finish():
                nonlocal ending, stopping, reader_added
                await finish_requested.wait()
                await ready.wait()
                ending = True
                publish(phase="finalizing")
                if reader_added:
                    loop.remove_reader(sys.stdin.fileno())
                    reader_added = False
                reconciled = False
                try:
                    # Drain captured audio and let final transcript/delegation events arrive.
                    settle_started = time.monotonic()
                    deadline = settle_started + 8
                    while time.monotonic() < deadline:
                        if chunks.empty() and time.monotonic() - max(last_input, settle_started) > 1.5 and not backend_busy():
                            break
                        await asyncio.sleep(0.1)
                    async with asyncio.timeout(25):
                        while backend_busy():
                            await asyncio.sleep(0.1)
                        count = backend.completed
                        backend.failed = False
                        backend.active = True
                        await connection.response.item.create(item={"type": "message", "role": "user", "content": [
                            {"type": "input_text", "text": "[APPLICATION END BUTTON] Final reconciliation: use get_food_log and check all user food reports/portions/corrections in this conversation. Save any missing ones exactly once with log_food; do not ask more questions. Unknown portions stay null. Finish when the saved log matches the conversation."}]})
                        await connection.response.create()
                        while backend.completed == count and not backend.failed:
                            await asyncio.sleep(0.1)
                        reconciled = not backend.failed
                except (TimeoutError, OSError):
                    print("Final reconciliation incomplete; retaining saved entries", file=sys.stderr)
                # Drain queued tool events before freezing. The lock also protects against
                # a late delegated call while the ending is being prepared.
                async with asyncio.timeout(30):
                    await backend_worker.drain()
                async with backend.ledger_lock:
                    await off_thread(ledger.freeze, reconciled)
                summary = await localize_recap(client, backend_model, ledger, language, reconciled)
                async with backend.ledger_lock:
                    ledger.data["summary"] = summary
                    await off_thread(ledger.save)
                publish(phase="synthesizing", backend_busy=False, score=ledger.snapshot()["score"], summary=summary)
                print("SUMMARY " + summary, file=sys.stderr, flush=True)
                try:
                    # Known text and finite PCM: unlike Live, this has an actual EOF.
                    async with asyncio.timeout(35):
                        speech = await client.audio.speech.create(model="gpt-4o-mini-tts", voice="marin",
                            input=summary, response_format="pcm", instructions=f"Speak only in {language}. Perform this as a witty, sharply sarcastic but supportive coach wrapping up with a friend. Conversational, not a report: brisk recap, dry comic emphasis and a short beat before the punchline. Warm when encouraging. Say only the supplied text.")
                        pcm = speech.content
                    if not pcm or len(pcm) % 2:
                        raise RuntimeError("Invalid summary PCM")
                    publish(phase="summary", summary_seconds=round(len(pcm) / 48000, 2))
                    await pcm_output.recap(pcm)
                    close_pcm_output()  # Actual pipe EOF, while the Live connection remains open.
                    publish(phase="draining")
                    async with asyncio.timeout(len(pcm) / 48000 + 20):
                        while not args.playback_ack.exists():
                            await asyncio.sleep(0.1)
                    ack = json.loads(args.playback_ack.read_text(encoding="utf-8"))
                    if ack.get("returncode") != 0:
                        raise RuntimeError("Audio player failed")
                    async with backend.ledger_lock:
                        ledger.data["playback"] = "aplay_drained"
                    publish(phase="closing", audio_playing=False)
                except Exception as exc:
                    async with backend.ledger_lock:
                        ledger.data["playback"] = "unconfirmed"
                    publish(phase="error", error="Summary playback unconfirmed; saved recap is available")
                    print(f"Summary failed ({type(exc).__name__}); saved recap retained", file=sys.stderr)
                finally:
                    async with backend.ledger_lock:
                        await off_thread(ledger.save)
                    stopping = True
                    await connection.session.close()
                    try:
                        await asyncio.wait_for(closed.wait(), 15)
                    except TimeoutError:
                        await connection.close()

            await connection.session.start(session={"model": "gpt-live-1", "instructions": PROMPT,
                "audio": {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "marin"}},
                "delegation": {"type": "responses", "responses": {"model": backend_model,
                    "instructions": BACKEND_PROMPT, "tools": TOOLS, "parallel_tool_calls": False}}})
            backend_worker = SerialWorker(handle_backend)
            sender = asyncio.create_task(send_audio())
            finisher = asyncio.create_task(finish())
            tasks = [sender, finisher, asyncio.create_task(sync_food_reports()), asyncio.create_task(activity_cues()), asyncio.create_task(greet()), backend_worker.task]
            supervised = tasks + [pcm_output.worker.task, screen.task]
            # Background failure must not leave a charged, silent session open.
            def task_done(task):
                if not task.cancelled() and task.exception():
                    asyncio.create_task(connection.close())
            for task in supervised:
                task.add_done_callback(task_done)
            loop.add_signal_handler(signal.SIGINT, finish_requested.set)
            loop.add_signal_handler(signal.SIGUSR1, finish_requested.set)
            try:
                async with asyncio.timeout(args.max_seconds + 120):
                    async for event in connection:
                        kind = event.type
                        if kind == "session.started":
                            if ready.is_set():
                                continue  # Duplicate startup must not replay the opening or timer.
                            ready.set()  # Release continuous microphone/silence PCM immediately.
                            publish(phase="connecting", greeting="requested")
                            loop.add_reader(sys.stdin.fileno(), read_audio)
                            reader_added = True
                            loop.call_later(args.max_seconds, finish_requested.set)
                            print("Coach connected; requesting opening greeting.", file=sys.stderr, flush=True)
                        elif greeting.handle(event):
                            publish(greeting="accepted")
                            if screen.state["phase"] == "connecting":
                                publish(phase="listening")
                            print("Opening instructions accepted; A ends the check-in.", file=sys.stderr, flush=True)
                        elif kind == "response.event":
                            if event.event["type"] in {"response.created", "response.output_item.done",
                                    "response.completed", "response.failed", "response.incomplete", "response.cancelled"}:
                                if event.event["type"] == "response.created":
                                    publish(backend_busy=True)
                                backend_worker.submit(event.event)
                        elif kind == "session.delegation.created":
                            backend.active = True
                            if not ending:
                                publish(backend_busy=True)
                        elif kind == "session.output_audio.delta" and not ending:
                            audio = base64.b64decode(event.delta)
                            output_bytes += len(audio)
                            gap = metrics.interval("output_receive_gap")
                            if gap > 300:
                                gaps = metrics.values.setdefault("output_gap_events", [])
                                gaps.append({"at_seconds": round(time.monotonic() - metrics.last.get("greeting_requested", time.monotonic()), 2),
                                             "gap_ms": round(gap, 2), "backend_busy": backend_busy(),
                                             "queued_pcm_ms": round(pcm_output.buffered / 48, 2),
                                             "recent_loop_lag_ms": metrics.values.get("event_loop_lag_latest_ms", 0)})
                                del gaps[:-20]
                            metrics.count("output_chunks")
                            if backend_busy():
                                metrics.count("output_chunks_during_backend")
                            if pcm_output.offer(audio):
                                metrics.count("output_non_silent_chunks")
                                if not metrics.values.get("input_transcript_fragments") and greeting.requested:
                                    metrics.count("opening_non_silent_chunks")
                                    if "opening_first_audio_ms" not in metrics.values:
                                        metrics.maximum("opening_first_audio_ms", (time.monotonic() - metrics.last["greeting_requested"]) * 1000)
                                if backend_busy():
                                    metrics.count("output_non_silent_chunks_during_backend")
                        elif kind in ("session.input_transcript.delta", "session.output_transcript.delta"):
                            is_input = kind == "session.input_transcript.delta"
                            if args.transcript_log and len(transcript_trace) < 5000:
                                transcript_trace.append({"speaker": "user" if is_input else "coach", "text": event.delta,
                                                         "start_ms": getattr(event, "start_ms", None), "end_ms": getattr(event, "end_ms", None)})
                            if is_input:
                                last_input = time.monotonic()
                                transcript_sync.heard(last_input)
                            if not ending:
                                publish(phase="listening" if is_input else "speaking")
                            metrics.count("input_transcript_fragments" if is_input else "output_transcript_fragments")
                            if not is_input and not metrics.values.get("input_transcript_fragments"):
                                metrics.count("opening_transcript_fragments")
                        elif kind == "session.closed":
                            closed.set()
                            async with backend.ledger_lock:
                                ledger.data["session_finalized"] = True
                                await off_thread(ledger.save)
                            publish(phase="done" if ledger.data.get("playback") == "aplay_drained" else "error", audio_playing=False)
                            print(f"SESSION_CLOSED input={input_bytes} output={output_bytes}", file=sys.stderr, flush=True)
                            break
                        elif kind in ("error", "session.error"):
                            # Error payloads can contain user data; do not dump them or keys.
                            backend.failed = True
                            backend.active = False
                            publish(backend_busy=False)
                            print(f"API_ERROR {getattr(getattr(event, 'error', None), 'code', kind)}", file=sys.stderr, flush=True)
                            if not ready.is_set():
                                raise RuntimeError("Session startup rejected")
            finally:
                if reader_added:
                    loop.remove_reader(sys.stdin.fileno())
                for sig in (signal.SIGINT, signal.SIGUSR1):
                    loop.remove_signal_handler(sig)
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        raise result
            if not closed.is_set():
                raise RuntimeError("Session finalization unconfirmed")
            if ledger.data.get("playback") != "aplay_drained":
                raise RuntimeError("Summary playback unconfirmed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    folder = LAB_DIR / ".food-coach" / uuid4().hex
    parser.add_argument("--record", type=Path, default=folder / "record.json")
    parser.add_argument("--status", type=Path, default=folder / "status.json")
    parser.add_argument("--playback-ack", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, default=180)
    parser.add_argument("--transcript-log", type=Path, help="Opt-in private transcript trace, written once after the session (synthetic diagnostics)")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as exc:
        try:
            state = json.loads(args.status.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.update(phase="error", error=type(exc).__name__, updated_at=time.time())
        atomic_json(args.status, state)
        print(f"Coach stopped: {type(exc).__name__}; saved records retained", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
