# -*- coding: utf-8 -*-
"""用 Git Credential Manager 里的凭据创建 GitHub Release 并流式上传安装包。

只用标准库，不依赖 gh CLI。token 不落盘、不打印，上传走分块流式，避免整包读进内存。
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = "SomnusWei/SW_Guided_Reader"
API = "https://api.github.com"
INSTALL_DIR = os.path.join("成品", "安装版")


def latest_installer() -> str:
    """取 成品\\安装版 下最新的安装包 exe（避免把版本号写死在脚本里）。"""
    candidates = [
        os.path.join(INSTALL_DIR, name)
        for name in os.listdir(INSTALL_DIR)
        if name.lower().endswith(".exe") and "安装包" in name
    ]
    if not candidates:
        raise SystemExit(f"{INSTALL_DIR} 下没有找到安装包 exe")
    return max(candidates, key=os.path.getmtime)


def token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise SystemExit("未取到 GitHub 凭据")


def api(method: str, url: str, tok: str, payload: bytes | None = None, ctype: str | None = None):
    headers = {"Authorization": f"token {tok}", "Accept": "application/vnd.github+json",
               "User-Agent": "sw-guided-reader-release"}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:200].decode("utf-8", "replace")}


def create_release(tok: str) -> dict:
    payload = json.load(open("release.json", encoding="utf-8"))
    status, data = api("POST", f"{API}/repos/{REPO}/releases", tok,
                       json.dumps(payload).encode("utf-8"), "application/json")
    if status not in (200, 201):
        raise SystemExit(f"创建 Release 失败：{status} {data}")
    return data


def upload(tok: str, upload_url: str, path: str) -> dict:
    name = os.path.basename(path)
    size = os.path.getsize(path)
    parsed = urllib.parse.urlsplit(upload_url.split("{")[0])
    target = parsed.path + "?name=" + urllib.parse.quote(name)
    print(f"上传 {name}（{size / 1024 / 1024:.1f} MB）→ {parsed.netloc}", flush=True)
    conn = http.client.HTTPSConnection(parsed.netloc, timeout=3600)
    conn.putrequest("POST", target)
    conn.putheader("Authorization", f"token {tok}")
    conn.putheader("Accept", "application/vnd.github+json")
    conn.putheader("Content-Type", "application/octet-stream")
    conn.putheader("Content-Length", str(size))
    conn.putheader("User-Agent", "sw-guided-reader-release")
    conn.endheaders()
    sent = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(8 << 20)
            if not chunk:
                break
            conn.send(chunk)
            sent += len(chunk)
            print(f"  已发送 {sent / 1024 / 1024:.0f} / {size / 1024 / 1024:.0f} MB", flush=True)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    if resp.status not in (200, 201):
        raise SystemExit(f"上传失败：{resp.status} {raw[:400]!r}")
    return json.loads(raw)


def main() -> None:
    tok = token()
    if "--upload-only" in sys.argv:
        idx = sys.argv.index("--upload-only")
        upload_url, path = sys.argv[idx + 1], sys.argv[idx + 2]
    else:
        release = create_release(tok)
        print("Release:", release["html_url"], flush=True)
        upload_url, path = release["upload_url"], latest_installer()
    asset = upload(tok, upload_url, path)
    print("资产地址:", asset["browser_download_url"])
    print("大小:", asset["size"], "字节")


if __name__ == "__main__":
    main()
