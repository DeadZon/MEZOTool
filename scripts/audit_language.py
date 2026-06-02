#!/usr/bin/env python3
"""Scan source files for remaining Vietnamese text markers."""
from __future__ import annotations

import re
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
INCLUDES = {
    ".py", ".yml", ".yaml", ".md", ".txt", ".json", ".ps1", ".bat", ".cmd"
}
SKIP_DIRS = {".git", "__pycache__", "build", "dist", ".venv", "venv"}

VIETNAMESE_RE = re.compile("[\\u00c0-\\u1ef9\\u0110\\u0111]")
PHRASES = [
    "\\u006b\\u0068\\u00f4\\u006e\\u0067",
    "\\u0063\\u0068\\u01b0\\u0061",
    "\\u0111\\u0061\\u006e\\u0067",
    "\\u0074\\u0068\\u0069\\u1ebf\\u0074 \\u0062\\u1ecb",
    "\\u0074\\u0068\\u01b0 \\u006d\\u1ee5\\u0063",
    "\\u0063\\u00e0\\u0069 \\u0111\\u1eb7\\u0074",
    "\\u006c\\u1ed7\\u0069",
    "\\u0074\\u0068\\u00e0\\u006e\\u0068 \\u0063\\u00f4\\u006e\\u0067",
    "\\u0074\\u1ea3\\u0069",
    "\\u0063\\u0068\\u1ecd\\u006e",
    "\\u0068\\u1ee7\\u0079",
    "\\u0062\\u01b0\\u1edb\\u0063",
    "\\u0074\\u0069\\u1ebf\\u0070 \\u0074\\u1ee5\\u0063",
    "\\u0068\\u006f\\u00e0\\u006e \\u0074\\u1ea5\\u0074",
]


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in INCLUDES:
            yield path


def main() -> int:
    hits = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            lower = line.lower()
            if VIETNAMESE_RE.search(line) or any(p in lower for p in PHRASES):
                hits.append((path.relative_to(ROOT), lineno, line.strip()))

    if not hits:
        print("Language audit passed: no Vietnamese text markers found.")
        return 0

    print("Language audit found possible Vietnamese text:")
    for path, lineno, line in hits:
        print(f"{path}:{lineno}: {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
