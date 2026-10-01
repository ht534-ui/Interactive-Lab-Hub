# Food coach audio and concurrency verification — September 30, 2026

This is an engineering test record, not a participant test. The author reported intermittent stalls or abrupt stops, especially around food-tool updates, and could not confidently distinguish those two symptoms. No live human listening test was performed during this iteration.

## Findings and changes

The previous controller already ran microphone capture and speaker playback concurrently. The Live client also had concurrent send/receive tasks, but the sole receive loop awaited backend tools and result sends, synchronously wrote to the speaker pipe, and synchronously fsynced screen state for transcript fragments. Those operations could delay both incoming audio dispatch and outgoing microphone scheduling. This establishes a blocking path; it does not prove which operation caused each earlier audible interruption.

The new client has separate ordered workers for PCM and backend events, off-loop ledger writes, a shared ledger lock for finalization, and a coalesced off-loop status writer. Audio buffering is bounded and fails explicitly on overflow. Recap audio uses the same writer and the existing PCM EOF / `aplay` exit acknowledgment. Processing cues use that writer too. The API connection is still shared; this change isolates local work rather than claiming independent network paths.

Silent PCM is not speech. Output activity uses an RMS threshold for UI/cue decisions, with a conservative tail estimate. This is not input VAD or echo cancellation. Full-duplex conversation remains enabled until the end button intentionally stops capture. The English persona, deterministic menu-game scoring, proactive opening and new status cues were integrated from independently scoped work.

## Device observations

A fresh strict-key SSH session verified host `Orange`, user `pi`, over the configured Tailscale route. The previously running controller used `/home/pi/food-coach-part2.8OiVJ8`; its relevant source hashes matched the Mac source before this change. The active output was the USB speaker and the active input was a USB microphone, with PipeWire providing the PulseAudio-compatible service. No explicit software echo-cancellation module was listed. That observation does not establish whether the hardware provides echo cancellation.

A 100-write atomic-status benchmark on Orange measured a median of 2.92 ms, a 95th percentile of 3.00 ms and a maximum of 6.89 ms. Synchronous writes belonged off the audio loop, but these measurements alone do not explain a large audible pause. The existing interactive controller was left running during isolated null-device API tests.

## Automated evidence

- The original 26 regression tests continued to pass. The integrated suite contains 73 tests covering ledger replay, scoring, runtime failure injection, status cues, opening requests and prompt grounding.
- Injected blocked ledger writes and delayed tool-result sends did not prevent the independent audio worker from draining. A blocked PCM writer did not stop the event loop. Other tests check output order, explicit overflow failure, cancellation joining an in-flight write, status coalescing, silent-PCM activity and finite pipe EOF.
- An intermediate real API test on Orange passed all three ongoing ledger checkpoints: two foods with one pending portion at 60, a correction at 55 without an extra entry, and the third food at 45. Final reconciliation, English lock, `aplay` exit 0 and `session.closed` all passed. With the null sink, the maximum queued PCM was 100 ms, maximum PCM write was 0.64 ms, tool execution 4.22 ms and tool-result send 1.20 ms. These are measurements from this run, not performance guarantees for the USB speaker.
- The first verbose native-opening instruction was acknowledged after 1.34 seconds but produced no speech or output transcript in the first six seconds. That test failed. It was not counted as a successful greeting.
- After reducing the request to a short, explicit English greeting, the empty-check-in API test passed. The first non-silent output arrived 2.483 seconds after the request, before any user transcript. During the initial silent window, there were 15 assistant transcript fragments and 38 non-silent audio chunks, no food entries and no food-tool calls. The instruction acknowledgment alone was not used as evidence of speech.

- The integrated ordinary-menu run also passed: opening at 3.494 seconds, ongoing scores 60 / 55 / 45, no duplicated correction, final reconciliation and playback acknowledgment. It received 117 non-silent output chunks while backend work was active. Maximum tool execution was 6.71 ms, result send 1.22 ms, PCM write 0.75 ms and application PCM queue 100 ms. There were no input-drop or output-overflow events.
- That run still contained an output receive gap of 1,257.11 ms and a whole-session maximum event-loop lag of 206.15 ms. Those maxima do not establish the same instant or an audible stall. They are evidence that removing local blocking paths is not a guarantee of gap-free delivery. Subsequent diagnostics retain up to 20 long-gap events with backend and recent loop-lag context.

- The first alternative-menu run saved the explicitly stated whole-food count as pending. A regression now verifies that a counted whole food is a valid known portion, and the tool schema/backend instructions explicitly preserve the count with its food noun rather than degrading it to null after a format error. This is a representation/interpretation boundary, not randomness in the scoring arithmetic.

- The final alternative-menu run passed with apple / plain rice / ice cream and ongoing scores 60 / 65 / 60. The first non-silent opening arrived after 2.129 seconds. The saved portions were `one apple`, `one bowl` and `one scoop`; there were no tool errors, input drops or PCM overflows. The assistant transcript and recap contained none of the old storyboard food names. There were 58 non-silent output chunks during backend activity, two processing cues, a 100 ms maximum PCM queue and a 1.29 ms maximum PCM write. Its largest receive gap was 265.94 ms. This is one successful alternative-menu run, not a claim that the model cannot misinterpret other reports.

Test records and logs are private under the isolated deployment's `.food-coach` directory. The application stores no raw microphone recording. Timing/count diagnostics remain in each private status file. The smoke test feeds synthetic speech, uses the ALSA `null` sink and explicitly enables a bounded private transcript trace for the scripted-food regression. Normal controller sessions do not enable that text trace.

## Remaining verification

A human should still check actual USB-speaker continuity, microphone/speaker feedback, natural interruption, opening wording, cue volume and the physical screen. Null-device playback completion is not evidence that a listener heard the full ending. Initial food recognition, synonyms, category interpretation and the truth of an additional serving remain model judgments; the fixed rubric makes the calculation reproducible once the stored entries are established.

References: [OpenAI Live audio transport](https://developers.openai.com/api/docs/guides/voice-websockets), [Live conversations and proactive greeting](https://developers.openai.com/api/docs/guides/live-conversations), and [Responses delegation/tool continuation](https://developers.openai.com/api/docs/guides/live-delegation).

## Deployment state at handoff

The verified source was copied into `/home/pi/food-coach-audio-20260930.ezvr2P`. After confirming the old controller was idle with no microphone capture, it exited via SIGINT. The new controller started successfully, logged READY, and remained running as PID 3265 (wrapper 3258), waiting for A. `piscreen.service` was inactive while this controller owned the display, as intended. Source hashes were compared with the Mac files. No participant button press or visual/audio judgment is implied by that process verification.

The previous `/home/pi/food-coach-part2.8OiVJ8` deployment and its records were retained for rollback. The device's main course checkout and Python environments were reused without replacing their source or installing packages.
