#!/usr/bin/env python3
"""Opt-in paid API smoke test with synthetic speech; never a participant test.

Run in Lab 3's environment. --device default uses the speaker; null is silent.
Records and diagnostic logs stay in the ignored .food-coach directory.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from food_coach import atomic_json
from roast_master_live import LAB_DIR, load_key


async def main(device, empty=False, language="zh", alternative=False):
    from openai import AsyncOpenAI
    load_key()
    folder = LAB_DIR / ".food-coach" / ("smoke-" + uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    print(f"SMOKE_DIR {folder}", flush=True)
    prompts = ["我今天吃了一碗西兰花，还吃了蛋糕。", "蛋糕是半块，不是整块。", "我还吃了两块炸鸡。"]
    if language == "en":
        prompts = ["I ate one bowl of broccoli and some cake.", "蛋糕是半块，不是整块。", "I also ate two pieces of fried chicken."]
    expected_counts, expected_scores, expected_pending = [2, 2, 3], [60, 55, 45], [1, 0, 0]
    expected_categories = {"vegetable", "dessert", "fried"}
    if alternative:
        prompts = ["I ate one apple.", "I also ate one bowl of plain rice.", "And I had one scoop of ice cream."]
        expected_counts, expected_scores, expected_pending = [1, 2, 3], [60, 65, 60], [0, 0, 0]
        expected_categories = {"fruit", "staple", "dessert"}
    if empty:
        prompts = []
    async with AsyncOpenAI(timeout=30, max_retries=0) as client:
        clips = []
        for text in prompts:
            result = await client.audio.speech.create(model="gpt-4o-mini-tts", voice="cedar", input=text, response_format="pcm")
            clips.append(result.content)
    log = (folder / "client.log").open("wb")
    agent = await asyncio.create_subprocess_exec(sys.executable, "-u", str(Path(__file__).with_name("roast_master_live.py")),
        "--record", str(folder / "record.json"), "--status", str(folder / "status.json"),
        "--playback-ack", str(folder / "playback.json"), "--max-seconds", "100", "--transcript-log", str(folder / "transcripts.json"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log)
    player = await asyncio.create_subprocess_exec("aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1", stdin=asyncio.subprocess.PIPE)
    async def relay():
        while data := await agent.stdout.read(4800):
            player.stdin.write(data)
            await player.stdin.drain()
        player.stdin.close()
        code = await player.wait()
        atomic_json(folder / "playback.json", {"returncode": code})
        print(f"PLAYER_DRAINED {code}", flush=True)
    relay_task = asyncio.create_task(relay())
    async def feed(pcm):
        for pos in range(0, len(pcm), 4800):
            agent.stdin.write(pcm[pos:pos+4800])
            await agent.stdin.drain()
            await asyncio.sleep(0.1)
    try:
        async with asyncio.timeout(190):
            while not (folder / "status.json").exists() or json.loads((folder / "status.json").read_text(encoding="utf-8"))["phase"] == "connecting":
                if agent.returncode is not None:
                    raise RuntimeError("Client failed to start")
                await asyncio.sleep(0.1)
            # Verify proactive speech, not just acceptance of the opening instruction.
            await feed(bytes(48000 * 12))
            initial_status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
            diagnostics = initial_status.get("audio_diagnostics", {})
            assert initial_status.get("greeting") == "accepted", "Opening instruction was not acknowledged"
            assert diagnostics.get("opening_non_silent_chunks", 0) > 0, "Coach did not greet before user speech"
            assert diagnostics.get("opening_transcript_fragments", 0) > 0, "Opening needs assistant transcript as well as audio"
            assert not diagnostics.get("input_transcript_fragments"), "Silence was transcribed as user speech"
            assert not diagnostics.get("tool_calls"), "Greeting should not trigger food bookkeeping"
            initial_record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
            assert not initial_record["entries"], "Greeting invented a saved food"
            print("OPENING_CHECK " + json.dumps({key: value for key, value in diagnostics.items() if key.startswith(("opening_", "greeting_"))}), flush=True)
            for index, (text, clip) in enumerate(zip(prompts, clips)):
                print("SYNTHETIC_USER " + text, flush=True)
                await feed(clip)
                await feed(bytes(48000 * 14))
                checkpoint = json.loads((folder / "record.json").read_text(encoding="utf-8"))
                snapshot = checkpoint["snapshot"]
                print("ONGOING_CHECKPOINT " + json.dumps(snapshot, ensure_ascii=False), flush=True)
                assert not checkpoint["closed"], "Record should update before the end button"
                assert len(snapshot["entries"]) == expected_counts[index], "A food report was not saved during conversation"
                assert snapshot["score"] == expected_scores[index]
                assert snapshot["pending_portions"] == expected_pending[index]
            agent.stdin.close()
            await agent.wait()
            await relay_task
        record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
        entries = list(record["entries"].values())
        print(json.dumps({"exit": agent.returncode, "entries": entries, "summary": record.get("summary"), "playback": record.get("playback"), "finalized": record.get("session_finalized"), "reconciled": record.get("reconciled")}, ensure_ascii=False), flush=True)
        assert agent.returncode == 0 and record.get("session_finalized")
        assert record.get("language") == "en", "Coach output must always be English"
        print("LANGUAGE_LOCK " + record["language"], flush=True)
        assert record.get("playback") == "aplay_drained" and record.get("reconciled")
        assert len(entries) == (0 if empty else 3) and all(e["portion"] for e in entries)
        assert {e["category"] for e in entries} == (set() if empty else expected_categories)
        if alternative:
            trace = json.loads((folder / "transcripts.json").read_text(encoding="utf-8"))
            spoken = "".join(event["text"] for event in trace["events"] if event["speaker"] == "coach").casefold()
            assert spoken, "Need actual coach speech for prompt grounding check"
            assert all(food not in spoken for food in ("broccoli", "cake", "fried chicken")), "Coach reused an unreported scripted food"
            assert all(food not in record["summary"].casefold() for food in ("broccoli", "cake", "fried chicken"))
            print("ALTERNATIVE_MENU_PASS (no old scripted foods in coach transcript or recap)", flush=True)
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        print("AUDIO_DIAGNOSTICS " + json.dumps(status.get("audio_diagnostics", {})), flush=True)
        print("SMOKE_PASS (synthetic speech, not a human test)", flush=True)
    finally:
        # Preserve opt-in transcript evidence even when a checkpoint fails.
        # Give the existing ending/EOF protocol one bounded chance before killing.
        if agent.returncode is None:
            agent.stdin.close()
            try:
                await asyncio.wait_for(agent.wait(), 45)
            except TimeoutError:
                pass
        for process in (agent, player):
            if process.returncode is None:
                process.kill()
                await process.wait()
        relay_task.cancel()
        await asyncio.gather(relay_task, return_exceptions=True)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="null", help="ALSA playback device (default null)")
    parser.add_argument("--empty", action="store_true", help="Quick no-food ending test")
    parser.add_argument("--language", choices=["zh", "en"], default="zh", help="Synthetic INPUT language; coach output must always stay English")
    parser.add_argument("--alternative", action="store_true", help="Use apple, rice and ice cream; reject old scripted foods in coach output")
    args = parser.parse_args()
    asyncio.run(main(args.device, args.empty, args.language, args.alternative))
