"""
Prompt construction and validation.

Generation is treated as unreliable by design: the model writes more questions
than requested, deterministic checks reject the malformed ones, and a second
model pass rewrites or drops what survives but shouldn't. What reaches the
learner has been through all three.
"""

from __future__ import annotations

import difflib
import random
import re

from .teaching import (CEFR, EXERCISE_TYPES, LANGUAGE_NAMES, interference_for,
                       level_budget)

# --------------------------------------------------------------- schemas

LESSON_SCHEMA = """{
  "title": "short title naming what this teaches, in English",
  "kind": "grammar" | "vocabulary" | "mixed",
  "cefr": "A1" | "A2" | "B1" | "B2" | "C1",
  "summary": "one sentence on what the learner will be able to do, in {native}",
  "explanation": "a clear explanation for a learner, 150-250 words, in {native}",
  "rules": [
    {
      "point": "the rule in one line, in {native}",
      "form": "the pattern in English, e.g. subject + should + base verb",
      "examples": ["2-3 short English example sentences"],
      "note": "why this specifically confuses speakers of {native_name}, in {native}, or null"
    }
  ],
  "common_mistakes": [
    {"wrong": "the incorrect English sentence this learner group writes",
     "right": "the corrected English sentence",
     "why": "one line in {native}"}
  ],
  "vocabulary": [
    {"word": "the English word or phrase",
     "pos": "noun|verb|adj|adv|phrase",
     "meaning_en": "short English definition",
     "meaning_native": "meaning in {native}",
     "example": "one natural English sentence using it"}
  ],
  "source_text": "the readable English text of the material",
  "anchors": ["the exact forms, structures and words exercises must be built on"]
}"""

READ_SOURCE = """You are looking at material an English learner is studying: a
coursebook page, a grammar explanation, a vocabulary list, a screenshot, or
their own handwritten notes.

Read it carefully, then build a lesson from it.

Return ONLY JSON matching this shape:

{schema}

Rules:
- Everything must come from the material in front of you. Do not add topics it
  does not cover.
- Write "explanation", "summary", "rules[].point", "rules[].note",
  "common_mistakes[].why" and "vocabulary[].meaning_native" in {native}.
  Keep all English examples in English.
- "anchors" is a contract with the exercise writer: list the specific target
  forms, structures and words the material actually teaches, quoted as they
  appear. Between 5 and 20 entries.
- "cefr" is your judgement of the material's own level.
- Fill "vocabulary" only for words the material presents as vocabulary, maximum
  25 entries. Use an empty list for pure grammar pages.
- "common_mistakes" must be errors THIS learner group makes, drawn from the
  interference notes below, and must relate to what the material teaches.
- If the material is unreadable, return empty fields and put whatever you could
  make out into "source_text".

{interference}"""

TOPIC_LESSON = """An English learner has asked about this: "{topic}"

They are at CEFR level {level}. Answer them properly — this is the whole lesson,
so it must stand on its own.

Return ONLY JSON matching this shape:

{schema}

Rules:
- Set "cefr" to {level}. Pitch every example at that level.
- Write "explanation", "summary", "rules[].point", "rules[].note",
  "common_mistakes[].why" and "vocabulary[].meaning_native" in {native}.
  Keep all English examples in English.
- "explanation" must answer their question directly and plainly. If they asked
  when something is used, lead with when it is used, not with terminology.
- "rules" must cover the distinct uses separately. For a modal verb that means
  each function on its own, never one vague summary.
- "anchors" lists the specific forms and phrases exercises must be built on.
- "source_text" holds your explanation and examples as plain English text.
- Fill "vocabulary" only if the topic is lexical rather than grammatical.
- "common_mistakes" must be errors THIS learner group makes with THIS topic.

{interference}"""

GENERATE = """Write {count} practice questions for an English learner.

THE MATERIAL THEY ARE STUDYING
Title: {title}

Forms and words the exercises must be built on:
{anchors}

Rules taught:
{rules}

Text of the material:
{source}

{budget}

WHO THIS IS FOR
{interference}
{focus}
About half the questions should aim at a documented interference point above,
wherever the material supports it. A question that a learner of any background
would find equally easy is a wasted question.

EXERCISE TYPES TO USE: {types}

Return ONLY a JSON array:

[
  {{
    "type": "one of the types listed above",
    "prompt": "exactly what the learner sees, in English unless it is a translation task",
    "options": ["..."] or null,
    "answer": "the correct answer",
    "accept": ["other wordings that must also count as correct"],
    "anchor": "the exact entry from the anchor list this question tests",
    "skill": "short lowercase tag, e.g. articles, present_perfect, prepositions",
    "explanation": "why the answer is right, in {native}, 1-2 sentences",
    "trap": "the {native_name} interference this targets, in {native}, or null"
  }}
]

Hard requirements:
- "anchor" must be copied exactly from the anchor list. A question you cannot
  anchor is a question you must not write.
- multiple_choice needs exactly 4 options and the correct one must appear in
  them character for character. All other types need "options": null.
- Distractors must be mistakes this learner group would actually make, never
  random words.
- gap_fill prompts must contain _____ where the answer goes.
- error_correction: "prompt" is the faulty sentence, "answer" is the corrected one.
- word_order: "prompt" gives scrambled words separated by " / ", "answer" is the
  correct sentence.
- translation: "prompt" is a sentence in {native_name}, "answer" is the English.
- Fill "accept" generously for anything not multiple choice — contractions,
  he/she alternatives, and equally correct phrasings all belong there.
- No two questions may test the same anchor in the same shape."""

REVIEW = """You are checking exercises before they reach a learner.

MATERIAL THEY STUDIED
{title}

Anchors the questions had to be built on:
{anchors}

{budget}

QUESTIONS
{questions}

For each question, decide:
- "keep" — correct English, unambiguous, on level, genuinely built on an anchor.
- "revise" — salvageable but off level, ambiguous, missing a valid alternative
  from "accept", or drifting from the material. Supply the fixed question in full.
- "drop" — wrong, unanswerable, or unrelated to the material.

Be strict. A question with two defensible answers is "revise", not "keep".

Return ONLY a JSON array:

[
  {{"index": 0,
    "verdict": "keep" | "revise" | "drop",
    "reason": "one short line in English",
    "revised": null or the full corrected question object}}
]"""

CHAT = """You are a patient English teacher. The learner is at CEFR level {level}
and their first language is {native_name}.

They are studying: {title}

{context}

Answer their question in {native}, keeping every English example in English.
Be concrete: lead with a direct answer, then one or two examples, then a
contrast with {native_name} if that is what makes the point land. Keep it under
150 words unless they asked for more. Do not pad with encouragement.

Return ONLY JSON: {{"reply": "your answer"}}"""


def lesson_schema(native: str, native_name: str) -> str:
    return (LESSON_SCHEMA
            .replace("{native_name}", native_name)
            .replace("{native}", native))


def read_source_prompt(native_code: str) -> str:
    native_name = LANGUAGE_NAMES.get(native_code, native_code)
    return READ_SOURCE.format(
        schema=lesson_schema(native_name, native_name),
        native=native_name,
        interference=interference_for(native_code),
    )


def topic_prompt(topic: str, level: str, native_code: str) -> str:
    native_name = LANGUAGE_NAMES.get(native_code, native_code)
    return TOPIC_LESSON.format(
        topic=topic, level=level,
        schema=lesson_schema(native_name, native_name),
        native=native_name,
        interference=interference_for(native_code),
    )


def generate_prompt(lesson: dict, level: str, types: list[str], count: int,
                    weak: list[str], native_code: str,
                    focus: list[str] | None = None,
                    avoid: list[str] | None = None) -> str:
    """Build the generation instruction.

    ``focus`` narrows a batch to a slice of the lesson's anchors and ``avoid``
    lists prompts already written. Parallel batches otherwise receive byte-identical
    instructions, and a model given the same instruction twice writes largely the
    same questions — which deduplication then throws away, leaving the learner
    with a fraction of what they asked for.
    """
    native_name = LANGUAGE_NAMES.get(native_code, native_code)
    focus_note = ""
    if focus:
        focus_note = (
            "\nBUILD THIS BATCH ON THESE ANCHORS FIRST:\n"
            + "\n".join(f"- {a}" for a in focus)
            + "\nUse others from the full list only if these cannot carry "
              f"{count} distinct questions.\n"
        )

    avoid_note = ""
    if avoid:
        avoid_note = (
            "\nQUESTIONS ALREADY WRITTEN — do not repeat these, and do not write "
            "a near-variant of any of them:\n"
            + "\n".join(f"- {p}" for p in avoid[:40]) + "\n"
        )

    focus_weak = (f"\nThis learner has been getting these wrong lately: "
                  f"{', '.join(weak)}. Aim about a third of the questions there, but "
                  f"only where the material supports it.\n" if weak else "")
    rules = "\n".join(f"- {rule.get('point')} ({rule.get('form')})"
                      for rule in (lesson.get("rules") or [])) or "(none stated)"

    return GENERATE.format(
        count=count,
        title=lesson.get("title", ""),
        anchors="\n".join(f"- {a}" for a in (lesson.get("anchors") or [])) or "(none)",
        rules=rules,
        source=(lesson.get("source_text") or "")[:3500],
        budget=level_budget(level),
        interference=interference_for(native_code),
        focus=focus_weak + focus_note + avoid_note,
        types=", ".join(types),
        native=native_name,
        native_name=native_name,
    )


def split_anchors(anchors: list[str], batches: int) -> list[list[str]]:
    """Deal the anchors round-robin so each batch starts from different material."""
    if not anchors or batches < 2:
        return [list(anchors or [])] * max(batches, 1)
    return [anchors[i::batches] or list(anchors) for i in range(batches)]


def review_prompt(questions: list[dict], lesson: dict, level: str) -> str:
    import json
    return REVIEW.format(
        title=lesson.get("title", ""),
        anchors="\n".join(f"- {a}" for a in (lesson.get("anchors") or [])),
        budget=level_budget(level),
        questions=json.dumps(questions, ensure_ascii=False, indent=1),
    )


def chat_prompt(question: str, lesson: dict, level: str, native_code: str) -> str:
    native_name = LANGUAGE_NAMES.get(native_code, native_code)
    context = ""
    if lesson.get("source_text"):
        context = f"Material text:\n{lesson['source_text'][:1500]}"
    return CHAT.format(
        level=level, native=native_name, native_name=native_name,
        title=lesson.get("title", "general English"), context=context,
    ) + f"\n\nTheir question: {question}"


# --------------------------------------------------------------- validation

BLANK = re.compile(r"_{2,}")


def normalise(text) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text)


def check_structure(question: dict, lesson: dict, level: str) -> str | None:
    """Deterministic checks. Returns a rejection reason, or None to pass.

    These run before the model review because they are free, instant and not a
    matter of opinion: a multiple-choice question whose answer is absent from
    its own options is broken no matter what any reviewer thinks.
    """
    if not isinstance(question, dict):
        return "not an object"
    if not question.get("prompt") or not question.get("answer"):
        return "missing prompt or answer"

    kind = question.get("type")
    if kind not in EXERCISE_TYPES:
        return f"unknown type {kind}"

    options = question.get("options")
    if kind == "multiple_choice":
        if not options or len(options) < 3:
            return "fewer than three options"
        if len({normalise(o) for o in options}) != len(options):
            return "duplicate options"
        if normalise(question["answer"]) not in {normalise(o) for o in options}:
            return "answer missing from options"
    elif options:
        return f"{kind} must not carry options"

    if kind == "gap_fill" and not BLANK.search(question["prompt"]):
        return "gap fill without a blank"
    if kind == "word_order" and "/" not in question["prompt"]:
        return "word order without separated words"

    words = len(re.findall(r"\w+", question["prompt"]))
    if words > CEFR[level]["max_words"]:
        return f"prompt too long for {level} ({words} words)"

    anchor = normalise(question.get("anchor"))
    if not anchor:
        return "no anchor"

    corpus = normalise(" ".join(
        [lesson.get("source_text") or ""]
        + (lesson.get("anchors") or [])
        + [f"{rule.get('point', '')} {rule.get('form', '')}"
           for rule in (lesson.get("rules") or [])]
        + [item.get("word", "") for item in (lesson.get("vocabulary") or [])]
    ))
    if anchor not in corpus:
        # Tolerate light rewording of a real anchor; reject an invented one.
        if not difflib.get_close_matches(anchor, lesson.get("anchors") or [],
                                         n=1, cutoff=0.7):
            return "anchor not grounded in the material"

    return None


def deduplicate(questions: list) -> list:
    seen, unique = set(), []
    for question in questions:
        if not isinstance(question, dict):
            continue
        key = normalise(question.get("prompt"))
        if key and key not in seen:
            seen.add(key)
            unique.append(question)
    return unique


def shuffle_options(questions: list[dict], seed: int | None = None) -> list[dict]:
    """Redistribute the correct answer across A–D.

    Models overwhelmingly write the correct option first, so an unshuffled set
    puts nearly every answer under A. A learner who notices that stops reading
    the question — which defeats the exercise entirely. Prompting alone does not
    fix this reliably, so the positions are assigned here instead.

    Each question is placed so the answer lands in the least-used slot so far,
    which spreads answers evenly rather than merely randomly: eight questions
    give roughly two of each letter instead of an accidental run of five A's.
    """
    rng = random.Random(seed)
    usage = [0, 0, 0, 0]

    for question in questions:
        options = question.get("options")
        if not options or len(options) < 2:
            continue

        answer = normalise(question.get("answer"))
        correct = next((o for o in options if normalise(o) == answer), None)
        if correct is None:
            continue  # check_structure rejects these; skip rather than corrupt

        distractors = [o for o in options if o is not correct]
        rng.shuffle(distractors)

        slots = list(range(min(len(options), len(usage))))
        fewest = min(usage[s] for s in slots)
        target = rng.choice([s for s in slots if usage[s] == fewest])
        usage[target] += 1

        reordered = distractors[:]
        reordered.insert(target, correct)
        question["options"] = reordered

    return questions
