from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path

EXPECTED = {'app.js': '63f79b81715e19d59b62736c19e129a8336896da4a0b929752c8c342ae3172e7', 'styles.css': '5b926d32042244d60de9c61ee02ffa5b2fe2af259973d14bc98ef9167a98da54'}
INSTALLED = {'app.js': '01a80a9ce94f557dc7aca0d690b433db6a80f45f331e22b83913dd2704e002b8', 'styles.css': '7a68c76ae7e8688c91c2471190520851ed919b2d606908028d7f36a389b77a97'}
FILES = ("app.js", "styles.css")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_repo() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [Path.cwd(), here.parent, here.parent / "aura"]
    for candidate in candidates:
        web = candidate / "aura" / "web"
        if (web / "app.js").is_file() and (web / "styles.css").is_file():
            return candidate
    raise SystemExit(
        "Could not find the Aura repo. Run this from the Aura repo root, or keep "
        "aura-adaptive-result-cards next to the Aura folder."
    )


def backup_path(target: Path) -> Path:
    return target.with_name(target.name + ".adaptive-result-cards.bak")


def restore(repo: Path) -> None:
    for name in FILES:
        target = repo / "aura" / "web" / name
        backup = backup_path(target)
        if not backup.is_file():
            raise SystemExit(f"Missing backup: {backup}")
    for name in FILES:
        target = repo / "aura" / "web" / name
        shutil.copy2(backup_path(target), target)
    print("Restored the UI state from before adaptive result cards.")


def apply(repo: Path) -> None:
    source_dir = Path(__file__).resolve().parent / "files" / "aura" / "web"
    states = {}
    for name in FILES:
        target = repo / "aura" / "web" / name
        digest = sha256(target)
        if digest == INSTALLED[name]:
            states[name] = "already"
        elif digest == EXPECTED[name]:
            states[name] = "ready"
        else:
            raise SystemExit(
                f"Refusing to overwrite {target}.\n"
                f"Expected SHA-256 {EXPECTED[name]} but found {digest}.\n"
                "That means the UI changed after the version this patch was built on. "
                "Send me the newer app.js/styles.css and I will rebase it instead of guessing."
            )
    if all(value == "already" for value in states.values()):
        print("Adaptive result cards are already installed.")
        return

    # Back up both files before writing either one.
    for name in FILES:
        target = repo / "aura" / "web" / name
        backup = backup_path(target)
        if not backup.exists():
            shutil.copy2(target, backup)

    for name in FILES:
        if states[name] == "ready":
            shutil.copy2(source_dir / name, repo / "aura" / "web" / name)

    node = shutil.which("node")
    if node:
        result = subprocess.run(
            [node, "--check", str(repo / "aura" / "web" / "app.js")],
            capture_output=True, text=True,
        )
        if result.returncode:
            for name in FILES:
                target = repo / "aura" / "web" / name
                shutil.copy2(backup_path(target), target)
            raise SystemExit("JavaScript syntax check failed; originals restored.\n" + result.stderr)

    print("Installed adaptive Aura result cards.")
    print("Changed only:")
    print(" - aura/web/app.js")
    print(" - aura/web/styles.css")
    print("No Python/backend files were changed.")
    print("Restart Aura to load the new UI.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    repo = find_repo()
    restore(repo) if args.restore else apply(repo)


if __name__ == "__main__":
    main()
