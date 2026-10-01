# Food coach scoring contract

`menu-game-v1` is a fictional menu game for the interaction prototype. It is not a nutrition, calorie, weight, or health assessment. These are product rules, not evidence about whether a meal is healthy. The existing implementation already used fixed arithmetic; this iteration makes its inputs and calculation easier to audit and closes several duplicate-entry paths.

## One executable definition

[`speech-scripts/food_coach.py`](speech-scripts/food_coach.py) owns the version, weights, base, caps, and score calculation. `rubric_definition()` returns that definition as JSON data; `rubric_instructions()` exposes the same data for the backend prompt. The tool schema's categories come from the same weight mapping. Neither model has an argument for submitting a score or changing the rubric; the execution boundary also rejects undeclared arguments.

| Category | Points per entry with a stated portion |
| --- | ---: |
| vegetable | +10 |
| fruit | +10 |
| protein | +5 |
| staple | +5 |
| dessert | −5 |
| fried | −10 |
| other | 0 |

For each category, add its eligible entries' points and cap the category total to −20…+20. Add the category totals to 50, then clamp the result to 0…100. Return `null` if no entry has a stated portion. A known portion in `other` contributes zero but makes the score 50 available. Unknown portions stay visible and contribute nothing; stating or changing a quantity does not multiply the points. One bowl and two bowls remain one entry unless the user explicitly reports a separate additional serving.

For example, broccoli with a portion, cake with a portion, and fried chicken with a portion produce 50 + 10 − 5 − 10 = **45**. A portion clarification changes the existing entry, not the entry count. Category caps apply to the group, so the application does not assign the cap to whichever entry arrived last. Reordering otherwise identical entries cannot change the score. With this rubric the lowest reachable score is 10 (both negative categories at −20); the lower clamp is a guard rather than a promise that zero is attainable.

## What is saved and auditable

The snapshot retains the existing `rubric`, `score`, `base`, `category_points`, `entries`, and `pending_portions` fields. It additionally includes:

- `rubric_definition`: version and complete numeric policy.
- `score_breakdown.entries`: each stable ID's saved category, eligibility, raw points, and reason.
- `score_breakdown.category_raw_points` and `category_cap_adjustments`: category totals before the cap and the cap's effect.
- `score_breakdown.before_final_clamp` and `final_clamp_adjustment`: the last arithmetic step; both are `null` when no score exists.
- `removed_ids`: IDs that have been removed and cannot be reused by a later new call.

Raw entry points sum into category totals; category adjustments produce `category_points`; the base plus those totals and the final clamp adjustment reproduce the saved score. The breakdown and definition are saved with each successful tool response and current snapshot. Older `menu-game-v1` records retain their entries and scores; loading them adds the new snapshot fields. Historical cached call results are returned as originally stored and may lack the new fields. An unsupported saved version fails explicitly rather than silently using the current rubric.

## Identity, corrections, and replay

A repeated call ID with identical arguments returns its original result. Reusing a call ID with changed arguments is rejected. A repeated add under an existing entry ID is idempotent if the stored entry matches; changed data needs `correct`. Replay after a removal returns the historical result without restoring the entry; a fresh `get_food_log` call obtains current state. Removed IDs remain reserved after restart.

New IDs cannot evade duplicate detection merely by changing the same food's portion, category, capitalization, Unicode width, or spacing. Identity comparison uses Unicode NFKC normalization, case folding, and collapsed whitespace. It does not erase meaningful punctuation or guess synonyms. An explicitly additional serving needs `separate_serving=true` and retains the saved category for the same food description. A different preparation should have a distinct description. If a rename would collide with another entry, retain and correct the intended entry and remove the redundant one; use the distinct-serving flag only when two servings really were reported.

`correct` still permits a real food/category correction, including correcting a classification for the same name. The backend must retain the saved category during routine rereading and portion clarification. Correction and removal immediately recompute the score; freezing the record blocks later writes. The recap placeholder interface is unchanged.

## What this does not establish

Deterministic arithmetic means **the same saved entries, portions-known status, and categories produce the same score**. It does not mean arbitrary spoken versions of the same meal will always produce identical records. Speech transcription, initial category selection, food-name paraphrases, recognizing a user's actual correction, and deciding whether a serving is additional still depend on model interpretation. The `separate_serving` flag is the model's assertion, not independent evidence of user consent or quantity. A fresh session may classify an ambiguous dish differently; no dictionary of a few food keywords could prove otherwise.

The backend uses one category per reported dish, preserves uncertainty as `other`, and must not manufacture ingredient entries. Its portion check is a syntax guard for an explicit amount and unit, not a verified food measurement or a complete multilingual quantity parser. No food database or nutrient measurement is involved. Tests submit explicit categories and flags and therefore verify ledger behavior, not recognition accuracy or live model compliance.

## Offline verification

```bash
python3 -m unittest discover -s 'Lab 3/tests' -p 'test_coach_rubric.py' -v
python3 -m unittest discover -s 'Lab 3/tests' -p 'test_food_coach.py' -v
```

The rubric tests cover all seven weights, six orderings of a three-food check-in, restart/replay, portion scaling invariance, unknown portions, category caps, final clamp, the entry limit, explicit category correction, duplicate normalization, merge handling, removed-ID reuse, model-supplied scores, result isolation, and legacy-record/recap compatibility. These checks do not call a paid API or operate Orange hardware.
