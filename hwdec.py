"""그래픽카드로 영상 풀기(하드웨어 디코딩) — 확인이 된 PC 에서만, 문제가 생기면 바로 일반 방식으로.

아이폰 4K HDR(HEVC 10비트) 내보내기에서 색 바꾸기를 줄인 뒤에는 '영상 풀기'가 CPU 의 절반 이상 (5초 4K 장면: 12 / 21 CPU초).
Windows 내장·외장 그래픽카드의 d3d11va 로 풀면 그만큼 CPU 가 빠짐. 하지만 이 기계(리눅스)에서는 시험할 수 없어서:
- Windows 에서만 · 그 영상이 HDR(=아이폰 HEVC 10비트)일 때만 · 남은 메모리가 HW_MIN_MB 이상일 때만
  (그래픽카드 풀기 버퍼는 내장 그래픽의 '공유 메모리' = 시스템 메모리를 쓰는데 프로세스 메모리에는 안 보임)
- 처음 쓸 때 한 번 확인 (명령마다 PROBE_SEC 초 넘으면 끄고 '안 됨'): ffmpeg -hwaccels 에 d3d11va 가 있고, 그 영상 1초를
  그래픽카드로 푼 프레임들이 일반 방식과 한 화소도 다르지 않고(framemd5 · HEVC 풀기는 원래 비트까지 같아야 함 →
  ffmpeg 가 몰래 일반 방식으로 돌아간 경우·색 정보가 틀린 경우도 걸러짐), 일반 방식보다 느리지 않아야 씀
- 확인용 ffmpeg 는 PROCS 에 넣어 둠 → 멈추기(✕)·앱 끄기(editor.cancel_export) 때 같이 꺼짐
- 내보내기 중 그래픽카드로 푼 구간이 영상 풀기 문제로 실패하면 이번 실행 동안은 다시 안 씀 (그 구간은 일반 방식으로 다시)
  · 그래픽카드 '인코더' 문제(editor.HwEncError)는 여기서 다루지 않음 (원래 처리로)
- FUTSAL_HWDEC=0 이면 끔 · =1 이면 Windows 가 아니어도 확인해 봄 (시험용)
프레임은 시스템 메모리로 받아(-hwaccel_output_format 없음) 뒤 필터(색 바꾸기·자막)는 그대로.
"""
import os
import subprocess
import sys
import threading
import time

import core

ACCELS = ("d3d11va",)
PROBE_SEC = 20
HW_MIN_MB = 3000
_ST = {"accel": None, "checked": False, "bad": False}
_LOCK = threading.Lock()
PROCS = set()


def _wanted():
    v = os.environ.get("FUTSAL_HWDEC")
    if v == "0":
        return False
    return sys.platform == "win32" or v == "1"


def _mem_ok():
    try:
        import worker
        free = worker.avail_mb()
    except Exception:  # noqa: BLE001
        return True
    return free is None or free >= HW_MIN_MB


def _run(cmd, timeout=PROBE_SEC):
    """→ (종료 코드, 표준 출력, 걸린 초) · 시간 초과·꺼짐이면 (None, '', 초). 멈추기(✕)로 끌 수 있음."""
    t0 = time.time()
    p = core.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)  # 검은 창 없이 · 앱이 꺼지면 같이 꺼짐
    PROCS.add(p)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return None, "", time.time() - t0
    finally:
        PROCS.discard(p)
    return p.returncode, out.decode("utf-8", "replace"), time.time() - t0


def _md5s(acc, path, t):
    """그 영상 1초를 풀어 프레임마다 md5 (10비트 같은 형식으로 맞춰서) → ([md5...], 걸린 초) · 실패면 (None, 초)."""
    cmd = [core.ffmpeg(), "-hide_banner", "-v", "error"] + (["-hwaccel", acc] if acc else []) + \
        ["-ss", f"{t:.3f}", "-t", "1", "-i", str(path), "-map", "0:v:0", "-vf", "format=yuv420p10le", "-f", "framemd5", "-"]
    rc, out, sec = _run(cmd)
    if rc != 0:
        return None, sec
    return [ln.rsplit(",", 1)[1].strip() for ln in out.splitlines() if ln and not ln.startswith("#")] or None, sec


def _check(path, t):
    """그래픽카드 풀기가 되는지 이 영상 1초로 확인 → accel 이름 또는 None."""
    rc, out, _ = _run([core.ffmpeg(), "-hide_banner", "-hwaccels"])
    have = set(out.split()) if rc == 0 else set()
    for acc in ACCELS:
        if acc not in have:
            continue
        sw, sw_sec = _md5s(None, path, t)  # 일반 방식을 먼저 (파일이 캐시에 올라가 비교가 공평)
        if not sw:
            return None
        got, hw_sec = _md5s(acc, path, t)
        if got == sw and hw_sec <= sw_sec:
            return acc
    return None


def will_probe():
    """이번 내보내기에서 처음 확인을 할 차례인지 (진행 표시용)."""
    return _wanted() and not _ST["checked"] and not _ST["bad"]


def input_opts(hdr, path, t=0.0):
    """내보내기 입력 앞에 붙일 옵션 (['-hwaccel', 'd3d11va'] 또는 []). hdr: 그 영상의 HDR 종류 (없으면 None → 안 씀)."""
    if not hdr or not _wanted() or _ST["bad"] or not _mem_ok():
        return []
    with _LOCK:
        if not _ST["checked"]:
            _ST["checked"] = True
            try:
                _ST["accel"] = _check(path, t)
            except Exception:  # noqa: BLE001
                _ST["accel"] = None
    return ["-hwaccel", _ST["accel"]] if _ST["accel"] and not _ST["bad"] else []


def strip(args):
    """입력 인자에서 그래픽카드 풀기 옵션을 뺌 (실패한 구간을 일반 방식으로 다시 만들 때)."""
    out, i = [], 0
    while i < len(args):
        if args[i] == "-hwaccel" and i + 1 < len(args):
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def kill_all():
    """확인 중인 ffmpeg 를 끔 (멈추기 ✕ · 앱 끄기)."""
    for p in list(PROCS):
        try:
            p.kill()
        except OSError:
            pass


def mark_bad():
    _ST["bad"] = True


def is_bad():
    return _ST["bad"]
