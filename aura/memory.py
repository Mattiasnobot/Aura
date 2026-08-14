from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class MemoryStore:
    PROFILE_VERSION = 1
    PROFILE_CATEGORIES = {
        "preference", "interest", "goal", "project", "tool", "work_style", "personal",
    }
    SENSITIVE_MARKERS = {
        "password", "passcode", "pin code", "secret", "api key", "access token", "private key",
        "credit card", "bank account", "social security", "passport", "home address", "phone number",
        "email address", "diagnosis", "medical", "medication", "religion", "political party",
        "sexual orientation",
    }
    TRANSIENT_ACTIONS = {
        "build", "create", "delete", "remove", "run", "open", "fix", "edit", "write",
        "move", "copy", "validate", "search", "inspect", "read", "rename", "download",
    }
    LEARNING_PATTERNS = (
        ("preference", r"\bi (?:really )?prefer\s+(.+)", 0.92),
        ("interest", r"\bi (?:really )?(?:like|love|enjoy)\s+(.+)", 0.84),
        ("preference", r"\bi (?:do not|don't|dislike|hate)\s+(.+)", 0.86),
        ("tool", r"\bi (?:usually )?(?:use|work with)\s+(.+)", 0.84),
        ("project", r"\bi(?:'m| am) working on\s+(.+)", 0.9),
        ("goal", r"\bmy (?:current )?goal is\s+(.+)", 0.94),
        ("work_style", r"\bi want aura to\s+(.+)", 0.88),
        ("work_style", r"\bi(?:'d| would) like aura to\s+(.+)", 0.88),
    )

    def __init__(self, path: Path, conversation_limit: int = 30) -> None:
        self.path = path
        self.limit = conversation_limit
        self._lock = threading.RLock()
        self.data = {"name": None, "preferences": {}, "conversation": [],
                     "profile_memories": [], "profile_memory_version": self.PROFILE_VERSION}
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
                except (json.JSONDecodeError, OSError):
                    pass
            if not isinstance(self.data.get("preferences"), dict):
                self.data["preferences"] = {}
            if not isinstance(self.data.get("conversation"), list):
                self.data["conversation"] = []
            if not isinstance(self.data.get("profile_memories"), list):
                self.data["profile_memories"] = []
            self.data["profile_memory_version"] = self.PROFILE_VERSION

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)

    def remember_message(self, role: str, text: str) -> None:
        with self._lock:
            self.data["conversation"].append({
                "role": role, "text": text, "time": datetime.now(timezone.utc).isoformat()
            })
            self.data["conversation"] = self.data["conversation"][-self.limit :]
            self.save()

    def set_name(self, name: str) -> None:
        with self._lock:
            self.data["name"] = name.strip()
            self.save()

    def set_preference(self, key: str, value: str) -> None:
        with self._lock:
            self.data["preferences"][key.strip()] = value.strip()
            self.save()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _clean_fact(value: str) -> str:
        cleaned = " ".join(str(value).replace("\n", " ").split()).strip(" \t\r\n.!?;:,\"'")
        return cleaned[:240]

    @classmethod
    def _is_sensitive(cls, value: str) -> bool:
        lowered = value.casefold()
        if any(marker in lowered for marker in cls.SENSITIVE_MARKERS):
            return True
        if re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", value):
            return True
        if re.search(r"(?:\+?\d[\s().-]*){8,}", value):
            return True
        return False

    @staticmethod
    def _fact_key(category: str, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
        return f"{category}:{normalized}"

    def learn_fact(self, category: str, value: str, *, source: str,
                   confidence: float = 0.8, explicit: bool = False,
                   topic: str = "") -> dict | None:
        category = str(category).casefold().strip()
        cleaned = self._clean_fact(value)
        if category not in self.PROFILE_CATEGORIES:
            raise ValueError("Unsupported memory category")
        if len(cleaned) < 3:
            raise ValueError("Memory is too short")
        if self._is_sensitive(cleaned):
            if explicit:
                raise ValueError("Aura will not store credentials or sensitive personal details in learned memory")
            return None
        key = self._fact_key(category, cleaned)
        now = self._now()
        with self._lock:
            memories = self.data["profile_memories"]
            existing = next((item for item in memories if item.get("key") == key), None)
            if existing:
                existing["last_confirmed"] = now
                existing["confidence"] = max(float(existing.get("confidence", 0)), float(confidence))
                if explicit:
                    existing["confirmed"] = True
                self.save()
                return {**existing, "learning_status": "confirmed"}
            item = {
                "id": uuid4().hex,
                "key": key,
                "category": category,
                "topic": self._clean_fact(topic) if topic else "",
                "value": cleaned,
                "source": self._clean_fact(source)[:400],
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "confirmed": bool(explicit),
                "pinned": False,
                "created": now,
                "updated": now,
                "last_confirmed": now,
            }
            memories.append(item)
            self.data["profile_memories"] = memories[-250:]
            self.save()
            return {**item, "learning_status": "learned"}

    def learn_from_message(self, message: str) -> list[dict]:
        """Learn only explicit, non-sensitive first-person statements."""
        text = " ".join(str(message).split())
        if not 3 <= len(text) <= 1200:
            return []
        learned: list[dict] = []
        for category, pattern, confidence in self.LEARNING_PATTERNS:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = re.split(r"(?<=[.!?])\s+", match.group(1), maxsplit=1)[0]
            first_word = re.sub(r"[^a-z]", "", value.casefold().split(maxsplit=1)[0]) if value else ""
            if category == "work_style" and first_word in self.TRANSIENT_ACTIONS:
                continue
            if "dislike|hate" in pattern or "do not|don't" in pattern:
                value = "Dislikes " + value
            item = self.learn_fact(category, value, source=text, confidence=confidence)
            if item and item.get("learning_status") == "learned":
                learned.append(item)
        return learned

    def profile_memories(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self.data.get("profile_memories", [])]

    def relevant_memories(self, query: str, limit: int = 12) -> list[dict]:
        words = {word for word in re.findall(r"[a-z0-9]{3,}", query.casefold())
                 if word not in {"the", "and", "that", "this", "with", "from", "have", "what"}}
        scored = []
        for item in self.profile_memories():
            haystack = f"{item.get('category', '')} {item.get('topic', '')} {item.get('value', '')}".casefold()
            overlap = sum(1 for word in words if word in haystack)
            general = item.get("category") in {"preference", "work_style"}
            score = overlap * 5 + (8 if item.get("pinned") else 0) + (2 if general else 0)
            score += float(item.get("confidence", 0))
            if overlap or general or item.get("pinned"):
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], pair[1].get("updated", "")), reverse=True)
        return [item for _, item in scored[:max(1, min(int(limit), 30))]]

    def update_profile_memory(self, memory_id: str, *, value: str | None = None,
                              category: str | None = None, pinned: bool | None = None) -> dict:
        with self._lock:
            item = next((entry for entry in self.data["profile_memories"]
                         if entry.get("id") == str(memory_id)), None)
            if item is None:
                raise KeyError("Memory not found")
            next_category = str(category or item.get("category", "personal")).casefold().strip()
            if next_category not in self.PROFILE_CATEGORIES:
                raise ValueError("Unsupported memory category")
            next_value = self._clean_fact(value if value is not None else item.get("value", ""))
            if len(next_value) < 3:
                raise ValueError("Memory is too short")
            if self._is_sensitive(next_value):
                raise ValueError("Aura will not store credentials or sensitive personal details in learned memory")
            item.update({"category": next_category, "value": next_value,
                         "key": self._fact_key(next_category, next_value),
                         "confirmed": True, "updated": self._now(),
                         "last_confirmed": self._now()})
            if pinned is not None:
                item["pinned"] = bool(pinned)
            self.save()
            return dict(item)

    def forget_profile_memory(self, memory_id: str) -> dict:
        with self._lock:
            memories = self.data["profile_memories"]
            for index, item in enumerate(memories):
                if item.get("id") == str(memory_id):
                    removed = memories.pop(index)
                    self.save()
                    return dict(removed)
            raise KeyError("Memory not found")

    def find_profile_memories(self, query: str) -> list[dict]:
        needle = self._clean_fact(query).casefold()
        if not needle:
            return []
        return [item for item in self.profile_memories()
                if needle in f"{item.get('category', '')} {item.get('topic', '')} {item.get('value', '')}".casefold()]
