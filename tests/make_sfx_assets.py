"""sfx/*.ogg 다시 만들기 (D-370): Kenney 원본 zip 네 개 → 앞뒤 무음만 자르고 최대 크기 -3 dBFS · 48kHz 스테레오 ogg.

예전 변환(a2f34e5)은 소리 앞 5ms 정도(PCM 약 1000바이트)만 남기고 나머지를 0으로 채워, 27개 중 26개가 '딸깍' 하는 짧은 소리로만 들렸음.
이 스크립트는 자르지 않음 — 원본에서 소리가 끝날 때까지(최대보다 60dB 작아질 때까지) 그대로 둠.

usage: python3 tests/make_sfx_assets.py <Kenney zip 폴더> [--check]
  zip 폴더: interface.zip · impact.zip · digital.zip · jingles.zip (sha256 은 sfx/LICENSE.txt 와 같아야 함 · 다르면 멈춤)
  --check: 파일을 쓰지 않고 저장소 ogg 의 들리는 길이를 원본과 견줌 (원본의 80% 넘게 들려야 통과)
"""
import hashlib
import io
import re
import subprocess
import sys
import tempfile
import wave
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SFX = ROOT / "sfx"
SR = 48000
PEAK_DB = -3.0
FLOOR_DB = -60.0   # 최대보다 이만큼 작은 소리까지를 '소리'로 봄 (앞뒤 자르기 기준)
PACK = {"Interface Sounds": "interface.zip", "Impact Sounds": "impact.zip", "Digital Audio": "digital.zip", "Music Jingles": "jingles.zip"}


def license_map():
    lic = (SFX / "LICENSE.txt").read_text(encoding="utf-8")
    sums = {}
    for m in re.finditer(r"^\s+(Interface Sounds|Impact Sounds|Digital Audio|Music Jingles): \S+\n\s+zip sha256 ([0-9a-f]{64})", lic, re.M):
        sums[m[1]] = m[2]
    files = [(m[1], m[2].strip(), m[3].strip()) for m in re.finditer(r"^\s+(\S+\.ogg)\s+<- (.+?) · (.+)$", lic, re.M)]
    return sums, files


def decode(data):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"], input=data, capture_output=True, check=True)
    return np.frombuffer(p.stdout, "<f4").astype(np.float64).reshape(-1, 2)


def audible(x, hop=240):
    """들리는 길이(초): 5ms 창 크기가 최대보다 30dB 안쪽인 첫 창 ~ 마지막 창."""
    m = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    n = len(m) // hop
    if n == 0:
        return 0.0
    env = 20 * np.log10(np.sqrt((m[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-12)
    loud = np.nonzero(env >= env.max() - 30)[0]
    return float((loud[-1] - loud[0] + 1) * hop / SR)


def prepare(x):
    """앞뒤 무음 자르기(앞 3ms · 뒤 20ms 여유) → 끝 5ms 페이드 → 최대 크기 PEAK_DB."""
    a = np.abs(x).max(axis=1)
    pk = float(a.max())
    on = np.nonzero(a >= pk * 10 ** (FLOOR_DB / 20))[0]
    i0 = max(0, int(on[0]) - int(0.003 * SR))
    i1 = min(len(x), int(on[-1]) + int(0.02 * SR))
    y = x[i0:i1].copy()
    f = min(len(y) // 4, int(0.005 * SR))
    if f > 0:
        y[-f:] *= np.linspace(1, 0, f)[:, None]
    return y * (10 ** (PEAK_DB / 20) / pk)


def encode(y, out):
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "a.wav"
        with wave.open(str(w), "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(SR)
            f.writeframes(np.clip(np.round(y * 32767), -32767, 32767).astype("<i2").tobytes())
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(w), "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                        "-c:a", "libvorbis", "-q:a", "6", str(out)], check=True)


def main():
    zdir = Path(sys.argv[1])
    check = "--check" in sys.argv[2:]
    sums, files = license_map()
    zips = {}
    for pack, fn in PACK.items():
        data = (zdir / fn).read_bytes()
        got = hashlib.sha256(data).hexdigest()
        if got != sums[pack]:
            sys.exit(f"{fn} 지문이 LICENSE.txt 와 달라요 ({got})")
        zips[pack] = zipfile.ZipFile(io.BytesIO(data))
    bad = 0
    for mine, pack, orig in files:
        z = zips[pack]
        name = next(n for n in z.namelist() if n.endswith(orig))
        x = decode(z.read(name))
        a0 = audible(x)
        if not check:
            encode(prepare(x), SFX / mine)
        a1 = audible(decode((SFX / mine).read_bytes()))
        ok = a1 >= 0.8 * a0 or a1 >= a0 - 0.0051   # (아주 짧은 틱은 5ms 창 하나 차이까지)
        bad += not ok
        print(f"{mine:18s} 원본 들림 {a0:5.3f}s · 저장소 {a1:5.3f}s {'' if ok else '← 잘림'}")
    if bad:
        sys.exit(f"{bad}개가 원본보다 짧아요")
    print("ok")


if __name__ == "__main__":
    main()
