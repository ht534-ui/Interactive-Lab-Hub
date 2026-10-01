# Food-coach prompt audit

The live persona previously named the storyboard's food sequence, told the actor to recall its first food later, and repeated those foods in sample punchlines and a portion question. Those examples were embedded in standing instructions even when the user had never reported them. This is a concrete prompt contamination risk; static inspection alone cannot establish which clause caused a particular generated sentence.

The revised `PROMPT` removes all named-food examples and scripted escalation. It keeps sincere, specific praise and strong menu roasts, with no mandatory order. Callbacks require an actual, still-valid user report in the current check-in; corrections supersede older claims and invalidate old punchlines. Suggestions, hypotheticals, quotations, and the coach's own jokes cannot become food memories. English, interruption, gentler-tone requests, and background bookkeeping remain explicit.

The revised `BACKEND_PROMPT` removes the same food examples from classification instructions, distinguishes retractions from corrections, and returns unresolved portions as facts for the actor rather than prescribing a spoken clarification. Category semantics and the scoring implementation are unchanged by this audit.

`food_coach.py` still produces a category-based closing, and `coach_language.py` translates that supplied closing rather than inventing a new one. That separate path can repeat a punchline across check-ins. Its category references are derived from saved entries; it does not seed the specific storyboard foods. Placeholder validation checks token identity and numbers, not the semantic truth of translated prose. Improving closing variety or validating generated food claims requires a separate change; changing the live persona alone cannot guarantee either outcome.

## Verification scope

`tests/test_coach_prompt.py` checks the two configured constants without importing the live SDK or accessing credentials. It protects against reintroducing the known storyboard anchors, sample dialogue, loss of grounding/correction rules, loss of either side of the persona, and accidental disconnection of the constants from session setup. These are static prompt-contract checks, not a model-quality test.

Before claiming the behavior is verified, run independent live check-ins with an unrelated single food, an uncertain portion, a correction that retracts the original food, a hypothetical food, and a request to soften. Check that the coach stays English, invents no foods or history, does not recycle invalid callbacks, still delivers distinct praise and sharp menu humor, and accepts interruption. The scripted storyboard sequence should be only one test case, not the sole evaluation input.

No paid API call, Orange deployment, or physical test was performed for this audit.
