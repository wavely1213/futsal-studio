"""클로드 계정으로 쓰기 — 사용자 PC 에 설치된 Claude Code CLI(Anthropic 공식 프로그램)를 headless(-p)로 불러 판단을 받음.

- 로그인은 사용자가 공식 프로그램 창에서 브라우저로 직접 함 (앱은 claude.ai 로그인을 받지 않음 · 사용자의 Pro/Max 구독 사용량을 씀).
- 실행은 빈 임시 폴더에서: 세션 저장·설정 파일·MCP 끔, 도구는 Read 만(그림이 있을 때, 그 폴더 안만) 또는 없음,
  권한 질문은 모두 거절(dontAsk), 프롬프트는 stdin 으로 넣고 바로 닫음, 창 없이, 벽시계 제한·멈추기(✕) 가능.
- 자식 환경에서 ANTHROPIC_API_KEY·ANTHROPIC_AUTH_TOKEN 을 지움 (그게 있으면 계정 로그인보다 먼저 쓰여 요금이 나감).
  선택: 'claude setup-token' 으로 받은 로그인 코드는 ~/.futsal-studio/claude_token 에 두고 CLAUDE_CODE_OAUTH_TOKEN 으로만 넘김.
- 프롬프트·대답·토큰은 기록(studio.log)에 남기지 않음. 호출부는 결과 종류만 기록.
문서: code.claude.com/docs (headless · cli-reference · authentication · setup)."""
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ENV_EXE = "FUTSAL_CLAUDE"       # 시험·고급용: 실행 파일 경로를 직접 지정
MODEL = "opus"                  # CLI 별칭 — 최신 Opus 로 풀림 (2026-10-07 기준 claude-opus-5-5)
TIMEOUT = 240                   # 한 번 판단 벽시계 제한(초)
STATUS_TTL = 60                 # 로그인 상태 확인 캐시(초)
INSTALL_PS = "irm https://claude.ai/install.ps1 | iex"   # 공식 Windows 설치 명령 (code.claude.com/docs setup)
HOME = Path.home() / ".futsal-studio"
TOKEN_RE = re.compile(r"sk-ant-oat[0-9A-Za-z_\-]{10,290}")
WIN = sys.platform == "win32"
NO_WINDOW, NEW_GROUP, NEW_CONSOLE = 0x08000000, 0x00000200, 0x00000010

_LOCK = threading.Lock()
_FEAT = {}
_STATUS = {"t": 0.0, "v": None}


class ClaudeError(Exception):
    """사용자에게 그대로 보여 줄 한국어 안내 + 종류(kind: missing·login·limit·timeout·network·cancel·update·format·error)."""

    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind


MSG = {
    "missing": "이 PC에 클로드 프로그램이 없어요. 위 '클로드 계정으로 쓰기'에서 [설치하기]를 눌러 주세요",
    "login": "클로드 로그인이 필요해요. 위 '클로드 계정으로 쓰기'에서 [로그인]을 눌러 주세요",
    "limit": "클로드 사용 한도에 닿았어요. 한도가 풀린 뒤 다시 눌러 주세요 (내 PC 분석 결과는 그대로 있어요)",
    "timeout": "클로드 대답이 너무 늦어요. 잠시 뒤 다시 해 주세요",
    "network": "인터넷 연결을 확인해 주세요 (내 PC 분석 결과는 그대로 있어요)",
    "cancel": "클로드 판단을 멈췄어요",
    "update": "클로드 프로그램이 오래된 판이에요. 위 '클로드 계정으로 쓰기'에서 [설치하기]를 다시 눌러 새 판으로 바꾼 뒤 눌러 주세요",
    "error": "클로드가 대답하지 못했어요. 잠시 뒤 다시 눌러 주세요 (내 PC 분석 결과는 그대로 있어요)",
}


# ---------- 실행 파일 찾기 ----------

def find_exe():
    """Claude Code 실행 파일 경로 (없으면 None): FUTSAL_CLAUDE → PATH 의 claude → 공식 설치 위치 → npm 전역 설치."""
    env = os.environ.get(ENV_EXE)
    if env:
        return env if Path(env).is_file() else None
    w = shutil.which("claude")
    if w:
        return w
    home = Path.home()
    cands = [home / ".local" / "bin" / ("claude.exe" if WIN else "claude")]
    if WIN and os.environ.get("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "npm" / "claude.cmd")
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def _is_cmd(exe):
    return str(exe).lower().endswith((".cmd", ".bat"))


def _base(exe):
    """npm 의 claude.cmd 는 cmd.exe 를 거쳐 실행."""
    return [os.environ.get("COMSPEC") or "cmd.exe", "/c", str(exe)] if _is_cmd(exe) else [str(exe)]


def _flags(hidden=True):
    if WIN:
        return {"creationflags": (NO_WINDOW | NEW_GROUP) if hidden else NEW_CONSOLE}
    return {"start_new_session": True}


def token_file():
    return HOME / "claude_token"


def read_token():
    try:
        t = token_file().read_text(encoding="utf-8").strip()
        return t if TOKEN_RE.fullmatch(t) else None
    except OSError:
        return None


def has_token():
    return read_token() is not None


def save_token(tok):
    """'claude setup-token' 으로 받은 로그인 코드 저장 (형식만 확인 · 사용자 폴더 안 · 앱 폴더·config.json 밖)."""
    t = str(tok or "").strip()
    if not TOKEN_RE.fullmatch(t):
        raise ClaudeError("format", "로그인 코드 형식이 달라요. 'sk-ant-oat'로 시작하는 코드를 그대로 붙여 넣어 주세요")
    HOME.mkdir(parents=True, exist_ok=True)
    f = token_file()
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(t, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, f)
    reset_status()


def clear_token():
    try:
        token_file().unlink()
    except FileNotFoundError:
        pass
    reset_status()


def child_env():
    """자식 프로세스 환경: API 키 변수를 지우고(계정 로그인보다 먼저 쓰여 요금이 나감), 저장한 로그인 코드가 있으면 그것만 넘김."""
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    tok = read_token()
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    else:
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return env


def _quick(args, timeout=10):
    """짧은 확인 실행 → (종료 코드, 표준 출력). 실행 파일이 없으면 None."""
    exe = find_exe()
    if not exe:
        return None
    try:
        r = subprocess.run(_base(exe) + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout, env=child_env(), cwd=tempfile.gettempdir(), **_flags())
        return r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return None


HELP_TIMEOUTS = (10, 30)   # 'claude --help' 확인 — 처음 켤 때 백신 검사·npm 의 cmd.exe 로 느릴 수 있어 한 번 더 길게


def features(exe=None):
    """'claude --help' 을 읽어 지원하는 옵션을 기억 (예전 판에 없는 옵션은 넣지 않음).
    확인이 실패하면(시간 초과·이상한 출력) 기억하지 않고 {} — build_cmd 는 그때 안전 옵션(도구·설정·MCP 제한)을 모두 넣음 (fail closed)."""
    exe = exe or find_exe()
    if not exe:
        return {}
    with _LOCK:
        if exe in _FEAT:
            return _FEAT[exe]
    out = ""
    for to in HELP_TIMEOUTS:
        r = _quick(["--help"], timeout=to)
        out = r[1] if r else ""
        if "--output-format" in out or "--print" in out:
            break
        out = ""
    if not out:
        return {}  # 모름: 기억하지 않음 (다음에 다시 확인)
    f = {"permission_prompts": "--permission-prompts" in out, "restricted": "--restricted" in out,
         "no_session": "--no-session-persistence" in out, "strict_mcp": "--strict-mcp-config" in out,
         "setting_sources": "--setting-sources" in out, "tools": "--tools" in out, "auth": bool(re.search(r"^\s+auth\b", out, re.M))}
    with _LOCK:
        _FEAT[exe] = f
    return f


# ---------- 상태 · 설치 · 로그인 ----------

def reset_status():
    with _LOCK:
        _STATUS.update(t=0.0, v=None)


def status(refresh=False):
    """{state: missing·login·ready·unknown, method, hasToken, detail}. 60초 동안 기억 (refresh 면 다시)."""
    with _LOCK:
        if not refresh and _STATUS["v"] is not None and time.time() - _STATUS["t"] < STATUS_TTL:
            return dict(_STATUS["v"])
    exe = find_exe()
    if not exe:
        v = {"state": "missing", "hasToken": has_token()}
    else:
        r = _quick(["auth", "status", "--json"])
        v = {"state": "unknown", "hasToken": has_token()}
        if r is not None:
            m = re.search(r"\{.*\}", r[1], re.S)
            try:
                d = json.loads(m[0]) if m else None
            except ValueError:
                d = None
            if isinstance(d, dict) and "loggedIn" in d:
                v["state"] = "ready" if d.get("loggedIn") else "login"
                v["method"] = str(d.get("authMethod") or "")[:40]
            elif r[0] != 0 and re.search(r"not logged in|log ?in", r[1], re.I):
                v["state"] = "login"
    with _LOCK:
        _STATUS.update(t=time.time(), v=dict(v))
    return v


def install():
    """공식 설치 프로그램을 보이는 PowerShell 창에서 실행 (Windows). 고정 문자열이라 -EncodedCommand 로 넘김 (사용자 입력 없음)."""
    if not WIN:
        raise ClaudeError("error", "설치 창은 Windows 에서만 띄울 수 있어요")
    enc = base64.b64encode(INSTALL_PS.encode("utf-16-le")).decode()
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-EncodedCommand", enc],
                     cwd=str(Path.home()), **_flags(hidden=False))
    reset_status()


def login():
    """보이는 창에서 'claude auth login --claudeai' (예전 판이면 'claude') — 사용자가 브라우저에서 직접 로그인. 창을 기다리지 않음."""
    exe = find_exe()
    if not exe:
        raise ClaudeError("missing", MSG["missing"])
    if not WIN:
        raise ClaudeError("error", "로그인 창은 Windows 에서만 띄울 수 있어요. 터미널에서 'claude' 를 실행해 로그인해 주세요")
    args = ["auth", "login", "--claudeai"] if features(exe).get("auth") else []
    env = child_env()
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    subprocess.Popen(_base(exe) + args, cwd=str(Path.home()), env=env, **_flags(hidden=False))
    reset_status()


# ---------- headless 실행 ----------

def build_cmd(exe, with_images, feat=None):
    """claude -p 명령 (프롬프트는 stdin). 그림이 있으면 Read 도구만 (작업 폴더 안), 없으면 도구 없음."""
    f = feat if feat is not None else features(exe)
    # 도구·설정·MCP·세션 제한은 판을 모르면(확인 실패) 늘 넣음 — CLI 가 모르는 옵션이라고 하면 '업데이트해 주세요' 로 안내 (제한 없이 돌리지 않음)
    cmd = _base(exe) + ["-p", "--output-format", "json", "--model", MODEL]
    if f.get("no_session", True):
        cmd.append("--no-session-persistence")
    if f.get("strict_mcp", True):
        cmd.append("--strict-mcp-config")
    if f.get("setting_sources", True) and not _is_cmd(exe):  # cmd.exe 를 거치면 빈 인자("")가 깨질 수 있어 뺌 (도구는 Read 로 묶음)
        cmd += ["--setting-sources", ""]
    cmd += ["--permission-mode", "dontAsk"]
    if f.get("permission_prompts"):
        cmd += ["--permission-prompts", "none"]
    if f.get("restricted"):
        cmd.append("--restricted")
    if f.get("tools", True):
        if with_images:
            cmd += ["--tools", "Read"]
        elif not _is_cmd(exe):
            cmd += ["--tools", ""]
        else:
            cmd += ["--tools", "Read"]  # 빈 인자를 못 넘김 → Read 만 (빈 임시 폴더 안)
    return cmd


def _kill(p):
    try:
        if WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW, timeout=10)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        p.kill()
    except OSError:
        pass


def explain(code, out, err):
    """종료 코드·JSON·오류 글 → (종류, 결과 dict 또는 None)."""
    d = None
    for ln in reversed((out or "").strip().splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                d = json.loads(ln)
                break
            except ValueError:
                continue
    if d is None:
        m = re.search(r"\{.*\}", out or "", re.S)
        try:
            d = json.loads(m[0]) if m else None
        except ValueError:
            d = None
    blob = " ".join(str(x) for x in ((d or {}).get("result"), (d or {}).get("api_error_status"), (d or {}).get("subtype"), err, out if d is None else ""))
    status_code = (d or {}).get("api_error_status")
    if isinstance(d, dict) and not d.get("is_error") and d.get("type") == "result" and d.get("subtype") == "success" and code == 0:
        return "ok", d
    if d is None and re.search(r"unknown option|unrecognized option|unknown argument|error: option .* argument missing", blob, re.I):
        return "update", d  # 안전 옵션을 모르는 예전 판 → 제한 없이 돌리지 않고 업데이트 안내
    if status_code == 401 or re.search(r"not logged in|please run /login|/login|invalid api key|oauth token (has )?expired|authentication|401", blob, re.I):
        return "login", d
    if status_code == 429 or re.search(r"usage limit|limit reached|rate limit|429|5-hour limit|weekly limit", blob, re.I):
        return "limit", d
    if re.search(r"ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|network|getaddrinfo|unable to connect|fetch failed", blob, re.I):
        return "network", d
    return "error", d


def _model_of(d):
    mu = (d or {}).get("modelUsage") or {}
    names = [k for k in mu if "haiku" not in k] or list(mu)
    return names[0] if names else MODEL


def run(prompt, images=None, timeout=TIMEOUT, cancel=None, on_tick=None):
    """프롬프트(+그림 파일 [(원본 경로, 넣을 이름)]) → {"text", "model"}. 실패하면 ClaudeError (한국어 안내).
    cancel: threading.Event (멈추기 ✕) · on_tick(초): 0.5초마다 (진행 표시용)."""
    exe = find_exe()
    if not exe:
        raise ClaudeError("missing", MSG["missing"])
    feat = features(exe)
    with tempfile.TemporaryDirectory(prefix="futsal-claude-", ignore_cleanup_errors=True) as tmp:
        for src, nm in images or []:
            try:
                shutil.copy2(src, Path(tmp) / Path(nm).name)
            except OSError:
                pass
        has_img = any(Path(tmp).iterdir())
        cmd = build_cmd(exe, has_img, feat)
        try:
            p = subprocess.Popen(cmd, cwd=tmp, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=child_env(), **_flags())
        except OSError:
            raise ClaudeError("missing", MSG["missing"]) from None
        res = {}

        def feed():
            try:  # 넣고 바로 닫음 (명령줄 길이 제한·프로세스 목록 노출 없이)
                p.stdin.write(str(prompt).encode("utf-8"))
            except OSError:
                pass
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass

        def drain(stream, key, cap):
            """한 줄기씩 따로 끝까지 읽음 (표준 오류가 파이프를 채워 멈추는 일 없이) · 마지막 cap 바이트만 남김."""
            buf = bytearray()
            try:
                while True:
                    b = stream.read(1 << 16)
                    if not b:
                        break
                    buf += b
                    if len(buf) > 2 * cap:
                        del buf[:-cap]
            except (OSError, ValueError):
                pass
            res[key] = bytes(buf[-cap:]).decode("utf-8", "replace")
        tf = threading.Thread(target=feed, daemon=True)
        to = threading.Thread(target=drain, args=(p.stdout, "out", 4 << 20), daemon=True)
        te = threading.Thread(target=drain, args=(p.stderr, "err", 64 << 10), daemon=True)
        tf.start()
        to.start()
        te.start()
        t0, why = time.time(), None
        while p.poll() is None:
            if cancel is not None and cancel.is_set():
                why = "cancel"
            elif time.time() - t0 > timeout:
                why = "timeout"
            if why:
                _kill(p)
                break
            if on_tick:
                on_tick(int(time.time() - t0))
            time.sleep(0.5)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill(p)
        to.join(10)
        te.join(5)
        for s in (p.stdout, p.stderr):
            try:
                s.close()
            except OSError:
                pass
    if why:
        raise ClaudeError(why, MSG[why])
    kind, d = explain(p.returncode, res.get("out", ""), res.get("err", ""))
    if kind != "ok":
        if kind == "login":
            reset_status()
        raise ClaudeError(kind, MSG[kind])
    text = str(d.get("result") or "")
    if not text.strip():
        raise ClaudeError("error", MSG["error"])
    return {"text": text, "model": _model_of(d)}
