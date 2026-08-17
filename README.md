# Lingua

**Learn English from whatever you're already studying — explained in your language, drilled where your language trips you up.**

---

## The problem

A learner in Tashkent opens a page on the present perfect, reads it, understands
it, and closes the book. Nothing has been practised. Practice is the scarce
resource: coursebooks carry a handful of exercises per unit, teachers manage
forty students, and online generators produce questions written for nobody in
particular.

That last part is what matters. **Language learners don't make random errors.
They make errors shaped by the language they already speak.**

An Uzbek speaker writes *"I am student"* because Uzbek has no articles, and
*"five book"* because Uzbek doesn't pluralise after a numeral. A Russian speaker
writes *"depend from"* — *зависеть от*. A German speaker writes *"I have seen him
yesterday"* because the German perfect covers both English past forms. A French
speaker writes *"I have 20 years"*.

A generic quiz generator knows none of this. So it spends its questions on things
the learner already gets right, and misses the handful of structures that account
for most of their mistakes.

## The solution

Lingua takes whatever material the learner has — a photographed coursebook page,
a pasted passage, or just a typed question like *"when do we use should?"* — and
builds a lesson from it in their own language. Then it generates practice aimed
at the collision between that material and their first language.

Get something wrong and it doesn't just show the answer. It names the
interference: *"Uzbek has no articles, which is why this blank feels optional."*

## What makes it different

### An interference model per language

Seven first languages carry a documented error profile: Uzbek, Russian, German,
French, Spanish, Turkish and Arabic. Each lists the specific collisions with
English — article omission, tense collapse, word order, preposition transfer,
countability, false friends. About half of every generated set targets these
points. Any other language falls back to a general contrastive prompt.

The claim isn't "we support many languages". It's *we know what goes wrong in
each one.*

### Level budgets enforced in code

"Write A2 questions" means nothing to a model on its own; output drifts to
whatever register the source used. Each CEFR level here carries an explicit
budget — grammar allowed, grammar forbidden, sentence length, vocabulary range.
A question that exceeds its budget is **rejected by the server**, not merely
discouraged in the prompt.

### Every question is anchored

Generated questions must cite an `anchor` — a form or word the source material
actually teaches. The server checks each anchor against the lesson text before
the learner sees anything. A question the model invented out of thin air cannot
reach the page, which is what stops exercises drifting away from the uploaded
page.

### A three-stage quality pipeline

```
generate (over-produce) ──► deterministic checks ──► model review ──► learner
                             structure, level,        keep / revise /
                             anchor grounding              drop
```

The app tells the learner what it threw away and why, under **Quality check**
below each result. Being visibly strict is part of the product.

### Grading that knows grammar from typing

Writing *"docter"* for *"doctor"* is a slip, accepted with a note. Writing
*"book"* for *"books"* is the mistake the question exists to catch, and is never
forgiven — even though those strings are closer together. The grader separates
inflectional differences from misspellings before deciding, and folds
contractions so *"should not"* and *"shouldn't"* both count.

### It follows the learner

Every answer updates a skill profile held in the browser. Miss enough
prepositions and the next generated set is told to aim there. Missed questions
return on a spaced ladder — 1, 3, 7, 16, 35 days — and retire only after
surviving all five steps.

### Two providers, one interface

Gemini is primary; Claude takes over on failure, rate limiting or an unparseable
reply, with retries and backoff between them. A demo that dies because one vendor
rate-limits is a demo nobody sees.

## Features

- Three ways in: ask a question, photograph a page, paste text
- Full lesson: explanation, rules with patterns, worked examples, and the
  mistakes speakers of *your* language make
- Six exercise types: multiple choice, gap fill, error correction,
  transformation, word order, translation
- Vocabulary game with distractors drawn from the same topic
- Follow-up chat about the current lesson, in your own language
- Pronunciation on every English example, using the browser's own speech engine
- Spaced review queue, daily streak, accuracy chart by grammar area
- Printable worksheet with answer key, for teachers
- Dark mode, mobile layout, no login, no tracking

## Privacy

There are no accounts and no database. Progress lives in the browser's
localStorage and is sent to the server only when the next set should be tailored
to it. Nothing about a learner is stored server-side, and **Clear my progress**
removes everything on the device.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | FastAPI, async httpx |
| AI | Gemini 2.5 (primary) with Claude failover |
| Frontend | Vanilla JS modules, no framework, no build step |
| Speech | Browser Web Speech API |
| Storage | Browser localStorage |

`app/teaching.py`, `app/prompts.py` and `app/study.py` hold the interference
models, prompt construction and grading. None of them import the web framework,
so the same engine could drive a Telegram bot — which is where this audience
actually is.

## Running it locally

```bash
pip install -r requirements.txt
cp .env.example .env      # add your keys
python run.py             # http://localhost:8000
```

Only one key is needed; the other becomes the fallback if present.

## Deploying

The repository includes `render.yaml`. On [Render](https://render.com): New →
Blueprint → point at the repo → add `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` as
environment variables. Nothing else to configure; the backend serves the
frontend.

## Who it's for

Self-studying English learners and the teachers who set their homework —
starting with Uzbek and Russian speakers, where the error model is deepest and
where AI learning tools written for this audience barely exist.

## Where it goes next

- Telegram bot delivery on the same engine
- Speaking practice: record an answer, get the same interference-aware feedback
- Teacher mode: one page in, thirty differentiated worksheets out
- More first-language profiles, contributed as plain data files
