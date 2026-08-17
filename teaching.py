"""
The teaching model.

Two things here decide whether the app is better than a generic quiz generator.

First, the interference profiles: learners do not make random errors, they make
errors shaped by the language they already speak. A Russian speaker omits
articles; a French speaker says "I have 20 years"; a German speaker sends the
verb to the end. Exercises aimed at those specific collisions are worth several
times a generic gap-fill.

Second, the level budgets: "write A2 questions" means nothing on its own, and
output drifts to whatever register the source happened to use. Each level
therefore carries an explicit budget — grammar allowed, grammar forbidden,
sentence length, vocabulary range — which is enforced in code after generation,
not merely requested in the prompt.
"""

from __future__ import annotations

# --------------------------------------------------------------- interfaces

UI_LANGUAGES = {
    "uz": "O'zbekcha",
    "ru": "Русский",
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "tr": "Türkçe",
    "ar": "العربية",
}

LANGUAGE_NAMES = {
    "uz": "Uzbek", "ru": "Russian", "en": "English", "de": "German",
    "fr": "French", "es": "Spanish", "tr": "Turkish", "ar": "Arabic",
}


# --------------------------------------------------------------- L1 profiles

INTERFERENCE = {
    "uz": """The learner's first language is Uzbek; most also speak Russian.
Their errors follow from these gaps:
- ARTICLES: neither language has a/an/the. They omit them ("I am student"), then
  overuse "the" once taught it exists.
- PRESENT PERFECT: no equivalent, so it collapses into past simple ("I already
  did it"). Present continuous is used for habits ("I am going to school every day").
- PLURALS AFTER NUMERALS: Uzbek does not pluralise after a number: "five book".
- THIRD PERSON -S: no agreement marker exists: "he go", "she want".
- PREPOSITIONS transferred from Russian: "in Monday", "depend from", "married
  with", "listen music", "discuss about", "explain me", "on the picture".
- QUESTIONS without do-support: "Where you are going?", "What means this word?"
- COUNTABILITY: "informations", "an advice", "news are", "furnitures".
- VERB PATTERNS: "I enjoy to read", "I want going".
- WORD ORDER: Uzbek is subject-object-verb, giving "I English study", and
  misplaced adverbs: "I speak very well English".
- FALSE FRIENDS from Russian: magazine (магазин = shop), fabric (фабрика =
  factory), actual (актуальный = relevant), accurate (аккуратный = tidy),
  sympathetic (симпатичный = good-looking), intelligent (интеллигентный =
  cultured), decade (декада = ten days), cabinet (кабинет = office),
  receipt (рецепт = recipe), prospect (проспект = avenue).""",

    "ru": """The learner's first language is Russian.
- ARTICLES: Russian has none. Omission is near-universal at lower levels.
- PRESENT PERFECT: no equivalent; past simple is used throughout.
- THE VERB "TO BE" in the present is dropped: "He doctor", "I from Moscow".
- PREPOSITIONS: "depend from", "in Monday", "married with", "laugh above",
  "listen music", "wait my friend", "explain me", "on the picture".
- QUESTIONS without do-support: "Where you live?", "What means this?"
- COUNTABILITY: "informations", "advices", "moneys", "news are".
- WORD ORDER is freer in Russian, producing "Yesterday went I to the shop".
- FALSE FRIENDS: magazine, fabric, actual, accurate, sympathetic, intelligent,
  decade, cabinet, receipt, prospect, biscuit (бисквит = sponge cake).
- ASPECT: Russian marks perfective/imperfective on the verb itself, so English
  continuous forms get overused or dropped unpredictably.""",

    "de": """The learner's first language is German.
- WORD ORDER: German sends the verb to the end in subordinate clauses, producing
  "when I home came". Time-manner-place order gives "I go with the bus to work".
- PRESENT PERFECT vs PAST SIMPLE: German perfect covers both, giving "I have
  seen him yesterday".
- FALSE FRIENDS: become/bekommen (= to get), gift/Gift (= poison),
  chef/Chef (= boss), handy/Handy (= mobile phone), also/also (= therefore),
  sensible/sensibel (= sensitive), eventually/eventuell (= possibly),
  actual/aktuell (= current).
- CAPITALISATION of nouns carries over into English writing.
- "SINCE" vs "FOR" both map to seit: "I live here since three years".
- MODAL "must not" is read as "need not", reversing the meaning.
- ADVERB/ADJECTIVE: German adverbs are unmarked, giving "he drives careful".""",

    "fr": """The learner's first language is French.
- "I HAVE 20 YEARS" — age uses avoir in French.
- PRESENT PERFECT: passé composé covers past simple, giving "I have gone
  yesterday".
- ADJECTIVE POSITION: "a car red", following French order.
- FALSE FRIENDS: actually/actuellement (= currently), library/librairie (=
  bookshop), sensible/sensible (= sensitive), attend/attendre (= to wait),
  assist/assister (= to attend), demand/demander (= to ask), eventually,
  location/location (= rental).
- PREPOSITIONS: "depend of", "interested by", "on the picture", "in the same time".
- "SINCE" for duration: "I work here since 2019".
- MISSING DO-SUPPORT in questions and negatives.
- PEOPLE/INFORMATION treated as plural or countable: "the people is",
  "an information".""",

    "es": """The learner's first language is Spanish.
- SUBJECT DROPPING: "Is very good" for "It is very good".
- "I HAVE 20 YEARS" — age uses tener.
- PRESENT PERFECT vs PAST SIMPLE confusion mirrors the pretérito split.
- ADJECTIVE POSITION and agreement: "a car red", "the childrens".
- FALSE FRIENDS: actually/actualmente, assist/asistir, carpet/carpeta (= folder),
  embarrassed/embarazada (= pregnant), sensible/sensible, realize/realizar
  (= to carry out), success/suceso (= event).
- PREPOSITIONS: "depend of", "in the morning of Monday", "think in".
- "PEOPLE IS", "the informations".
- QUESTIONS formed by intonation only, without inversion or do-support.""",

    "tr": """The learner's first language is Turkish.
- ARTICLES: Turkish has no definite article, so a/an/the are omitted or overused.
- WORD ORDER: Turkish is subject-object-verb, giving "I book read".
- NO GENDER in pronouns, so he/she are used interchangeably.
- PLURALS AFTER NUMERALS are unmarked: "five book".
- PREPOSITIONS: Turkish uses case suffixes, so English prepositions are guessed.
- PRESENT PERFECT has no equivalent and collapses into past simple.
- QUESTIONS use a particle rather than inversion: "You are coming?"
- RELATIVE CLAUSES are built with participles, producing "the reading book man".""",

    "ar": """The learner's first language is Arabic.
- THE VERB "TO BE" is absent in the present: "He teacher", "I happy".
- ARTICLES: Arabic has a definite article but no indefinite one, giving "I am
  student" and "the life is hard".
- ADJECTIVE POSITION and agreement: "a car red", "the books beautifuls".
- P vs B: written confusion between the two sounds.
- WORD ORDER: verb-subject-object is common, giving "Went the boy to school".
- PREPOSITIONS transfer directly and often do not match.
- PLURALS and DUALS: "two books" marked differently, plural agreement errors.
- RELATIVE CLAUSES carry a resumptive pronoun: "the man who I saw him".""",
}

GENERIC_INTERFERENCE = """The learner's first language is {language}. Consider
the structural differences between {language} and English — articles, tense
and aspect, word order, prepositions, countability, agreement — and target the
points where a speaker of {language} would predictably struggle rather than
points that are easy for everyone."""


def interference_for(code: str, language_name: str | None = None) -> str:
    if code in INTERFERENCE:
        return INTERFERENCE[code]
    name = language_name or LANGUAGE_NAMES.get(code, code)
    return GENERIC_INTERFERENCE.format(language=name)


# --------------------------------------------------------------- CEFR budgets

CEFR = {
    "A1": {
        "label": "Beginner",
        "sentence": "5 to 9 words",
        "vocabulary": "only the ~600 most frequent English words",
        "grammar": "present simple, to be, can, have got, there is/are, plurals, "
                   "articles, basic prepositions of place and time, imperatives",
        "forbid": "any perfect tense, passive voice, conditionals, modals beyond "
                  "can/must, phrasal verbs, relative clauses, reported speech",
        "max_words": 14,
    },
    "A2": {
        "label": "Elementary",
        "sentence": "7 to 13 words",
        "vocabulary": "the ~1500 most frequent English words",
        "grammar": "past simple, present continuous, going to, comparatives and "
                   "superlatives, should, have to, countable/uncountable nouns, "
                   "adverbs of frequency",
        "forbid": "perfect continuous tenses, third conditional, passive beyond "
                  "simple present forms, academic vocabulary",
        "max_words": 19,
    },
    "B1": {
        "label": "Intermediate",
        "sentence": "10 to 18 words",
        "vocabulary": "the ~2500 most frequent words plus common phrasal verbs",
        "grammar": "present perfect vs past simple, first and second conditionals, "
                   "passive voice, relative clauses, reported speech, gerund vs "
                   "infinitive after common verbs",
        "forbid": "inversion, cleft sentences, academic register, rare idioms",
        "max_words": 26,
    },
    "B2": {
        "label": "Upper intermediate",
        "sentence": "14 to 24 words",
        "vocabulary": "wide general vocabulary including collocations and idioms",
        "grammar": "all perfect and continuous forms, third and mixed conditionals, "
                   "modals of deduction, participle clauses, wish/if only",
        "forbid": "highly technical or literary register",
        "max_words": 34,
    },
    "C1": {
        "label": "Advanced",
        "sentence": "18 to 32 words",
        "vocabulary": "precise, abstract and academic vocabulary; nuanced collocation",
        "grammar": "inversion, cleft sentences, ellipsis, subtle modality, "
                   "discourse markers, hedging",
        "forbid": "nothing, but questions must hinge on nuance rather than on rules",
        "max_words": 46,
    },
}

LEVELS = list(CEFR)


def level_budget(level: str) -> str:
    spec = CEFR[level]
    return (
        f"CEFR {level} ({spec['label']}) BUDGET — every question must respect all of it:\n"
        f"- Sentence length: {spec['sentence']}.\n"
        f"- Vocabulary: {spec['vocabulary']}.\n"
        f"- Grammar available: {spec['grammar']}.\n"
        f"- Must not appear: {spec['forbid']}.\n"
        f"A question a learner one level below could answer without thinking is too "
        f"easy. A question requiring grammar from the forbidden list is too hard. "
        f"Both are failures."
    )


# --------------------------------------------------------------- exercises

EXERCISE_TYPES = {
    "multiple_choice": "Multiple choice",
    "gap_fill": "Gap fill",
    "error_correction": "Find the mistake",
    "transformation": "Rewrite the sentence",
    "word_order": "Put in order",
    "translation": "Translate into English",
}

DEFAULT_TYPES = ["multiple_choice", "gap_fill", "error_correction", "translation"]

# Review intervals in days. Short at the start on purpose: an item met once and
# not seen again for a week was never learned.
REVIEW_LADDER = [1, 3, 7, 16, 35]
