"""Personal memory and permission methods of the local interface.

Split out of `web_bridge.py`, which had grown to 64 HTTP-exposed methods in one
class. These are mixed back into `AuraWebBridge`, so every method keeps the name
the HTTP layer already calls it by; only the file it lives in changed.
"""

from __future__ import annotations


from .permissions import PermissionRefused
from . import services


class MemoryBridge:
    def get_personal_memory(self) -> dict:
        try:
            self.agent.memory.load()
            memories = self.agent.memory.profile_memories()
            return {
                "ok": True,
                "name": self.agent.memory.data.get("name"),
                "preferences": dict(self.agent.memory.data.get("preferences", {})),
                "learning_enabled": bool(self.agent.config.data.get("learn_from_conversations", True)),
                "memories": memories,
                "count": len(memories),
                "conflicts": self.agent.memory.conflicting_pairs(),
                "categories": sorted(self.agent.memory.PROFILE_CATEGORIES),
                "privacy": (("Automatic learning is on. " if self.agent.config.data.get("learn_from_conversations", True)
                             else "Automatic learning is off. ") +
                            "Only clear, non-sensitive facts are eligible; everything stays local and editable."),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "memories": [], "count": 0}

    def add_personal_memory(self, category: str, value: str) -> dict:
        try:
            item = self.agent.memory.learn_fact(
                str(category), str(value), source="Added from What Aura knows",
                confidence=1.0, explicit=True,
            )
            self.agent.log.record("remember_personal_fact", "ok", memory_id=item["id"],
                                  category=item["category"], value=item["value"])
            self._push("memory_changed", action="added", memory=item)
            return {"ok": True, "memory": item}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def list_permissions(self) -> dict:
        store = self.agent.permissions
        return {"ok": True, "active": store.active(), "history": store.history(30),
                "note": ("Aura can only read folders you grant here. It cannot grant "
                         "itself access, and nothing outside the workspace is readable "
                         "by default.")}

    def grant_folder_access(self, path: str, mode: str = "session",
                            project: str | None = None, writable: bool = False) -> dict:
        """Grant access to one folder. Only ever called by the user.

        Write access is a separate grant rather than something a read grant
        quietly implies, so the Permissions list always shows it explicitly.
        """
        try:
            grants = [self.agent.permissions.grant("read_folder", str(path), str(mode),
                                                   project=project or None)]
            if writable:
                grants.append(self.agent.permissions.grant(
                    "write_folder", str(path), str(mode), project=project or None))
            for grant in grants:
                self.agent.log.record("grant_folder_access", "ok", path=grant["root"],
                                      capability=grant["capability"],
                                      mode=grant["mode"], grant_id=grant["id"])
                self._push("permissions_changed", action="granted", grant=grant)
            return {"ok": True, "grant": grants[0], "grants": grants}
        except (PermissionRefused, OSError, ValueError) as exc:
            self.agent.log.record("grant_folder_access", "error", path=str(path),
                                  error=str(exc))
            return {"ok": False, "error": str(exc)}

    def grant_domain_access(self, domain: str, mode: str = "session",
                            project: str | None = None) -> dict:
        """Allow Aura to read one domain. Only ever called by the user.

        There is deliberately no tool for this: as with folders, the model can
        use a grant but can never ask for one, so nothing it reads on the
        network can talk Aura into reaching further.
        """
        try:
            grant = self.agent.permissions.grant("reach_domain", str(domain), str(mode),
                                                 project=project or None)
            self.agent.log.record("grant_domain_access", "ok", domain=grant["root"],
                                  mode=grant["mode"], grant_id=grant["id"])
            self._push("permissions_changed", action="granted", grant=grant)
            self._push("network", **self.network_status()["network"])
            return {"ok": True, "grant": grant}
        except (PermissionRefused, OSError, ValueError) as exc:
            self.agent.log.record("grant_domain_access", "error", domain=str(domain),
                                  error=str(exc))
            return {"ok": False, "error": str(exc)}

    def autonomy_status(self) -> dict:
        """What background work is allowed right now, and why not when it is not."""
        guard = self.agent.autonomy
        verdict = guard.may_run()
        start, end = guard.quiet_window()
        return {"ok": True, "autonomy": {
            "paused": guard.paused(),
            "allowed": bool(verdict),
            "reason": verdict.reason,
            "quiet_hours": f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}",
            "in_quiet_hours": guard.in_quiet_hours(),
            "runs_today": guard.runs_today(),
            "daily_cap": guard.daily_cap(),
            "run_seconds": guard.run_seconds(),
        }}

    def pause_autonomy(self, paused: bool = True) -> dict:
        """Pause or resume background work. Only the user calls this."""
        if paused:
            self.agent.autonomy.pause("paused from the interface")
        else:
            self.agent.autonomy.resume()
        status = self.autonomy_status()
        self._push("autonomy", **status["autonomy"])
        return status

    def emergency_stop(self) -> dict:
        """Stop everything now: pause background work *and* cancel what is running.

        Pausing alone would let an in-flight run finish, which is not what
        anybody means when they reach for a stop control.
        """
        self.agent.autonomy.pause("emergency stop")
        self.agent.cancel_current()
        self._deny_pending_approvals()
        self.speech.stop()
        self.agent.log.record("emergency_stop", "ok")
        status = self.autonomy_status()
        self._push("autonomy", **status["autonomy"])
        self._push("state", value="idle")
        return status

    def network_status(self) -> dict:
        """Whether Aura can reach anything at all, and exactly what."""
        domains = sorted({str(grant.get("root", "")) for grant in self.agent.permissions.active()
                          if grant.get("capability") == "reach_domain" and grant.get("root")})
        return {"ok": True, "network": {
            "online": bool(domains),
            "domains": domains,
            "services": [{"name": service.name, "domains": list(service.domains),
                          "hint": service.grant_hint}
                         for service in services.services()],
        }}

    def external_changes(self, limit: int = 20) -> dict:
        return {"ok": True, "changes": self.agent.external_writer.changes(int(limit))}

    def undo_external_change(self) -> dict:
        try:
            result = self.agent.external_writer.undo_last()
            self.agent.log.record("undo_external_change", "ok", path=result["path"],
                                  action=result["action"])
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def revoke_folder_access(self, grant_id: str) -> dict:
        try:
            grant = self.agent.permissions.revoke(str(grant_id))
            self.agent.log.record("revoke_folder_access", "ok", path=grant["root"],
                                  grant_id=grant["id"])
            self._push("permissions_changed", action="revoked", grant=grant)
            return {"ok": True, "grant": grant}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    def revoke_all_permissions(self) -> dict:
        count = self.agent.permissions.revoke_all()
        self.agent.log.record("revoke_all_permissions", "ok", revoked=count)
        self._push("permissions_changed", action="revoked_all", revoked=count)
        return {"ok": True, "revoked": count}
