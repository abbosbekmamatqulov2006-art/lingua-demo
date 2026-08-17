"""
Grading and study scheduling.

The grader's job is to separate two things that look identical to a string
comparison: a slip of the finger and the error the question was written to
catch. Forgiving "bagg" for "bag" costs nothing. Forgiving "book" for "books"
would hide a missing plural — the exact mistake the question exists to find —
even though those two strings are closer together.
"""

from __future__ import annotations

import difflib
import random
import re
import string
from datetime import date, timedelta

from .teaching import REVIEW_LADDER

# Suffixes that carry grammar in English. A difference here is never a typo.
INFLECTIONS = ("s", "es", "ed", "d", "ing", "er", "est", "ly", "n", "en")

CONTRACTIONS = {
    "do not": "don't", "does not": "doesn't", "did not": "didn't",
    "is not": "isn't", "are not": "aren't", "was not": "wasn't",
    "were not": "weren't", "have not": "haven't", "has not": "hasn't",
    "had not": "hadn't", "will not": "won't", "would not": "wouldn't",
    "should not": "shouldn't", "could not": "couldn't", "cannot": "can't",
    "can not": "can't", "must not": "mustn't", "i am": "i'm",
    "you are": "you're", "he is": "he's", "she is": "she's", "it is": "it's",
    "we are": "we're", "they are": "they're", "i have": "i've",
    "i will": "i'll", "let us": "let's",
}


def normalise(text) -> str:
    text = str(text or "").strip().lower()
    text = (text.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"'))
    text = re.sub(r"\s+", " ", text)
    return text.strip(string.punctuation + " ")


def _contract(text: str) -> str:
    """Fold expanded forms into contractions so both spellings compare equal."""
    for long_form, short in CONTRACTIONS.items():
        text = re.sub(rf"\b{re.escape(long_form)}\b", short, text)
    return text


def _is_inflection(first: str, second: str) -> bool:
    """True when two word forms differ by grammar rather than by typing."""
    short, long_ = sorted((first, second), key=len)
    if not short:
        return False
    if long_.startswith(short) and long_[len(short):] in INFLECTIONS:
        return True
    if short.endswith("y") and long_ == short[:-1] + "ies":
        return True
    if short.endswith("y") and long_ == short[:-1] + "ied":
        return True
    # Doubled consonant before a suffix: stop/stopped, run/running.
    if (len(short) >= 3 and long_.startswith(short + short[-1])
            and long_[len(short) + 1:] in INFLECTIONS):
        return True
    return False


def check_answer(question: dict, given) -> dict:
    """Grade a single answer.

    Returns {correct, note, typo}. A near miss on a free-text answer is accepted
    with a spelling note; a near miss among fixed options is simply wrong,
    because the learner was choosing, not typing.
    """
    if not str(given or "").strip():
        return {"correct": False, "note": "", "typo": False}

    submitted = _contract(normalise(given))
    valid = [_contract(normalise(question.get("answer")))]
    valid += [_contract(normalise(a)) for a in (question.get("accept") or [])]
    valid = [v for v in valid if v]

    if submitted in valid:
        return {"correct": True, "note": "", "typo": False}

    if question.get("options"):
        return {"correct": False, "note": "", "typo": False}

    for candidate in valid:
        mine, theirs = submitted.split(), candidate.split()
        if len(mine) != len(theirs):
            continue
        differing = [(a, b) for a, b in zip(mine, theirs) if a != b]
        if len(differing) != 1:
            continue
        wrote, expected = differing[0]
        if _is_inflection(wrote, expected):
            continue  # this is the mistake under test, not a typo
        if difflib.SequenceMatcher(None, wrote, expected).ratio() >= 0.7:
            return {"correct": True, "typo": True,
                    "note": f"{wrote} → {expected}"}

    return {"correct": False, "note": "", "typo": False}


def grade(questions: list[dict], answers: dict) -> dict:
    """Grade a whole set and report which skills held up."""
    results, by_skill = [], {}
    for index, question in enumerate(questions):
        verdict = check_answer(question, answers.get(str(index), answers.get(index, "")))
        results.append(verdict)
        skill = question.get("skill") or "general"
        bucket = by_skill.setdefault(skill, {"seen": 0, "wrong": 0})
        bucket["seen"] += 1
        if not verdict["correct"]:
            bucket["wrong"] += 1

    correct = sum(1 for r in results if r["correct"])
    return {
        "results": results,
        "correct": correct,
        "total": len(questions),
        "percent": round(100 * correct / len(questions)) if questions else 0,
        "by_skill": by_skill,
    }


# --------------------------------------------------------------- scheduling


def schedule(queue: list[dict], questions: list[dict], graded: dict,
             today: date | None = None) -> list[dict]:
    """Fold one session into the review queue.

    A correct answer moves an item up the ladder; a wrong one sends it back to
    the start. Items that survive the whole ladder are retired as known.
    """
    today = today or date.today()
    queue = [dict(item) for item in queue]
    position_of = {item.get("question", {}).get("prompt"): i
                   for i, item in enumerate(queue)}

    for question, verdict in zip(questions, graded["results"]):
        position = position_of.get(question.get("prompt"))

        if verdict["correct"]:
            if position is None:
                continue
            item = queue[position]
            if item.get("step", 0) >= len(REVIEW_LADDER) - 1:
                item["retired"] = True
                continue
            item["step"] = item.get("step", 0) + 1
            item["due"] = (today + timedelta(days=REVIEW_LADDER[item["step"]])).isoformat()
        elif position is None:
            queue.append({
                "question": question, "step": 0, "lapses": 1,
                "due": (today + timedelta(days=REVIEW_LADDER[0])).isoformat(),
            })
        else:
            item = queue[position]
            item["step"] = 0
            item["lapses"] = item.get("lapses", 0) + 1
            item["due"] = (today + timedelta(days=REVIEW_LADDER[0])).isoformat()

    return [item for item in queue if not item.get("retired")]


def due_now(queue: list[dict], today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [item["question"] for item in queue
            if item.get("due", "9999-01-01") <= today.isoformat()]


def weak_skills(skills: dict, minimum: int = 3, limit: int = 4) -> list[str]:
    """Skills with enough attempts to be meaningful and poor enough to target."""
    candidates = [(name, counts["wrong"] / counts["seen"])
                  for name, counts in skills.items()
                  if counts.get("seen", 0) >= minimum
                  and counts["wrong"] / counts["seen"] > 0.3]
    candidates.sort(key=lambda pair: pair[1], reverse=True)
    return [name for name, _ in candidates[:limit]]


def merge_skills(existing: dict, session: dict) -> dict:
    merged = {name: dict(counts) for name, counts in existing.items()}
    for name, counts in session.items():
        entry = merged.setdefault(name, {"seen": 0, "wrong": 0})
        entry["seen"] += counts["seen"]
        entry["wrong"] += counts["wrong"]
    return merged


def streak_after(last_day: str | None, current: int, today: date | None = None) -> tuple[int, str]:
    """Daily streak: same day holds, yesterday extends, any longer gap resets."""
    today = today or date.today()
    stamp = today.isoformat()
    if last_day == stamp:
        return current, stamp
    if last_day == (today - timedelta(days=1)).isoformat():
        return current + 1, stamp
    return 1, stamp


# --------------------------------------------------------------- vocabulary


def build_rounds(vocabulary: list[dict], seed: int | None = None) -> list[dict]:
    """Turn a word list into four-option recognition rounds.

    Distractors come from the same list so they are plausible: choosing between
    four meanings from one topic is a real test, while choosing between one
    meaning and three random ones is not.
    """
    pool = [item for item in vocabulary
            if item.get("word") and item.get("meaning_en")]
    if len(pool) < 4:
        return []

    rng = random.Random(seed)
    order = pool[:]
    rng.shuffle(order)

    rounds = []
    for item in order:
        others = [o for o in pool if o["word"] != item["word"]]
        distractors = rng.sample(others, 3)
        options = [item["meaning_en"]] + [d["meaning_en"] for d in distractors]
        rng.shuffle(options)
        rounds.append({
            "word": item["word"],
            "pos": item.get("pos"),
            "options": options,
            "answer": item["meaning_en"],
            "meaning_native": item.get("meaning_native"),
            "example": item.get("example"),
        })
    return rounds


# --------------------------------------------------------------- worksheet


def worksheet(questions: list[dict], lesson: dict) -> str:
    """Printable version with an answer key, for classroom use."""
    lines = [f"# {lesson.get('title', 'English practice')}", "",
             "Name: ______________________    Date: ____________", ""]

    if lesson.get("explanation"):
        lines += ["## Before you start", "", lesson["explanation"], "", "---", ""]

    for number, question in enumerate(questions, 1):
        lines += [f"**{number}.** {question.get('prompt', '')}", ""]
        if question.get("options"):
            lines += [f"   {letter}) {option}"
                      for letter, option in zip("ABCD", question["options"])]
        else:
            lines.append("   Answer: ______________________________")
        lines.append("")

    lines += ["---", "", "## Answer key", ""]
    for number, question in enumerate(questions, 1):
        lines.append(f"**{number}.** {question.get('answer', '')} — "
                     f"{question.get('explanation', '')}")
    return "\n".join(lines)
