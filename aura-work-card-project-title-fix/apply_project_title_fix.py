from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

EXPECTED = 'a8a2efd27d741266a16411bc906a6df921959bb3107bec963f3358df40fccc13'
INSTALLED = '63f79b81715e19d59b62736c19e129a8336896da4a0b929752c8c342ae3172e7'

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def find_repo() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (Path.cwd(), here.parent, here.parent / "aura"):
        target = candidate / "aura" / "web" / "app.js"
        if target.is_file():
            return candidate
    raise SystemExit("Could not find Aura. Run this from the Aura repo root or keep this folder next to Aura.")

def main() -> None:
    repo = find_repo()
    target = repo / "aura" / "web" / "app.js"
    backup = target.with_name(target.name + ".project-title-fix.bak")
    digest = sha256(target)
    if digest == INSTALLED:
        print("Project-title fix is already installed.")
        return
    if digest != EXPECTED:
        raise SystemExit(
            f"Refusing to overwrite {target}.\n"
            f"Expected SHA-256 {EXPECTED} but found {digest}.\n"
            "Your app.js differs from the interactive-work-card version this hotfix targets. "
            "Send me the current file and I will rebase the fix."
        )
    if not backup.exists():
        shutil.copy2(target, backup)
    source = Path(__file__).resolve().parent / "files" / "aura" / "web" / "app.js"
    shutil.copy2(source, target)
    node = shutil.which("node")
    if node:
        checked = subprocess.run([node, "--check", str(target)], capture_output=True, text=True)
        if checked.returncode:
            shutil.copy2(backup, target)
            raise SystemExit("JavaScript syntax check failed; original restored.\n" + checked.stderr)
    print("Installed project-title hotfix.")
    print("Changed only: aura/web/app.js")
    print("Backup:", backup)

if __name__ == "__main__":
    main()
