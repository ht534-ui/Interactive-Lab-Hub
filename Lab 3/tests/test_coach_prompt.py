"""Offline prompt-contract checks, not evidence of real-model behavior."""
import ast
from pathlib import Path
import runpy
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "speech-scripts"


def prompt_constant(filename, name):
    """Read only the shipped constant; avoid SDK, credentials, audio and GPIO."""
    tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing {name} in {filename}")


class PromptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.voice = prompt_constant("roast_master_live.py", "PROMPT")
        # food_coach is dependency-free; evaluate its final prompt so a
        # shared rubric suffix is checked too, without loading the live SDK.
        cls.backend = runpy.run_path(str(SCRIPTS / "food_coach.py"))["BACKEND_PROMPT"]

    def test_storyboard_foods_are_not_seeded_into_either_model(self):
        # These exact anchors caused the observed unsupported callback. They
        # belong in demo inputs, never in either model's standing instructions.
        for name, prompt in (("voice", self.voice), ("backend", self.backend)):
            with self.subTest(prompt=name):
                self.assertNotRegex(prompt.lower(), r"broccoli|cake|fried chicken|西兰花|蛋糕|炸鸡")

    def test_voice_has_no_sample_dialogue_to_mistake_for_history(self):
        self.assertNotRegex(self.voice, r"(?im)^\s*(?:user|assistant|orange)\s*:")
        self.assertNotRegex(self.voice, r"[\u4e00-\u9fff]")
        self.assertRegex(self.voice, r"Always speak English")
        self.assertRegex(self.backend, r"always speaks English")

    def test_grounding_and_correction_contract_covers_both_roles(self):
        for name, prompt in (("voice", self.voice), ("backend", self.backend)):
            with self.subTest(prompt=name):
                self.assertIn("Start with no assumed foods", prompt)
                # Reject stripping the grounding boundary while keeping only
                # generic "be truthful" wording. These are distinct sources of
                # false food memory in a playful, interruptible conversation.
                for concept in ("hypothetical", "quoted examples", "suggestions", "jokes", "retract", "correct"):
                    self.assertIn(concept, prompt.lower())
        self.assertRegex(self.voice, r"correction takes precedence over an older report")
        self.assertRegex(self.voice, r"Drop callbacks to a retracted or corrected claim")
        self.assertRegex(self.backend, r"retractions remove the existing entry")

    def test_de_scripting_preserves_both_sides_of_the_character(self):
        # A fix that only deletes all persona guidance would avoid the seed
        # foods but fail the intended interaction.
        self.assertRegex(self.voice, r"supportive AND judgemental")
        self.assertRegex(self.voice, r"sincere, specific")
        self.assertRegex(self.voice, r"roast it hard")
        self.assertRegex(self.voice, r"no required praise-to-roast progression")
        self.assertRegex(self.voice, r"never the user's body.*personality.*worth")
        self.assertRegex(self.voice, r"stop roasting when asked")
        self.assertRegex(self.voice, r"If the user interrupts, stop and listen")

    def test_background_updates_are_facts_not_spoken_receipts(self):
        self.assertRegex(self.voice, r"Backend updates are factual context, not lines to read aloud")
        self.assertRegex(self.backend, r"do not compose a spoken clarification")
        self.assertRegex(self.backend, r"Never make up scores or calories")
        self.assertNotRegex(self.backend, r"(?i)ask ONE short clarification")

    def test_live_session_uses_the_audited_constants(self):
        # Ensure edits affect the configured live session, not dead constants.
        tree = ast.parse((SCRIPTS / "roast_master_live.py").read_text(encoding="utf-8"))
        configured = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "start"):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Dict):
                    for key, value in zip(child.keys, child.values):
                        if isinstance(key, ast.Constant) and key.value == "instructions" and isinstance(value, ast.Name):
                            configured.add(value.id)
        self.assertTrue({"PROMPT", "BACKEND_PROMPT"} <= configured)


if __name__ == "__main__":
    unittest.main()
