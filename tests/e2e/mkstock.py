"""썸네일 퀄리티 시험 영상 만들기 (개발용 · 배포 안 됨): python3 tests/e2e/mkstock.py <내려받기 폴더> <시험 영상 폴더> [번호 ...]

판정 4회차: 시험 영상 폴더가 다른 작업의 디스크 정리로 두 번 지워져 같은 영상을 다시 만들 길이 없었음 → 원본 목록과 만드는 법을 저장소에.
원본 (모두 상업적 사용 가능 · 영상은 저장소에 넣지 않음):
- Mixkit 무료 라이선스 (https://mixkit.co/license/#videoFree · 출처 표시 필요 없음): 아래 MIXKIT 번호의 1080p 파일
- Wikimedia Commons 'File:Cienfuegos vs Mayabeque.webm' — CC BY 3.0, 출처: Cienfuegos Perlavision (THQSTOCK001)
같은 번호·같은 순서로 이어 붙이므로 다시 만들어도 같은 장면이 나옴 (인코딩만 다를 수 있음).
번호를 주면 그 영상만 (예: 002 006). Commons 가 한도(429)로 막히면 001 은 건너뛰고 나머지는 만듦."""
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

UA = "FutsalStudioDev/1.0 (thumbnail test footage)"
FF = shutil.which("ffmpeg") or "ffmpeg"
COMMONS = {"cienfuegos": "Cienfuegos vs Mayabeque.webm"}
# 영상 → (이름, 크기, [(원본, 시작초, 길이초)], 세로 자르기?)
PLAN = {
    "001": ("풋살 경기 하이라이트 해설", (1280, 720), [("commons:cienfuegos", 0, 105.7)]),
    "002": ("1대1 돌파 이렇게 하세요", (1920, 1080), [("43483", 0, 5.0), ("43484", 0, 3.5), ("42530", 0, 10.6), ("43485", 0, 6.7), ("43482", 0, 7.8), ("43481", 0, 5.2)]),
    "003": ("유소년 드리블 훈련", (1280, 720), [("6652", 0, 17.4), ("43490", 0, 12.9), ("43491", 0, 14.4), ("4576", 0, 12.0)]),
    "004": ("발바닥 드래그 기본기", (1920, 1080), [("43486", 0, 22.1), ("43490", 0, 12.9), ("43501", 0, 18.6), ("42530", 0, 10.6), ("43491", 0, 14.4), ("43483", 0, 5.0)]),
    "005": ("코치 인터뷰 실전 전술", (1920, 1080), [("4567", 0, 30.0), ("43499", 0, 8.1), ("43485", 0, 6.7), ("41372", 0, 23.2), ("43482", 0, 7.8), ("43481", 0, 5.2), ("43479", 0, 6.0)]),
    "006": ("슈팅 연습 세로 영상", (1080, 1920), [("42541", 0, 14.6), ("2915", 0, 11.0), ("43494", 0, 5.6), ("43495", 0, 7.1)]),
}


# Commons 가 막히면(한도 429) 대신 쓰는 Mixkit 묶음 (판정 4회차 개발 판정은 이것으로 함 — 실내 풋살장 장면이 없어짐)
FALLBACK = {"001": [("43481", 0, 5.2), ("43482", 0, 7.8), ("43485", 0, 6.7), ("43499", 0, 8.1), ("41372", 0, 23.2), ("4576", 0, 13.0), ("43483", 0, 5.0), ("43484", 0, 3.5)]}


def fetch(url, dst, tries=int(__import__('os').environ.get('MKSTOCK_TRIES', 8))):
    for k in range(tries):
        r = subprocess.run(["curl", "-sS", "-f", "-L", "--max-time", "600", "-A", UA, "-o", str(dst), url], capture_output=True, text=True)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 10000:
            return True
        print("  다시 받기", url, r.stderr.strip()[:120], flush=True)
        time.sleep(30 * (k + 1))
    return False


def source(dl, key):
    if key.startswith("commons:"):
        name = COMMONS[key.split(":", 1)[1]]
        fn = name.replace(" ", "_")
        h = hashlib.md5(fn.encode()).hexdigest()
        dst = dl / ("commons_" + fn)
        if not dst.exists() and not fetch(f"https://upload.wikimedia.org/wikipedia/commons/{h[0]}/{h[:2]}/{urllib.parse.quote(fn)}", dst):
            return None
        return dst
    dst = dl / f"mixkit_{key}.mp4"
    if not dst.exists() and not fetch(f"https://assets.mixkit.co/videos/{key}/{key}-1080.mp4", dst) and not fetch(f"https://assets.mixkit.co/videos/{key}/{key}-720.mp4", dst):
        return None
    return dst


def build(dl, out, vid):
    title, (W, H), parts = PLAN[vid]
    dst = out / f"20261007_THQSTOCK{vid}_{title}.mp4"
    srcs = [(source(dl, k), a, d) for k, a, d in parts]
    if any(s is None for s, _, _ in srcs) and vid in FALLBACK:
        print("Commons 를 못 받아 Mixkit 대체 묶음으로", vid, flush=True)
        srcs = [(source(dl, k), a, d) for k, a, d in FALLBACK[vid]]
    if any(s is None for s, _, _ in srcs):
        print("건너뜀 (원본을 못 받음)", vid, flush=True)
        return None
    args, fl = [], []
    for i, (s, a, d) in enumerate(srcs):
        args += ["-ss", str(a), "-t", str(d), "-i", str(s)]
        fl.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,setsar=1,format=yuv420p[v{i}]")
    fl.append("".join(f"[v{i}]" for i in range(len(srcs))) + f"concat=n={len(srcs)}:v=1:a=0[v]")
    # anullsrc 는 -map 뒤에 넣으면 입력 순서가 바뀌므로 따로 섞음
    cmd = [FF, "-y", "-v", "error"] + args + ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-filter_complex", ";".join(fl), "-map", "[v]", "-map", f"{len(srcs)}:a",
                                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-shortest", str(dst)]
    subprocess.run(cmd, check=True)
    print("ok", dst.name, flush=True)
    return dst


if __name__ == "__main__":
    dl, out = Path(sys.argv[1]), Path(sys.argv[2])
    dl.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    which = sys.argv[3:] or sorted(PLAN)
    done = {v: str(build(dl, out, v) or "") for v in which}
    print(json.dumps(done, ensure_ascii=False))
