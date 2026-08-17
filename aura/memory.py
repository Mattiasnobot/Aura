from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class MemoryStore:
    PROFILE_VERSION = 1
    MAX_HISTORY = 5
    PROFILE_CATEGORIES = {
        "preference", "interest", "goal", "project", "tool", "work_style", "personal",
    }
    SENSITIVE_MARKERS = {
        "password", "passcode", "pin code", "secret", "api key", "access token", "private key",
        "credit card", "bank account", "social security", "passport", "home address", "phone number",
        "email address", "diagnosis", "medical", "medication", "religion", "political party",
        "sexual orientation",
    }
    COMPARISON_STOPWORDS = {
        "the", "and", "that", "this", "with", "from", "have", "what", "for", "you",
        "your", "aura", "prefer", "prefers", "like", "likes", "want", "wants", "use",
        "uses", "using", "always", "over", "previous", "when", "into", "more",
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
            self._adopt_legacy_preferences()

    def save(self) -> None:
        with self._lock:
            # Derived here rather than at each of the five places a memory can
            # change: a view that is rebuilt on every write cannot go stale,
            # and forgetting one call site is exactly the failure this phase is
            # about.
            self._refresh_preference_view()
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

    #: How a preference reads once it is a memory like every other fact.
    PREFERENCE_SHAPE = "{key} = {value}"
    _PREFERENCE_PATTERN = re.compile(r"^\s*([^=:]{1,60}?)\s*[=:]\s*(.+?)\s*$")

    def set_preference(self, key: str, value: str) -> None:
        """Remember a preference as an ordinary personal memory.

        A preference used to live in a flat `preferences` dict while the same
        kind of fact learned in conversation became a `profile_memory` — two
        stores for one thing, which is why Aura Mind drew one preference as two
        unrelated nodes. Only the memory store can be edited, confirmed,
        reverted, or exported, so that is the one that survived.
        """
        key, value = str(key).strip(), str(value).strip()
        if not key or not value:
            return
        with self._lock:
            self.learn_fact("preference", self.PREFERENCE_SHAPE.format(key=key, value=value),
                            source="Explicitly remembered as a preference",
                            confidence=1.0, explicit=True)
            self._refresh_preference_view()
            self.save()

    def _refresh_preference_view(self) -> None:
        """Rebuild `data["preferences"]` from the memories that now own it.

        Kept as a derived dict rather than deleted, because the provider
        context, Aura Mind, and the memory panel all read that shape; they
        should not each learn a new one for a change they cannot observe.
        """
        view: dict[str, str] = {}
        for item in self.data.get("profile_memories", []):
            if str(item.get("category")) != "preference":
                continue
            match = self._PREFERENCE_PATTERN.match(str(item.get("value", "")))
            if match:
                view.setdefault(match.group(1).strip(), match.group(2).strip())
        self.data["preferences"] = view

    def _adopt_legacy_preferences(self) -> None:
        """Move anything left in the old dict into the memory store, once."""
        stored = self.data.get("preferences") or {}
        known = {self._comparable_fact(str(item.get("value", "")))
                 for item in self.data.get("profile_memories", [])
                 if str(item.get("category")) == "preference"}
        adopted = 0
        for key, value in list(stored.items()):
            phrase = self.PREFERENCE_SHAPE.format(key=str(key).strip(), value=str(value).strip())
            if self._comparable_fact(phrase) in known:
                continue
            if self.learn_fact("preference", phrase,
                               source="Migrated from Aura's earlier preference list",
                               confidence=1.0, explicit=True):
                adopted += 1
        self._refresh_preference_view()
        if adopted:
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

    @classmethod
    def _comparable_fact(cls, value: str) -> tuple[frozenset[str], bool]:
        """Split a fact into its meaningful words and whether it is a negation."""
        lowered = str(value).casefold().strip()
        negated = lowered.startswith("dislikes ")
        if negated:
            lowered = lowered[len("dislikes "):]
        words = frozenset(word for word in re.findall(r"[a-z0-9]{3,}", lowered)
                          if word not in cls.COMPARISON_STOPWORDS)
        return words, negated

    def conflicting_pairs(self) -> list[dict]:
        """Report memories in one category that may contradict or restate each other.

        Deliberately conservative and computed on read: only word overlap and an
        explicit negation marker are used, never a guess about meaning, and a
        forgotten memory can never leave a stale flag behind.
        """
        prepared = [(item, *self._comparable_fact(item.get("value", "")))
                    for item in self.profile_memories()]
        pairs: list[dict] = []
        for index, (first, first_words, first_negated) in enumerate(prepared):
            for second, second_words, second_negated in prepared[index + 1:]:
                if first.get("category") != second.get("category"):
                    continue
                if not first_words or not second_words:
                    continue
                shared = first_words & second_words
                ratio = len(shared) / min(len(first_words), len(second_words))
                opposed = first_negated != second_negated
                strong_enough = len(shared) >= 2 or (opposed and shared)
                if not shared or ratio < 0.5 or not strong_enough:
                    continue
                pairs.append({
                    "a": first.get("id"), "b": second.get("id"),
                    "kind": "contradicts" if opposed else "overlaps",
                })
        return pairs

    def learn_fact(self, category: str, value: str, *, source: str,
                   confidence: float = 0.8, explicit: bool = False,
                   topic: str = "", project: str | None = None) -> dict | None:
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
                if project and not existing.get("project"):
                    existing["project"] = project
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
                "project": project,
                "created": now,
                "updated": now,
                "last_confirmed": now,
                "last_used": None,
            }
            memories.append(item)
            self.data["profile_memories"] = self._within_cap(memories)
            self.save()
            return {**item, "learning_status": "learned"}

    MAX_MEMORIES = 250

    @classmethod
    def _within_cap(cls, memories: list[dict]) -> list[dict]:
        """Enforce the cap by dropping only what the user has not vouched for.

        Slicing the list blindly evicted the oldest entry even when it was
        pinned or user-confirmed, so a memory the user had deliberately kept
        could vanish once the cap was reached.
        """
        if len(memories) <= cls.MAX_MEMORIES:
            return memories
        protected = [item for item in memories
                     if item.get("pinned") or item.get("confirmed")]
        droppable = [item for item in memories
                     if not (item.get("pinned") or item.get("confirmed"))]
        room = max(0, cls.MAX_MEMORIES - len(protected))
        keep = set(id(item) for item in protected) | {id(item) for item in droppable[-room:]}
        return [item for item in memories if id(item) in keep]

    def learn_from_message(self, message: str, project: str | None = None) -> list[dict]:
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
            item = self.learn_fact(category, value, source=text, confidence=confidence, project=project)
            if item and item.get("learning_status") == "learned":
                learned.append(item)
        return learned

    def profile_memories(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self.data.get("profile_memories", [])]

    def relevant_memories(self, query: str, limit: int = 12,
                          project: str | None = None) -> list[dict]:
        words = {word for word in re.findall(r"[a-z0-9]{3,}", query.casefold())
                 if word not in {"the", "and", "that", "this", "with", "from", "have", "what"}}
        with self._lock:
            scored = []
            for item in self.data.get("profile_memories", []):
                haystack = f"{item.get('category', '')} {item.get('topic', '')} {item.get('value', '')}".casefold()
                overlap = sum(1 for word in words if word in haystack)
                general = item.get("category") in {"preference", "work_style"}
                same_project = bool(project) and item.get("project") == project
                score = overlap * 5 + (8 if item.get("pinned") else 0) + (2 if general else 0)
                score += (6 if same_project else 0)
                score += float(item.get("confidence", 0))
                if overlap or general or item.get("pinned") or same_project:
                    # Keep the rationale next to the score so the UI can explain the
                    # recall instead of the reason being recomputed or guessed later.
                    reasons = []
                    if item.get("pinned"):
                        reasons.append("Pinned for stronger recall")
                    if same_project:
                        reasons.append(f"About the {project} project")
                    if overlap:
                        reasons.append("Matches your wording")
                    if general and not reasons:
                        reasons.append("General preference Aura always considers")
                    scored.append((score, ", ".join(reasons), item))
            scored.sort(key=lambda entry: (entry[0], entry[2].get("updated", "")), reverse=True)
            chosen = scored[:max(1, min(int(limit), 30))]
            if chosen:
                # A memory actually pulled into the model's context just got used —
                # distinct from "updated" (when its content last changed).
                now = self._now()
                for _, _, item in chosen:
                    item["last_used"] = now
                self.save()
            # recall_reason is per-query, so it is attached to the returned copies
            # only and never written back to the stored memory.
            return [{**item, "recall_reason": reason} for _, reason, item in chosen]

    def update_profile_memory(self, memory_id: str, *, value: str | None = None,
                              category: str | None = None, pinned: bool | None = None,
                              project: str | None = None) -> dict:
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
            changed = (next_value != item.get("value")
                       or next_category != item.get("category"))
            if changed:
                # Keep what it said before so a correction can be walked back.
                history = list(item.get("history") or [])
                history.append({"value": item.get("value"),
                                "category": item.get("category"),
                                "replaced": self._now()})
                item["history"] = history[-self.MAX_HISTORY:]
            item.update({"category": next_category, "value": next_value,
                         "key": self._fact_key(next_category, next_value),
                         "confirmed": True, "updated": self._now(),
                         "last_confirmed": self._now()})
            if pinned is not None:
                item["pinned"] = bool(pinned)
            if project is not None:
                # The only relationship in the graph the user owns: every other
                # edge is derived, while this one comes from a guess made when
                # the fact was learned. An empty string detaches it, which has
                # to stay possible — a wrong link is worse than no link.
                cleaned = " ".join(str(project).split())[:80]
                item["project"] = cleaned or None
            self.save()
            return dict(item)

    def revert_profile_memory(self, memory_id: str) -> dict:
        """Restore the most recent previous wording, walking back one step."""
        with self._lock:
            item = next((entry for entry in self.data["profile_memories"]
                         if entry.get("id") == str(memory_id)), None)
            if item is None:
                raise KeyError("Memory not found")
            history = list(item.get("history") or [])
            if not history:
                raise ValueError("This memory has no earlier version to restore")
            previous = history.pop()
            category = str(previous.get("category") or item.get("category"))
            value = str(previous.get("value") or "")
            if category not in self.PROFILE_CATEGORIES or len(value) < 3:
                raise ValueError("The earlier version can no longer be restored safely")
            # The restored step is consumed rather than re-recorded, so repeated
            # reverts walk further back instead of toggling between two versions.
            item.update({"category": category, "value": value,
                         "key": self._fact_key(category, value),
                         "history": history, "confirmed": True,
                         "updated": self._now(), "last_confirmed": self._now()})
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
