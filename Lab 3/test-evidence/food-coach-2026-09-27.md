# Food coach verification — 2026-09-27

This note separates application assertions, live-service observations and human feedback. The test is a developer check with the author; it does not satisfy the assignment's two-participant usability study.

## Environment and scope

- Mac source checkout: Hub `Fall2026`, initially clean, baseline `2b6b37420ec0c92f282d0fd8f4f15cf5af190f16`.
- Fresh SSH verified hostname `Orange`, user `pi`, and the configured Tailscale route with strict host-key checking.
- Orange testing used `/home/pi/food-coach-part2.8OiVJ8`, preserving the device checkout's existing `.gitignore`, `Lab 2/screen_clock.py` changes and untracked Lab 3 files.
- Existing environments: repository-root hardware venv and Lab 3 voice venv, OpenAI SDK **3.19.1**. No course-image environment or authentication settings were changed.
- Credential contents were not logged, copied into source, or committed. Runtime records and raw diagnostic logs remain private in the ignored `.food-coach` directory / isolated device test directory.

## Automated checks

**21 targeted tests pass on both Mac and Orange.** They cover saved scores, empty and unknown-portion records, corrections, deletion, category caps, call-ID replay/conflict, semantic duplicate guarding, frozen records, private file permissions, nested Responses tool continuation, backend failures, repeated end presses and playback acknowledgment.

A pipe-level regression test verifies PCM EOF arrives while the client process is still alive. Python compilation, shell syntax and `git diff --check` also pass.

Two integration issues were found and repaired:

1. Orange's default text encoding rejected Chinese record writes. Application JSON reads/writes now explicitly use UTF-8.
2. Closing Python's standard output wrapper did not necessarily close file descriptor 1. The client now explicitly closes the PCM descriptor, allowing the player to drain before the Live connection closes.

The SDK also exposes the nested Live Responses event as a dictionary; the bridge dispatches it directly rather than assuming a Pydantic model.

## Live API and audio tests

A synthetic Chinese-speech run through the actual GPT-Live endpoint recorded one bowl of broccoli and a cake with an unknown portion. The portion update to half a slice modified the same cake entry. Adding two pieces of fried chicken yielded three entries and the expected score **45**. Final reconciliation and `session.closed` were observed. This run exposed the PCM EOF issue, so its original playback state correctly remained `unconfirmed`.

After repairing PCM EOF, a separate empty-check-in test using actual ALSA `default` playback passed: no foods, no invented score, `reconciled=true`, `playback=aplay_drained`, `session_finalized=true`, and client exit 0. This is synthetic/API testing, not participant feedback.

## Screen

The controller initialized the real Mini PiTFT and GPIO23, rendered the ready screen, exited after a bounded four-second run and restored `piscreen.service` to active. The generated renderer preview was inspected for cropping and label legibility:

![Generated screen renderer frames, not device photographs](food-coach-render-preview.png)

The face and neutral state cue are separate. No standalone LED, camera recognition, exact one-second comedic beat or phoneme-synchronized animation is claimed.

## Physical check-in

The author was asked to press A, report foods and portions, then press A again for the saved recap. Live device logs observed the real GPIO23 presses, microphone transcript events and food-tool updates. The reported foods were one bowl of broccoli, one large piece of cake, and two pieces of fried chicken. The cake began as a pending-portion record and was updated in place; the computed score was **45**.

The final private record independently showed `reconciled=true`, `playback=aplay_drained`, and `session_finalized=true`; the client exited 0. The controller then exited and `piscreen.service` was independently verified active. All seven deployed application-file SHA-256 values matched the Mac sources at the end of this test. The device checkout retained its original changes.

**Author feedback:** the author said the interaction had ended, but felt too mechanical and did not feel like a conversation; they wanted a coach with a stronger sense of a living personality. This feedback is a design failure of the first implementation, despite the functioning data/audio loop. The author did not separately confirm every screen label or the exact spoken score. It is not evidence from a second participant.

The next iteration keeps the authoritative tools and playback handshake, reduces bookkeeping narration and redundant ledger reads, and changes the voice prompt and recap delivery. Its validation is recorded separately below.


## Personality iteration

The author clarified that both supportive and judgmental reactions had disappeared. They wanted a sharper tongue, genuine encouragement, and much stronger roasts. The revised live prompt now asks for distinct emotional reactions, specific encouragement, callbacks to earlier foods, and sharper menu-directed jokes without body/personal insults. Backend updates no longer instruct the voice actor to announce bookkeeping, and known record IDs are reused to avoid redundant reads. The finite summary is shorter and its TTS instructions request conversational, expressive delivery.

All 21 tests still pass on Mac and Orange after these changes. A second physical listening trial uses `personality-test.log` in the same isolated deployment. A stronger prompt is not itself evidence that the author finds the character convincing.

**Second-trial feedback:** the author said the character now felt good, but the closing still sounded mechanical, especially formal closing statements and score evaluation wording. They requested a humorous sign-off and confirmed that food logging had been missed during the conversation. The record was completed during final reconciliation; that does not satisfy continuous scoring. The author reported broccoli, a large piece of chocolate cake and steak in this trial. Logs again showed client exit 0 and session finalization; the boot display was restored afterward.

**Application changes after that feedback:** a transcript-activity scheduler now starts background bookkeeping after a short pause, independently of whether GPT-Live volunteers a delegation. The backend receives a caution that a pause may be mid-sentence, uses stable IDs, and reconciles later corrections. The final button still performs a separate reconciliation. A new application check requires an explicit amount/unit for a known portion; “large steak” alone cannot count as a confirmed quantity. The sign-off uses saved food names, the computed number and a short category-dependent joke, with no formal “session closing” or “menu game score” announcement. The rubric remains disclosed in the report and run guide.

The regression suite is now **23 tests**, adding the background scheduling boundary and rejection of size-only portions. The live smoke test now checks each record snapshot **before microphone EOF**, so end-of-session reconciliation can no longer hide missed ongoing updates.


**Ongoing-scoring regression passed:** the updated real-API smoke run saved two entries with one pending portion and score **60**, corrected the existing cake entry to half a slice for **55**, then added fried chicken for **45**. Each assertion ran while the ledger was still open, before EOF. The humorous closing retained all three food names and 45. Final state: `reconciled=true`, `playback=aplay_drained`, `session_finalized=true`, client exit 0. This run used ALSA `null`, so it verifies the API/pipe path, not a new speaker listening test.

## First-utterance language lock

The user subsequently requested that the first utterance determine the language for the rest of the interaction. The app saves that utterance and a detected language code, pins the Live voice to it, and localizes the humorous ending into the same language. Placeholder checks require every food and computed numeric value to survive localization; numeric literals supplied by the translator are rejected. Later code-switching does not reset the language. The regression suite now has **26 tests**.

**English-lock live regression passed:** synthetic speech opened in English, corrected the cake portion in Chinese, then added fried chicken in English. The saved language stayed `en`; all observed Live coach transcript output remained English. Open-record checkpoints again showed **60 → 55 → 45**, with the same cake ID corrected in place. The localized recap kept the three food names and **45**, ending: “Meeting adjourned—next time, give them some reliable teammates!” Playback to ALSA `null` drained successfully, then the session finalized and the client exited 0. This verifies language behavior in API output, not a new human listening trial. Other languages have not been tested end-to-end.

A Python-version difference in Unicode numeric classification was caught on Orange: “两” is now explicitly recognized by the portion validator. After the fix, **all 26 tests pass on both machines**. The main Orange checkout is unchanged, the isolated deployment remains available, and `piscreen.service` is active. No camera, separate LED, daily aggregation, two-participant study or publication is claimed.

## Fixed English output (supersedes first-utterance selection)

The user subsequently changed the requirement to fixed English. Every new record and UI status now starts with `language=en`; the first-utterance classifier and its extra model request have been removed. Live dialogue, clarification questions and the humorous recap are instructed to stay English even after Chinese input. The backend stores English food names so the recap does not insert Chinese labels into otherwise English speech. Stated portions and application-owned scoring remain unchanged.

All 26 targeted tests pass on Mac and Orange after this change. The earlier first-utterance-lock results above describe the preceding version, not the current language-selection behavior.

**Fixed-English live check passed:** all three synthetic user turns were Chinese, including the opening. The observed coach transcript remained English, the saved language was `en`, and the backend stored broccoli, cake and fried chicken in English. Open-record scores were **60 → 55 → 45**. The final recap was English, retained the three foods and 45, and ended with a humorous sign-off. ALSA `null` playback drained, the session finalized, and the client exited 0. Deployed application hashes matched the Mac files; `piscreen.service` remained active. This was an API/pipe test, not another human listening trial.
