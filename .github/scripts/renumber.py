#!/usr/bin/env python3
"""Renumber ri/{hd,hl,vd,vl}/*.webp sequentially (1..N, no gaps),
rebuild hash_registry.json and ri/count.json. Local-filesystem version."""
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
RI = ROOT / "ri"
FOLDERS = ["hd", "hl", "vd", "vl"]


def ahash(img, size=8):
    g = img.convert("L").resize((size, size))
    px = list(g.getdata())
    avg = sum(px) / len(px)
    bits = "".join("1" if p >= avg else "0" for p in px)
    return f"{int(bits, 2):016x}"


def main():
    registry = {}
    count = {}
    for folder in FOLDERS:
        d = RI / folder
        files = sorted(
            (p for p in d.glob("*.webp") if p.stem.isdigit()),
            key=lambda p: int(p.stem),
        )
        # two-phase rename via temp names to avoid collisions
        temps = []
        for i, p in enumerate(files):
            t = d / f"__tmp_{i}.webp"
            p.rename(t)
            temps.append(t)
        for i, t in enumerate(temps, start=1):
            final = d / f"{i}.webp"
            t.rename(final)
            raw = final.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            try:
                img = Image.open(io.BytesIO(raw))
                registry[sha] = {"file": f"{folder}/{i}.webp", "ahash": ahash(img)}
            except Exception:
                registry[sha] = {"file": f"{folder}/{i}.webp"}
        count[folder] = {"max": len(files), "exclude": []}
        print(folder, len(files))
    (RI / "hash_registry.json").write_text(json.dumps(registry, indent=2) + "\n")
    (RI / "count.json").write_text(json.dumps(count, indent=2) + "\n")
    print("done", count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
