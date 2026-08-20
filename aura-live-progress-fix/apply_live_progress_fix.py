from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

EXPECTED = {'aura/web/app.js': '01a80a9ce94f557dc7aca0d690b433db6a80f45f331e22b83913dd2704e002b8', 'aura/web_bridge.py': 'cc8d7e4dd58daf4d136a56e28ba70600fde19ad4850f35b8d07b5b74273409e7'}
INSTALLED = {'aura/web/app.js': 'c827107072549494b414f2831ba5b56c42ad4244001989af52330e7c8ef11074', 'aura/web_bridge.py': '95bcb33775386c7d981a249b3910c1912181585e1b95a18385d9b4ee6188e356'}
FILES = ("aura/web/app.js", "aura/web_bridge.py")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_repo() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [Path.cwd(), here.parent, here.parent / "aura"]
    for candidate in candidates:
        if all((candidate / rel).is_file() for rel in FILES):
            return candidate
    raise SystemExit(
        "Could not find the Aura repo. Run this from the Aura repo root, or keep "
        "aura-live-progress-fix next to the Aura folder."
    )


def backup_path(target: Path) -> Path:
    return target.with_name(target.name + ".live-progress.bak")


def restore(repo: Path) -> None:
    restored = []
    for rel in FILES:
        target = repo / rel
        backup = backup_path(target)
        if not backup.is_file():
            raise SystemExit(f"Missing backup: {backup}")
        shutil.copy2(backup, target)
        restored.append(rel)
    print("Restored:")
    for rel in restored:
        print(" -", rel)


def validate(repo: Path) -> None:
    py = subprocess.run(
        [sys.executable, "-m", "py_compile", str(repo / "aura/web_bridge.py")],
        capture_output=True, text=True
    )
    if py.returncode:
        raise RuntimeError("Python syntax check failed:\n" + py.stderr)

    node = shutil.which("node")
    if node:
        js = subprocess.run(
            [node, "--check", str(repo / "aura/web/app.js")],
            capture_output=True, text=True
        )
        if js.returncode:
            raise RuntimeError("JavaScript syntax check failed:\n" + js.stderr)


def apply(repo: Path) -> None:
    source_root = Path(__file__).resolve().parent / "files"
    states = {}
    for rel in FILES:
        target = repo / rel
        current = digest(target)
        if current == INSTALLED[rel]:
            states[rel] = "already"
        elif current == EXPECTED[rel]:
            states[rel] = "ready"
        else:
            raise SystemExit(
                f"Refusing to overwrite {target}.\n"
                f"Expected SHA-256 {EXPECTED[rel]} but found {current}.\n"
                "That file changed after the version this fix was built from. "
                "Send me the newer copy and I will rebase the fix."
            )

    if all(value == "already" for value in states.values()):
        print("Aura live-progress fix is already installed.")
        return

    # Back up everything before changing anything.
    for rel in FILES:
        target = repo / rel
        backup = backup_path(target)
        if not backup.exists():
            shutil.copy2(target, backup)

    try:
        for rel in FILES:
            if states[rel] == "ready":
                shutil.copy2(source_root / rel, repo / rel)
        validate(repo)
    except Exception as exc:
        for rel in FILES:
            target = repo / rel
            backup = backup_path(target)
            if backup.is_file():
                shutil.copy2(backup, target)
        raise SystemExit(f"Install failed; originals restored.\n{exc}")

    print("Installed Aura live-progress fix.")
    print("Changed only:")
    for rel in FILES:
        print(" -", rel)
    print("\nRestart Aura and repeat a task with an approved plan.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true",
                        help="Restore the pre-fix backups.")
    args = parser.parse_args()
    repo = find_repo()
    if args.restore:
        restore(repo)
    else:
        apply(repo)


if __name__ == "__main__":
    main()
