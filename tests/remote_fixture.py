"""원격 접속 시험 도우미 (tests/test_remote*.py 가 같이 씀) — 가짜 시계·가짜 ntfy·가짜 Bridge·서명 요청.

가짜 ntfy 는 ntfy.sh 가 하는 일 중 우리가 쓰는 것만 한다: POST /<주제> (글 그대로) · POST / (JSON 알림) ·
GET /<주제>/json?poll=1&since=latest (마지막 글 한 줄) · 모든 응답에 Access-Control-Allow-Origin: *.
"""
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import remote


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, s):
        self.t += s


class FakeNtfy:
    def __init__(self):
        self.posts = []  # (주제, 글, 머리글 dict)
        self.lock = threading.Lock()
        self.fail = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8")
                if outer.fail:
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                path = urlparse(self.path).path.strip("/")
                if not path:
                    d = json.loads(body)
                    topic, msg, hdr = d["topic"], d["message"], {k: d[k] for k in d if k not in ("topic", "message")}
                else:
                    topic, msg, hdr = path, body, dict(self.headers)
                with outer.lock:
                    outer.posts.append((topic, msg, hdr, time.time()))
                out = json.dumps({"id": secrets.token_hex(6), "time": int(time.time()), "event": "message", "topic": topic, "message": msg}).encode()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self):
                u = urlparse(self.path)
                parts = u.path.strip("/").split("/")
                lines = b""
                if len(parts) == 2 and parts[1] == "json" and parse_qs(u.query).get("poll") == ["1"]:
                    with outer.lock:
                        msgs = [p for p in outer.posts if p[0] == parts[0]]
                    if msgs:
                        t, m, _, at = msgs[-1]
                        lines = (json.dumps({"id": "x", "time": int(at), "event": "message", "topic": t, "message": m}) + "\n").encode()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(lines)))
                self.end_headers()
                self.wfile.write(lines)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def topic(self, t):
        with self.lock:
            return [p for p in self.posts if p[0] == t]

    def wait(self, pred, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                hit = [p for p in self.posts if pred(p)]
            if hit:
                return hit
            time.sleep(0.05)
        return []

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeBridge:
    """app 대신: 작업은 실제 스레드로 한 번에 하나 (app.start_job 과 같은 규칙)."""

    def __init__(self):
        self.lines = []
        self.job_state = {"name": None, "id": None, "by": None, "t0": None, "progress": {}, "result": None, "error": None}
        self.next_id = 0
        self.started = []
        self.lock = threading.Lock()
        self.hooks = []
        self.version = "9.9.9"
        self.analyzed = []

    def log(self, msg):
        with self.lock:
            self.lines.append(msg)

    def logs(self, since):
        with self.lock:
            return self.lines[since:], len(self.lines)

    def job(self):
        with self.lock:
            return dict(self.job_state)

    def start_job(self, name, fn, by=None):
        with self.lock:
            if self.job_state["name"]:
                return False
            self.next_id += 1
            jid = self.next_id
            self.job_state.update(name=name, id=jid, by=by, t0=time.time(), result=None, error=None)
            self.started.append((name, by))

        def run():
            err, res = None, None
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001
                err = str(e)
            with self.lock:
                self.job_state.update(name=None, id=None, result=res, error=err)
            for h in self.hooks:
                h(name, err, res, by, 1.0)
        threading.Thread(target=run, daemon=True).start()
        return jid

    def wait_idle(self, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if not self.job()["name"]:
                return True
            time.sleep(0.05)
        return False

    def analyze(self, b):
        self.analyzed.append(b)
        return {"ok": True}

    def refs_job(self, fn):
        return dict(fn(), ok=True)

    def bridge(self):
        return remote.Bridge(log=self.log, start_job=self.start_job, job=self.job, logs=self.logs, analyze=self.analyze,
                             refs_job=self.refs_job, version=self.version)


def make_home():
    d = Path(tempfile.mkdtemp(prefix="원격 시험 "))
    return d, lambda: shutil.rmtree(d, ignore_errors=True)


def service(home, clock=None, ntfy="http://127.0.0.1:1", bridge=None):
    svc = remote.Service(home=home, clock=clock or time.time, ntfy=ntfy)
    fb = bridge or FakeBridge()
    svc.init(fb.bridge())
    fb.hooks.append(svc.job_hook)
    return svc, fb


def pair_body(p, name="iPhone · Safari", install=None, ts=None, nonce=None):
    """휴대폰이 보내는 /r/pair 본문: 코드 대신 증명 (p = svc.pairing.create() 또는 {"proof": 증명 열쇠})."""
    ts = int(ts if ts is not None else time.time())
    nonce = nonce or remote._b64u(secrets.token_bytes(16))
    b = {"v": 2, "nonce": nonce, "ts": ts, "proof": remote.pair_proof(p["proof"], nonce, ts), "name": name}
    if install:
        b["install"] = install
    return b


def open_pair(p, resp):
    """잠긴 /r/pair 대답 → 속 (휴대폰 proto.openPairResponse 와 같음)."""
    aad = remote.pair_aad(p["topic"], resp["pc"]["id"], resp["device"]["id"])
    return json.loads(remote.gcm_open(p["key"], resp["sealed"][5:], aad))


def pair_device(svc, name="iPhone · Safari", install=None):
    """PC 코드 만들기 → /r/pair 와 같은 길로 기기 하나 (state 를 잠깐 on 으로) → 휴대폰이 푼 모양 + host(서명할 PC 주소)."""
    old = svc.state
    svc.state = "on"
    p = svc.pairing.create()
    svc.state = old
    resp = svc.r_pair(pair_body(p, name, install, ts=svc.clock()))
    inner = open_pair(p, resp)
    return dict(inner, api=resp["api"], time=resp["time"], host=svc.host(), raw=resp)


def signed(cred, method, path, body=b"", ts=None, nonce=None, clock=time.time, host=None):
    ts = str(int(ts if ts is not None else clock()))
    nonce = nonce or remote._b64u(secrets.token_bytes(16))
    host = cred.get("host", "") if host is None else host
    sig = remote.sign(remote._unb64u(cred["keys"]["auth"]), host, method, path, ts, nonce, body)
    return f"FSR2 {cred['device']['id']}.{ts}.{nonce}.{sig}"


def topics_of(svc, cred, status):
    """/r/status 의 잠긴 주제 → {beacon, notify} (휴대폰 proto.openTopics 와 같음)."""
    aad = remote.topics_aad(svc.store.data["pc"]["id"], cred["device"]["id"])
    return json.loads(remote.gcm_open(remote._unb64u(cred["keys"]["beacon"]), status["topics"], aad))


def http(port, method, path, body=None, headers=None, raw=None):
    """원격 리스너에 바로 (dev 모드: Host = 127.0.0.1:<포트>) → (상태, 머리글, 본문 바이트)."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Host": f"127.0.0.1:{port}"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def dev_env(**extra):
    env = {"FUTSAL_REMOTE_DEV": "1", "FUTSAL_REMOTE_ORIGINS": "http://127.0.0.1:8973"}
    env.update(extra)
    return env


ORIGIN = "https://mulgyeol.kr"
os.environ.pop("FUTSAL_NTFY", None)  # 시험이 진짜 ntfy.sh 로 보내지 않게 (서비스마다 가짜 주소를 줌)
