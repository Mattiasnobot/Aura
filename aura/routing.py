"""Choosing which tools to offer for a request.

Lifted out of `agent.py` in phase 52.4. Deciding *which* tools fit a request is a
different job from conducting a turn with them, and it was 130-odd lines sitting
in a 2300-line file that is supposed to be about the second thing.

**Nothing about the behaviour changed in the move.**
`AuraAgent.select_tool_definitions` still exists with the same signature and
delegates here, because a dozen tests call it directly and the rule for this
phase — as for phase 50 — is that no existing test may change.

The one place that understands a language other than English is
`language.with_english_hints`, called at the top of `select`. Every rule below it
reads the annotated text, which is why teaching Aura another language means
adding stems to one file rather than touching any rule here.
"""

from __future__ import annotations

import re

from . import language

#: Offered when keyword routing matches nothing at all. Read-only by
#: design: guessing is acceptable for looking, never for changing.
FALLBACK_TOOLS = ("list_files", "read_file", "file_info", "search_files",
                  "find_relevant_files", "workspace_summary")


def strip_negative_clauses(message: str) -> str:
    # A read-only request often lists the exact operations that must *not*
    # happen ("do not create, edit, move, or delete anything").  Remove
    # those negative clauses before looking for an action verb so their
    # safety wording cannot accidentally turn validation into a build job.
    def without_negative_clause(match: re.Match[str]) -> str:
        clause = match.group(0)
        for separator in (" but ", " instead ", " however "):
            position = clause.find(separator)
            if position >= 0:
                return clause[position + 1:]
        return " "

    return re.sub(
        r"\b(?:do\s+not|don't|dont|never|without|"
        + "|".join(language.NEGATIONS) + r")\b[^.!?;\n]*",
        without_negative_clause,
        message,
    )


def select(message: str, autonomy: str = "balanced",
           reasoning_depth: str = "balanced",
           definitions: list[dict] | None = None) -> list[dict]:
    # Estonian stems are annotated with the English words these rules already
    # match, in place, so every rule below fires unchanged. Measured before
    # this existed: sixteen of twenty ordinary Estonian requests produced no
    # tools at all, and Aura reported that as being unable to help.
    raw_lower = language.with_english_hints(
        message.casefold().replace("don’t", "don't"))
    lower = strip_negative_clauses(raw_lower)
    names: set[str] = set()
    def includes(*words: str) -> bool:
        return any(word in lower for word in words)
    build_intent = includes("create", "make", "build", "generate", "write", "improve", "polish",
                            "enhance",
                            # Measured live: "record a plan of three steps … then do
                            # the first one" offered six read-only tools, so Aura read
                            # and searched thirty-eight times and then described work
                            # she had no way to perform.
                            "implement", "finish", "complete", "add", "set up", "do the")
    run_forbidden = bool(re.search(
        r"\b(?:do not|don't|dont|never|without|" + "|".join(language.NEGATIONS)
        + r")\b[^.!?;\n]*\b(?:run|execute)\b", raw_lower))
    if build_intent:
        names.update({"list_files", "read_file", "create_file", "write_file", "validate_project"})
        if autonomy == "powerful" or reasoning_depth == "deep":
            names.update({"workspace_summary", "read_many_files", "write_files", "search_text",
                          "inspect_code", "compare_files"})
        if includes("folder", "directory"):
            names.add("create_folder")
        if includes("run", "test", "execute") and not run_forbidden:
            names.add("run_command")
    if includes("edit", "change", "replace", "update", "modify", "fix", "refactor", "append"):
        names.update({"list_files", "read_file", "file_info", "search_text", "write_file",
                      "read_many_files", "append_file", "replace_in_file", "apply_edits",
                      "write_files", "inspect_code", "compare_files", "run_command"})
    if includes("read", "inspect", "show", "find", "search", "look", "summar", "analy"):
        names.update({"list_files", "read_file", "file_info", "search_files", "search_text",
                      "read_many_files", "workspace_summary", "inspect_code",
                      "find_relevant_files"})
    if includes("list", "files", "folder contents", "directory contents"):
        names.add("list_files")
    if includes("copy", "duplicate"):
        names.update({"list_files", "copy_file", "read_file"})
    if includes("move", "rename"):
        names.update({"list_files", "move_file", "read_file"})
    if includes("delete", "remove", "trash") and not includes("memory", "what you know", "about me", "forget"):
        names.update({"list_files", "safe_delete_file"})
    # "Is anything broken in shop?" is a request to validate, and it carries
    # none of the verbs above — measured, and it came back offering only the
    # read-only fallback, so the one tool that answers the question was the one
    # tool missing.
    asks_if_sound = includes("broken", "wrong with", "anything wrong", "problems",
                             "issues", "errors", "mistakes", "valid")
    if includes("run", "test", "check", "validate", "compile", "execute") or asks_if_sound:
        names.update({"list_files", "read_file", "validate_project"})
        # Not offered when the question is "is this sound?". `validate_project`
        # answers it, and a shell beside it is an invitation: asked to check the
        # shop, the model reached past the offered tool for
        # `python -m validate_project`, which cost a round and a refusal.
        if not run_forbidden and not asks_if_sound:
            names.add("run_command")
    if includes("code", "symbol", "function", "class", "outline", "architecture", "entry point"):
        names.update({"inspect_code", "read_file", "search_text"})
    if includes("compare", "difference", "diff"):
        names.update({"compare_files", "read_file"})
    if includes("calculate", "math", "equation", "percentage"):
        names.add("calculate")
    if includes("system info", "computer info", "environment", "disk space", "python version"):
        names.add("system_info")
    if includes("http", "url", "endpoint", "api", "localhost", "server response"):
        names.add("http_get")
    if includes("weather", "forecast", "temperature outside", "raining", "ilm"):
        names.add("get_weather")
    # Offered whether or not a search service is configured. When there is
    # none the tool refuses and names what is missing, which is how the user
    # finds out the option exists — withholding it instead makes Aura say
    # "I cannot search the web", which is true of the turn and false of her.
    if includes("search the web", "web search", "search online", "look it up",
                "look up online", "browse the web", "on the internet", "google",
                "latest news", "news about", "veebist", "internetist", "netist",
                "guugelda"):
        names.add("search_web")
    # "meelde" was hardcoded here before the hint layer existed, and it now does
    # real damage: it sits inside "jäta meelde" (keep this in mind) and routed a
    # fact about the user to the scheduler. The language belongs in one place.
    if includes("remind", "reminder", "later", "in an hour", "tomorrow",
                "don't let me forget"):
        names.add("set_reminder")
    if includes("keep an eye", "watch for", "check regularly", "every day",
                "notice when", "let me know if"):
        names.add("set_check")
    if includes("zip", "archive", "compress"):
        names.update({"create_archive", "extract_archive", "list_files"})
    if includes("open", "launch", "preview"):
        names.update({"open_workspace_item", "list_files"})
    if includes("what can you do", "your tools", "capabilities", "tool check"):
        names.add("capability_summary")
    if includes("image", "screenshot", "picture", "photo", "logo", "mockup",
                "look at", "what does it look like", "icon", "design"):
        names.update({"look_at_image", "list_files"})
    if includes("screenshot", "how does it look", "what does it look like", "render",
                "capture", "preview", "visual", "layout", "appearance"):
        names.update({"capture_page", "look_at_image", "list_files"})
    if includes("compare", "difference", "differ", "regression", "changed visually",
                "reference", "before and after", "same as"):
        names.update({"compare_images", "look_at_image", "list_files"})
    if includes("accessib", "a11y", "screen reader", "alt text", "wcag", "aria",
                "usable for everyone"):
        names.update({"check_accessibility", "read_file", "list_files"})
    if includes("outside the workspace", "external", "granted", "permission",
                "my documents", "another folder", "downloads folder"):
        names.update({"list_granted_folders", "list_external_folder",
                      "read_external_file", "write_external_file",
                      "undo_external_change"})
    if includes("undo", "revert", "rollback", "history", "change history"):
        names.update({"change_history", "undo_last_change", "rollback_task", "read_file"})
    if includes("remember", "preference", "call me", "my name", "learn about me",
                "know about me", "what do you know about me"):
        names.update({"remember_name", "remember_preference", "remember_personal_fact",
                      "list_personal_memory"})
    if includes("forget", "unlearn", "remove that memory"):
        names.update({"list_personal_memory", "forget_personal_fact"})
    if includes("correct", "actually i", "that is wrong", "update what you know"):
        names.update({"list_personal_memory", "correct_personal_fact"})
    if includes("recent task", "task history", "what did you do"):
        names.add("recent_tasks")
    # The plan is state, so reaching it must not depend on the word "plan" being
    # used. Anything that continues, finishes or reports on a project needs it —
    # and a build request needs it most, because that is when steps get recorded.
    if build_intent or includes("plan", "step", "steps", "continue", "carry on",
                                "progress", "next", "finish", "resume", "todo",
                                "plaan", "samm", "jätka", "edasi"):
        names.update({"record_plan_steps", "update_plan_step"})
    # Work over a *set* of files is what `execute_code` is for: reading many and
    # reporting on them, or making one change across all of them. Offered on the
    # words that mean "more than one", because a script whose whole body is a
    # single tool call costs a process and saves nothing.
    if includes("every", "each", "all the", "all of", "any file", "across",
                "everywhere", "bulk", "one by one", "in turn", "throughout",
                "kõik", "igas", "iga"):
        names.add("execute_code")
        names.update({"list_files", "search_text"})
    # A correction is the moment a lesson is worth keeping, so the words that
    # carry one have to reach the tool that keeps it. Built this afternoon and
    # unroutable until a test went looking for tools nothing could offer.
    if includes("rule", "always", "never", "from now on", "remember that",
                "do not", "don't", "correction", "reegel", "alati", "mitte kunagi"):
        names.add("remember_lesson")
    if includes("self check", "self-check", "are you ok", "are you healthy",
                "diagnose yourself", "health", "something wrong with you"):
        names.add("self_check")
    # Asked how fast she is or what has been failing, Aura answered with a table
    # headed "Real Data Only" containing invented numbers, having run no tools —
    # because the tool that measures exactly that was not among the four she was
    # offered. A question about herself has to reach the mirror, or she has no
    # option but to imagine the answer.
    if includes("how fast", "how slow", "your speed", "response time", "performance",
                "how long do you take", "how are you doing", "what could be better",
                "what could be improved", "your weakness", "your shortcomings",
                "failing", "failures", "what went wrong", "how have you been",
                "kui kiire", "kui aeglane", "ebaõnnestunud", "mis läks valesti"):
        names.add("how_i_have_been_running")
    if autonomy == "powerful" and names and reasoning_depth == "deep":
        names.update({"workspace_summary", "file_info", "read_many_files"})
    definitions = list(definitions or [])
    # If the request names a tool outright, always offer it. Keyword routing
    # cannot anticipate every phrasing, and silently withholding a tool the
    # user asked for by name looks like the model refusing to work.
    lowered = message.casefold()
    names.update(definition["function"]["name"] for definition in definitions
                 if definition["function"]["name"] in lowered)
    if not names and not language.is_greeting(message):
        # Nothing matched, and an empty tool list is the worst possible
        # answer: Aura cannot even look before saying she cannot help. These
        # are all read-only, so an unrouted request can still be understood
        # without anything being changed on the strength of a guess.
        # A greeting is the one case where nothing really is the right
        # answer: "tere" is not a request to go and look at anything.
        names.update(FALLBACK_TOOLS)
    return [definition for definition in definitions if definition["function"]["name"] in names]

#: Questions that cannot be answered without looking. A count or a size is
#: a fact about a file, not an opinion about it.
MEASURING_WORDS = ("mitu", "kui suur", "kui pikk", "kui palju", "how many",
                   "how large", "how big", "how long", "what size", "line count")

def question_needs_looking(message: str, projects: list[str]) -> bool:
    """Does this question ask for something only the workspace can answer?

    The first attempt at this counted the words *file*, *folder* and
    *project*, and that was too loose: "How does my project look these
    days?" is conversation, and demanding a tool for it burns the retry
    budget proving something nobody asked about — the exact failure an
    existing test was written to prevent.

    What actually separates the two is whether the question asks for a
    **fact**: a named file, a real project folder, or a count or size.
    """
    lowered = str(message).casefold()
    if re.search(r"[\w.-]+\.(?:py|json|toml|md|txt|html|htm|css|js|ts|tsx|jsx|yaml|yml)\b",
                 lowered):
        return True
    if any(word in lowered for word in MEASURING_WORDS):
        return True
    return any(re.search(rf"(?<![\w-]){re.escape(name.casefold())}(?![\w-])", lowered)
               for name in projects)

