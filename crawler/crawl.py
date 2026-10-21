#!/usr/bin/env python3
"""
Image crawler for hemeilin-picbed.
Supports Cloudflare 5s-shield bypass and lazy-loading via Playwright.
"""
import argparse
import hashlib
import io
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RI = ROOT / "ri"
FOLDERS = ["hd", "hl", "vd", "vl"]
COUNT_PATH = RI / "count.json"
REGISTRY_PATH = RI / "hash_registry.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DARK_THRESHOLD = 115
MAX_W_H = 1920
MAX_H_V = 1920
WEBP_QUALITY = 82

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif")
SKIP_PATH_HINTS = (
    "login", "register", "signup", "search",
    "feed", "rss", "xmlrpc", "wp-admin", "wp-login", "#"
)


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
    nums = [int(p.stem) for p in d.glob("*.webp") if p.stem.isdigit()]
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

    if max(img.width, img.height) < 400 or min(img.width, img.height) < 250:
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
    print(f"  + {rel} ({img.width}x{img.height})")
    return True


def fetch_api(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  ! fetch failed {url}: {e}")
        return None


def crawl_picsum(count: int, orientation: str, seed_prefix: str, registry, seen, stats):
    if count <= 0:
        return
    print(f"picsum {orientation}: {count}")
    for i in range(count):
        seed = f"{seed_prefix}-{orientation}-{i}-{random.randint(0, 99999)}"
        url = (
            f"https://picsum.photos/seed/{seed}/1920/1200"
            if orientation == "h"
            else f"https://picsum.photos/seed/{seed}/1200/1920"
        )
        raw = fetch_api(url)
        if raw:
            add_bytes(raw, registry, seen, stats)
        time.sleep(0.3)


def crawl_bing(count: int, registry, seen, stats):
    if count <= 0:
        return
    print(f"bing wallpapers: up to {count}")
    try:
        data = requests.get(
            "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=8&mkt=en-US",
            headers=HEADERS,
            timeout=30,
        ).json()
    except Exception as e:
        print(f"  ! bing index failed: {e}")
        return
    for item in data.get("images", [])[:count]:
        url = "https://www.bing.com" + item["urlbase"] + "_1920x1080.jpg"
        raw = fetch_api(url)
        if raw:
            add_bytes(raw, registry, seen, stats)
        time.sleep(0.3)


# ---------- Playwright 增强与容错控制 ----------

def _looks_like_image(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(IMG_EXTS)


def safe_evaluate(page, script: str, max_retries: int = 3):
    """防止页面在重定向时执行 evaluate 抛出 execution context destroyed 异常"""
    for i in range(max_retries):
        try:
            return page.evaluate(script)
        except Exception as e:
            if "Execution context was destroyed" in str(e) or "navigation" in str(e):
                page.wait_for_timeout(1500)
            else:
                if i == max_retries - 1:
                    raise e
                page.wait_for_timeout(1000)
    return None


def wait_for_cf(page, max_wait: int = 20):
    """自动检测并等待 Cloudflare 5秒盾，等待跳转落地完成后再返回"""
    cf_keywords = ("just a moment", "请稍候", "attention required", "security check")
    start = time.time()
    
    # 刚加载先等待 1.5 秒，给 CF 注入和初始重定向预留时间
    page.wait_for_timeout(1500)
    
    while time.time() - start < max_wait:
        try:
            title = (page.title() or "").lower()
        except Exception:
            page.wait_for_timeout(1000)
            continue

        if any(k in title for k in cf_keywords):
            print(f"    [Cloudflare] 正在等待 CF 盾验证与重定向... ({int(time.time() - start)}s)")
            page.wait_for_timeout(1500)
        else:
            break

    # CF 校验通过后通常有一次最终的 location.href 跳转，等待稳定
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(2000)


def crawl_url_browser(start_url: str, max_pages: int, max_images: int, registry, seen, stats):
    from playwright.sync_api import sync_playwright

    print(f"url source: {start_url} (max_pages={max_pages}, max_images={max_images})")
    host = urlparse(start_url).netloc
    queue = [start_url]
    visited_pages = set()
    seen_img_urls = set()
    added_here = 0

    proxy_env = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    launch_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
    proxy_config = {"server": proxy_env} if proxy_env else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=proxy_config, args=launch_args)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        while queue and len(visited_pages) < max_pages and added_here < max_images:
            curr_url = queue.pop(0)
            if curr_url in visited_pages:
                continue
            visited_pages.add(curr_url)

            try:
                page.goto(curr_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"  ! page open failed {curr_url}: {e}")
                continue

            # 处理 CF 等待与跳转
            wait_for_cf(page, max_wait=20)

            # 单页操作全部纳入独立 try 块，防止单个异常导致全局退出
            try:
                safe_evaluate(page, "window.scrollTo(0, document.body.scrollHeight / 2);")
                page.wait_for_timeout(600)
                safe_evaluate(page, "window.scrollTo(0, document.body.scrollHeight);")
                page.wait_for_timeout(800)

                img_srcs = safe_evaluate(page, """() => {
                    const results = new Set();
                    document.querySelectorAll('img').forEach(img => {
                        const src = img.getAttribute('data-original') 
                                 || img.getAttribute('data-src') 
                                 || img.getAttribute('data-full')
                                 || img.getAttribute('data-zoom-src')
                                 || img.getAttribute('data-lazy-src')
                                 || img.src;
                        if (src && !src.startsWith('data:')) results.add(src);
                    });
                    return Array.from(results);
                }""") or []

                page_links = safe_evaluate(page, """() => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => a.href);
                }""") or []

                # 下载与录入图片
                for raw_src in img_srcs:
                    if added_here >= max_images:
                        break
                    img_url = urljoin(curr_url, raw_src)
                    if img_url in seen_img_urls:
                        continue
                    seen_img_urls.add(img_url)

                    if not _looks_like_image(img_url) and "upload" not in img_url and "image" not in img_url:
                        continue

                    try:
                        resp = context.request.get(img_url, headers={"Referer": curr_url}, timeout=25000)
                        if resp.status == 200:
                            content = resp.body()
                            if add_bytes(content, registry, seen, stats):
                                added_here += 1
                    except Exception:
                        pass

                # 队列递归跟进
                for href in page_links:
                    nxt = urljoin(curr_url, href)
                    p_url = urlparse(nxt)
                    if p_url.netloc != host or nxt in visited_pages or nxt in queue:
                        continue
                    low = nxt.lower()
                    if any(h in low for h in SKIP_PATH_HINTS):
                        continue

                    if _looks_like_image(nxt):
                        if nxt not in seen_img_urls:
                            seen_img_urls.add(nxt)
                            try:
                                resp = context.request.get(nxt, headers={"Referer": curr_url}, timeout=25000)
                                if resp.status == 200 and add_bytes(resp.body(), registry, seen, stats):
                                    added_here += 1
                            except Exception:
                                pass
                        continue

                    if re.search(r"\d", p_url.path) or any(k in low for k in ("post", "photo", "article", "view")):
                        queue.append(nxt)

            except Exception as e:
                print(f"  ! page parse error on {curr_url}: {e}")

        browser.close()

    print(f"  url source done: +{added_here} images from {len(visited_pages)} pages")


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
    ap.add_argument("--url", action="append", default=[], help="user-supplied page URL to crawl (repeatable)")
    ap.add_argument("--from-sources", action="store_true", help="also crawl every URL listed in crawler/sources.txt")
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument("--max-images", type=int, default=60)
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

    urls = list(args.url)
    if args.from_sources:
        src_file = Path(__file__).resolve().parent / "sources.txt"
        if src_file.exists():
            for line in src_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    for u in urls:
        crawl_url_browser(u, args.max_pages, args.max_images, registry, seen, stats)

    REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n")
    rebuild_count()
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
