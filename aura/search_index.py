from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import PurePosixPath

from .safety import WorkspaceSandbox


MAX_INDEXED_BYTES = 1_000_000
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svgz",
    ".zip", ".gz", ".tar", ".7z", ".rar",
    ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi",
    ".pdf", ".exe", ".dll", ".so", ".dylib", ".onnx", ".bin", ".pyc",
}
STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "have", "what", "for", "you",
    "are", "was", "were", "into", "there", "their", "them", "then", "than",
    "does", "did", "not", "but", "all", "any", "can", "how", "why", "who", "its",
}
# BM25 defaults; b controls length normalisation, k1 term-frequency saturation.
BM25_K1 = 1.5
BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    """Split text into lowercase words, also breaking camelCase identifiers.

    Code matters here as much as prose, so `avatarFace` must also yield
    `avatar` and `face`, and `avatar_face` must yield the same two words.
    """
    tokens: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9]+", text):
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", raw) or [raw]
        for part in parts:
            word = part.casefold()
            if len(word) >= 2 and word not in STOPWORDS:
                tokens.append(word)
    return tokens


class WorkspaceIndex:
    """Rank workspace files against a natural-language query using BM25.

    Standard library only: this matches words, never meaning, so it will not
    find synonyms the way an embedding model would. It does find multi-word
    queries that plain substring search misses entirely, and it ranks results.
    Documents are cached per file and refreshed only when size or mtime moves.
    """

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        self.sandbox = sandbox
        self._documents: dict[str, dict] = {}

    def refresh(self, relative: str = ".") -> int:
        """Re-read changed files under `relative`; return the document count."""
        seen: set[str] = set()
        for name in self.sandbox.list_files(relative):
            if PurePosixPath(name).suffix.casefold() in SKIP_SUFFIXES:
                continue
            try:
                stat = self.sandbox.path(name).stat()
            except (OSError, ValueError):
                continue
            if stat.st_size > MAX_INDEXED_BYTES:
                continue
            seen.add(name)
            cached = self._documents.get(name)
            # st_mtime_ns rather than st_mtime: a same-length edit made within one
            # coarse timestamp tick must still invalidate the cached document.
            if cached and cached["mtime"] == stat.st_mtime_ns and cached["size"] == stat.st_size:
                continue
            try:
                content = self.sandbox.read_file(name)
            except (OSError, UnicodeDecodeError, ValueError):
                self._documents.pop(name, None)
                seen.discard(name)
                continue
            # The path is part of the document so a filename match still ranks.
            terms = Counter(tokenize(name) + tokenize(content))
            self._documents[name] = {
                "mtime": stat.st_mtime_ns, "size": stat.st_size,
                "terms": terms, "length": sum(terms.values()),
            }
        for stale in set(self._documents) - seen:
            del self._documents[stale]
        return len(self._documents)

    def search(self, query: str, limit: int = 10, relative: str = ".") -> list[dict]:
        """Return the best-matching files, most relevant first."""
        self.refresh(relative)
        words = set(tokenize(query))
        if not words or not self._documents:
            return []
        total = len(self._documents)
        average_length = sum(doc["length"] for doc in self._documents.values()) / total or 1
        # Inverse document frequency depends only on the corpus, so compute it
        # once per query term rather than once per (term, document) pair.
        idf = {}
        for word in words:
            containing = sum(1 for doc in self._documents.values() if doc["terms"].get(word))
            if containing:
                idf[word] = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        results: list[dict] = []
        for name, doc in self._documents.items():
            score = 0.0
            matched: list[str] = []
            norm = 1 - BM25_B + BM25_B * (doc["length"] / average_length)
            for word, weight in idf.items():
                frequency = doc["terms"].get(word, 0)
                if not frequency:
                    continue
                score += weight * (frequency * (BM25_K1 + 1)) / (frequency + BM25_K1 * norm)
                matched.append(word)
            if score > 0:
                results.append({"path": name, "score": round(score, 4),
                                "matched": sorted(matched)})
        results.sort(key=lambda item: (-item["score"], item["path"]))
        return results[:max(1, min(int(limit), 50))]
