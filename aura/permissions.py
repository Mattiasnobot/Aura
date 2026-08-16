from __future__ import annotations

import json
import os
import shutil
import socket
import threading
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .errors import AuraError
from .store import Database


CAPABILITIES = {"read_folder", "write_folder", "reach_domain"}
# Only these are scoped to a folder; the rest grant the capability itself.
PATH_CAPABILITIES = {"read_folder", "write_folder"}
# Scoped to a hostname instead: the grant covers that host and its subdomains.
HOST_CAPABILITIES = {"reach_domain"}
MODES = {"once", "session", "project", "persistent"}


class PermissionDenied(AuraError, PermissionError):
    """Raised when a capability was used without an active grant."""


class PermissionRefused(AuraError, ValueError):
    """Raised when a grant may not be created at all, whatever the user says."""


def _forbidden_roots() -> list[Path]:
    """Locations no grant may ever cover, even if the user asks for them.

    Granting a whole drive or a system/credential directory to a model-driven
    agent cannot be made safe by a confirmation dialog, so it is refused
    outright rather than left to a click.
    """
    roots: list[Path] = []
    for variable in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
                     "ProgramData"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    home = Path.home()
    roots.extend([
        home / ".ssh", home / ".aws", home / ".config" / "gcloud",
        home / "AppData" / "Roaming" / "Microsoft" / "Crypto",
        home / "AppData" / "Local" / "Microsoft" / "Credentials",
        home / "AppData" / "Roaming" / "Microsoft" / "Credentials",
        Path("/etc"), Path("/boot"), Path("/sys"), Path("/proc"),
        Path("/private/etc"), Path("/System"), Path("/Library/Keychains"),
    ])
    return roots


def normalize_host(target: str) -> str:
    """Reduce whatever the user typed to a bare, comparable hostname.

    Accepts "https://example.com/path", "Example.COM:443", or "example.com".
    A grant is stored as the hostname alone so it can never be widened later by
    a path, a port, or a differently-cased spelling.
    """
    raw = str(target).strip()
    if not raw:
        raise PermissionRefused("Name the domain Aura may reach.")
    if "//" not in raw:
        raw = "//" + raw
    parsed = urlsplit(raw if "://" in raw else "https:" + raw)
    host = (parsed.hostname or "").strip().strip(".").casefold()
    if not host:
        raise PermissionRefused(f"{target!r} is not a domain name.")
    if parsed.username or parsed.password:
        raise PermissionRefused("A domain grant cannot carry credentials.")
    if "*" in host:
        raise PermissionRefused("Wildcard domains cannot be granted; name the host itself.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PermissionRefused(f"{target!r} is not a valid domain name.") from exc
    if "." not in host:
        raise PermissionRefused(
            f"{host!r} is not a public domain. Aura reaches localhost without a grant.")
    return host


def is_private_address(address: str) -> bool:
    """Would reaching this address mean reaching the user's own machine or LAN?"""
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return bool(parsed.is_private or parsed.is_loopback or parsed.is_link_local
                or parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified)


def reject_unsafe_host(host: str) -> None:
    """Refuse a domain that resolves onto the local machine or private network.

    A public name may still point at 127.0.0.1 or a router at 192.168.x.x, and
    a grant dialog showing only the name gives the user no way to see that. This
    is checked when the grant is made and again on every request, because DNS
    can change between the two.
    """
    if is_private_address(host):
        raise PermissionRefused(
            f"{host} is a local or private address, not a public domain.")
    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as exc:
        raise PermissionRefused(f"{host} could not be resolved: {exc}") from exc
    private = sorted(address for address in resolved if is_private_address(address))
    if private:
        raise PermissionRefused(
            f"{host} resolves to a private address ({private[0]}), so reaching it "
            "would mean reaching this machine or its network.")


class PermissionStore:
    """Durable, revocable grants for capabilities outside the safe workspace.

    Nothing outside `aura-workspace` is reachable unless a grant exists here.
    A grant records an absolute, symlink-resolved root, so it can never be
    widened later by a relative path, a `..`, or a link planted inside it.
    """

    def __init__(self, path: Path, session_id: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or uuid4().hex
        self._lock = threading.RLock()
        self._grants: list[dict] = []
        self.load()

    # ------------------------------------------------------------------ store

    def load(self) -> None:
        with self._lock:
            self._grants = []
            if not self.path.exists():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            if isinstance(data, dict) and isinstance(data.get("grants"), list):
                self._grants = [item for item in data["grants"] if isinstance(item, dict)]

    def save(self) -> None:
        with self._lock:
            payload = {"version": 1, "grants": self._grants}
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
            temporary.replace(self.path)

    # ----------------------------------------------------------------- grants

    @staticmethod
    def _resolve(target: str | Path) -> Path:
        return Path(target).expanduser().resolve(strict=False)

    @classmethod
    def _reject_unsafe_root(cls, resolved: Path) -> None:
        if resolved.parent == resolved:
            raise PermissionRefused("A whole drive or filesystem root cannot be granted.")
        for forbidden in _forbidden_roots():
            try:
                candidate = forbidden.expanduser().resolve(strict=False)
            except OSError:
                continue
            if resolved == candidate or candidate in resolved.parents \
                    or resolved in candidate.parents:
                raise PermissionRefused(
                    f"{resolved} overlaps a protected system or credential location.")

    def grant(self, capability: str, target: str | Path = "", mode: str = "once",
              *, project: str | None = None) -> dict:
        capability = str(capability).strip()
        mode = str(mode).strip().casefold()
        if capability not in CAPABILITIES:
            raise PermissionRefused(f"Unknown capability: {capability}")
        if mode not in MODES:
            raise PermissionRefused(f"Unknown grant mode: {mode}")
        if capability in PATH_CAPABILITIES:
            resolved = self._resolve(target)
            if not resolved.is_dir():
                raise PermissionRefused(f"{resolved} is not an existing folder.")
            self._reject_unsafe_root(resolved)
            root = str(resolved)
        elif capability in HOST_CAPABILITIES:
            root = normalize_host(target)
            reject_unsafe_host(root)
        else:
            # Extension point: a capability with nothing to scope by path is
            # granted as itself. None exist today; every current capability is
            # folder-scoped.
            root = ""
        record = {
            "id": uuid4().hex[:12],
            "capability": capability,
            "root": root,
            "mode": mode,
            "project": project,
            "session": self.session_id if mode in {"once", "session"} else None,
            "granted_at": datetime.now(timezone.utc).isoformat(),
            "used": 0,
            "revoked": False,
        }
        with self._lock:
            self._grants.append(record)
            self.save()
            return dict(record)

    def _live(self, grant: dict) -> bool:
        if grant.get("revoked"):
            return False
        mode = grant.get("mode")
        if mode in {"once", "session"}:
            # A session grant dies with the process that created it, so a
            # restart never silently restores earlier access.
            if grant.get("session") != self.session_id:
                return False
        if mode == "once" and int(grant.get("used", 0)) >= 1:
            return False
        return True

    def check(self, capability: str, target: str | Path = "",
              *, project: str | None = None, consume: bool = True) -> dict:
        """Return the grant permitting this access, or raise PermissionDenied."""
        scoped = capability in PATH_CAPABILITIES
        host_scoped = capability in HOST_CAPABILITIES
        resolved = self._resolve(target) if scoped else None
        host = normalize_host(target) if host_scoped else ""
        with self._lock:
            for grant in self._grants:
                if grant.get("capability") != capability or not self._live(grant):
                    continue
                if grant.get("mode") == "project" and grant.get("project") not in (None, project):
                    continue
                if scoped:
                    root = Path(str(grant.get("root", "")))
                    if resolved != root and root not in resolved.parents:
                        continue
                if host_scoped:
                    # A grant on example.com covers its subdomains, so a redirect
                    # to www. does not turn into a second permission question.
                    granted = str(grant.get("root", "")).casefold()
                    if host != granted and not host.endswith("." + granted):
                        continue
                if consume and grant.get("mode") == "once":
                    grant["used"] = int(grant.get("used", 0)) + 1
                    self.save()
                return dict(grant)
        if host_scoped:
            raise PermissionDenied(
                f"Aura has no permission to reach {host}. Grant that domain under "
                "Permissions if you want her to read it.")
        where = f" at {resolved}" if scoped else ""
        raise PermissionDenied(
            f"Aura has no active permission to {capability}{where}.")

    def revoke(self, grant_id: str) -> dict:
        with self._lock:
            for grant in self._grants:
                if grant.get("id") == str(grant_id):
                    grant["revoked"] = True
                    grant["revoked_at"] = datetime.now(timezone.utc).isoformat()
                    self.save()
                    return dict(grant)
        raise KeyError("Permission not found")

    def revoke_all(self) -> int:
        """Emergency stop: withdraw every outside-the-workspace permission."""
        with self._lock:
            stamp = datetime.now(timezone.utc).isoformat()
            count = 0
            for grant in self._grants:
                if not grant.get("revoked"):
                    grant["revoked"] = True
                    grant["revoked_at"] = stamp
                    count += 1
            if count:
                self.save()
            return count

    def forget_old_revocations(self, days: int = 90) -> int:
        """Drop long-revoked grants. They carry no recovery value, but the
        window is generous so the audit trail stays useful for a while."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        with self._lock:
            keep = []
            removed = 0
            for grant in self._grants:
                revoked_at = grant.get("revoked_at")
                if grant.get("revoked") and revoked_at:
                    try:
                        when = datetime.fromisoformat(str(revoked_at))
                    except ValueError:
                        keep.append(grant)
                        continue
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if when < cutoff:
                        removed += 1
                        continue
                keep.append(grant)
            if removed:
                self._grants = keep
                self.save()
            return removed

    def active(self) -> list[dict]:
        with self._lock:
            return [dict(grant) for grant in self._grants if self._live(grant)]

    def history(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return [dict(grant) for grant in self._grants[-max(1, int(limit)):]]


class ExternalReader:
    """Read-only access to folders the user has explicitly granted."""

    MAX_READ_BYTES = 1_000_000

    def __init__(self, permissions: PermissionStore) -> None:
        self.permissions = permissions

    def _allowed(self, target: str | Path, project: str | None) -> Path:
        resolved = PermissionStore._resolve(target)
        grant = self.permissions.check("read_folder", resolved, project=project,
                                       consume=False)
        root = Path(str(grant["root"]))
        # Re-resolve after the grant check so a symlink inside the granted
        # folder cannot be used to step outside it.
        final = resolved.resolve(strict=False)
        if final != root and root not in final.parents:
            raise PermissionDenied(f"{final} resolves outside the granted folder.")
        return final

    def list_files(self, target: str | Path, *, project: str | None = None,
                   limit: int = 500) -> list[str]:
        base = self._allowed(target, project)
        if not base.is_dir():
            raise NotADirectoryError(str(base))
        names: list[str] = []
        for item in sorted(base.rglob("*")):
            if len(names) >= max(1, int(limit)):
                break
            if item.is_file() and not item.is_symlink():
                names.append(item.relative_to(base).as_posix())
        return names

    def read_file(self, target: str | Path, *, project: str | None = None) -> str:
        final = self._allowed(target, project)
        if not final.is_file():
            raise FileNotFoundError(str(final))
        if final.stat().st_size > self.MAX_READ_BYTES:
            raise ValueError("File is larger than Aura's 1 MB external read limit.")
        return final.read_text(encoding="utf-8")


class ExternalWriter:
    """Write into granted folders, always recoverably.

    A write outside the workspace loses the sandbox's protection, so the
    previous version is copied into Aura's history first and every change is
    journalled with its absolute path. Nothing here deletes anything.
    """

    MAX_WRITE_BYTES = 1_000_000
    CAPABILITY = "write_folder"

    def __init__(self, permissions: PermissionStore, history: Path,
                 change_log) -> None:
        self.permissions = permissions
        self.history = Path(history)
        self.history.mkdir(parents=True, exist_ok=True)
        # A Database keeps every external write in the same transactional store
        # as workspace changes; a Path is accepted so older callers still work.
        if isinstance(change_log, Database):
            self.db = change_log
        else:
            self.db = Database(Path(change_log).parent / "aura.db")
        self._lock = threading.RLock()

    def _allowed(self, target: str | Path, project: str | None) -> Path:
        resolved = PermissionStore._resolve(target)
        grant = self.permissions.check(self.CAPABILITY, resolved, project=project,
                                       consume=False)
        root = Path(str(grant["root"]))
        final = resolved.resolve(strict=False)
        if final != root and root not in final.parents:
            raise PermissionDenied(f"{final} resolves outside the granted folder.")
        if final.is_dir():
            raise IsADirectoryError(str(final))
        return final

    def write_file(self, target: str | Path, content: str, *,
                   project: str | None = None, task_id: str | None = None) -> dict:
        data = str(content)
        if len(data.encode("utf-8")) > self.MAX_WRITE_BYTES:
            raise ValueError("Content is larger than Aura's 1 MB external write limit.")
        final = self._allowed(target, project)
        with self._lock:
            change_id = uuid4().hex
            backup = None
            existed = final.is_file()
            if existed:
                if final.is_symlink():
                    raise PermissionDenied("Refusing to write through a symbolic link.")
                backup = f"ext_{change_id}.bak"
                shutil.copy2(final, self.history / backup)
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_text(data, encoding="utf-8")
            self.db.add_external_change({
                "id": change_id, "path": str(final), "backup": backup,
                "created": not existed, "task_id": task_id,
                "time": datetime.now(timezone.utc).isoformat(),
                "undone": False,
            })
            return {"path": str(final), "bytes": len(data.encode("utf-8")),
                    "created": not existed, "change_id": change_id}

    def changes(self, limit: int = 20) -> list[dict]:
        return self.db.external_changes(max(1, int(limit)))

    def undo_last(self) -> dict:
        """Restore the newest external write that has not been undone yet."""
        with self._lock:
            target_entry = self.db.last_external_change()
            if target_entry is None:
                raise ValueError("There is no external change left to undo.")
            path = Path(str(target_entry["path"]))
            # The grant must still be active; an undo is another write.
            self.permissions.check(self.CAPABILITY, path, consume=False)
            if target_entry.get("backup"):
                shutil.copy2(self.history / str(target_entry["backup"]), path)
                restored = "restored"
            else:
                if path.is_file():
                    path.unlink()
                restored = "removed"
            self.db.mark_external_undone(str(target_entry["id"]))
            return {"path": str(path), "action": restored,
                    "change_id": target_entry.get("id")}
