from __future__ import annotations

from pathlib import Path

from fairyclaw.sdk.tools import resolve_memory_root

LOGICAL_MEMORY_FILES = {"USER.md", "SOUL.md", "MEMORY.md"}
_LOGICAL_MEMORY_LOOKUP = {name.lower(): name for name in LOGICAL_MEMORY_FILES}


def normalize_memory_name(name: str) -> str:
    normalized = str(name or "").strip()
    canonical = _LOGICAL_MEMORY_LOOKUP.get(normalized.lower())
    if canonical is None:
        raise ValueError("name must be one of USER.md, SOUL.md, MEMORY.md")
    return canonical


def resolve_memory_file_path(*, name: str, memory_root: str | None = None) -> Path:
    filename = normalize_memory_name(name)
    if memory_root:
        root = Path(memory_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = resolve_memory_root(mkdir=True)
    return root / filename


def read_memory_text(*, name: str, memory_root: str | None = None) -> str:
    path = resolve_memory_file_path(name=name, memory_root=memory_root)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_memory_text(*, name: str, content: str, memory_root: str | None = None) -> Path:
    path = resolve_memory_file_path(name=name, memory_root=memory_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return path


def append_memory_text(*, name: str, content: str, memory_root: str | None = None) -> Path:
    path = resolve_memory_file_path(name=name, memory_root=memory_root)
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    glue = "\n\n" if existing and not existing.endswith("\n") else "\n" if existing else ""
    return write_memory_text(name=name, content=f"{existing}{glue}{content}", memory_root=memory_root)
