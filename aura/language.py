"""Understanding a request that is not written in English.

Aura routes tools by keyword: `select_tool_definitions` reads the message,
matches English words, and hands the model the tools that fit. `_requires_mutation`
does the same to decide whether a request changes anything. Both were English
only, and the user writes Estonian.

Measured before this module existed, on twenty ordinary requests: **sixteen
Estonian ones produced no tools at all**, against none of their English
translations. Aura then answered, honestly and uselessly, that she could not do
it — the same shape as the search bug, where a capability she had been denied
read as a capability she lacked. The four that happened to work did so by
accident, because "meelde", "testid" and "zipiks" contain fragments of English.

The approach here is deliberately *not* a second copy of the routing rules.
Estonian stems are annotated with the English word the existing rules already
understand, and every rule then fires unchanged:

    "loe fail notes.txt"  ->  "loe fail notes.txt read file"

Hints are appended **at the end of the clause they were found in**, and both
halves of that matter. They were first inserted inline, next to the word they
explained, and that broke English: the rules match some keywords as whole
phrases, so "look it up" became "look create it up" and stopped matching. But
collecting every hint at the end of the *message* is wrong too — a hint would
then escape the negative clause meant to suppress it, and "ehita leht, aga ära
käivita seda" would offer the run tool it just forbade. Per clause, commas
included, is the placement that survives both.

Stems are matched at the start of a word and allowed to run on, because Estonian
inflects by suffix: `fail` catches *failid*, *failist*, *failide*. They are not
matched mid-word, so *teenus* does not become *make*.

The asymmetry worth naming: offering a tool that turns out to be unnecessary
costs a little context, while withholding one the request needed makes Aura
claim she cannot do her job. When a stem is a close call, it goes in.
"""

from __future__ import annotations

import re

#: Estonian stem -> the English word the routing rules already look for.
#: Order does not matter; every match is annotated.
ESTONIAN_HINTS: tuple[tuple[str, str], ...] = (
    # things
    ("fail", "file"),
    ("kaust", "folder"),
    ("kataloog", "directory"),
    ("tööruum", "workspace"),
    ("projekt", "project"),
    ("kood", "code"),
    ("funktsioon", "function"),
    ("klass", "class"),
    ("ülesehitus", "architecture"),
    ("arhitektuur", "architecture"),
    # looking
    ("loe", "read"),
    ("luge", "read"),
    ("näita", "show"),
    ("vaata", "look"),
    ("uuri", "inspect"),
    ("sisu", "contents"),
    ("nimekiri", "list"),
    ("loetle", "list"),
    ("kokkuvõt", "summarise"),
    ("analüüsi", "analyse"),
    # searching
    ("otsi", "search"),
    ("leia", "find"),
    ("üles", "find"),
    # building
    ("tee ", "make "),
    ("teha", "make"),
    ("loo", "create"),
    ("ehita", "build"),
    ("kirjuta", "write"),
    ("genereeri", "generate"),
    ("koosta", "create"),
    # editing
    ("muuda", "change"),
    ("muut", "change"),
    ("paranda", "fix"),
    ("asenda", "replace"),
    ("uuenda", "update"),
    ("lisa", "append"),
    ("täienda", "improve"),
    ("korrasta", "refactor"),
    # removing and moving
    ("kustuta", "delete"),
    ("eemalda", "remove"),
    ("prügi", "trash"),
    ("kopeeri", "copy"),
    ("dubleeri", "duplicate"),
    ("teisalda", "move"),
    ("liiguta", "move"),
    ("nimeta", "rename"),
    # undoing
    ("tagasi", "undo"),
    ("taasta", "revert"),
    ("ajalugu", "history"),
    # running and checking
    ("käivita", "run"),
    ("jooksuta", "run"),
    ("kontrolli", "check"),
    ("valideeri", "validate"),
    ("veendu", "check"),
    # comparing
    ("võrdle", "compare"),
    ("erinevus", "difference"),
    ("vahe", "difference"),
    # arithmetic
    ("arvuta", "calculate"),
    ("protsent", "percentage"),
    # memory
    ("mäleta", "remember"),
    ("meelespe", "remember"),
    ("unusta", "forget"),
    ("eelistus", "preference"),
    ("minu kohta", "know about me"),
    ("minust", "know about me"),
    ("tead minust", "what do you know about me"),
    ("minu nimi", "my name"),
    ("kutsu mind", "call me"),
    # pictures
    ("ekraanipilt", "screenshot"),
    ("kuvatõmmis", "screenshot"),
    ("pilt", "image"),
    ("välimus", "appearance"),
    ("kujundus", "design"),
    ("paigutus", "layout"),
    # accessibility
    ("ligipääs", "accessibility"),
    ("ekraanilugeja", "screen reader"),
    ("juurdepääs", "accessibility"),
    # archives
    ("pakenda", "archive"),
    ("pakkima", "archive"),
    ("arhiiv", "archive"),
    # opening
    ("ava", "open"),
    ("eelvaade", "preview"),
    # the web
    ("veebist", "on the internet"),
    ("internetist", "on the internet"),
    ("netist", "on the internet"),
    ("guugelda", "google"),
    ("uudise", "news about"),
    # reminders and watching
    ("meeldetuletus", "reminder"),
    ("meelde", "remind"),
    ("hiljem", "later"),
    ("homme", "tomorrow"),
    ("jälgi", "watch for"),
    ("iga päev", "every day"),
    ("anna teada", "let me know if"),
    # elsewhere on disk
    ("väljaspool", "outside the workspace"),
    ("dokumendid", "my documents"),
    ("allalaadimis", "downloads folder"),
    ("õigus", "permission"),
    # this machine
    ("kettaruum", "disk space"),
    ("süsteemi", "system info"),
    # what she can do
    ("mida sa oskad", "what can you do"),
    ("mis sa oskad", "what can you do"),
    ("sinu tööriistad", "your tools"),
    ("võimekus", "capabilities"),
)

#: Estonian negation, for the clause-stripping that keeps a forbidden action
#: from being offered. Without these, "ära käivita" would read as "run".
NEGATIONS: tuple[str, ...] = ("ära", "ärge", "mitte", "ilma")

#: English words an Estonian stem happens to start. `loo` (create) matches
#: *look*, and annotating that turned "look at notes.txt" into a request that
#: creates something; `fail` matches *failed* and `ava` matches *available*.
#: Kept as data because a test scans every tool description for words an
#: Estonian stem would fire on, so this list is provably complete rather than
#: merely plausible — and a stem added later that collides fails that test.
#: Note `fail` is here as an English word only: the Estonian *fail* keeps its
#: inflections (failid, failist, failis), none of which appear above.
ENGLISH_LOOKALIKES = frozenset({
    "look", "looks", "looking", "looked", "lookup",      # loo (create)
    "fail", "fails", "failed", "failing", "failure", "failures",   # fail (file)
    "available", "availability",                          # ava (open)
})

_PATTERNS = tuple(
    (re.compile(r"\b" + re.escape(stem), re.IGNORECASE), hint)
    for stem, hint in ESTONIAN_HINTS
)

#: Hints are attached at the end of the clause they were found in. Commas count,
#: because "ehita leht, aga ära käivita seda" has to keep *build* while losing
#: *run*, and a negative clause runs to the end of the line.
_CLAUSE = re.compile(r"[^,.!?;\n]+")
_WORD_TAIL = re.compile(r"[^\W\d_]*", re.UNICODE)


def _token_at(text: str, start: int) -> str:
    return text[start:start + len(_WORD_TAIL.match(text, start).group(0))]


def with_english_hints(message: str) -> str:
    """Annotate Estonian stems with the English words the rules look for.

    The hints go at the **end of their own clause**, never inline. Inline
    insertion split English phrases the rules match as a whole — "look it up"
    became "look create it up" and stopped matching — while appending everything
    at the end of the message would put a hint outside the negative clause that
    was supposed to suppress it.
    """
    text = str(message or "")
    if not text.strip():
        return text

    def annotate(clause: re.Match[str]) -> str:
        body = clause.group(0)
        hints: list[str] = []
        for pattern, hint in _PATTERNS:
            for found in pattern.finditer(body):
                if _token_at(body, found.start()).casefold() in ENGLISH_LOOKALIKES:
                    continue
                if hint not in hints:
                    hints.append(hint)
                break
        return f"{body} {' '.join(hints)}" if hints else body

    return _CLAUSE.sub(annotate, text)


def looks_estonian(message: str) -> bool:
    """A cheap check, used only for reporting and tests."""
    return any(pattern.search(str(message or "")) for pattern, _ in _PATTERNS)


# --------------------------------------------------------------- which language

#: Estonian letters that no English word carries. One is enough to decide.
ESTONIAN_LETTERS = frozenset("õäöüšž")

#: Short, extremely common Estonian words. Deliberately function words rather
#: than topic words: a reply about files is full of English filenames either
#: way, and it is the grammar around them that says which language it is.
ESTONIAN_MARKERS = frozenset("""
on ei ja või aga see seda selle need siis kui kas ka mis mida miks kuidas
ma sa ta me te nad minu sinu tema meie teie oled olen oleme olete
ning kuid nagu veel juba ainult kõik midagi mitte pole ole
""".split())

#: English function words, so a reply that is mostly English is not dragged over
#: the line by one borrowed word.
ENGLISH_MARKERS = frozenset("""
the a an is are was were and or but if then that this these those of to in on
for with from you your i we they it its have has had will would can could
""".split())

#: Words spelled the same in both languages. `on` is the one that mattered:
#: it is everywhere in Estonian and also an English preposition, so counting it
#: on both sides made "Eesti pealinn on Tallinn" score one-all and come out
#: English. A word that is evidence for both is evidence for neither.
AMBIGUOUS_MARKERS = ESTONIAN_MARKERS & ENGLISH_MARKERS

_ESTONIAN_ONLY = ESTONIAN_MARKERS - AMBIGUOUS_MARKERS
_ENGLISH_ONLY = ENGLISH_MARKERS - AMBIGUOUS_MARKERS

_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect(text: str, default: str = "en") -> str:
    """Return "et" or "en" for one reply.

    A whole reply, not a sentence: Aura's answers mix languages constantly —
    Estonian prose around English filenames, tool names and code — and switching
    voice mid-sentence would sound worse than reading the lot in one voice.

    `default` is what a reply with no evidence falls back to, and the caller
    passes the language of the *request*: Aura answers in the language she was
    addressed in, which is far better evidence than four words of reply.
    "Eesti pealinn on Tallinn" carries no Estonian letter and no function word
    that is not also English, and is unmistakable to anyone who saw the question.
    """
    body = str(text or "")
    if not body.strip():
        return "en"
    # One of these letters settles it: no English word carries them.
    if ESTONIAN_LETTERS & set(body.casefold()):
        return "et"
    words = [word.casefold() for word in _WORDS.findall(body)]
    estonian = sum(word in _ESTONIAN_ONLY for word in words)
    english = sum(word in _ENGLISH_ONLY for word in words)
    if estonian != english:
        return "et" if estonian > english else "en"
    # No grammar to go on — "Meeldetuletus: venita" has neither a marker nor a
    # special letter. The routing vocabulary already knows a hundred Estonian
    # stems, so ask it rather than inventing a second list.
    if looks_estonian(body):
        return "et"
    return default if default in {"et", "en"} else "en"
