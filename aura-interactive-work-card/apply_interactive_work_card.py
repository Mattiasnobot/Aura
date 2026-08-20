from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path

EXPECTED = {'app.js': 'fbf335d6f19bb6609fdb064788dae313efb81fcaa7ee17efc293bc0c4ec53be0', 'styles.css': 'bb510d8329ecf4884eb7283144837108ccb2cce0f18035a11210640b5a02cf5d'}
INSTALLED = {'app.js': 'a8a2efd27d741266a16411bc906a6df921959bb3107bec963f3358df40fccc13', 'styles.css': '5b926d32042244d60de9c61ee02ffa5b2fe2af259973d14bc98ef9167a98da54'}
FILES = ("app.js", "styles.css")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_repo() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        Path.cwd(),
        here.parent,
        here.parent / "aura",
    ]
    for candidate in candidates:
        if (candidate / "aura" / "web" / "app.js").is_file() and (candidate / "aura" / "web" / "styles.css").is_file():
            return candidate
    raise SystemExit(
        "Could not find the Aura repo. Run this from the Aura repo root, or keep "
        "aura-interactive-work-card next to the Aura folder."
    )


def backup_path(target: Path) -> Path:
    return target.with_name(target.name + ".interactive-work-card.bak")


def restore(repo: Path) -> None:
    restored = []
    for name in FILES:
        target = repo / "aura" / "web" / name
        backup = backup_path(target)
        if not backup.is_file():
            raise SystemExit(f"Missing backup: {backup}")
        shutil.copy2(backup, target)
        restored.append(str(target))
    print("Restored:")
    for item in restored:
        print(" -", item)


def apply(repo: Path) -> None:
    package_dir = Path(__file__).resolve().parent / "files" / "aura" / "web"

    states = {}
    for name in FILES:
        target = repo / "aura" / "web" / name
        digest = sha256(target)
        if digest == INSTALLED[name]:
            states[name] = "already"
            continue
        if digest != EXPECTED[name]:
            raise SystemExit(
                f"Refusing to overwrite {target}.\n"
                f"I expected SHA-256 {EXPECTED[name]} but found {digest}.\n"
                "That means the file changed after the version you uploaded. "
                "Send me the newer file and I will rebase the UI change."
            )
        states[name] = "ready"

    if all(value == "already" for value in states.values()):
        print("Interactive work card is already installed.")
        return

    # Back up both files before writing either one.
    for name in FILES:
        target = repo / "aura" / "web" / name
        backup = backup_path(target)
        if not backup.exists():
            shutil.copy2(target, backup)

    for name in FILES:
        if states[name] == "ready":
            source = package_dir / name
            target = repo / "aura" / "web" / name
            shutil.copy2(source, target)

    # Check the JavaScript parser when Node is available. Aura does not require
    # Node for this UI, so absence is not an installation failure.
    node = shutil.which("node")
    if node:
        result = subprocess.run(
            [node, "--check", str(repo / "aura" / "web" / "app.js")],
            capture_output=True, text=True
        )
        if result.returncode:
            # Put the originals back automatically rather than leaving a broken UI.
            for name in FILES:
                target = repo / "aura" / "web" / name
                shutil.copy2(backup_path(target), target)
            raise SystemExit("JavaScript syntax check failed; originals restored.\n" + result.stderr)

    print("Installed Aura interactive work card.")
    print("Changed only:")
    print(" - aura/web/app.js")
    print(" - aura/web/styles.css")
    print("Backups:")
    print(" - aura/web/app.js.interactive-work-card.bak")
    print(" - aura/web/styles.css.interactive-work-card.bak")
    print("\nRestart Aura, then try a project build that produces an approved file plan.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true", help="Restore the pre-change backups.")
    args = parser.parse_args()
    repo = find_repo()
    if args.restore:
        restore(repo)
    else:
        apply(repo)


if __name__ == "__main__":
    main()
