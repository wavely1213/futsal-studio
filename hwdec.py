"""그래픽카드로 영상 풀기(하드웨어 디코딩) — 확인이 된 PC 에서만, 문제가 생기면 바로 일반 방식으로.

아이폰 4K HDR(HEVC 10비트) 내보내기에서 색 바꾸기를 줄인 뒤에는 '영상 풀기'가 CPU 의 절반 이상 (5초 4K 장면: 12 / 21 CPU초).
Windows 내장·외장 그래픽카드의 d3d11va 로 풀면 그만큼 CPU 가 빠짐. 하지만 이 기계(리눅스)에서는 시험할 수 없어서:
- Windows 에서만 · 그 영상이 HDR(=아이폰 HEVC 10비트)일 때만
- 처음 쓸 때 한 번 확인: ffmpeg -hwaccels 에 d3d11va 가 있고, 그 영상 1초를 그래픽카드로 풀어 일반 방식과 프레임 수가 같아야 씀
- 내보내기 중 그래픽카드로 푼 구간이 하나라도 실패하면 이번 실행 동안은 다시 안 씀 (그 구간은 일반 방식으로 다시)
- FUTSAL_HWDEC=0 이면 끔 · =1 이면 Windows 가 아니어도 확인해 봄 (시험용)
프레임은 시스템 메모리로 받아(-hwaccel_output_format 없음) 뒤 필터(색 바꾸기·자막)는 그대로.
"""
import os
import re
import sys
import threading

import core

ACCELS = ("d3d11va",)
_ST = {"accel": None, "checked": False, "bad": False}
_LOCK = threading.Lock()


def _wanted():
    v = os.environ.get("FUTSAL_HWDEC")
    if v == "0":
        return False
    return sys.platform == "win32" or v == "1"


def _frames(cmd):
    r = core.run(cmd)
    if r.returncode:
        return None
    m = re.findall(r"frame=\s*(\d+)", r.stderr or "")
    return int(m[-1]) if m else None


def _check(path, t):
    """그래픽카드 풀기가 되는지 이 영상 1초로 확인 → accel 이름 또는 None."""
    hw = core.run([core.ffmpeg(), "-hide_banner", "-hwaccels"])
    have = set((hw.stdout or "").split()) if hw.returncode == 0 else set()
    for acc in ACCELS:
        if acc not in have:
            continue
        base = ["-ss", f"{t:.3f}", "-t", "1", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"]
        sw = _frames([core.ffmpeg(), "-hide_banner"] + base)
        got = _frames([core.ffmpeg(), "-hide_banner", "-hwaccel", acc] + base)
        if sw and got == sw:
            return acc
    return None


def input_opts(hdr, path, t=0.0):
    """내보내기 입력 앞에 붙일 옵션 (['-hwaccel', 'd3d11va'] 또는 []). hdr: 그 영상의 HDR 종류 (없으면 None → 안 씀)."""
    if not hdr or not _wanted() or _ST["bad"]:
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


def mark_bad():
    _ST["bad"] = True


def is_bad():
    return _ST["bad"]
