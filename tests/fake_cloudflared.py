#!/usr/bin/env python3
"""시험용 가짜 cloudflared (tests/test_remote_tunnel.py · 저장소 밖 e2e).

FAKE_CF_MODE: ok(주소 → 등록) · silent(주소만, 등록 없음) · http2(--protocol http2 일 때만 등록) · crash(등록 뒤 곧 꺼짐) · fail(바로 꺼짐)
FAKE_CF_LOG: 켤 때마다 {"argv", "pid"} 한 줄을 덧붙임.
"""
import json
import os
import secrets
import sys
import time

argv = sys.argv[1:]
if "--version" in argv:
    print("cloudflared version 2026.10.0 (built 2026-10-05-1200 UTC)")
    sys.exit(0)
log = os.environ.get("FAKE_CF_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "pid": os.getpid()}) + "\n")
mode = os.environ.get("FAKE_CF_MODE", "ok")
if mode == "fail":
    sys.exit(1)
name = "-".join(secrets.choice(["quiet", "river", "blue", "mango", "tiger", "paper", "sound", "cable"]) for _ in range(4))
out = sys.stdout
out.write("2026-10-07T00:00:00Z INF Thank you for trying Cloudflare Tunnel.\n")
out.write("2026-10-07T00:00:00Z INF +--------------------------------------------------------------------------------------------+\n")
out.write("2026-10-07T00:00:00Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |\n")
out.write(f"2026-10-07T00:00:00Z INF |  https://{name}.trycloudflare.com                                                     |\n")
out.flush()
registered = mode in ("ok", "crash") or (mode == "http2" and "--protocol" in argv and argv[argv.index("--protocol") + 1] == "http2")
time.sleep(0.2)
if registered:
    out.write("2026-10-07T00:00:01Z INF Registered tunnel connection connIndex=0 connection=abc event=0 ip=198.41.200.1 location=icn01 protocol=quic\n")
    out.flush()
if mode == "crash":
    time.sleep(0.6)
    sys.exit(1)
while True:
    time.sleep(1)
