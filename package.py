"""Build a distributable Aura folder as a single zip.

Run it with `python package.py`. The result is `dist/aura-<version>.zip`,
containing exactly what someone needs to run Aura and nothing else.

The rule that matters most here is what is *left out*. Aura's workspace holds
the user's files, `.aura` holds their conversations, memory, permissions, and
undo history, and the logs hold whatever went wrong on their machine. None of
that may ever travel inside a package, so the exclusions are matched against
every path and the result is verified after the archive is written.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from aura import __version__


PROJECT = Path(__file__).resolve().parent
DIST = PROJECT / "dist"

# Everything a fresh install needs, and only that.
INCLUDED_FILES = ("aura_app.py", "aura_diagnostics.py", "Start Aura.bat",
                  "README.md", "ASSUMPTIONS.md", "requirements-voice.txt",
                  "requirements-neural-voice.txt")
INCLUDED_PACKAGES = ("aura",)

# Personal data and build noise. Matched against each path part, so a name
# appearing at any depth is excluded.
EXCLUDED_NAMES = {
    "aura-workspace", ".aura", ".aura-trash", "aura-voices", "outputs", "work",
    "__pycache__", ".git", ".venv", "dist", "tests", "node_modules",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm",
                     ".jsonl", ".jsonl.migrated", ".bak"}
# Files whose *name* is enough to reject them, wherever they appear.
EXCLUDED_FILENAMES = {"config.json", "memory.json", "permissions.json",
                      "aura.db", "web-url.txt"}


def is_private(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDED_NAMES:
        return True
    if path.name in EXCLUDED_FILENAMES:
        return True
    return path.suffix in EXCLUDED_SUFFIXES


def collect(root: Path) -> list[Path]:
    """Every file to ship, as paths relative to the project root."""
    chosen: list[Path] = []
    for name in INCLUDED_FILES:
        candidate = root / name
        if candidate.is_file() and not is_private(Path(name)):
            chosen.append(Path(name))
    for package in INCLUDED_PACKAGES:
        for path in sorted((root / package).rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if not is_private(relative):
                chosen.append(relative)
    return chosen


def build(root: Path = PROJECT, destination: Path | None = None) -> Path:
    files = collect(root)
    if not any(path.name == "aura_app.py" for path in files):
        raise RuntimeError("aura_app.py is missing; this is not an Aura checkout.")
    for required in ("index.html", "app.js", "styles.css", "avatar-face.js"):
        if not any(path.name == required for path in files):
            raise RuntimeError(f"The interface asset {required} is missing.")

    target = destination or (DIST / f"aura-{__version__}.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(root / relative, f"aura-{__version__}/{relative.as_posix()}")

    # Verify the written archive rather than trusting the loop above: this is
    # the check that a package never carries someone's data.
    with zipfile.ZipFile(target) as archive:
        for name in archive.namelist():
            inside = Path(name).relative_to(f"aura-{__version__}")
            if is_private(inside):
                target.unlink(missing_ok=True)
                raise RuntimeError(f"Refusing to ship a private path: {name}")
    return target


def main() -> int:
    built = build()
    size = built.stat().st_size / 1024
    with zipfile.ZipFile(built) as archive:
        count = len(archive.namelist())
    print(f"Aura {__version__} packaged: {built}")
    print(f"{count} files, {size:.0f} KB")
    print("No workspace, conversation, memory, permission, or log file is included.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
