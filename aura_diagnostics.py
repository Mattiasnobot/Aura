from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
os.chdir(PROJECT)

from aura.config import ConfigStore
from aura.provider import LMStudioProvider


def check(label: str, operation) -> bool:
    try:
        value = operation()
        print(f"[OK] {label}: {value}")
        return True
    except Exception as exc:
        print(f"[FAIL] {label}: {exc}")
        return False


def html_interface_check() -> str:
    from aura.http_app import API_METHODS  # noqa: F401
    web_root = Path("aura") / "web"
    required = [
        web_root / "index.html",
        web_root / "styles.css",
        web_root / "avatar-face.js",
        web_root / "app.js",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("missing interface assets: " + ", ".join(missing))
    return f"localhost server ready; {len(API_METHODS)} whitelisted operations; HTML assets ready"


def lm_studio_check() -> str:
    config = ConfigStore(Path("aura-workspace") / ".aura" / "config.json").data
    provider = LMStudioProvider(str(config["lm_studio_url"]), config["model"], timeout=10,
                                temperature=float(config["temperature"]),
                                max_tokens=int(config["max_tokens"]))
    models = provider.available_models()
    if not models:
        raise RuntimeError("server is reachable, but it exposes no models")
    return f"{len(models)} models; selected {provider.selected_model()}"


def main() -> int:
    print("Aura diagnostics")
    print(f"Python {sys.version.split()[0]} on {platform.platform()}")
    results = [
        check("Python version", lambda: "supported" if sys.version_info >= (3, 10)
              else (_ for _ in ()).throw(RuntimeError("Python 3.10+ required"))),
        check("HTML interface", html_interface_check),
        check("LM Studio", lm_studio_check),
    ]
    print("Ready." if all(results) else "One or more checks need attention.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
