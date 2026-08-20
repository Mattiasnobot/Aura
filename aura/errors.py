"""One root for everything Aura raises on purpose.

Aura already had seven deliberate exception types, each subclassing whichever
builtin fitted — `SandboxViolation(ValueError)`, `PermissionDenied(PermissionError)`,
`ProviderError(RuntimeError)`, and so on. What was missing is a way to say "an
error Aura raised" as opposed to one that escaped from the standard library, so
callers ended up catching combinations like `(PermissionRefused, OSError,
ValueError)` and hoping the list was complete.

`AuraError` is added *alongside* those builtin bases rather than replacing them.
That is deliberate: every `except ValueError` already written — in Aura and in
its tests — keeps working exactly as before, while new code can catch
`AuraError` and mean it. Nothing had to be re-raised or re-typed to gain this.

    try:
        ...
    except AuraError as exc:        # anything Aura decided to refuse
        report(str(exc))
"""

from __future__ import annotations


class AuraError(Exception):
    """Base for every error Aura raises deliberately.

    Subclasses keep their builtin base as well, so this is safe to introduce
    without changing what any existing handler catches.
    """


class UserFacingError(AuraError):
    """An error whose message is written to be shown to the user as-is."""
