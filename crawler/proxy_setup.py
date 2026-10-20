#!/usr/bin/env python3
"""
Multi-protocol proxy bootstrap script for GitHub Actions.
Supports: vmess://, vless://, ss://, trojan://, or http(s) subscription links.
Converts node to Mihomo (Clash.Meta) config and runs in background.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "mihomo"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
BIN_PATH = Path("/usr/local/bin/mihomo")
MIHOMO_URL = (
    "https://github.com/MetaCubeX/mihomo/releases/download/v1.18.9/mihomo-linux-amd64-v1.18.9.gz"
)


def safe_b64decode(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s.encode("utf-8")).decode("utf-8", errors="ignore")


def parse_vmess(link: str) -> dict:
    raw = link[len("vmess://") :]
    data = json.loads(safe_b64decode(raw))
    proxy = {
        "name": "proxy_node",
        "type": "vmess",
        "server": data.get("add"),
        "port": int(data.get("port", 443)),
        "uuid": data.get("id"),
        "alterId": int(data.get("aid", 0)),
        "cipher": data.get("scy", "auto") or "auto",
        "udp": True,
        "tls": str(data.get("tls", "")).lower() in ("tls", "1", "true"),
        "skip-cert-verify": str(data.get("insecure", "0")) == "1",
        "servername": data.get("sni") or data.get("host") or "",
        "network": data.get("net", "tcp"),
    }
    if data.get("fp"):
        proxy["client-fingerprint"] = data["fp"]

    if proxy["network"] == "ws":
        ws_opts = {}
        if data.get("path"):
            ws_opts["path"] = data["path"]
        if data.get("host"):
            ws_opts["headers"] = {"Host": data["host"]}
        proxy["ws-opts"] = ws_opts
    elif proxy["network"] == "grpc":
        proxy["grpc-opts"] = {"grpc-service-name": data.get("path", "")}
    return proxy


def parse_vless(link: str) -> dict:
    u = urllib.parse.urlsplit(link)
    params = dict(urllib.parse.parse_qsl(u.query))
    proxy = {
        "name": "proxy_node",
        "type": "vless",
        "server": u.hostname,
        "port": u.port or 443,
        "uuid": u.username,
        "udp": True,
        "tls": params.get("security") in ("tls", "reality"),
        "servername": params.get("sni", ""),
        "network": params.get("type", "tcp"),
    }
    if params.get("flow"):
        proxy["flow"] = params["flow"]
    if params.get("fp"):
        proxy["client-fingerprint"] = params["fp"]
    if params.get("security") == "reality":
        reality_opts = {}
        if params.get("pbk"):
            reality_opts["public-key"] = params["pbk"]
        if params.get("sid"):
            reality_opts["short-id"] = params["sid"]
        proxy["reality-opts"] = reality_opts
    if proxy["network"] == "ws":
        ws_opts = {}
        if params.get("path"):
            ws_opts["path"] = params["path"]
        if params.get("host"):
            ws_opts["headers"] = {"Host": params["host"]}
        proxy["ws-opts"] = ws_opts
    elif proxy["network"] == "grpc":
        proxy["grpc-opts"] = {"grpc-service-name": params.get("serviceName", "")}
    return proxy


def parse_ss(link: str) -> dict:
    raw = link[len("ss://") :]
    part, _, _ = raw.partition("#")
    if "@" in part:
        user_info, _, host_info = part.partition("@")
        decoded_user = safe_b64decode(user_info) if ":" not in user_info else user_info
        cipher, password = decoded_user.split(":", 1)
        server, port = host_info.split(":", 1)
    else:
        decoded = safe_b64decode(part)
        user_info, _, host_info = decoded.partition("@")
        cipher, password = user_info.split(":", 1)
        server, port = host_info.split(":", 1)

    return {
        "name": "proxy_node",
        "type": "ss",
        "server": server,
        "port": int(port),
        "cipher": cipher,
        "password": password,
        "udp": True,
    }


def parse_trojan(link: str) -> dict:
    u = urllib.parse.urlsplit(link)
    params = dict(urllib.parse.parse_qsl(u.query))
    return {
        "name": "proxy_node",
        "type": "trojan",
        "server": u.hostname,
        "port": u.port or 443,
        "password": u.username,
        "udp": True,
        "sni": params.get("sni") or u.hostname,
        "skip-cert-verify": False,
    }


def generate_config(node_str: str) -> str:
    node_str = node_str.strip()
    if node_str.startswith("vmess://"):
        proxy = parse_vmess(node_str)
    elif node_str.startswith("vless://"):
        proxy = parse_vless(node_str)
    elif node_str.startswith("ss://"):
        proxy = parse_ss(node_str)
    elif node_str.startswith("trojan://"):
        proxy = parse_trojan(node_str)
    else:
        raise ValueError(f"Unsupported proxy URI format: {node_str[:15]}...")

    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "proxies": [proxy],
        "rules": ["MATCH,proxy_node"],
    }
    # JSON is valid YAML
    return json.dumps(config, indent=2)


def install_mihomo():
    if BIN_PATH.exists():
        return
    print("Downloading Mihomo proxy core...")
    gz_path = Path("/tmp/mihomo.gz")
    urllib.request.urlretrieve(MIHOMO_URL, gz_path)
    subprocess.run(["gunzip", "-f", str(gz_path)], check=True)
    unzipped = Path("/tmp/mihomo")
    shutil.move(str(unzipped), str(BIN_PATH))
    BIN_PATH.chmod(0o755)
    print("Mihomo installed successfully.")


def export_github_env():
    env_file = os.getenv("GITHUB_ENV")
    if not env_file:
        return
    with open(env_file, "a", encoding="utf-8") as f:
        f.write("HTTP_PROXY=http://127.0.0.1:7890\n")
        f.write("HTTPS_PROXY=http://127.0.0.1:7890\n")
        f.write("ALL_PROXY=socks5h://127.0.0.1:7890\n")
        f.write("http_proxy=http://127.0.0.1:7890\n")
        f.write("https_proxy=http://127.0.0.1:7890\n")
        f.write("all_proxy=socks5h://127.0.0.1:7890\n")
    print("Exported HTTP_PROXY/HTTPS_PROXY/ALL_PROXY to GITHUB_ENV.")


def main():
    proxy_node = os.getenv("PROXY_NODE", "").strip()
    if not proxy_node:
        print("Notice: PROXY_NODE secret not set. Skipping proxy.")
        sys.exit(0)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if proxy_node.startswith("http://") or proxy_node.startswith("https://"):
        print("Detected subscription link, downloading config...")
        req = urllib.request.Request(
            proxy_node, headers={"User-Agent": "clash-meta; mihomo"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            CONFIG_FILE.write_bytes(resp.read())
    else:
        print("Parsing custom node link...")
        yaml_content = generate_config(proxy_node)
        CONFIG_FILE.write_text(yaml_content, encoding="utf-8")

    install_mihomo()

    print("Starting Mihomo on 127.0.0.1:7890...")
    log_file = open("/tmp/mihomo.log", "w")
    subprocess.Popen([str(BIN_PATH), "-d", str(CONFIG_DIR)], stdout=log_file, stderr=log_file)

    # 等待端口监听并检测连通性
    time.sleep(2)
    export_github_env()

    # 简单握手测试
    test_cmd = [
        "curl",
        "-I",
        "--connect-timeout",
        "8",
        "-x",
        "http://127.0.0.1:7890",
        "https://www.google.com",
    ]
    res = subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode == 0:
        print("Proxy connected successfully!")
    else:
        print("Warning: Proxy started, but test request to Google timed out. Node may be slow or unstable.")


if __name__ == "__main__":
    main()
