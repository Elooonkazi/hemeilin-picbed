#!/usr/bin/env python3
"""
Image crawler for hemeilin-picbed.

Fetches public images, converts them to WebP, classifies them into
ri/{hd,hl,vd,vl} (horizontal/vertical x dark/light), deduplicates by
SHA-256 of the source bytes and by a simple perceptual average-hash,
then renumbers folders sequentially and rewrites ri/count.json and
ri/hash_registry.json.

Sources (all license-friendly, no key required):
  - picsum.photos  (Lorem Picsum, Unsplash-sourced photos, seeded)
  - Bing daily wallpaper archive (last 8 days, landscape)

Usage:
  python3 crawler/crawl.py --picsum-h 24 --picsum-v 24 --bing 8
  python3 crawler/crawl.py --seed-prefix myrun   # new picsum seeds
"""
import argparse
import hashlib
import io
import json
import random
import sys
import time
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RI = ROOT / "ri"
FOLDERS = ["hd", "hl", "vd", "vl"]
COUNT_PATH = RI / "count.json"
REGISTRY_PATH = RI / "hash_registry.json"
UA = {"User-Agent": "hemeilin-picbed-crawler/1.0 (+https://github.com)"}
DARK_THRESHOLD = 115  # mean luminance below this -> dark folder
MAX_W_H = 1920        # cap horizontal width
MAX_H_V = 1920        # cap vertical height
WEBP_QUALITY = 82


def ahash(img: Image.Image, size: int = 8) -> str:
    g = img.convert("L").resize((size, size))
    px = list(g.getdata())
    avg = sum(px) / len(px)
    bits = "".join("1" if p >= avg else "0" for p in px)
    return f"{int(bits, 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {}


def classify(img: Image.Image) -> str:
    w, h = img.size
    orientation = "h" if w >= h else "v"
    small = img.convert("L").resize((32, 32))
    mean = sum(small.getdata()) / (32 * 32)
    tone = "d" if mean < DARK_THRESHOLD else "l"
    return orientation + tone


def next_index(folder: str) -> int:
    d = RI / folder
    d.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in d.glob("*.webp"):
        if p.stem.isdigit():
            nums.append(int(p.stem))
    return (max(nums) + 1) if nums else 1


def save_image(img: Image.Image, folder: str) -> str:
    if folder.startswith("h") and img.width > MAX_W_H:
        img = img.resize((MAX_W_H, int(img.height * MAX_W_H / img.width)), Image.LANCZOS)
    if folder.startswith("v") and img.height > MAX_H_V:
        img = img.resize((int(img.width * MAX_H_V / img.height), MAX_H_V), Image.LANCZOS)
    idx = next_index(folder)
    out = RI / folder / f"{idx}.webp"
    img.convert("RGB").save(out, "WEBP", quality=WEBP_QUALITY, method=6)
    return f"{folder}/{idx}.webp"


def add_bytes(raw: bytes, registry: dict, seen_ahashes: list, stats: dict) -> bool:
    sha = hashlib.sha256(raw).hexdigest()
    if sha in registry:
        stats["dup_sha"] += 1
        return False
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        stats["bad"] += 1
        return False
    if img.width < 400 or img.height < 400:
        stats["small"] += 1
        return False
    ah = ahash(img)
    if any(hamming(ah, old) <= 4 for old in seen_ahashes):
        stats["dup_ahash"] += 1
        return False
    folder = classify(img)
    rel = save_image(img, folder)
    registry[sha] = {"file": rel, "ahash": ah}
    seen_ahashes.append(ah)
    stats["added"] += 1
    stats[folder] = stats.get(folder, 0) + 1
    print(f"  + {rel}")
    return True


def fetch(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=UA, timeout=40)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  ! fetch failed {url}: {e}")
        return None


def crawl_picsum(count: int, orientation: str, seed_prefix: str, registry, seen, stats):
    print(f"picsum {orientation}: {count}")
    for i in range(count):
        seed = f"{seed_prefix}-{orientation}-{i}-{random.randint(0, 99999)}"
        if orientation == "h":
            url = f"https://picsum.photos/seed/{seed}/1920/1200"
        else:
            url = f"https://picsum.photos/seed/{seed}/1200/1920"
        raw = fetch(url)
        if raw:
            add_bytes(raw, registry, seen, stats)
        time.sleep(0.3)


def crawl_bing(count: int, registry, seen, stats):
    print(f"bing wallpapers: up to {count}")
    try:
        data = requests.get(
            "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=8&mkt=en-US",
            headers=UA, timeout=30,
        ).json()
    except Exception as e:
        print(f"  ! bing index failed: {e}")
        return
    for item in data.get("images", [])[:count]:
        url = "https://www.bing.com" + item["urlbase"] + "_1920x1080.jpg"
        raw = fetch(url)
        if raw:
            add_bytes(raw, registry, seen, stats)
        time.sleep(0.3)


def rebuild_count():
    count = {}
    for folder in FOLDERS:
        d = RI / folder
        nums = sorted(int(p.stem) for p in d.glob("*.webp") if p.stem.isdigit())
        mx = max(nums) if nums else 0
        exclude = [n for n in range(1, mx + 1) if n not in nums]
        count[folder] = {"max": mx, "exclude": exclude}
    COUNT_PATH.write_text(json.dumps(count, indent=2) + "\n")
    total = sum(c["max"] - len(c["exclude"]) for c in count.values())
    print(f"count.json rebuilt, total images: {total}")
    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--picsum-h", type=int, default=24)
    ap.add_argument("--picsum-v", type=int, default=24)
    ap.add_argument("--bing", type=int, default=8)
    ap.add_argument("--seed-prefix", default="hemeilin")
    args = ap.parse_args()

    for f in FOLDERS:
        (RI / f).mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    seen = [v["ahash"] for v in registry.values() if "ahash" in v]
    stats = {"added": 0, "dup_sha": 0, "dup_ahash": 0, "bad": 0, "small": 0}

    crawl_picsum(args.picsum_h, "h", args.seed_prefix, registry, seen, stats)
    crawl_picsum(args.picsum_v, "v", args.seed_prefix, registry, seen, stats)
    if args.bing:
        crawl_bing(args.bing, registry, seen, stats)

    REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n")
    rebuild_count()
    print(json.dumps(stats, ensure_ascii=False))
    if stats["added"] == 0:
        print("no new images added", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
