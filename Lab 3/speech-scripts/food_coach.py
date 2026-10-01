"""Application-owned check-in ledger. No model, audio or GPIO dependencies."""
import copy
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from types import MappingProxyType

# This is the sole executable definition of the fictional scoring rules.
RUBRIC_VERSION = "menu-game-v1"
RUBRIC = MappingProxyType({"vegetable": 10, "fruit": 10, "protein": 5, "staple": 5,
                         "dessert": -5, "fried": -10, "other": 0})
BASE_SCORE = 50
CATEGORY_CAP = 20
SCORE_MIN, SCORE_MAX = 0, 100
SCORE_SCOPE = "this check-in only; fictional menu game, not nutrition or calories"


def rubric_definition():
    """JSON-safe rule metadata; callers cannot mutate the scoring constants."""
    return {"version": RUBRIC_VERSION, "base": BASE_SCORE, "points_per_entry": dict(RUBRIC),
            "category_cap": CATEGORY_CAP, "score_min": SCORE_MIN, "score_max": SCORE_MAX,
            "portion_policy": "unknown is excluded; stated amount never scales points",
            "empty_policy": "no score until at least one portion is stated", "scope": SCORE_SCOPE}


def rubric_instructions():
    """Append to the backend prompt so numeric rules never need a second copy."""
    return "Authoritative application scoring rubric: " + json.dumps(rubric_definition(), sort_keys=True)


def food_identity(food):
    # Formatting normalization, deliberately not a guessed food synonym database.
    return " ".join(unicodedata.normalize("NFKC", food).casefold().split())


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {
            "rubric": RUBRIC_VERSION, "entries": {}, "calls": {}, "closed": False}
        if self.data["rubric"] != RUBRIC_VERSION:
            raise ValueError("Unsupported saved rubric")
        self.data.setdefault("removed_ids", [])
        self.save()

    def save(self):
        self.data["snapshot"] = self.snapshot()
        atomic_json(self.path, self.data)

    def snapshot(self):
        entries = list(self.data["entries"].values())
        known = [e for e in entries if e["portion"] is not None]
        totals = {category: sum(RUBRIC[category] for e in known if e["category"] == category)
                  for category in RUBRIC}
        contributions = {k: max(-CATEGORY_CAP, min(CATEGORY_CAP, v)) for k, v in totals.items()}
        before_clamp = BASE_SCORE + sum(contributions.values()) if known else None
        score = max(SCORE_MIN, min(SCORE_MAX, before_clamp)) if known else None
        breakdown = {
            "entries": [{"entry_id": e["entry_id"], "category": e["category"],
                         "counted": e["portion"] is not None,
                         "raw_points": RUBRIC[e["category"]] if e["portion"] is not None else 0,
                         "reason": "stated portion" if e["portion"] is not None else "portion pending"}
                        for e in entries],
            "category_raw_points": totals,
            "category_cap_adjustments": {k: contributions[k] - totals[k] for k in RUBRIC},
            "before_final_clamp": before_clamp,
            "final_clamp_adjustment": score - before_clamp if known else None,
        }
        return {"rubric": RUBRIC_VERSION, "score": score, "base": BASE_SCORE,
                "category_points": contributions, "entries": copy.deepcopy(entries),
                "pending_portions": sum(e["portion"] is None for e in entries),
                "rubric_definition": rubric_definition(), "score_breakdown": breakdown,
                "removed_ids": list(self.data.get("removed_ids", [])), "scope": SCORE_SCOPE}

    def execute(self, call_id, name, args):
        if not isinstance(call_id, str) or not call_id.strip():
            return {"ok": False, "error": "call_id must be a nonempty string"}
        try:
            fingerprint = json.dumps([name, args], sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            return {"ok": False, "error": "arguments must be JSON values"}
        previous = self.data["calls"].get(call_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                return {"ok": False, "error": "call_id reused with different arguments"}
            return copy.deepcopy(previous["result"])
        before = copy.deepcopy(self.data)
        try:
            if name == "get_food_log":
                if args != {}:
                    raise ValueError("get_food_log takes an empty object")
                result = {"ok": True, **self.snapshot()}
            elif self.data["closed"]:
                result = {"ok": False, "error": "check-in is frozen"}
            elif name == "log_food":
                result = self._log(args)
            else:
                result = {"ok": False, "error": "unknown tool"}
            self.data["calls"][call_id] = {"fingerprint": fingerprint, "result": result}
            self.save()
            return copy.deepcopy(result)
        except (ValueError, TypeError, KeyError) as exc:
            self.data = before
            return {"ok": False, "error": str(exc)}
        except BaseException:
            self.data = before
            raise

    def _log(self, args):
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        allowed = {"entry_id", "action", "food", "portion", "category", "separate_serving"}
        if set(args) - allowed:
            raise ValueError("Unknown arguments; score and rubric are application-owned")
        if "separate_serving" in args and type(args["separate_serving"]) is not bool:
            raise ValueError("separate_serving must be boolean")
        entry_id = args.get("entry_id")
        action = args.get("action")
        if not isinstance(entry_id, str) or (
                entry_id not in self.data["entries"]
                and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,49}", entry_id)):
            raise ValueError("entry_id must be a stable short ID")
        entries = self.data["entries"]
        if action == "remove":
            if entry_id not in entries:
                raise ValueError("Unknown entry_id; inspect get_food_log first")
            del entries[entry_id]
            self.data["removed_ids"].append(entry_id)
        elif action in ("add", "correct"):
            food, portion, category = args.get("food"), args.get("portion"), args.get("category")
            if not isinstance(food, str) or not food.strip() or len(food) > 40:
                raise ValueError("food must have 1-40 characters")
            if portion is not None and (not isinstance(portion, str) or not portion.strip() or len(portion) > 30):
                raise ValueError("portion must be null (unknown) or 1-30 characters stated by user")
            if portion is not None:
                words = r"\b(?:a|an|half|one|two|three|four|five|six|seven|eight|nine|ten)\b"
                chinese_numbers = "零〇一二三四五六七八九十百千万两半"
                has_amount = any(c.isnumeric() or c in chinese_numbers for c in portion) or re.search(words, portion, re.I)
                remainder = re.sub(words, "", portion, flags=re.I)
                has_unit = any(c.isalpha() and not c.isnumeric() and c not in chinese_numbers for c in remainder)
                if not has_amount or not has_unit:
                    raise ValueError("Portion needs an explicit amount and unit; size alone is unknown. Use portion=null, never invent a quantity. Explicit number words may be normalized to digits in the user's language.")
            if not isinstance(category, str) or category not in RUBRIC:
                raise ValueError("Unknown category")
            entry = {"entry_id": entry_id, "food": food.strip(), "portion": portion.strip() if portion else None,
                     "category": category}
            if action == "correct" and entry_id not in entries:
                raise ValueError("Unknown correction target; inspect get_food_log first")
            if action == "add" and entry_id in self.data["removed_ids"]:
                raise ValueError("Removed entry_id cannot be reused; an explicitly new serving needs a new ID")
            if action == "add" and entry_id in entries:
                if entries[entry_id] != entry:
                    raise ValueError("Existing ID: use correct, not add")
                return {"ok": True, "duplicate": True, **self.snapshot()}
            same_food = [e for e in entries.values() if e["entry_id"] != entry_id
                         and food_identity(e["food"]) == food_identity(food)]
            if action == "add" and same_food:
                if args.get("separate_serving") is not True:
                    raise ValueError("Possible duplicate food; correct its existing ID for portion clarification or changed classification. Only an explicitly additional serving uses separate_serving=true.")
                if any(e["category"] != category for e in same_food):
                    raise ValueError("Same food has a saved category; retain it for an additional serving. A different preparation needs a distinct food description.")
            if action == "correct" and same_food and food_identity(entries[entry_id]["food"]) != food_identity(food):
                if not args.get("separate_serving", False):
                    raise ValueError("Correction would duplicate another food; retain and correct the intended ID, then remove the redundant ID. Use separate_serving only for an explicitly distinct serving.")
            if action == "add" and len(entries) >= 12:
                raise ValueError("Demo check-in limit: 12 entries")
            entries[entry_id] = entry
        else:
            raise ValueError("action must be add, correct or remove")
        return {"ok": True, **self.snapshot()}

    def freeze(self, reconciled):
        self.data["closed"] = True
        self.data["reconciled"] = reconciled
        self.data["summary"] = self.summary(reconciled)
        self.save()
        return self.data["summary"]

    def summary_template(self, reconciled=True):
        state = self.snapshot()
        values = {f"[[FOOD_{i}]]": e["food"] for i, e in enumerate(state["entries"])}
        if state["score"] is not None:
            values["[[SCORE]]"] = state["score"]
        if state["pending_portions"]:
            values["[[PENDING]]"] = state["pending_portions"]
        return self.summary(reconciled, placeholders=True), values

    def summary(self, reconciled=True, placeholders=False):
        state = self.snapshot()
        foods = "；".join(e["food"] + "，" + (e["portion"] or "份量未确认") for e in state["entries"])
        names = [f"[[FOOD_{i}]]" if placeholders else e["food"] for i, e in enumerate(state["entries"])]
        text = ("、".join(names) + "——" if foods else "这次没有保存食物，")
        if state["score"] is None:
            text += "份量还没说清，分数先欠着。"
        else:
            text += ("[[SCORE]]" if placeholders else str(state["score"])) + "分。"
        if state["pending_portions"]:
            text += "还有" + ("[[PENDING]]" if placeholders else str(state["pending_portions"])) + "项没说份量，未计分。"
        if not reconciled:
            text += "最后那段没核对完，这里只算已保存的。"
        categories = {e["category"] for e in state["entries"]}
        produce = "蔬菜" if "vegetable" in categories else "水果"
        indulgence = "甜点和炸物" if {"dessert", "fried"} <= categories else "甜点" if "dessert" in categories else "炸物"
        if {"vegetable", "dessert", "fried"} <= categories:
            text += "蔬菜本来想拯救全场，结果被甜点和炸物架空了。散会，下次给它配点靠谱的队友！"
        elif categories & {"vegetable", "fruit"} and not categories & {"dessert", "fried"}:
            text += f"这回我先给你鼓掌，{produce}确实有戏份。继续保持，咱们下顿见！"
        elif categories & {"vegetable", "fruit"} and categories & {"dessert", "fried"}:
            text += f"{produce}刚想当主角，{indulgence}就带资进组了。行，今天先不拍续集，咱们下顿见！"
        elif categories & {"dessert", "fried"}:
            text += "这菜单的快乐部门经费真足。今天先到这儿，下次争取给其他部门也拨点款！"
        elif categories:
            text += "菜单我收下了，今天先放你下班。下顿见，别给我憋个大反转！"
        else:
            text += "看来今天是来试音的。下次带着菜单来，我的嘴可不白开工！"
        return text


TOOLS = [{"type": "function", "name": "get_food_log", "description": "Read authoritative saved entries, IDs, removed IDs, rubric and score calculation before editing.",
          "parameters": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": True},
         {"type": "function", "name": "log_food", "description": "Save one user-reported food, correct by existing ID, or remove. Never guess portions or scores. Retries and portion clarifications reuse IDs. Retain the saved category unless the user explicitly corrects it. Removed IDs cannot be reused. No calorie estimates.",
          "strict": True, "parameters": {"type": "object", "additionalProperties": False,
          "properties": {"entry_id": {"type": "string", "description": "Reuse the exact saved ID for corrections. New IDs use 1-50 ASCII letters, digits, underscores or hyphens, starting with a letter or digit."}, "action": {"type": "string", "enum": ["add", "correct", "remove"]},
                         "food": {"type": "string"}, "portion": {"type": ["string", "null"], "description": "Preserve the complete stated amount and unit or counted food noun, not a bare number. A stated count of a whole food is a known portion. Null only when the amount is truly unstated."},
                         "category": {"type": "string", "enum": list(RUBRIC)},
                         "separate_serving": {"type": "boolean", "description": "True only for a distinct additional serving explicitly reported by the user, never a repeated mention or portion clarification."}},
          "required": ["entry_id", "action", "food", "portion", "category", "separate_serving"]}}]

BACKEND_PROMPT = """You maintain the authoritative record for a spoken food check-in. The coach always speaks English. Store food names in English, faithfully translating user reports when necessary. Preserve the user's stated portions; explicitly stated number words may be normalized to digits, but never infer a quantity.

Only the user's actual reports of eating in this check-in justify entries. Start with no assumed foods. Never log hypothetical food, quoted examples, assistant suggestions or jokes as eaten. Never complete a familiar food sequence or add ingredients that were not reported. If the user retracts or corrects a report, reconcile to the latest account rather than retaining an old premise from the conversation.

Read get_food_log at the start, when an entry ID is uncertain, and during final reconciliation. Otherwise reuse authoritative IDs and records from tool results; do not reread the full log before every add. Log every reported food using log_food, including unknown portions with portion=null. Flag an unresolved portion for the voice actor; do not compose a spoken clarification yourself. An explicitly stated amount and unit is enough for this fictional game. A stated count of a whole food also qualifies: its food noun is the unit. Preserve that count together with the food noun in portion; do not send a bare number or discard an explicitly stated count as unknown. If a tool rejects a bare number, repair the representation using the actual report rather than changing a known amount to null. This is not calorie counting.

Categories: vegetable, fruit, protein, staple, dessert, fried, other. Classify only from the actual report; use fried over protein when frying is established, and dessert when the reported dish is a dessert. Mixed or unclear dishes use other; never invent ingredient entries or preparation details. Preserve a saved category on routine rereads and portion-only corrections. Change it only when the user actually corrects the food or its classification, not because you reconsider an unchanged report. Keep stable entry IDs. Corrections replace the original ID, not a new add; retractions remove the existing entry. Repeated mentions and recaps are not additional meals. Use separate_serving=true only when the user explicitly reports an additional serving. On final reconciliation inspect for unlogged reports and corrections, apply them once, and finish.

Tool errors mean not saved. Return a SHORT factual update for the voice actor: what changed, whether it saved, and any unresolved portion. Do not write spoken acknowledgments, jokes, recaps, or instructions to announce saving or corrections. The voice actor should converse naturally while you maintain the record. Include the authoritative score only when the user asks or at final reconciliation. Never make up scores or calories; never turn routine bookkeeping into dialogue. Humor belongs to the voice actor and targets the menu, never bodies or personal worth. Respect requests for a gentler tone."""

# Keep backend guidance synchronized with the executable scoring policy.
BACKEND_PROMPT += "\n" + rubric_instructions()
