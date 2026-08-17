"""
Lingua — HTTP layer.

Progress lives in the browser, not on the server: no accounts, no database, no
personal data held anywhere. The client posts what it knows when it wants the
next set tailored, and the server stays stateless.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, prompts, study
from .teaching import (CEFR, DEFAULT_TYPES, EXERCISE_TYPES, LANGUAGE_NAMES,
                       LEVELS, UI_LANGUAGES)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lingua")

ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD = 8 * 1024 * 1024

app = FastAPI(title="Lingua", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# --------------------------------------------------------------- models


class TopicRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=400)
    level: str = "A2"
    native: str = "uz"


class TextRequest(BaseModel):
    text: str = Field(min_length=10, max_length=20000)
    level: str = "A2"
    native: str = "uz"


class ExerciseRequest(BaseModel):
    lesson: dict
    level: str = "A2"
    native: str = "uz"
    types: list[str] = Field(default_factory=lambda: list(DEFAULT_TYPES))
    count: int = Field(default=8, ge=3, le=20)
    weak: list[str] = Field(default_factory=list)
    deep: bool = False
    review: bool = True


class GradeRequest(BaseModel):
    questions: list[dict]
    answers: dict
    queue: list[dict] = Field(default_factory=list)
    skills: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    lesson: dict = Field(default_factory=dict)
    level: str = "A2"
    native: str = "uz"


class VocabRequest(BaseModel):
    vocabulary: list[dict]


class WorksheetRequest(BaseModel):
    questions: list[dict]
    lesson: dict


def _validate(level: str, native: str) -> None:
    if level not in CEFR:
        raise HTTPException(400, f"Unknown level {level}")
    if not native or len(native) > 8:
        raise HTTPException(400, "Invalid language code")


def _clean_lesson(lesson: dict) -> dict:
    """Fill in anything the model left out so the client never sees a hole."""
    lesson.setdefault("title", "Lesson")
    lesson.setdefault("kind", "grammar")
    lesson.setdefault("cefr", "A2")
    for key in ("rules", "common_mistakes", "vocabulary", "anchors"):
        if not isinstance(lesson.get(key), list):
            lesson[key] = []
    lesson.setdefault("explanation", "")
    lesson.setdefault("summary", "")
    lesson.setdefault("source_text", "")

    if not lesson["anchors"]:
        # Without anchors nothing can be validated, so derive a fallback set
        # from whatever structure the lesson does carry.
        derived = [rule.get("form") or rule.get("point")
                   for rule in lesson["rules"] if rule.get("form") or rule.get("point")]
        derived += [item.get("word") for item in lesson["vocabulary"] if item.get("word")]
        lesson["anchors"] = [a for a in derived if a][:20]
    return lesson


# --------------------------------------------------------------- endpoints


@app.get("/api/config")
async def config():
    return {
        "levels": [{"code": code, "label": CEFR[code]["label"],
                    "hint": CEFR[code]["sentence"]} for code in LEVELS],
        "languages": [{"code": code, "label": label,
                       "english": LANGUAGE_NAMES.get(code, label)}
                      for code, label in UI_LANGUAGES.items()],
        "exercise_types": [{"code": code, "label": label}
                           for code, label in EXERCISE_TYPES.items()],
        "default_types": DEFAULT_TYPES,
        "providers": ai.available(),
    }


@app.get("/api/health")
async def health():
    return {"providers": await ai.health()}


@app.post("/api/lesson/topic")
async def lesson_from_topic(request: TopicRequest):
    _validate(request.level, request.native)
    try:
        data, reply = await ai.ask_json(
            prompts.topic_prompt(request.topic, request.level, request.native),
            temperature=0.4, max_tokens=6000,
        )
    except Exception as error:  # noqa: BLE001
        log.exception("topic lesson failed")
        raise HTTPException(502, str(error)[:300]) from error

    return {"lesson": _clean_lesson(data if isinstance(data, dict) else {}),
            "provider": reply.provider}


@app.post("/api/lesson/text")
async def lesson_from_text(request: TextRequest):
    _validate(request.level, request.native)
    prompt = prompts.read_source_prompt(request.native) + f"\n\nMATERIAL:\n{request.text}"
    try:
        data, reply = await ai.ask_json(prompt, temperature=0.3, max_tokens=6000)
    except Exception as error:  # noqa: BLE001
        log.exception("text lesson failed")
        raise HTTPException(502, str(error)[:300]) from error

    return {"lesson": _clean_lesson(data if isinstance(data, dict) else {}),
            "provider": reply.provider}


def _shrink(image_bytes: bytes, max_side: int = 1600) -> tuple[bytes, str]:
    """Downscale to what the vision endpoints keep at full fidelity.

    Both vendors resize large images server-side; sending a 12MP phone photo
    means the model reads a blurry thumbnail of it. Resizing ourselves keeps
    control of the quality.
    """
    from PIL import Image, ImageOps

    picture = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
    picture = picture.convert("RGB")
    if max(picture.size) > max_side:
        ratio = max_side / max(picture.size)
        picture = picture.resize((round(picture.width * ratio),
                                  round(picture.height * ratio)), Image.LANCZOS)
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue(), "image/jpeg"


@app.post("/api/lesson/file")
async def lesson_from_file(file: UploadFile = File(...), level: str = Form("A2"),
                           native: str = Form("uz")):
    _validate(level, native)
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 8 MB")

    name = (file.filename or "").lower()
    prompt = prompts.read_source_prompt(native)

    try:
        if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            image, mime = _shrink(raw)
            data, reply = await ai.ask_json(prompt, image=image, mime=mime,
                                            temperature=0.3, max_tokens=6000)
        elif name.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:8])
            if len(text.strip()) < 40:
                raise HTTPException(
                    422, "This PDF has no readable text — it is probably scanned. "
                         "Take a photo of the page instead.")
            data, reply = await ai.ask_json(f"{prompt}\n\nMATERIAL:\n{text[:15000]}",
                                            temperature=0.3, max_tokens=6000)
        else:
            text = raw.decode("utf-8", errors="ignore")
            if len(text.strip()) < 10:
                raise HTTPException(422, "That file appears to be empty")
            data, reply = await ai.ask_json(f"{prompt}\n\nMATERIAL:\n{text[:15000]}",
                                            temperature=0.3, max_tokens=6000)
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        log.exception("file lesson failed")
        raise HTTPException(502, str(error)[:300]) from error

    return {"lesson": _clean_lesson(data if isinstance(data, dict) else {}),
            "provider": reply.provider}


# A C1 question runs several times the length of an A1 one — long stems, longer
# explanations — and the reasoning models spend further tokens before writing
# anything. A flat ceiling truncated C1 sets mid-object, which reached the user
# as "No JSON found in reply".
_LEVEL_COST = {"A1": 320, "A2": 380, "B1": 480, "B2": 620, "C1": 780}


def _token_budget(count: int, level: str = "B1") -> int:
    return min(32000, 2500 + count * _LEVEL_COST.get(level, 480))


# Above this, a single request is both slow and close to the output ceiling.
# Smaller requests in parallel finish sooner and truncate far less often.
BATCH_SIZE = 6


async def _generate_batch(lesson: dict, level: str, types: list[str],
                          count: int, weak: list[str], native: str, deep: bool,
                          focus: list[str] | None = None,
                          avoid: list[str] | None = None) -> list:
    raw, _ = await ai.ask_json(
        prompts.generate_prompt(lesson, level, types, count, weak, native,
                                focus=focus, avoid=avoid),
        temperature=0.85, max_tokens=_token_budget(count, level), deep=deep,
    )
    return raw if isinstance(raw, list) else []


async def _review_batch(questions: list, lesson: dict, level: str) -> dict:
    verdicts, _ = await ai.ask_json(
        prompts.review_prompt(questions, lesson, level),
        temperature=0.0, max_tokens=_token_budget(len(questions), level),
    )
    if not isinstance(verdicts, list):
        return {}
    return {v.get("index"): v for v in verdicts if isinstance(v, dict)}


def _sizes_for(total: int) -> list[int]:
    sizes, remaining = [], total
    while remaining > 0:
        sizes.append(min(BATCH_SIZE, remaining))
        remaining -= sizes[-1]
    return sizes


def _keep_valid(raw: list, lesson: dict, level: str, report: dict,
                seen: set[str]) -> list:
    """Validate, drop duplicates against what we already hold, and log rejects."""
    kept = []
    for question in prompts.deduplicate(raw):
        key = prompts.normalise(question.get("prompt"))
        if key in seen:
            continue
        reason = prompts.check_structure(question, lesson, level)
        if reason:
            report["rejected"].append({"prompt": str(question.get("prompt", ""))[:80],
                                       "reason": reason})
        else:
            seen.add(key)
            kept.append(question)
    return kept


@app.post("/api/exercises")
async def exercises(request: ExerciseRequest):
    _validate(request.level, request.native)
    types = [t for t in request.types if t in EXERCISE_TYPES] or list(DEFAULT_TYPES)
    lesson = _clean_lesson(dict(request.lesson))

    report = {"requested": request.count, "generated": 0, "rejected": [],
              "revised": 0, "dropped": 0}

    # Ask for extra on purpose: some will fail validation, and over-producing
    # once is cheaper than a second round trip.
    sizes = _sizes_for(request.count + max(2, request.count // 3))
    # Each parallel batch gets its own slice of the lesson's anchors. Identical
    # prompts produce largely identical questions, and deduplication then leaves
    # the learner with a fraction of what they asked for.
    slices = prompts.split_anchors(lesson.get("anchors") or [], len(sizes))

    try:
        results = await asyncio.gather(
            *(_generate_batch(lesson, request.level, types, size, request.weak,
                              request.native, request.deep, focus=slices[i])
              for i, size in enumerate(sizes)),
            return_exceptions=True,
        )
    except Exception as error:  # noqa: BLE001
        log.exception("generation failed")
        raise HTTPException(502, str(error)[:300]) from error

    raw, failures = [], []
    for outcome in results:
        if isinstance(outcome, Exception):
            failures.append(str(outcome)[:200])
            log.warning("one generation batch failed: %s", outcome)
        else:
            raw.extend(outcome)

    if not raw:
        raise HTTPException(502, failures[0] if failures
                            else "The model did not return any questions")
    report["generated"] = len(raw)

    seen: set[str] = set()
    survivors = _keep_valid(raw, lesson, request.level, report, seen)

    # Overlap between batches, truncated replies, or a strict validator can all
    # leave us short. Top up sequentially, listing what already exists, until the
    # count is met or a round stops adding anything.
    for _ in range(3):
        if len(survivors) >= request.count:
            break
        shortfall = request.count - len(survivors)
        before = len(survivors)
        try:
            extra = await _generate_batch(
                lesson, request.level, types, min(shortfall + 2, BATCH_SIZE),
                request.weak, request.native, request.deep,
                avoid=[q.get("prompt", "") for q in survivors],
            )
            report["generated"] += len(extra)
            survivors += _keep_valid(extra, lesson, request.level, report, seen)
        except Exception as error:  # noqa: BLE001 — a top-up is a bonus, not a gate
            log.warning("top-up generation skipped: %s", error)
            break
        if len(survivors) == before:
            break  # the model has nothing new to add; stop paying for retries

    if request.review and survivors:
        chunks = [survivors[i:i + BATCH_SIZE]
                  for i in range(0, len(survivors), BATCH_SIZE)]
        verdict_sets = await asyncio.gather(
            *(_review_batch(chunk, lesson, request.level) for chunk in chunks),
            return_exceptions=True,
        )

        kept = []
        for chunk, verdicts in zip(chunks, verdict_sets):
            # Review improves the set; it never gates it. A failed review pass
            # means the questions ship as generated, not that the request fails.
            if isinstance(verdicts, Exception):
                log.warning("review batch skipped: %s", verdicts)
                kept.extend(chunk)
                continue

            for index, question in enumerate(chunk):
                verdict = verdicts.get(index, {})
                decision = verdict.get("verdict", "keep")
                if decision == "drop":
                    report["dropped"] += 1
                    report["rejected"].append({
                        "prompt": str(question.get("prompt", ""))[:80],
                        "reason": verdict.get("reason", "rejected in review")})
                elif decision == "revise" and isinstance(verdict.get("revised"), dict):
                    fixed = verdict["revised"]
                    if not prompts.check_structure(fixed, lesson, request.level):
                        report["revised"] += 1
                        kept.append(fixed)
                    else:
                        kept.append(question)
                else:
                    kept.append(question)
        survivors = kept

    final = prompts.shuffle_options(survivors[:request.count])
    report["final"] = len(final)

    if not final:
        raise HTTPException(
            422, "No question survived the quality checks. Try a different level "
                 "or exercise type.")

    return {"questions": final, "report": report}


@app.post("/api/grade")
async def grade(request: GradeRequest):
    graded = study.grade(request.questions, request.answers)
    queue = study.schedule(request.queue, request.questions, graded)
    skills = study.merge_skills(request.skills, graded["by_skill"])
    return {
        "graded": graded,
        "queue": queue,
        "skills": skills,
        "weak": study.weak_skills(skills),
        "due_today": len(study.due_now(queue)),
    }


@app.post("/api/vocabulary/rounds")
async def vocabulary_rounds(request: VocabRequest):
    rounds = study.build_rounds(request.vocabulary)
    if not rounds:
        raise HTTPException(422, "At least four words are needed for the game")
    return {"rounds": rounds}


@app.post("/api/chat")
async def chat(request: ChatRequest):
    _validate(request.level, request.native)
    try:
        data, reply = await ai.ask_json(
            prompts.chat_prompt(request.question, request.lesson,
                                request.level, request.native),
            temperature=0.5, max_tokens=1500,
        )
    except Exception as error:  # noqa: BLE001
        log.exception("chat failed")
        raise HTTPException(502, str(error)[:300]) from error

    text = data.get("reply") if isinstance(data, dict) else str(data)
    return {"reply": text, "provider": reply.provider}


@app.post("/api/worksheet")
async def worksheet(request: WorksheetRequest):
    return PlainTextResponse(
        study.worksheet(request.questions, request.lesson),
        headers={"Content-Disposition": 'attachment; filename="lingua-worksheet.md"'},
    )


# --------------------------------------------------------------- static

app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse({
        "name": "Lingua", "short_name": "Lingua",
        "start_url": "/", "display": "standalone",
        "background_color": "#FBFAF8", "theme_color": "#1B1D22",
        "icons": [],
    })


@app.exception_handler(404)
async def not_found(request, exc):  # noqa: ARG001
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(ROOT / "templates" / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0",
                port=int(os.getenv("PORT", "8000")), reload=True)
