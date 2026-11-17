"""Atomic UTF-8/JSON writes for local artifacts (not a cross-process lock)."""
from pathlib import Path
import json
import os
import tempfile

def atomic_text(path, text):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dest.parent,
                                         prefix="." + dest.name + ".", delete=False) as f:
            name = f.name
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, dest)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return str(dest)

def atomic_json(path, data):
    return atomic_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
