"""편집실 v2 검수 (Playwright + ffmpeg/ffprobe).

사용: python3 ed2_test.py            (edtest2 서버를 8766 에 새로 띄우고 전체 검사)
      python3 ed2_test.py --no-export (내보내기 검사 생략)
      ED_DIR=$(bash $SCRATCH/ed2_clone.sh <이름> <포트>) ED_PORT=<포트> ED_REPO=<저장소> python3 ed2_test.py  (따로 · 회귀 reg.sh)
같은 내용을 저장소 tests/e2e/ed2_test.py 에도 둠 (개발용 · unittest 가 줍지 않음 · 고치면 둘을 함께 · 시험 자료 edtest2·ed2_clone.sh 는 $SCRATCH).
재생 시작 뒤 재는 확인(L·K · 배경음악 줄이기 · A3 소리)은 재생 시계가 출발한 뒤부터 재고, 출발까지는 START_MAX(2초) 안인지 따로 봄 (D-515 · D-516).
"""
import json
import re
import glob
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import numpy as np
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
D = Path(os.environ["ED_DIR"]) if os.environ.get("ED_DIR") else HERE / "edtest2"  # 따로 돌릴 때: ed2_clone.sh 로 만든 폴더
WORK = D / "work"
SHOTS = D / "shots"
NAME = "20200320_TESTVIDEO01_테스트 영상.mp4"
RED = "20200101_REDBROLL001_빨간 화면.mp4"
PORT = int(os.environ.get("ED_PORT", "8766"))
URL = f"http://127.0.0.1:{PORT}/editor?name=" + quote(NAME)
NO_EXPORT = "--no-export" in sys.argv
RESULTS = []


def check(name, ok, info=""):
    RESULTS.append((name, bool(ok), info))
    print(("PASS " if ok else "FAIL ") + name + (f"  · {info}" if info else ""), flush=True)


def approx(a, b, tol=0.02):
    return a is not None and abs(a - b) <= tol


# ---------- 준비: 작업 폴더 초기화 ----------
SHOTS.mkdir(exist_ok=True)
for f in glob.glob(str(SHOTS / "*.png")):
    os.remove(f)
for f in glob.glob(str(WORK / "projects" / "*.json")):
    os.remove(f)
shutil.rmtree(WORK / "projects" / "backup", ignore_errors=True)
shutil.rmtree(WORK / "edit_media", ignore_errors=True)
for d_ in glob.glob(str(WORK / "analysis" / "*REDBROLL*")) + [str(WORK / "analysis" / "_media")]:
    shutil.rmtree(d_, ignore_errors=True)
for f in glob.glob(str(WORK / "out" / "*")):
    shutil.rmtree(f, ignore_errors=True) if os.path.isdir(f) else os.remove(f)
shutil.copy(D / "media_src" / "broll_red.mp4", WORK / "videos" / RED)
(WORK / "styles").mkdir(exist_ok=True)
(WORK / "styles" / "빠른컷.json").write_text(json.dumps({
    "source": "test", "duration": 300, "analyzed": "2026-10-06 10:00", "cutsPerMin": 12, "avgShot": 5.0, "medianShot": 4.2,
    "zoomCutsPerMin": 10, "zoomRatio": 0.4, "avgZoom": 1.25, "silenceRatio": 0.1, "pauseP75": 0.3, "pauses": 40,
    "captionRatio": 0.8, "captionPos": "middle", "captionColor": "#FFE14D", "lufs": -12.0, "charsPerSec": 6.0}, ensure_ascii=False), encoding="utf-8")
# 예전(v1.4) 형식 프로젝트 → 열 때 v2 로 바뀌어야 함
caps = json.loads((WORK / "analysis" / NAME.rsplit(".", 1)[0] / "transcript.json").read_text(encoding="utf-8"))
old = {"source": NAME, "info": {"duration": 60.0, "width": 1280, "height": 720, "fps": 30.0},
       "captions": [{"id": f"c{i}", "start": s["start"], "end": s["end"], "text": s["text"]} for i, s in enumerate(caps)],
       "sequences": [{"id": "old1", "name": "옛 편집본", "format": "shorts",
                      "clips": [{"id": "k1", "in": 1.0, "out": 5.0, "volume": 0.5, "fadeIn": 0.2, "fadeOut": 0.0, "mute": False, "reframe": 0.3},
                                {"id": "k2", "in": 12.0, "out": 16.0, "volume": 1.0, "fadeIn": 0.0, "fadeOut": 0.5, "mute": False, "reframe": 0.5}],
                      "captionsOn": True, "captionStyle": {"weight": "Black", "size": 72, "fill": "#FFFFFF", "stroke": "#000000", "strokeW": 8, "bgOn": False,
                                                           "bg": "#000000", "bgOpacity": 0.55, "y": 0.8, "effect": "pop", "highlight": "#FFD400"},
                      "titles": [{"id": "t1", "text": "옛 제목", "start": 0, "dur": 8, "style": {"weight": "Black", "size": 100, "fill": "#FFE14D", "stroke": "#111111",
                                  "strokeW": 9, "bgOn": False, "bg": "#000000", "bgOpacity": 0.55, "y": 0.22, "effect": "pop", "highlight": "#FFD400"}}],
                      "shapes": [], "layout": {"mode": "box", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0},
                      "master": {"volume": 1.0, "normalize": True}}], "active": "old1"}
(WORK / "projects" / (NAME.rsplit(".", 1)[0] + ".json")).write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
subprocess.run([str(D / "srv.sh")], check=True, env={**os.environ, "ED_DIR": str(D), "ED_PORT": str(PORT)})


def ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                        "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout)


def frame(path, t, w, h):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3).astype(np.float32)


def tone(path, t0, t1, f0):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "f32le", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, np.float32)
    s = a[int(t0 * 48000):int(t1 * 48000)]
    w = np.hanning(len(s))
    F = np.abs(np.fft.rfft(s * w))
    fq = np.fft.rfftfreq(len(s), 1 / 48000)
    k = np.argmin(np.abs(fq - f0))
    return 20 * np.log10(F[k - 2:k + 3].max() / w.sum() * 2 + 1e-12)


# ---------- R. 검수(감사) 반영 회귀 검사 · 서버/내보내기 (브라우저 없이) ----------
BACKEND_ONLY = "--backend-only" in sys.argv
sys.path.insert(0, str(D / "app"))
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402
import threading  # noqa: E402
import core as _core  # noqa: E402
import editor as _ed  # noqa: E402
from PIL import Image  # noqa: E402
import tempfile  # noqa: E402

API = f"http://127.0.0.1:{PORT}"


def api(path, body=None, headers=None, raw=False):
    """서버 호출 → (상태 코드, JSON 또는 바이트)."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return r.status, (b if raw else json.loads(b or b"{}"))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b or b"{}")
        except ValueError:
            return e.code, b


def wait_job(timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, s = api("/api/state?since=999999")
        if not s.get("job"):
            return s
        time.sleep(0.3)
    return {}


def be_export(name, seq, opts, timeout=300):
    proj = load_seq_proj(name, seq)
    code, r = api("/api/edit/export", {"name": name, "project": proj, "opts": opts})
    if code != 200:
        return {"error": str(r)}
    return wait_job(timeout)


INFO = _ed.media_info(NAME)
CAPS = [{"id": f"c{i}", "start": c["start"], "end": c["end"], "text": c["text"]} for i, c in enumerate(caps)]
MAIN = {"id": "main", "kind": "video", "src": "videos", "file": NAME, "dur": INFO["duration"], "w": INFO["width"], "h": INFO["height"], "fps": INFO["fps"], "audio": True}


def load_seq_proj(name, seq):  # 화면 검사용 프로젝트는 건드리지 않고 내보낼 프로젝트를 직접 만듦
    return {**seq, "captions": CAPS, "info": INFO, "source": name, "media": [MAIN] + seq.pop("_media", [])}


def mkseq(sid, name, items, fmt="long", **kw):
    s = {"id": sid, "name": name, "format": fmt, "v": 2, "captionsOn": False, "captionStyle": dict(_ed.LONG_STYLE), "titles": [], "shapes": [],
         "layout": {"mode": "fill"}, "master": {"volume": 1, "normalize": False, "lufs": -14}, "duck": {"on": False, "amount": -14},
         "tracks": _ed.default_tracks(), "items": items, "trans": [], "markers": []}
    s.update(kw)
    return s


def pair(a, b, start, media="main"):
    v, au = _ed._pair(a, b, media=media, start=start)
    return [v, au]


OUT = WORK / "out"
STEM = NAME.rsplit(".", 1)[0]
MS = D / "media_src"

# R1 · 영상 이름 검사 (절대 경로·네트워크 경로·다른 사이트)
c1, _ = api("/api/edit/autoseq?name=" + quote(str((MS / "broll_red.mp4").resolve())))
c2, _ = api("/frame?name=" + quote("/etc/passwd") + "&t=1")
c3, _ = api("/api/edit/open?name=" + quote(r"\\evil.example\share\a.mp4"))
c4, _ = api("/api/state", headers={"Host": "evil.example:8766"})
c5, _ = api("/api/edit/save", {"name": "../../x.mp4", "project": {}})
check("[14] 이름 대신 경로를 넣으면 거절 (절대·네트워크 경로, 다른 사이트 Host)", (c1, c2, c3, c4, c5) == (404, 404, 404, 403, 400), str((c1, c2, c3, c4, c5)))

# R2 · ffmpeg 는 앱과 함께 깔린 것 먼저
check("[62] ffmpeg: 함께 설치된 imageio-ffmpeg 를 먼저 씀", "imageio_ffmpeg" in _core.ffmpeg(), _core.ffmpeg())

# R3 · 대괄호 이름 영상: 백업 목록·정리 · 원자적 저장 · 손상 복구 · 판 번호 충돌
BR = "20200321_TESTVIDEO02_[풋살 꿀팁] 테스트.mp4"
shutil.copy(WORK / "videos" / NAME, WORK / "videos" / BR)
shutil.copytree(WORK / "analysis" / STEM, WORK / "analysis" / BR.rsplit(".", 1)[0], dirs_exist_ok=True)
code, j = api("/api/edit/open?name=" + quote(BR))
proj = j["project"]
rev = proj.get("rev", 0)
for i in range(3):
    proj["sequences"][0]["name"] = f"고침 {i}"
    code, r = api("/api/edit/save", {"name": BR, "project": proj, "rev": rev})
    rev = r.get("rev", rev)
    time.sleep(1.05)
_, bk = api("/api/edit/backups?name=" + quote(BR))
files = [f for f in os.listdir(WORK / "projects" / "backup") if f.startswith(BR.rsplit(".", 1)[0] + "__")]
check("[7·8] 이름에 [ ] 가 있어도 백업 목록이 보이고, 5분에 한 벌만 (저장마다 쌓이지 않음)", len(bk["backups"]) >= 1 and len(files) == len(bk["backups"]) <= 2, f"목록 {len(bk['backups'])} · 파일 {len(files)}")
# 판 번호: 오래된 판으로 저장하면 409, force 는 백업 후 저장
code_old, r_old = api("/api/edit/save", {"name": BR, "project": proj, "rev": rev - 1})
code_new, r_new = api("/api/edit/save", {"name": BR, "project": proj, "rev": rev})
code_f, r_f = api("/api/edit/save", {"name": BR, "project": proj, "rev": 0, "force": True})
check("[15] 다른 창이 먼저 저장했으면 덮어쓰지 않음 (409) · '이 창 내용으로'는 백업 뒤 저장", code_old == 409 and r_old.get("conflict") and code_new == 200 and code_f == 200
      and any("덮어쓰기" in b["time"] for b in api("/api/edit/backups?name=" + quote(BR))[1]["backups"]), f"{code_old} {code_new} {code_f}")
# 동시에 저장 (큰 것·작은 것 번갈아) → 파일이 깨지지 않음
big = json.loads(json.dumps(proj))
big["captions"] = big["captions"] * 300
small = json.loads(json.dumps(proj))
bad = []


def hammer(pj, n):
    for _ in range(n):
        api("/api/edit/save", {"name": BR, "project": pj})


ths = [threading.Thread(target=hammer, args=(pj, 15)) for pj in (big, small, big, small)]
for t in ths:
    t.start()
for t in ths:
    t.join()
pp = WORK / "projects" / (BR.rsplit(".", 1)[0] + ".json")
try:
    json.loads(pp.read_text(encoding="utf-8"))
    ok_json = True
except ValueError:
    ok_json = False
check("[5·6] 여러 저장이 겹쳐도 프로젝트 파일이 깨지지 않음 (임시 파일 → 바꿔 끼우기 · 잠금)", ok_json and not list((WORK / "projects").glob("*.tmp")))
# 반쯤 쓰다 꺼진 파일 → 백업으로 열림
txt = pp.read_text(encoding="utf-8")
pp.write_text(txt[: len(txt) // 2], encoding="utf-8")
code, j = api("/api/edit/open?name=" + quote(BR))
check("[5] 깨진 프로젝트 파일 → 최근 백업으로 열고 알려 줌 (깨진 파일은 옆에 남김)", code == 200 and j.get("recovered") and list((WORK / "projects").glob("*손상*.bad")), str(j.get("recovered")))

# R4 · 편집점 다시 찾기: 사용자가 만든 편집본은 그대로 + 새 가편집 추가 (받아쓰기는 흉내)
code, j = api("/api/edit/open?name=" + quote(BR))
proj = j["project"]
proj["sequences"].insert(0, mkseq("mine1", "내가 만든 최종 편집본", pair(1, 3, 0)))
api("/api/edit/save", {"name": BR, "project": proj, "rev": proj.get("rev", 0)})
n_before = len(proj["sequences"])
r = subprocess.run([sys.executable, "-c", "import app, core; core.analyze_many = lambda names, log, model='': []; app.Handler._analyze({'names': [" + repr(BR) + "]})"],
                   cwd=str(D / "app"), capture_output=True, text=True, encoding="utf-8")
after = json.loads(pp.read_text(encoding="utf-8"))
names = [q["name"] for q in after["sequences"]]
code_stale, _ = api("/api/edit/save", {"name": BR, "project": proj, "rev": proj.get("rev", 0) + 1})
check("[0·17] 편집점 다시 찾기: 만든 편집본은 그대로, 새 가편집은 옆에 추가 · 그 전 상태 백업", r.returncode == 0 and "내가 만든 최종 편집본" in names and len(names) > n_before
      and any("(새로 찾음)" in n for n in names) and any("편집점 다시 찾기 전" in b["time"] for b in api("/api/edit/backups?name=" + quote(BR))[1]["backups"]),
      f"{r.stderr[-200:]} {names}")
check("[0·15] 다시 찾은 뒤 예전 창의 저장은 막힘 (409)", code_stale == 409, str(code_stale))

# R5 · 썸네일 띠: 키프레임만 읽어 빠르게 + 끝까지 채움 · 미디어 탭 그림(포스터)
BENCH = "20990101_BENCHMARK01_벤치.mp4"
shutil.rmtree(WORK / "analysis" / BENCH.rsplit(".", 1)[0], ignore_errors=True)
t0 = time.time()
th = _ed.thumbs(BENCH)
dt_th = time.time() - t0
strip = np.asarray(Image.open(_ed.thumbs_file(BENCH)).convert("L"), np.float32)
tw = strip.shape[1] // th["count"]
last_tile = strip[:, -tw:].mean()
check("[12] 썸네일 띠: 10분 1080p 영상도 몇 초 안에 (키프레임만) · 마지막 칸까지 그림", dt_th < 8 and last_tile > 5, f"{dt_th:.1f}초 · 마지막 칸 밝기 {last_tile:.0f}")
t0 = time.time()
code, _b = api("/api/edit/poster.jpg?src=videos&f=" + quote(BENCH), raw=True)
check("[11·40] 미디어 탭 그림은 한 장면만 빠르게 (/api/edit/poster.jpg)", code == 200 and time.time() - t0 < 3 and len(_b) > 500, f"{time.time() - t0:.2f}초")

# R6 · 가져온 파일 캐시: 이름이 같고 확장자만 다른 파일이 섞이지 않음
(WORK / "edit_media").mkdir(exist_ok=True)
shutil.copy(MS / "bgm.mp3", WORK / "edit_media" / "same.mp3")
shutil.copy(MS / "hum.wav", WORK / "edit_media" / "same.wav")
w1, w2 = _ed.waveform("same.mp3", 50, "assets"), _ed.waveform("same.wav", 50, "assets")
check("[38] bgm.mp3 / bgm.wav 처럼 이름만 같은 파일은 따로 (파형·미리보기 파일이 섞이지 않음)", _ed._cache_dir("same.mp3", "assets") != _ed._cache_dir("same.wav", "assets")
      and _ed._cache_dir("Logo.png", "assets") != _ed._cache_dir("logo.jpg", "assets") and len(w1["peaks"]) != len(w2["peaks"]), f"{len(w1['peaks'])} vs {len(w2['peaks'])}")

# R7 · 휴대폰 세로 사진 (EXIF 회전) → 세워서 저장, 크기도 세로
im = Image.new("RGB", (1600, 1200), (255, 0, 0))
im.paste((0, 0, 255), (800, 0, 1600, 1200))
ex = im.getexif()
ex[0x0112] = 6
im.save(MS / "phone_exif.jpg", exif=ex.tobytes())
data = (MS / "phone_exif.jpg").read_bytes()
req = urllib.request.Request(API + "/api/edit/upload?name=" + quote("폰사진.jpg"), data=data, method="POST", headers={"Content-Type": "application/octet-stream"})
up = json.loads(urllib.request.urlopen(req).read())
saved = Image.open(WORK / "edit_media" / up["file"])
check("[39] 휴대폰 세로 사진(EXIF 회전): 세로 1200×1600 으로 세워서 저장", (up["w"], up["h"]) == (1200, 1600) and saved.size == (1200, 1600) and saved.getexif().get(0x0112, 1) == 1,
      f"{up['w']}x{up['h']} {saved.size}")

# R8 · 전환 길이가 클립보다 길어지면 저절로 줄어듦 (잘라낸 장면이 다시 안 보임)
A_, B_, C_ = pair(5, 9, 0), pair(20, 20.2, 4), pair(30, 34, 4.2)
sq = mkseq("trc", "전환 길이 검사", A_ + B_ + C_)
sq["trans"] = [{"id": "t1", "track": "V1", "a": A_[0]["id"], "b": B_[0]["id"], "type": "dissolve", "dur": 1.0, "align": "center"}]
r8 = _ed.trans_range(sq, sq["trans"][0])
st8 = _ed.track_state(sq, "V1", 4.3, _ed.valid_trans(sq))
check("[1] 클립을 줄이면 전환도 줄어듦: 0.2초 클립 → 전환 0.4초 · 4.3초엔 다음 클립", abs(r8[0] - 3.8) < 1e-6 and abs(r8[1] - 4.2) < 1e-6 and st8[0] == "item" and st8[1]["in"] == 30,
      f"{r8} {st8[0]}")

# R9 · 가운데 맞춘 디졸브·검정 전환은 빠른 xfade 로 한 구간
A_, B_ = pair(5, 9, 0), pair(20, 24, 4)
sq = mkseq("xf", "xfade", A_ + B_)
sq["trans"] = [{"id": "t1", "track": "V1", "a": A_[0]["id"], "b": B_[0]["id"], "type": "dissolve", "dur": 1.0, "align": "center"}]
media = {"main": {"id": "main", "kind": "video", "src": "videos", "file": NAME, "dur": 60, "w": 1280, "h": 720, "fps": 30, "audio": True}}
segs = _ed._segments(sq, 0, 8, 30, sq["trans"], media)
tmpd = Path(tempfile.mkdtemp())
_ed._build_segment(sq, media, 1920, 1080, 30, *([x for x in segs if x[0] == 105] or [segs[0]])[0], sq["trans"], tmpd, 0)
fc_d = (tmpd / "fc0.txt").read_text(encoding="utf-8")
sq["trans"][0]["type"] = "black"
segs_b = _ed._segments(sq, 0, 8, 30, sq["trans"], media)
_ed._build_segment(sq, media, 1920, 1080, 30, *[s for s in segs_b if s[0] == 105][0], sq["trans"], tmpd, 1)
fc_b = (tmpd / "fc1.txt").read_text(encoding="utf-8")
s13 = be_export(NAME, json.loads(json.dumps(sq)), {"preset": "small", "hw": False, "xml": False, "srt": False})  # fc 글자만으론 ffmpeg 7 의 1/0 프레임 수 실패를 못 잡음 → 실제로 내보냄
d13 = float(ffprobe(OUT / s13["result"][0])["format"]["duration"]) if s13.get("result") else 0.0
check("[13] 가운데 디졸브 = 한 구간 xfade (픽셀마다 계산하는 blend 안 씀) · 검정 전환도 xfade · 검정 전환을 실제로 내보내면 8초", (105, 135) in segs and "xfade" in fc_d and "blend=" not in fc_d and "xfade" in fc_b and "blend=" not in fc_b
      and abs(d13 - 8.0) < 0.05, f"{segs} · {d13:.2f}초 {(s13.get('error') or '')[-120:]}")

# R10 · 고정 확대(줌 컷)는 겹치기 없이 잘라내기 · 회전 없으면 RGBA 변환 없음 · 사진은 1초에 한 번만 읽음
Z = pair(5, 9, 0)
Z[0]["fx"] = {"scale": {"v": 125, "k": []}}
sq = mkseq("zm", "zoom", Z)
_ed._build_segment(sq, media, 1920, 1080, 30, 0, 60, [], tmpd, 2)
fc_z = (tmpd / "fc2.txt").read_text(encoding="utf-8")
Z[0]["fx"] = {"scale": {"v": 100, "k": [{"t": 5, "v": 100, "e": "lin"}, {"t": 9, "v": 140, "e": "lin"}]}}
_ed._build_segment(sq, media, 1920, 1080, 30, 0, 60, [], tmpd, 3)
fc_za = (tmpd / "fc3.txt").read_text(encoding="utf-8")
logo_md = {"id": "lg", "kind": "image", "src": "assets", "file": "logo.png", "w": 400, "h": 300, "dur": 0}
shutil.copy(MS / "logo.png", WORK / "edit_media" / "logo.png")
L = {"id": "L", "track": "V2", "media": "lg", "start": 0, "in": 0, "out": 2, "speed": 1, "rev": False, "link": None, "fit": "auto", "fx": {}, "color": {}}
sq = mkseq("im", "img", pair(5, 7, 0) + [L])
args, _f, _n = _ed._build_segment(sq, {**media, "lg": logo_md}, 1920, 1080, 30, 0, 60, [], tmpd, 4)
fc_i = (tmpd / "fc4.txt").read_text(encoding="utf-8")
img_line = [ln for ln in fc_i.split(";\n") if ln.startswith("[1:v]")][0]
check("[46·59] 고정 확대 125% = 키우고 잘라내기만 (겹치기·RGBA 없음) · 움직이는 확대도 RGBA 없음", "crop=1920:1080" in fc_z and "overlay" not in fc_z and "rgba" not in fc_z and "rgba" not in fc_za, fc_z[:160])
check("[44·85] 사진: 1초에 한 장만 읽고, 크기 맞추기는 늘리기 전에 한 번", "-framerate" in args and args[args.index("-framerate") + 1] == "1" and img_line.index("scale=") < img_line.index("fps=30"), img_line[:160])

# R11 · 거꾸로 재생 구간 길이 (메모리 상한) · 흐림 정도 · 불투명도 명령 시각 · 반 프레임 일찍 찾기
RV = pair(5, 25, 0)
for x in RV:
    x.update(speed=4.0, rev=True)
sq = mkseq("rv", "rev", RV)
seg4 = _ed._segments(sq, 0, 5, 30, [], media)
big4k = {"main": dict(media["main"], w=3840, h=2160)}
seg4k = _ed._segments(sq, 0, 5, 30, [], big4k)
check("[43·45] 거꾸로 4배속: 한 번에 원본 약 2초(1080p)·0.5초(4K) 만 모음", max(b - a for a, b in seg4) == 15 and max(b - a for a, b in seg4k) <= 4, f"{seg4[:3]} {seg4k[:3]}")
SH = pair(5, 9, 0)
sq = mkseq("bl", "blur", SH, fmt="shorts", layout={"mode": "blur"})
_ed._build_segment(sq, media, 720, 1280, 30, 0, 30, [], tmpd, 5)
fc_bl = (tmpd / "fc5.txt").read_text(encoding="utf-8")
_ed._build_segment(sq, media, 2160, 3840, 30, 0, 30, [], tmpd, 6)
fc_bl4 = (tmpd / "fc6.txt").read_text(encoding="utf-8")
check("[77] 블러 배경 흐림 정도가 화면 크기에 비례 (720p 4 · 4K 12)", "boxblur=4:3" in fc_bl and "boxblur=12:3" in fc_bl4)
OP = pair(5, 9, 0)
OP[0]["fx"] = {"opacity": {"v": 100, "k": [{"t": 5, "v": 0, "e": "lin"}, {"t": 7, "v": 100, "e": "lin"}]}}
sq = mkseq("op", "op", OP)
args, _f, _n = _ed._build_segment(sq, media, 1920, 1080, 30, 0, 60, [], tmpd, 7)
cmdf = list(tmpd.glob("op7_*.cmd"))[0].read_text().splitlines()
times = [float(x.split()[0]) for x in cmdf]
check("[78] 불투명도 명령은 프레임보다 반 프레임 앞 (반올림으로 한 프레임 늦는 일 없음)", all(abs(t - max(0, (f - 0.5) / 30)) < 1e-6 for f, t in enumerate(times)), str(times[:4]))
ss = float(args[args.index("-ss") + 1])
check("[76] 원본 찾아가기: 반 프레임 일찍 (가장 가까운 프레임 = 소리와 싱크)", abs(ss - (5 - 0.5 / 30)) < 1e-5, f"-ss {ss}")

# R12 · 활기(vibrance)는 미리보기와 같은 식 (채도 행렬에 합침)
cf = _ed._color_filters({"color": {"vib": 60}})
check("[42] 활기 = 미리보기와 같은 채도 행렬 (ffmpeg vibrance 안 씀)", len(cf) == 1 and "colorchannelmixer" in cf[0] and "vibrance" not in "".join(cf) and "rr=1.1653" in cf[0], str(cf))

# R13 · 자막(ASS) 글자 그대로: { } \\n · 노래방 줄바꿈
ass = _ed.build_ass({"captionStyle": dict(_ed.LONG_STYLE), "captions": [], "items": [], "titles": [
    {"id": "a", "text": "{꿀팁} 대공개", "start": 0, "dur": 2, "style": dict(_ed.TITLE_STYLE)},
    {"id": "b", "text": "슛 \\n 패스", "start": 0, "dur": 2, "style": dict(_ed.TITLE_STYLE)},
    {"id": "c", "text": "첫째 줄\n둘째 줄", "start": 0, "dur": 2, "style": dict(_ed.TITLE_STYLE, effect="karaoke")}], "shapes": []}, 1920, 1080)
lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
check("[41·75] 타이틀 글자 그대로: {꿀팁} 은 \\{꿀팁\\} · 역슬래시 무력화 · 노래방은 줄바꿈 \\N 유지", r"\{꿀팁\}" in lines[0] and "\\\u2060n" in lines[1] and r"\N" in lines[2] and lines[2].count(r"\kf") == 4, lines[2][-80:])

print("   (백엔드 검사: 내보내기)", flush=True)
# R14 · 같은 이름 내보내기는 덮어쓰지 않음 · 잠긴(열려 있는) 파일이어도 결과를 버리지 않음
sq = mkseq("nm1", "이름 검사", pair(5, 7, 0))
s1 = be_export(NAME, json.loads(json.dumps(sq)), {"preset": "small", "hw": False, "xml": False, "srt": False})
s2 = be_export(NAME, json.loads(json.dumps(sq)), {"preset": "small", "hw": False, "xml": False, "srt": False})
check("[79] 같은 편집본을 또 내보내면 ' (2)' 로 (덮어쓰지 않음)", (s1.get("result") or [""])[0].endswith("_이름 검사.mp4") and (s2.get("result") or [""])[0].endswith("_이름 검사 (2).mp4"),
      f"{s1.get('result')} {s2.get('result')} {s1.get('error')}")
sq = mkseq("lk1", "잠긴 파일", pair(5, 15, 0))
target = OUT / f"{STEM}_잠긴 파일.mp4"
stop = threading.Event()


def locker():  # 렌더 도중에 같은 이름 '파일'이 다른 프로그램에 잡힌 것처럼 (폴더로 막음)
    while not stop.is_set():
        if any(d.name.startswith(".render_") for d in OUT.iterdir()):
            target.mkdir(exist_ok=True)
            return
        time.sleep(0.05)


lt = threading.Thread(target=locker)
lt.start()
s3 = be_export(NAME, json.loads(json.dumps(sq)), {"preset": "small", "hw": False, "xml": False, "srt": False})
stop.set()
lt.join()
check("[10] 같은 이름 파일이 잠겨 있어도 다 만든 영상을 버리지 않고 ' (2)' 로 저장", target.is_dir() and (s3.get("result") or [""])[0] == f"{STEM}_잠긴 파일 (2).mp4" and (OUT / f"{STEM}_잠긴 파일 (2).mp4").exists(),
      f"{s3.get('result')} {s3.get('error')}")
shutil.rmtree(target, ignore_errors=True)

# R15 · I/O 구간 SRT: 음수 시각 없음 · 구간 길이 안 · 영상에 자막을 안 넣어도 SRT 는 만들어짐 · XML 도 구간만
sq = mkseq("srt1", "자막 구간", pair(0, 20, 0), captionsOn=False)
s4 = be_export(NAME, json.loads(json.dumps(sq)), {"video": False, "xml": True, "srt": True, "range": [2.0, 8.0]})
srt_name = [x for x in (s4.get("result") or []) if x.endswith(".srt")]
srt = (OUT / srt_name[0]).read_text(encoding="utf-8") if srt_name else ""
tss = re.findall(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)", srt) if srt_name else []
secs = [(int(a) * 3600 + int(b) * 60 + int(c) + int(d) / 1000, int(e) * 3600 + int(f) * 60 + int(g) + int(h) / 1000) for a, b, c, d, e, f, g, h in tss]
xmlr = [x for x in (s4.get("result") or []) if x.endswith(".xml")]
xdur = re.search(r"<duration>(\d+)</duration>", (OUT / xmlr[0]).read_text(encoding="utf-8")).group(1) if xmlr else "0"
check("[50·51·80] 구간 SRT: 0초부터 · 음수 없음 · 6초 안 · 자막을 영상에 안 넣어도 만들어짐 · XML 도 6초", secs and secs[0][0] == 0 and not re.search(r"(^|\n)-", srt) and secs[-1][1] <= 6.0 and xdur == "180",
      f"{secs[:2]} … {secs[-1:] } xml={xdur}")

# R16 · 빠진 음악 파일 → 분명한 오류 (조용히 소리 빠진 영상 X)
shutil.copy(MS / "bgm.mp3", WORK / "edit_media" / "gone.mp3")
gmd = _ed.media_entry("gone.mp3", "assets")
(WORK / "edit_media" / "gone.mp3").unlink()
M_ = {"id": "gm", "track": "A2", "media": gmd["id"], "start": 0, "in": 0, "out": 2, "speed": 1, "rev": False, "link": None, "fx": {}, "gain": 0, "fadeIn": 0, "fadeOut": 0, "mute": False}
sq = mkseq("miss", "빠진 음악", pair(5, 7, 0) + [M_], _media=[gmd])
t0 = time.time()
s5 = be_export(NAME, sq, {"preset": "small", "hw": False, "xml": False, "srt": False})
check("[48·52] 음악 파일이 없으면 렌더 전에 바로 '미디어 파일이 없어요'", "미디어 파일이 없어요" in (s5.get("error") or "") and "gone.mp3" in s5.get("error", "") and time.time() - t0 < 10, str(s5.get("error"))[:120])

# R17 · 소리 없는 짧은(2초) 사진 영상도 내보내기 성공 · 소리 크기 맞추기를 켜도 끝부분 소리가 안 잘림
shutil.copy(MS / "logo.png", WORK / "edit_media" / "still.png")
smd = _ed.media_entry("still.png", "assets")
S_ = {"id": "st", "track": "V1", "media": smd["id"], "start": 0, "in": 0, "out": 2, "speed": 1, "rev": False, "link": None, "fit": "auto", "fx": {}, "color": {}}
sq = mkseq("sil", "무음 사진", [S_], _media=[smd], master={"volume": 1, "normalize": True, "lufs": -14})
s6 = be_export(NAME, sq, {"preset": "small", "hw": False, "xml": False, "srt": False})
check("[82] 소리 없는 2초 사진 영상도 내보내기 (소리 크기 맞추기 켜짐)", not s6.get("error") and (s6.get("result") or [""])[0].endswith(".mp4"), str(s6.get("error"))[:150])
sr_ = 48000
tail_wav = MS / "tail_tone.wav"
tt_ = np.arange(int(7.95 * sr_)) / sr_
sig = (0.3 * np.sin(2 * np.pi * 1000 * tt_)).astype(np.float32)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "-", str(WORK / "edit_media" / "tail_tone.wav")], input=sig.tobytes(), check=True)
tmd = _ed.media_entry("tail_tone.wav", "assets")
T_ = {"id": "tt", "track": "A1", "media": tmd["id"], "start": 0, "in": 0, "out": 7.95, "speed": 1, "rev": False, "link": None, "fx": {}, "gain": 0, "fadeIn": 0, "fadeOut": 0, "mute": False}
sq = mkseq("tail", "끝소리", [dict(pair(5, 12.95, 0)[0])] + [T_], _media=[tmd], master={"volume": 1, "normalize": True, "lufs": -14})
s7 = be_export(NAME, sq, {"preset": "small", "hw": False, "xml": False, "srt": False})
if s7.get("result"):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(OUT / s7["result"][0]), "-ac", "1", "-ar", "48000", "-f", "f32le", "-"], capture_output=True)
    y = np.frombuffer(r.stdout, np.float32)
    nz = np.nonzero(np.abs(y) > 0.02)[0]
    last = nz[-1] / sr_ if len(nz) else 0
    pdur = float(ffprobe(OUT / s7["result"][0])["format"]["duration"])
else:
    last, pdur = 0, 0
check("[83] 소리 크기 맞추기를 켜도 끝 소리가 잘리지 않음 (7.95초 끝까지) · 영상 길이 그대로", abs(last - 7.95) < 0.03 and abs(pdur - 7.95) < 0.06, f"마지막 소리 {last:.3f}초 · 길이 {pdur}")

# R18 · 잡음 줄이기(afftdn) 를 켜도 소리가 늦지 않음
clk = np.zeros(int(4 * sr_), np.float32)
for c in (1.0, 2.0, 3.0):
    i0 = int(c * sr_)
    clk[i0:i0 + 480] = 0.8 * np.sin(2 * np.pi * 300 * np.arange(480) / sr_)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "-", str(WORK / "edit_media" / "clicks.wav")], input=clk.tobytes(), check=True)
cmd_ = _ed.media_entry("clicks.wav", "assets")


def onset(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "f32le", "-"], capture_output=True)
    y = np.frombuffer(r.stdout, np.float32)
    return [np.argmax(np.abs(y[int((c - 0.2) * sr_):int((c + 0.2) * sr_)]) > 0.1) / sr_ + c - 0.2 for c in (1.0, 2.0, 3.0)]


on = {}
for nr in (0, 2):
    C_ = {"id": "ck", "track": "A1", "media": cmd_["id"], "start": 0, "in": 0, "out": 4, "speed": 1, "rev": False, "link": None, "fx": {}, "gain": 0, "fadeIn": 0, "fadeOut": 0, "mute": False}
    sq = mkseq(f"nr{nr}", f"잡음 {nr}", [dict(pair(5, 9, 0)[0])] + [C_], _media=[cmd_], voice={"nr": nr})
    s8 = be_export(NAME, sq, {"preset": "small", "hw": False, "xml": False, "srt": False})
    on[nr] = onset(OUT / s8["result"][0]) if s8.get("result") else [9, 9, 9]
shift = max(abs(a - b) for a, b in zip(on[0], on[2]))
check("[49] 잡음 줄이기를 켜도 소리 시작이 그대로 (25ms 늦어지지 않음)", shift < 0.004, f"{on[0]} vs {on[2]} (차이 {shift * 1000:.1f}ms)")

# R19 · HDR(HLG 10비트, 아이폰) 영상: 색이 바래지 않게 (미리보기 파일과 같은 색)
HDR = "20200106_HDRCLIP001_아이폰 HDR.mp4"
if not (MS / "hdr_hlg.mp4").exists():
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3",
                    "-vf", "format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,zscale=p=bt2020:t=arib-std-b67:m=bt2020nc:r=tv,format=yuv420p10le", "-c:v", "libx265", "-x265-params",
                    "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error", "-tag:v", "hvc1", str(MS / "hdr_hlg.part.mp4")], check=True)
    os.replace(MS / "hdr_hlg.part.mp4", MS / "hdr_hlg.mp4")
shutil.copy(MS / "hdr_hlg.mp4", WORK / "videos" / HDR)
hmd = _ed.media_entry(HDR, "videos")
H_ = {"id": "hd", "track": "V1", "media": hmd["id"], "start": 0, "in": 0, "out": 3, "speed": 1, "rev": False, "link": None, "fit": "auto", "fx": {}, "color": {}, "reframe": 0.5}
sq = mkseq("hdr", "HDR 쇼츠", [H_], fmt="shorts", layout={"mode": "box", "bar": "#000000", "zoom": 1, "vpos": 0.5, "cropTop": 0, "cropBottom": 0}, _media=[hmd])
s9 = be_export(NAME, sq, {"preset": "small", "hw": False, "xml": False, "srt": False})
_ed.make_proxy(HDR, "videos", lambda *a: None)
if s9.get("result"):
    fe = frame(OUT / s9["result"][0], 1.0, 720, 1280)
    band = fe[460:820]  # 가운데 영상 띠
    fp = frame(_ed.proxy_path(HDR), 1.0, 720, 405)
    sat_e = float((band.max(axis=2) - band.min(axis=2)).mean())
    sat_p = float((fp.max(axis=2) - fp.min(axis=2)).mean())
else:
    sat_e, sat_p = 0, 1
check("[9] HDR(HLG) 영상: 내보내기 색이 바래지 않음 (미리보기 파일과 같은 채도)", hmd.get("hdr") == "hlg" and sat_e > 100 and abs(sat_e - sat_p) < 0.15 * sat_p, f"내보내기 채도 {sat_e:.0f} · 미리보기 {sat_p:.0f}")

# R20 · 그래픽카드 오류가 아닌 문제(예: 처음 보는 오류)는 바로 일반 방식 확인 → 이번 크기만 끄고, 전체 꺼버리지 않음
_ed._HW.clear()
_ed._HW["enc"] = "h264_nvenc"  # 이 PC 엔 없는 그래픽카드 인코더
sq = mkseq("hw", "가짜 그래픽카드", pair(5, 7, 0))
pj = load_seq_proj(NAME, json.loads(json.dumps(sq)))
outs = _ed.export(NAME, pj, {"preset": "small", "hw": True, "xml": False, "srt": False}, lambda m: None)
check("[52] 그래픽카드 인코더가 안 되면 일반 방식으로 · 그 크기만 기억 (그래픽카드를 아예 끄지 않음)", outs and outs[0].endswith(".mp4") and _ed._HW.get("enc") == "h264_nvenc"
      and ("h264_nvenc", 1280, 720) in _ed._HW.get("bad", set()), str(_ed._HW))
_ed._HW.clear()

# R21 · 멈추면(소리 섞는 중) 임시 파일을 바로 놓아 Windows 에서도 지워짐 · 남은 임시 폴더 없음
sq = mkseq("cx", "멈춤 검사", pair(5, 25, 0))
pj = load_seq_proj(NAME, json.loads(json.dumps(sq)))
orig_kf = _ed._kf_np
mapped = []


def kf_hook(*a, **k):  # 소리를 섞는 도중에 멈추기
    if not _ed.CANCEL.is_set():
        _ed.cancel_export()
    return orig_kf(*a, **k)


_ed._kf_np = kf_hook
orig_rm = shutil.rmtree


def rm_hook(p, *a, **k):
    try:
        maps = Path("/proc/self/maps").read_text()
        mapped.extend(ln.rsplit("/", 1)[-1] for ln in maps.splitlines() if str(p) in ln and ".f32" in ln)
    except OSError:
        pass
    return orig_rm(p, *a, **k)


_ed.shutil.rmtree = rm_hook
try:
    _ed.export(NAME, pj, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda m: None)
    err = ""
except RuntimeError as e:
    err = str(e)
finally:
    _ed._kf_np = orig_kf
    _ed.shutil.rmtree = orig_rm
left = [d.name for d in OUT.iterdir() if d.name.startswith(".render_")]
check("[47·53·54] 멈추면 소리 임시 파일(.f32)을 놓고 임시 폴더를 지움", "멈췄어요" in err and not mapped and not left, f"{err} mapped={mapped} left={left}")

# R22 · 미리보기 파일 만들기·검수도 멈추기(✕)로 멈춤
code, _r = api("/api/edit/proxy", {"src": "videos", "file": BENCH})
time.sleep(1.0)
api("/api/edit/cancel", {})
t0 = time.time()
sp_ = wait_job(60)
check("[16·73] 미리보기 파일 만들기를 멈추기(✕)로 바로 멈춤", "멈췄어요" in (sp_.get("error") or "") and time.time() - t0 < 5, f"{sp_.get('error')} {time.time() - t0:.1f}초")
shutil.copy(WORK / "videos" / BENCH, OUT / "qa_long.mp4")
code, _r = api("/api/edit/qa", {"name": NAME, "file": "qa_long.mp4", "format": "long", "master": {"normalize": True, "lufs": -14}})
time.sleep(0.8)
api("/api/edit/cancel", {})
sq_ = wait_job(60)
check("[73] 검수도 멈추기(✕)로 건너뜀", "멈췄어요" in (sq_.get("error") or ""), str(sq_.get("error"))[:80])

# R23 · 검수: 내보낼 때의 목표 소리 크기 기준 (−20 LUFS 로 맞춘 영상을 '작다'고 하지 않음)
import qa as _qa  # noqa: E402
sq = mkseq("q20", "조용한 목표", [dict(pair(5, 15, 0)[0]), dict(pair(5, 15, 0)[1])], master={"volume": 1, "normalize": True, "lufs": -20})
s10 = be_export(NAME, sq, {"preset": "small", "hw": False, "xml": False, "srt": False})
rep = _qa.check_video(OUT / s10["result"][0], "long", {"normalize": True, "lufs": -20}) if s10.get("result") else {"items": []}
titles = [i["title"] for i in rep["items"] if i["lv"] != "ok"]
if hasattr(_qa, "QUIET"):  # E6(D-045 6): 목표와 상관없이 -16 LUFS 밑이면 '소리가 작아요' (유튜브는 작은 소리를 키우지 않음) + 목표를 -14로
    check("[61·E6] 검수: 목표(-20)대로 맞췄어도 -16 LUFS 밑이면 '소리가 작아요' · 목표를 -14로 바꾸라는 안내", "소리가 작아요" in titles and any("-14로" in i["msg"] for i in rep["items"]), str([i["msg"] for i in rep["items"] if "LUFS" in i["msg"]]))
else:
    check("[61] 검수: 목표(-20 LUFS)대로 맞춘 소리를 '작아요'라고 하지 않음", "소리가 작아요" not in titles and any("목표" in i["msg"] for i in rep["items"]), str([i["msg"] for i in rep["items"] if "LUFS" in i["msg"]]))

shutil.rmtree(tmpd, ignore_errors=True)
for f in glob.glob(str(OUT / "*")):  # 아래 화면 검사의 파일 이름이 겹치지 않게
    shutil.rmtree(f, ignore_errors=True) if os.path.isdir(f) else os.remove(f)
if BACKEND_ONLY:
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n결과: {len(RESULTS) - len(fails)}/{len(RESULTS)} 통과")
    for n, _, i in fails:
        print("  실패:", n, i)
    sys.exit(1 if fails else 0)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    errs = []
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" else None)
    pg.on("dialog", lambda d: d.accept())
    pg.on("response", lambda r: errs.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
    ev = pg.evaluate
    W = pg.wait_for_timeout

    def shot(n):
        pg.screenshot(path=str(SHOTS / f"{n}.png"))

    def x_of(t):
        return ev(f"$('tracks').getBoundingClientRect().left + HD + {t} * zoom")

    def y_of(tk):
        bb = pg.locator(f'#tracks .row[data-tk="{tk}"]').bounding_box()
        return bb["y"] + bb["height"] / 2

    def drag(x0, y0, x1, y1, mods=()):
        for m in mods:
            pg.keyboard.down(m)
        pg.mouse.move(x0, y0)
        pg.mouse.down()
        pg.mouse.move(x0 + (x1 - x0) * 0.3 + 3, y0 + (y1 - y0) * 0.3, steps=3)
        pg.mouse.move(x1, y1, steps=6)
        pg.mouse.up()
        for m in mods:
            pg.keyboard.up(m)
        W(150)

    def items(track=None):
        return ev(f"S.items.filter(i => {'true' if track is None else repr(track) + ' === i.track'}).sort((a,b)=>a.start-b.start).map(i => ({{id:i.id, track:i.track, start:+i.start.toFixed(4), end:+iEnd(i).toFixed(4), in:+i.in.toFixed(4), out:+i.out.toFixed(4), speed:sp(i), link:i.link, media:i.media}}))")

    def v1(k):
        return items("V1")[k]

    def key(k):
        pg.keyboard.press(k)
        W(120)

    def rec_tab():
        """추천 탭 열기 + 접힌 '다른 자동 편집'(MSG round5 부터 접혀 있음 · 스타일 가편집·1차 가편집)도 펼침."""
        pg.click('[data-lt="rec"]')
        W(600)
        ev("(() => { const d = document.getElementById('recMore'); if (d && !d.open) d.open = true; })()")
        W(200)

    def undo():
        pg.mouse.click(800, 20)
        key("Control+z")

    def press_tool(k):
        pg.mouse.click(800, 20)
        key(k)

    # 재생 시작(Space·L) 뒤 재생 시계가 출발할 때까지 — 편집실은 옮긴 자리 그림·소리를 받아 둔 뒤 출발함(D-362 · 받아 두는 중 HOLD).
    # 그 기다림은 CPU·영상 키프레임 간격에 달려 바쁜 PC(단위 시험을 같이 돌릴 때)에서 0.3~1.5초 걸림(D-515 · D-516 계측 · v2.23.0 도 같음)
    # → 재생 속도·소리 검사는 출발한 뒤부터 재고, 출발까지 걸린 시간은 START_MAX 안인지 따로 봄(느려지면 떨어지게)
    START_MAX = 2.0

    def play_key(k, limit=3000):
        """k 를 눌러 재생 → 시계가 출발할 때까지 걸린 초 (limit ms 안에 안 나가면 None)."""
        t0 = time.time()
        key(k)
        try:
            pg.wait_for_function("playing && !HOLD && performance.now() >= clock.at", timeout=limit)
            return time.time() - t0
        except Exception:
            return None

    def start_ok(st):
        return st is not None and st <= START_MAX

    def start_txt(st):
        return f"시작까지 {'3초 넘음' if st is None else f'{st:.2f}초'} (한도 {START_MAX:.1f}초)"

    # ---------- A. 열기 · 예전 프로젝트 변환 ----------
    pg.goto(URL)
    pg.wait_for_function("document.getElementById('saved').textContent.includes('자동 저장')", timeout=60000)
    W(1200)
    seq = ev("({v: S.v, n: S.items.length, name: S.name, tracks: S.tracks.map(t=>t.id).join(','), lvl: prm(S.items.find(i=>i.track==='A1'), 'level').v, fi: S.items.find(i=>i.track==='A1').fadeIn, rf: S.items.find(i=>i.track==='V1').reframe, titles: S.titles.length})")
    check("예전 프로젝트(v1.4 클립 형식)가 v2 멀티트랙으로 변환되어 열림", seq["v"] == 2 and seq["n"] == 4 and seq["name"] == "옛 편집본", str(seq))
    check("변환 시 볼륨(0.5→-6dB)·페이드·화면 위치·타이틀 유지", approx(seq["lvl"], -6.02, 0.05) and seq["fi"] == 0.2 and seq["rf"] == 0.3 and seq["titles"] == 1, str(seq))
    check("트랙 V1~V3 · A1~A3 헤더", seq["tracks"] == "V1,V2,V3,A1,A2,A3" and pg.locator("#tracks .hd .tb").count() == 6)
    labels = pg.inner_text("#tracks")
    check("한국어 트랙 이름 표시", all(s in labels for s in ("영상 1", "대사 1", "음악 2", "자막", "타이틀")), labels[:80])
    shot("a1_open_migrated")

    # 검사용 시퀀스 (롱폼, 4컷: 0-4/4-8/8-12/12-16)
    ev("""(() => { commit('검사 시퀀스'); const q = { id: 'tseq', name: '검사 편집본', format: 'long', v: 2, captionsOn: true, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)),
        titles: [], shapes: [], layout: { mode: 'fill' }, master: { volume: 1, normalize: true }, items: itemsFromCuts([{in:5,out:9},{in:20,out:24},{in:30,out:34},{in:40,out:44}]) };
        ensureSeq(q); P.sequences.push(q); switchSeq('tseq'); })()""")
    W(500)
    ev("setZoom(40); $('tlscroll').scrollLeft = 0; renderTimeline(); snapOn = false;")
    W(300)
    check("검사 시퀀스 4컷", len(items("V1")) == 4 and len(items("A1")) == 4)

    # ---------- B. 재생 ----------
    ev("seek(1.0)")
    W(400)
    pg.mouse.click(800, 20)
    key("Space")
    W(1600)
    t_play = ev("T")
    vstate = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block').map(e => ({t: e.el.currentTime, paused: e.el.paused, rs: e.el.readyState}))")
    key("Space")
    check("재생(Space): 재생 위치가 앞으로 감", 2.0 < t_play < 3.2, f"T={t_play:.2f}")
    check("재생 중 영상 요소가 실제로 재생됨", any(not v["paused"] and v["rs"] >= 2 for v in vstate), str(vstate))
    ev("seek(0.5)")
    # L 은 멈춘 뒤 프레임 맞춤 찾기 + 0.5초 찾기가 끝난 뒤 출발 (D-515 · I-515)
    started = play_key("l")
    W(800)
    key("k")
    check("L 재생 · K 정지", start_ok(started) and ev("!playing") and ev("T") > 0.9, f"T={ev('T'):.2f} · {start_txt(started)}")
    ev("seek(3)")
    key("j")
    W(600)
    key("k")
    check("J 뒤로 재생", ev("T") < 2.8, f"T={ev('T'):.2f}")

    # ---------- C. 편집 도구 · 단축키 ----------
    for k, t in [("v", "select"), ("a", "tsel"), ("b", "ripple"), ("n", "roll"), ("r", "rate"), ("c", "razor"), ("y", "slip"), ("u", "slide"), ("h", "hand"), ("z", "zoom")]:
        press_tool(k)
        if ev("tool") != t:
            check(f"도구 단축키 {k}", False, ev("tool"))
            break
    else:
        check("도구 단축키 V A B N R C Y U H Z", ev(f"document.querySelector('[data-tool=zoom]').classList.contains('on')"))
    press_tool("v")
    yv1, ya1, yv2, ya2 = y_of("V1"), y_of("A1"), y_of("V2"), y_of("A2")

    # 연결 선택
    pg.mouse.click(x_of(1.0), yv1)
    W(150)
    check("클릭하면 영상+소리가 함께 선택됨 (연결 선택)", ev("selI.size") == 2)
    pg.keyboard.down("Alt")
    pg.mouse.click(x_of(1.0), yv1)
    pg.keyboard.up("Alt")
    W(150)
    check("Alt+클릭은 한쪽만 선택", ev("selI.size") == 1)
    shot("c1_selected")

    # 자르기 도구
    press_tool("c")
    pg.mouse.click(x_of(2.0), yv1)
    W(150)
    check("자르기 도구(C): 영상·소리 같이 잘림", len(items("V1")) == 5 and len(items("A1")) == 5, str([(i["start"], i["end"]) for i in items("V1")]))
    undo()
    check("Ctrl+Z 되돌리기", len(items("V1")) == 4)
    key("Control+Shift+z")
    check("Ctrl+Shift+Z 다시 실행", len(items("V1")) == 5)
    undo()
    press_tool("v")
    ev("seek(6)")
    key("Control+k")
    check("Ctrl+K: 재생 위치에서 대상 트랙 자르기", len(items("V1")) == 5 and len(items("A1")) == 5)
    undo()
    ev("selI.clear(); seek(6); renderTimeline()")
    key("Control+Shift+k")
    check("Ctrl+Shift+K: 모든 트랙 자르기", len(items("V1")) == 5)
    undo()

    # 옮기기 (덮어쓰기)
    c3 = v1(2)
    drag(x_of(9.0), yv1, x_of(10.0), yv1)
    its = items("V1")
    check("끌어서 옮기기 + 덮어쓰기 (뒤 클립 앞부분이 잘림)", approx(its[2]["start"], 9) and approx(its[3]["start"], 13) and approx(its[3]["in"], 41), str([(i["start"], i["in"]) for i in its]))
    a1 = items("A1")
    check("연결된 소리도 같이 옮겨짐", approx(a1[2]["start"], 9))
    undo()
    # 자석
    ev("snapOn = true; $('snapBtn').classList.add('on'); seek(10.0)")
    pg.mouse.move(x_of(9.0), yv1)
    pg.mouse.down()
    pg.mouse.move(x_of(9.5), yv1, steps=3)
    pg.mouse.move(x_of(11.1), yv1, steps=5)
    W(100)
    snapvis = ev("$('snapline').style.display")
    shot("c2_snap")
    pg.mouse.up()
    W(150)
    check("자석: 재생 위치(10초)에 착 붙음 + 표시선", approx(v1(2)["start"], 10.0, 0.001) and snapvis == "block", f"start={v1(2)['start']} line={snapvis}")
    undo()
    ev("snapOn = false")
    # Alt+끌기 복제 → V2
    drag(x_of(1.0), yv1, x_of(21.0), yv2, mods=("Alt",))
    v2 = items("V2")
    check("Alt+끌기: 복제 (V2·A2로 함께)", len(v2) == 1 and len(items("A2")) == 1 and len(items("V1")) == 4 and approx(v2[0]["start"], 20), str(v2))
    undo()
    # Ctrl+끌기 끼워 넣기
    drag(x_of(13.0), yv1, x_of(5.0), yv1, mods=("Control",))
    its = items("V1")
    check("Ctrl+끌기: 끼워 넣기 (뒤 클립이 밀림)", [round(i["in"]) for i in its] == [5, 40, 20, 30] and approx(its[-1]["end"], 16), str([(i["start"], i["in"]) for i in its]))
    undo()
    # 끝 다듬기
    drag(x_of(3.95), yv1, x_of(2.95), yv1)
    its = items("V1")
    check("끝 끌어 다듬기 (빈자리 남음)", approx(its[0]["end"], 3.0, 0.04) and approx(its[1]["start"], 4.0), str([(i["start"], i["end"]) for i in its[:2]]))
    drag(x_of(2.95), yv1, x_of(5.5), yv1)
    check("다듬기는 옆 클립을 넘지 못함", approx(v1(0)["end"], 4.0, 0.04), str(v1(0)))
    undo()
    undo()
    # 잔물결 편집 (B)
    press_tool("b")
    drag(x_of(3.95), yv1, x_of(2.95), yv1)
    its = items("V1")
    check("잔물결 편집(B): 뒤 클립이 따라옴", approx(its[0]["end"], 3.0, 0.04) and approx(its[1]["start"], 3.0, 0.04) and approx(its[3]["end"], 15.0, 0.04), str([(i["start"], i["end"]) for i in its]))
    undo()
    # 롤링 편집 (N)
    press_tool("n")
    drag(x_of(3.95), yv1, x_of(4.98), yv1)
    its = items("V1")
    check("롤링 편집(N): 편집점만 이동, 전체 길이 그대로", approx(its[0]["end"], 5.0, 0.04) and approx(its[1]["start"], 5.0, 0.04) and approx(its[1]["in"], 21.0, 0.04) and approx(its[3]["end"], 16.0), str([(i["start"], i["in"], i["end"]) for i in its]))
    undo()
    # 속도 조정 (R)
    press_tool("r")
    drag(x_of(15.95), yv1, x_of(19.98), yv1)
    c4 = v1(3)
    check("속도 조정(R): 끝을 끌면 속도가 바뀜 (4초→8초 = 50%)", approx(c4["speed"], 0.5, 0.02) and approx(c4["end"], 20.0, 0.05), str(c4))
    check("속도 조정 시 연결된 소리도 같은 속도", approx(items("A1")[3]["speed"], c4["speed"], 0.001))
    undo()
    # 밀어넣기 (Y)
    press_tool("y")
    drag(x_of(6.0), yv1, x_of(7.0), yv1)
    c2 = v1(1)
    check("밀어넣기(Y): 자리 그대로, 안의 장면만 이동", approx(c2["start"], 4.0) and approx(c2["in"], 19.0, 0.04) and approx(c2["out"], 23.0, 0.04), str(c2))
    undo()
    # 밀기 (U)
    press_tool("u")
    drag(x_of(6.0), yv1, x_of(7.0), yv1)
    its = items("V1")
    check("밀기(U): 클립이 옮겨지고 양옆 길이가 맞춰짐", approx(its[1]["start"], 5.0, 0.04) and approx(its[0]["end"], 5.0, 0.04) and approx(its[2]["start"], 9.0, 0.04) and approx(its[2]["in"], 31.0, 0.04), str([(i["start"], i["in"], i["end"]) for i in its]))
    undo()
    # 앞으로 트랙 선택 (A)
    press_tool("a")
    pg.mouse.click(x_of(7.5), yv1)
    W(150)
    selected = ev("[...selI].map(id => byId(id)).filter(Boolean).map(i => i.start).sort((a,b)=>a-b)")
    check("앞으로 트랙 선택(A): 누른 곳 뒤 클립 모두 선택", selected == [8, 8, 12, 12], str(selected))
    press_tool("v")
    # 지우기 / 잔물결 삭제 / 빈 곳 지우기
    pg.mouse.click(x_of(6.0), yv1)
    key("Delete")
    its = items("V1")
    check("Delete: 지우고 빈자리 남김", len(its) == 3 and approx(its[1]["start"], 8.0))
    pg.mouse.click(x_of(6.0), yv1)
    W(100)
    gap = ev("selX && selX.k")
    shot("c3_gap")
    key("Delete")
    its = items("V1")
    check("빈 곳 클릭 → Delete: 빈 곳을 당김", gap == "gap" and approx(its[1]["start"], 4.0) and approx(its[-1]["end"], 12.0), f"gap={gap} {[(i['start'], i['end']) for i in its]}")
    undo()
    undo()
    pg.mouse.click(x_of(6.0), yv1)
    key("Shift+Delete")
    its = items("V1")
    check("Shift+Delete: 잔물결 삭제", len(its) == 3 and approx(its[1]["start"], 4.0) and len(items("A1")) == 3)
    undo()
    pg.mouse.click(x_of(6.0), yv1)
    key("Alt+Backspace")
    check("Alt+Backspace: 잔물결 삭제", len(items("V1")) == 3 and approx(items("V1")[1]["start"], 4.0))
    undo()
    # Q / W
    ev("selI.clear(); seek(10); renderTimeline()")
    key("q")
    its = items("V1")
    check("Q: 재생 위치까지 앞부분 잘라내고 당김", approx(its[2]["start"], 8.0) and approx(its[2]["in"], 32.0, 0.04) and approx(its[-1]["end"], 14.0, 0.04), str([(i["start"], i["in"]) for i in its]))
    undo()
    ev("seek(10)")
    key("w")
    its = items("V1")
    check("W: 재생 위치부터 뒷부분 잘라내고 당김", approx(its[2]["out"], 32.0, 0.04) and approx(its[3]["start"], 10.0, 0.04), str([(i["start"], i["out"]) for i in its]))
    undo()
    # I/O 들어내기 · 잘라내기
    ev("seek(2)")
    key("i")
    ev("seek(6)")
    key("o")
    shot("c4_inout")
    key(";")
    its = items("V1")
    check("; 들어내기: 구간 비우고 빈자리 남김", approx(its[0]["end"], 2.0) and approx(its[1]["start"], 6.0) and approx(its[-1]["end"], 16.0), str([(i["start"], i["end"]) for i in its]))
    undo()
    ev("markIn = 2; markOut = 6; renderTimeline()")
    key("'")
    check("' 잘라내기: 구간 빼고 뒤를 당김", approx(items("V1")[-1]["end"], 12.0), str(items("V1")[-1]))
    undo()
    # 조금 옮기기 · 이동
    pg.mouse.click(x_of(13), yv1)
    key("Alt+ArrowRight")
    check("Alt+→: 선택 클립 1프레임 옮기기", approx(v1(3)["start"], 12 + 1 / 30, 0.002) and approx(items("A1")[3]["start"], 12 + 1 / 30, 0.002))
    undo()
    ev("selI.clear(); seek(1)")
    key("Shift+ArrowRight")
    check("Shift+→: 5프레임 이동", approx(ev("T"), 1 + 5 / 30, 0.002))
    key("ArrowRight")
    check("→: 1프레임 이동", approx(ev("T"), 1 + 6 / 30, 0.002))
    ev("seek(5)")
    key("ArrowDown")
    t_dn = ev("T")
    key("ArrowUp")
    check("↓/↑: 다음·이전 편집점", approx(t_dn, 8.0) and approx(ev("T"), 4.0), f"{t_dn} {ev('T')}")
    key("End")
    t_end = ev("T")
    key("Home")
    check("Home / End", approx(t_end, 16.0) and ev("T") == 0)
    z0 = ev("zoom")
    key("=")
    z1 = ev("zoom")
    key("-")
    key("\\")
    check("= / − / \\ 타임라인 확대·축소·전체", z1 > z0 * 1.3 and ev("zoom") < z1, f"{z0} {z1} {ev('zoom')}")
    ev("setZoom(40); $('tlscroll').scrollLeft = 0; renderTimeline()")
    # 복사 · 붙여넣기 · 연결
    pg.mouse.click(x_of(1.0), yv1)
    key("Control+c")
    ev("seek(16)")
    key("Control+v")
    check("Ctrl+C / Ctrl+V: 재생 위치에 붙여넣기 (영상+소리)", len(items("V1")) == 5 and approx(items("V1")[-1]["start"], 16) and len(items("A1")) == 5)
    undo()
    pg.mouse.click(x_of(1.0), yv1)
    key("Control+l")
    check("Ctrl+L: 연결 해제", v1(0)["link"] is None and items("A1")[0]["link"] is None)
    undo()
    # 트랙 헤더
    pg.click('[data-lock="V1"]')
    W(100)
    drag(x_of(9.0), yv1, x_of(10.0), yv1)
    check("잠금: 잠긴 트랙 클립은 안 움직임", approx(v1(2)["start"], 8.0) and ev("TRK('V1').lock"))
    pg.click('[data-lock="V1"]')
    pg.click('[data-hide="V1"]')
    W(100)
    ev("seek(1)")
    W(250)
    hid = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block').length")
    check("눈(숨기기): V1 숨기면 화면에서 빠짐", ev("TRK('V1').hide") and hid == 0, f"visible layers={hid}")
    pg.click('[data-hide="V1"]')
    pg.click('#tracks [data-mute="A1"]')
    pg.click('#tracks [data-solo="A2"]')
    W(100)
    check("M(음소거) · S(솔로) 버튼", ev("TRK('A1').mute && TRK('A2').solo"))
    pg.click('#tracks [data-mute="A1"]')
    pg.click('#tracks [data-solo="A2"]')
    pg.click('[data-ht="V1"]')
    W(100)
    check("트랙 높이 바꾸기", "tall" in pg.get_attribute('#tracks .row[data-tk="V1"]', "class"))
    pg.click('[data-ht="V1"]')
    pg.click('[data-tgt="A1"]')
    W(50)
    tg = ev("TRK('A1').target")
    pg.click('[data-tgt="A1"]')
    check("대상 트랙 켜기/끄기", tg is False and ev("TRK('A1').target") is True)
    key("s")
    s1 = ev("snapOn")
    key("s")
    check("S: 자석 켜기/끄기", s1 is True and ev("snapOn") is False)
    ev("snapOn = false")
    yv1, ya1, yv2, ya2 = y_of("V1"), y_of("A1"), y_of("V2"), y_of("A2")

    # 기록 패널
    pg.click('[data-rt="hist"]')
    W(200)
    nrow = pg.locator("#rt-hist .hrow").count()
    shot("c5_history")
    n_before = len(items())
    pg.locator('#rt-hist .hrow[data-h="1"]').click()
    W(300)
    back_n = ev("S.id")
    pg.locator("#rt-hist .hrow[data-h]").last.click()
    W(300)
    check("기록 패널: 단계 목록 + 눌러서 이동", nrow > 5 and len(items()) == n_before, f"rows={nrow} seq@1={back_n}")

    # ---------- D. 미디어 가져오기 ----------
    pg.click('[data-bt="media"]')
    W(500)
    pg.set_input_files("#fileIn", [str(D / "media_src" / "bgm.mp3"), str(D / "media_src" / "logo.png")])
    pg.wait_for_function("P.media.length >= 3", timeout=20000)
    W(500)
    kinds = ev("P.media.map(m => m.kind).sort().join(',')")
    check("가져오기: 음악·이미지 업로드", kinds == "audio,image,video", kinds)
    shot("d1_media")
    ev("seek(0)")
    bgm_add = pg.locator('#bt-media [data-add^="assets|bgm"]').first
    bgm_add.click()
    W(400)
    a2 = items("A2")
    if ev("typeof musicFit === 'function'"):  # E8(feat_e2b): 영상보다 긴 음악은 영상 끝에 맞춰 자르고 2초 줄임 → 확인 뒤 '음악 길이 그대로'로 (아래 검사는 30초 음악 그대로 씀)
        vend = ev("videoEnd()")
        check("+ 버튼: 음악이 A2(음악 트랙)에 들어감 · 영상 끝에 맞춤(2초 페이드)", len(a2) == 1 and approx(a2[0]["start"], 0) and approx(a2[0]["end"], vend, 0.05)
              and ev("S.items.find(i => i.track === 'A2').fadeOut") == 2 and vend < 30, f"{a2} 영상 끝 {vend}")
        ev("(() => { const a = S.items.find(i => i.track === 'A2'); commit('음악 길이'); musicFit(a, 'full'); changed(); })()")
        a2 = items("A2")
    else:
        check("+ 버튼: 음악이 A2(음악 트랙)에 들어감", len(a2) == 1 and approx(a2[0]["start"], 0) and approx(a2[0]["end"], 30, 0.1), str(a2))
    # 배경음악이 깔린 채로 잔물결 삭제 → 음악은 그대로, 영상·대사만 당겨짐
    pg.mouse.click(x_of(6.0), yv1)
    key("Shift+Delete")
    its, bg = items("V1"), items("A2")
    check("배경음악이 걸쳐 있어도 잔물결 삭제 (음악은 끊기지 않음)", len(its) == 3 and approx(its[1]["start"], 4.0) and len(bg) == 1 and approx(bg[0]["end"], 30, 0.1), str([(i["start"], i["end"]) for i in its]) + " bgm " + str([(i["start"], i["end"]) for i in bg]))
    undo()
    logo_row = pg.locator('#bt-media .item[data-name^="assets|logo"]').first
    lb = logo_row.bounding_box()
    drag(lb["x"] + 60, lb["y"] + lb["height"] / 2, x_of(1.0), yv2)
    W(300)
    v2 = items("V2")
    check("끌어다 놓기: 사진을 V2 1초 지점에 (5초)", len(v2) == 1 and approx(v2[0]["start"], 1.0, 0.05) and approx(v2[0]["end"], 6.0, 0.05), str(v2))
    logo_id = v2[0]["id"] if v2 else None
    ev("seek(9)")
    pg.locator(f'#bt-media [data-add="videos|{RED}"]').first.click()
    pg.wait_for_function("S.items.some(i => i.track === 'V2' && MED[i.media] && MED[i.media].file.includes('REDBROLL'))", timeout=15000)
    W(300)
    red = [i for i in items("V2") if i["start"] >= 8.9]
    check("보관함 다른 영상을 빈 위 트랙(V2)에 넣기 + 소리는 A2/A3", len(red) == 1 and approx(red[0]["start"], 9.0) and any(i["media"] == red[0]["media"] for i in items("A2") + items("A3")), str(red))
    shot("d2_tracks_filled")

    # 탐색기에서 파일 끌어다 놓기 (V3 의 2초 지점)
    ev(f"""(async () => {{
      const blob = await (await fetch('/api/edit/file?src=assets&f=logo.png')).blob();
      const dt = new DataTransfer(); dt.items.add(new File([blob], 'drop_test.png', {{ type: 'image/png' }}));
      const x = $('tracks').getBoundingClientRect().left + HD + 2 * zoom, r = document.querySelector('#tracks .row[data-tk=V3]').getBoundingClientRect();
      window.dispatchEvent(new DragEvent('drop', {{ dataTransfer: dt, clientX: x, clientY: r.top + r.height / 2, bubbles: true, cancelable: true }}));
    }})()""")
    pg.wait_for_function("S.items.some(i => MED[i.media] && MED[i.media].file === 'drop_test.png')", timeout=15000)
    dropped = [i for i in items("V3")]
    check("탐색기에서 파일을 타임라인에 끌어다 놓기 (V3 · 2초)", len(dropped) == 1 and approx(dropped[0]["start"], 2.0, 0.05), str(dropped))
    undo()

    # 미리보기 파일(프록시)
    pg.click('[data-bt="media"]')
    W(300)
    rid = ev("P.media.find(m => m.file.includes('REDBROLL')).id")
    pg.locator(f'#bt-media [data-proxy="{rid}"]').click()
    pg.wait_for_function(f"MED['{rid}'].proxy === true", timeout=60000)
    pg.wait_for_function("document.getElementById('bt-media').innerText.includes('가벼움')", timeout=10000)
    prox = glob.glob(str(WORK / "analysis" / "*REDBROLL*" / "proxy.mp4"))
    pinfo = ffprobe(prox[0])["streams"] if prox else []
    check("미리보기 파일(프록시) 만들기: 540p H.264 · 미리보기 주소 전환", prox and any(s.get("height") == 540 and s.get("codec_name") == "h264" for s in pinfo)
          and "proxy=1" in ev(f"mediaUrl(MED['{rid}'])") and "가벼움" in pg.inner_text("#bt-media"), str(pinfo)[:120])
    ev(f"MED['{rid}'].proxy = false; for (const e of [...POOL.values()]) if (e.mid === '{rid}') killEntry(e);")  # 테스트용 Chromium 은 H.264 재생 불가 → 원본(VP9)으로 계속

    # 소스 모니터: 두 번 클릭 → I/O → 덮어쓰기
    pg.locator(f'#bt-media .item[data-name="videos|{RED}"]').dblclick()
    pg.wait_for_function("monMode === 'src' && SRC && SRC.el.readyState >= 1", timeout=15000)
    ev("srcSeek(2.0)")
    W(300)
    key("i")
    ev("srcSeek(4.0)")
    W(300)
    key("o")
    shot("d3_source_monitor")
    srcio = ev("[SRC.in, SRC.out]")
    ev("seek(20.0)")
    pg.click("#sOver")
    W(300)
    newv = [i for i in items() if i["start"] == 20.0 and i["track"][0] == "V"]
    check("소스 모니터: 미리 보고 I·O 로 고른 부분만 덮어쓰기", srcio == [2, 4] and len(newv) == 1 and approx(newv[0]["in"], 2.0) and approx(newv[0]["out"], 4.0), f"{srcio} {newv}")
    key("Escape")
    check("Esc 로 프로그램 모니터로", ev("monMode") == "prog")
    undo()
    # 정지 화면 (프레임 고정)
    ev("selI.clear(); seek(6.0); renderTimeline()")
    ev("freezeFrame()")
    pg.wait_for_function("S.items.some(i => MED[i.media] && MED[i.media].file.startsWith('정지_'))", timeout=15000)
    W(300)
    fz = [i for i in items("V1") if approx(i["start"], 6.0) and i["out"] == 2]
    check("정지 화면 넣기: 2초 사진이 끼워지고 뒤가 밀림", len(fz) == 1 and approx(items("V1")[-1]["end"], 18.0), str([(i["start"], i["end"]) for i in items("V1")]))
    undo()
    # 트랙 추가 · 빈 트랙 지우기
    pg.click("#addTrk")
    pg.locator("#cmenu button", has_text="영상 트랙 추가").click()
    W(200)
    has_v4 = ev("S.tracks.some(t => t.id === 'V4')") and pg.locator('#tracks .row[data-tk="V4"]').count() == 1
    pg.click("#addTrk")
    pg.locator("#cmenu button", has_text="빈 트랙 지우기").click()
    W(200)
    check("트랙 추가(V4) · 뒤쪽 빈 트랙 지우기", has_v4 and not ev("S.tracks.some(t => t.id === 'V4')"))
    # 마커 이동
    ev("S.markers = []; seek(3); renderTimeline()")
    key("m")
    ev("seek(9)")
    key("m")
    ev("seek(0)")
    key("Shift+m")
    t1 = ev("T")
    key("Shift+m")
    t2 = ev("T")
    key("Control+Shift+m")
    check("마커(M) · 다음 마커(Shift+M) · 이전 마커(Ctrl+Shift+M)", approx(t1, 3) and approx(t2, 9) and approx(ev("T"), 3), f"{t1} {t2} {ev('T')}")
    ev("selI.clear(); selX = null; renderTimeline(); renderPanels()")
    yv1, ya1, yv2, ya2 = y_of("V1"), y_of("A1"), y_of("V2"), y_of("A2")

    # ---------- E. 효과 컨트롤 · 키프레임 ----------
    pg.click('[data-rt="fx"]')
    ev(f"seek(1.0)")
    pg.mouse.click(x_of(1.5), yv2)
    W(200)
    check("사진 클립 선택 → 효과 컨트롤 표시", pg.locator('#rt-fx [data-sw="scale"]').count() == 1)
    ev("seek(1.0)")
    pg.click('#rt-fx [data-sw="scale"]')
    W(150)
    ev("seek(5.0)")
    W(150)
    pg.fill('#rt-fx .num[data-k="scale"]', "200")
    pg.dispatch_event('#rt-fx .num[data-k="scale"]', "change")
    W(200)
    kf = ev(f"prm(byId('{logo_id}'), 'scale').k.map(k => [k.t, k.v])")
    check("스톱워치 → 다른 시간에 값 입력 = 키프레임 2개", len(kf) == 2 and kf[0][1] == 100 and kf[1][1] == 200, str(kf))

    def fg_scale(t):
        ev(f"seek({t})")
        W(250)
        return ev(f"(() => {{ const e = [...POOL.values()].find(e => e.it && e.it.id === '{logo_id}'); const m = (e.fg.style.transform.match(/scale\\(([\\d.]+)\\)/) || [])[1]; return +m; }})()")

    s_a, s_m, s_b = fg_scale(1.0), fg_scale(3.0), fg_scale(5.0)
    check("미리보기가 키프레임을 따라감 (100%→150%→200%)", approx(s_a, 1.0) and approx(s_m, 1.5) and approx(s_b, 2.0), f"{s_a} {s_m} {s_b}")
    shot("e1_keyframes")
    kd = pg.locator('#rt-fx .kl[data-kl="scale"] .kd').first
    kd.click(button="right")
    W(150)
    pg.locator("#cmenu button", has_text="부드럽게").click()
    W(200)
    s_q = fg_scale(2.0)
    check("키프레임 보간 '부드럽게' (ease) 적용", ev(f"prm(byId('{logo_id}'), 'scale').k[0].e") == "ease" and approx(s_q, 1.15625, 0.01), f"scale@2s={s_q}")
    ev("seek(1.0)")
    pg.click('#rt-fx [data-sw="opacity"]')
    ev("seek(4.0)")
    W(150)
    pg.fill('#rt-fx .num[data-k="opacity"]', "30")
    pg.dispatch_event('#rt-fx .num[data-k="opacity"]', "change")
    W(150)
    op = ev(f"(() => {{ seek(2.5); renderFrame(); const e = [...POOL.values()].find(e => e.it && e.it.id === '{logo_id}'); return +e.fg.style.opacity; }})()")
    check("불투명도 키프레임 (100→30, 중간 65%)", approx(op, 0.65, 0.01), str(op))
    ev("seek(2.0)")
    W(200)
    pg.fill('#rt-fx .num[data-k="rot"]', "15")
    pg.dispatch_event('#rt-fx .num[data-k="rot"]', "change")
    W(150)
    check("회전 값 입력", ev(f"prm(byId('{logo_id}'), 'rot').v") == 15)
    # 화면에서 끌어 위치 옮기기
    ev("seek(2.0)")
    W(250)
    mb = pg.locator("#mbox").bounding_box()
    p0 = ev(f"prm(byId('{logo_id}'), 'pos').v.slice()")
    drag(mb["x"] + mb["width"] / 2, mb["y"] + mb["height"] / 2, mb["x"] + mb["width"] / 2 + 80, mb["y"] + mb["height"] / 2 + 20)
    p1 = ev(f"prm(byId('{logo_id}'), 'pos').v")
    check("미리보기에서 흰 상자 끌어 위치 옮기기", p1[0] > p0[0] + 0.03 and p1[1] > p0[1], f"{p0} → {p1}")
    shot("e2_motion_box")
    # 소리 레벨 키프레임 (음악)
    pg.mouse.click(x_of(2.0), ya2)
    W(200)
    ev("seek(0.5)")
    pg.click('#rt-fx [data-sw="level"]')
    ev("seek(6)")
    W(150)
    pg.fill('#rt-fx .num[data-k="level"]', "-12")
    pg.dispatch_event('#rt-fx .num[data-k="level"]', "change")
    W(200)
    lv = ev("(() => { const a = S.items.find(i => i.track === 'A2' && i.start === 0); return [prm(a,'level').k.length, +val(a,'level',3.25).toFixed(2)]; })()")
    check("볼륨(레벨) 키프레임 (0 → -12dB, 중간 -6dB)", lv[0] == 2 and approx(lv[1], -6.0, 0.05), str(lv))
    shot("e3_audio_kf")

    # 소리 클립 볼륨 선 끌기 (A1 마지막 클립, 키프레임 없음)
    a_last = items("A1")[-1]
    ev("seek(0)")
    W(150)
    ab = pg.locator(f'#tracks .clip[data-id="{a_last["id"]}"]').bounding_box()
    by = ab["y"] + ab["height"] * (1 - 40 / 52)
    drag(ab["x"] + ab["width"] / 2, by, ab["x"] + ab["width"] / 2, by + ab["height"] * 12 / 52)
    lv2 = ev(f"prm(byId('{a_last['id']}'), 'level').v")
    check("소리 클립의 노란 볼륨 선을 아래로 끌면 볼륨이 줄어듦", -14 < lv2 < -9, f"{lv2} dB")
    pg.keyboard.down("Control")
    pg.mouse.click(ab["x"] + ab["width"] * 0.3, ab["y"] + ab["height"] * (1 - (lv2 + 40) / 52))
    pg.keyboard.up("Control")
    W(150)
    check("Ctrl+클릭: 볼륨 선에 키프레임 추가", ev(f"prm(byId('{a_last['id']}'), 'level').k.length") == 1)
    undo()
    undo()
    # 마퀴(끌어서 여러 개 선택)
    ev("selI.clear(); renderTimeline()")
    drag(x_of(4.5), y_of("V3") - 10, x_of(9.0), ya1)
    picked = sorted(set(ev("[...selI].map(id => byId(id).track + '@' + Math.round(byId(id).start))")))
    check("빈 곳에서 끌어 사각형으로 여러 클립 선택", "V1@4" in picked and "A1@8" in picked and "V1@0" not in picked, str(picked))
    # 손 도구 · 확대 도구
    ev("setZoom(80)")
    press_tool("h")
    sl0 = ev("$('tlscroll').scrollLeft")
    drag(x_of(6), yv1, x_of(6) - 200, yv1)
    sl1 = ev("$('tlscroll').scrollLeft")
    press_tool("z")
    z0 = ev("zoom")
    pg.mouse.click(x_of(4), yv1)
    W(150)
    z1 = ev("zoom")
    pg.keyboard.down("Alt")
    pg.mouse.click(x_of(4), yv1)
    pg.keyboard.up("Alt")
    W(150)
    check("손 도구(H)로 둘러보기 · 확대 도구(Z) 확대/Alt 축소", sl1 > sl0 + 150 and z1 > z0 * 1.4 and ev("zoom") < z1, f"scroll {sl0}->{sl1} zoom {z0}->{z1}->{ev('zoom')}")
    press_tool("v")
    ev("setZoom(40); $('tlscroll').scrollLeft = 0; renderTimeline()")
    W(200)
    yv1, ya1, yv2, ya2 = y_of("V1"), y_of("A1"), y_of("V2"), y_of("A2")

    # ---------- F. 장면 전환 ----------
    pg.click('[data-lt="fxl"]')
    W(300)
    row = pg.locator('#lt-fxl .fxi[data-name="dissolve"]')
    rb = row.bounding_box()
    drag(rb["x"] + 40, rb["y"] + rb["height"] / 2, x_of(4.3), yv1)
    W(300)
    tr = ev("S.trans.map(t => ({type: t.type, a: !!t.a, b: !!t.b, track: t.track, dur: t.dur}))")
    check("효과 패널에서 '교차 디졸브'를 편집점에 끌어 놓기", len(tr) == 1 and tr[0]["type"] == "dissolve" and tr[0]["a"] and tr[0]["b"] and tr[0]["track"] == "V1", str(tr))
    ev("seek(4.0)")
    W(400)
    lay = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block' && e.it.track === 'V1').map(e => +e.lay.style.opacity)")
    check("디졸브 중간: 두 장면이 겹쳐 보임 (위 장면 50%)", len(lay) == 2 and any(approx(o, 0.5, 0.05) for o in lay), str(lay))
    shot("f1_dissolve")
    pg.select_option("#trType", "wipe")
    W(200)
    ev("seek(4.0)")
    W(300)
    clip = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block' && e.it.track === 'V1').map(e => e.lay.style.clipPath)")
    check("전환 종류 바꾸기 → 닦아내기 미리보기", any("inset" in c and "50%" in c for c in clip), str(clip))
    pg.select_option("#trType", "push")
    ev("seek(4.0)")
    W(300)
    tx = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block' && e.it.track === 'V1').map(e => e.lay.style.transform)")
    check("밀어내기 미리보기 (양쪽이 50%씩 이동)", sorted(tx) == ["translateX(-50%)", "translateX(50%)"], str(tx))
    pg.fill("#trDur", "0.5")
    pg.dispatch_event("#trDur", "change")
    W(150)
    check("전환 길이 조절", approx(ev("S.trans[0].dur"), 0.5, 0.034), str(ev("S.trans[0].dur")))
    pg.select_option("#trType", "dissolve")
    ev("selI.clear(); selX = null; seek(8.0); renderTimeline()")
    key("Control+d")
    key("Control+Shift+d")
    tr = ev("S.trans.map(t => t.track + ':' + t.type).sort()")
    check("Ctrl+D 영상 전환 · Ctrl+Shift+D 소리 전환(부드럽게 섞기)", tr == ["A1:cpower", "V1:dissolve", "V1:dissolve"], str(tr))
    pg.click('[data-lt="fxl"]')
    shot("f2_transitions")

    # ---------- G. 속도 · 길이 ----------
    pg.mouse.click(x_of(13.0), yv1)
    key("Control+r")
    W(200)
    pg.fill("#spVal", "200")
    pg.click("#spGo")
    W(300)
    its = items("V1")
    check("Ctrl+R 속도 200% → 길이 절반, 뒤 클립 당김", approx(its[3]["speed"], 2.0) and approx(its[3]["end"], 14.0, 0.04), str(its[3]))
    check("속도 바꾸면 연결된 소리도 2배 (음높이 유지는 내보낼 때 atempo)", approx(items("A1")[3]["speed"], 2.0))
    shot("g1_speed")

    # ---------- H. 색보정 ----------
    pg.mouse.click(x_of(10.0), yv1)
    pg.click('[data-rt="color"]')
    W(200)
    pg.fill("#col_sat", "0")
    pg.dispatch_event("#col_sat", "change")
    W(200)
    ev("seek(10.0)")
    W(300)
    flt = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block' && e.it.track === 'V1').map(e => e.el.style.filter)")
    check("색보정: 채도 0 → 미리보기에 색 필터 적용", any("#cf_" in f for f in flt), str(flt))
    shot("h1_color")

    # ---------- I. 오디오 믹서 ----------
    pg.click('[data-rt="audio"]')
    W(200)
    pg.fill('[data-fader="A2"]', "-6")
    pg.dispatch_event('[data-fader="A2"]', "change")
    check("믹서 페이더: A2 -6dB", ev("TRK('A2').vol") == -6)
    ev("seek(0.2)")
    pg.mouse.click(800, 20)
    # 말(자막 자리 - 0.2초)은 1.3초부터 — 출발한 뒤 1.3초 재생하면 T≈1.5 (예전처럼 Space 뒤 1.5초를 재면 출발이 0.45초 넘게 늦을 때 말에 못 닿아 duckG=1.00 · D-516)
    started = play_key("Space")
    W(1300)
    meter = ev("[parseFloat($('mL').style.height)||0, parseFloat(document.querySelector('[data-mt=master]').style.height)||0, parseFloat(document.querySelector('[data-mt=A2]').style.height)||0]")
    duck = ev("duckG")
    shot("i1_mixer_playing")
    key("Space")
    check("재생 중 소리 막대(미터) 움직임 (전체·A2 음악)", meter[0] > 5 and meter[1] > 5 and meter[2] > 5, str(meter) + " (테스트용 Chromium은 AAC 소리를 못 읽어 A1 대사는 조용함)")
    check("말할 때 배경음악 자동 줄이기 (미리보기)", start_ok(started) and duck < 0.5, f"duckG={duck:.2f} · {start_txt(started)}")

    # 영상과 같은 구간의 소리는 영상 재생기 소리를 그대로 (입·소리 싱크)
    red_a = ev("S.items.find(i => !isV(i) && MED[i.media] && MED[i.media].file.includes('REDBROLL')).id")
    ev("seek(9.3)")
    W(300)
    pg.mouse.click(800, 20)
    # 새 자리(빨간 화면 클립)를 받아 둔 뒤 출발 — 출발한 뒤 1초 재생하고 잼 (예전처럼 Space 뒤 1.2초를 재면 출발이 늦을 때 A3 level=0.000 · D-516)
    started = play_key("Space")
    W(1000)
    lv_a3 = ev("lvl(BUS['A3'] && BUS['A3'].an)")
    sep = ev(f"[...POOL.values()].some(e => e.audio && e.it.id === '{red_a}')")
    muted = ev(f"[...POOL.values()].filter(e => e.lay && e.md.file.includes('REDBROLL')).map(e => e.el.muted)")
    key("Space")
    check("링크된 소리는 영상 재생기에서 바로 (따로 오디오 재생기 없음 · A3 소리 나옴)", start_ok(started) and lv_a3 > 0.01 and not sep and muted == [False], f"A3 level={lv_a3:.3f} separate={sep} muted={muted} · {start_txt(started)}")

    # ---------- J. 그래픽 프리셋 ----------
    pg.click('[data-lt="gfx"]')
    W(200)
    ev("seek(2)")
    for n in ("하단 이름 자막", "구독 버튼", "강조 팝 텍스트"):
        pg.locator(f'#lt-gfx .fxi[data-name="{n}"]').dblclick()
        W(150)
    ev("seek(2.5)")
    W(300)
    ov = pg.locator("#ov .txt").all_inner_texts()
    check("그래픽 프리셋 (로워 서드 · 구독 버튼 · 강조 팝)", ev("S.titles.length") == 3 and ev("S.shapes.length") == 2 and any("구독" in t for t in ov), str(ov))
    shot("j1_graphics")
    # 모니터에서 타이틀 끌어 옮기기
    tid = ev("S.titles.find(t => t.text === '구독').id")
    gx = x_of(ev(f"S.titles.find(t => t.id === '{tid}').start") + 0.2)
    ev(f"selX = {{k: 'title', id: '{tid}'}}; selI.clear(); seek(2.5); renderTimeline(); renderPanels()")
    W(300)
    gb = pg.locator("#gbox").bounding_box()
    sx0 = ev(f"[S.titles.find(t => t.id === '{tid}').style.x, S.titles.find(t => t.id === '{tid}').style.y]")
    drag(gb["x"] + gb["width"] / 2, gb["y"] + gb["height"] / 2, gb["x"] + gb["width"] / 2 - 60, gb["y"] + gb["height"] / 2 - 40)
    sx1 = ev(f"[S.titles.find(t => t.id === '{tid}').style.x, S.titles.find(t => t.id === '{tid}').style.y]")
    check("미리보기에서 타이틀을 끌어 옮기기", gb is not None and sx1[0] < sx0[0] - 0.03 and sx1[1] < sx0[1] - 0.03, f"{sx0} → {sx1}")
    pg.click('[data-rt="seq"]')
    W(200)
    shot("j2_seq_tab")

    # 자동 백업에서 되돌리기
    pg.click('[data-rt="hist"]')
    pg.wait_for_function("document.querySelectorAll('#bkList [data-bk]').length >= 1", timeout=10000)
    nseq_before = ev("P.sequences.length")
    pg.locator("#bkList [data-bk]").last.click()
    W(800)
    restored = ev("P.sequences.length")
    shot("j3_backup")
    key("Control+z")
    W(300)
    check("기록 탭: 자동 백업 열기 + Ctrl+Z 로 다시 지금 상태", restored >= 1 and ev("P.sequences.length") == nseq_before and ev("S.id") is not None, f"{nseq_before} → {restored} → {ev('P.sequences.length')}")
    ev("switchSeq('tseq')")
    W(300)

    # ---------- K. 스타일로 자동 편집 ----------
    rec_tab()
    check("스타일 목록 표시", pg.locator("#lt-rec [data-sty]").count() == 1 and "빠른컷" in pg.inner_text("#lt-rec"))
    pg.click('#styKind button[data-v="both"]')
    n0 = ev("P.sequences.length")
    pg.click("#styGo")
    pg.wait_for_function(f"P.sequences.length > {n0}", timeout=20000)
    W(500)
    st = ev("""P.sequences.filter(q => q.name.includes('빠른컷 스타일 가편집')).map(q => ({name: q.name, fmt: q.format, zoom: q.items.filter(i => i.fx && i.fx.scale && i.fx.scale.v > 120).length,
               y: q.captionStyle.y, fill: q.captionStyle.fill, lufs: q.master.lufs}))""")
    check("스타일로 새 편집본 (롱폼+쇼츠, 기존 편집본은 그대로)", len(st) >= 2 and st[0]["name"] == "빠른컷 스타일 가편집" and any(q["fmt"] == "shorts" for q in st) and ev("P.sequences.some(q => q.id === 'tseq')"), str([q["name"] for q in st]))
    want_lufs = _ed.auto_lufs(-12) if hasattr(_ed, "auto_lufs") else -12  # E6(D-045 6): 가편집 소리 목표는 유튜브 기준 범위(editor.AUTO_LUFS = -14~-13) 안으로
    check(f"스타일 적용: 줌 컷 125% · 자막 가운데(0.55)·노랑 · {want_lufs:g} LUFS", all(q["zoom"] > 0 and q["y"] == 0.55 and q["fill"] == "#FFE14D" and q["lufs"] == want_lufs for q in st), str(st))
    shot("k1_style")
    # 확대한 첫 클립 안으로 (예전엔 9.0초 고정 — E6 부터 확대 자리가 잘라 낸 자리·자막 경계를 따라 바뀜)
    ev("seek((() => { const it = S.items.filter(i => i.track === 'V1' && i.fx && i.fx.scale && i.fx.scale.v > 120).sort((a, b) => a.start - b.start)[0]; return it ? it.start + 0.3 : 9.0; })())")
    W(400)
    zoomed = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block').map(e => e.fg.style.transform)")
    check("스타일 편집본 미리보기에 줌 컷이 보임", any("scale(1.25" in z for z in zoomed), str(zoomed))

    # ---------- L. 자동 저장 ----------
    W(1200)
    saved = json.loads((WORK / "projects" / (NAME.rsplit(".", 1)[0] + ".json")).read_text(encoding="utf-8"))
    check("자동 저장 (디스크 파일에 v2 · 편집본 저장)", saved.get("v") == 2 and len(saved["sequences"]) == ev("P.sequences.length"), f"{len(saved['sequences'])}")
    check("자동 백업 폴더", len(glob.glob(str(WORK / "projects" / "backup" / "*.json"))) >= 1)

    # ---------- M. 내보내기 ----------
    QA = []

    def run_export(preset="youtube", rng=False, cancel=False, timeout=600, after_go=None):
        pg.click("#exportBtn")
        W(300)
        if rng:
            pg.click('#exRange button[data-v="io"]')
        pg.click(f'#exPresets [data-ep="{preset}"]')
        shot("m0_export_modal")
        pg.click("#exGo")
        if after_go:
            W(150)
            after_go()
        t0 = time.time()
        seen_pct, seen_eta = set(), False
        pg.wait_for_function("document.getElementById('busy').classList.contains('show')", timeout=10000)
        toast_end = ""
        while time.time() - t0 < timeout:
            W(400)
            if not ev("$('busy').classList.contains('show')"):
                if not toast_end:
                    toast_end = pg.inner_text("#toast")
                W(1000)  # 내보낸 뒤 자동 검수가 이어서 시작될 수 있음
                if not ev("$('busy').classList.contains('show') || !!qaRunning"):
                    break
                continue
            if ev("!!qaRunning"):
                continue
            txt = pg.inner_text("#busyPct")
            if txt:
                seen_pct.add(txt)
            if "남음" in pg.inner_text("#busyTxt"):
                seen_eta = True
                if len(seen_pct) == 3:
                    shot("m1_progress")
            if cancel and len(seen_pct) >= 2:
                pg.click("#busyStop")
                cancel = False
        W(800)
        QA.clear()
        if ev("$('qaModal').classList.contains('show')"):
            QA.extend(pg.locator("#qaBody .qi").all_inner_texts())
            pg.click("#qaClose")
            W(200)
        return seen_pct, seen_eta, toast_end or pg.inner_text("#toast"), time.time() - t0

    if not NO_EXPORT:
        # 검증용 시퀀스: 디졸브(원본→빨강) · 2배속 · 흑백 · 밀어내기 · 키프레임 로고 · 배경음악 덕킹
        ev("""(() => {
          const red = P.media.find(m => m.file.includes('REDBROLL')), logo = P.media.find(m => m.kind === 'image'), bgm = P.media.find(m => m.kind === 'audio');
          commit('내보내기 검사');
          const A = newPair(5, 8, 0), C = newPair(30, 34, 6), Dd = newPair(40, 42, 8);
          C[0].speed = C[1].speed = 2; Dd[0].color = { sat: 0 };
          const R1 = { ...newPair(0, 3, 3, red.id)[0], link: null }, R2 = { ...newPair(2, 4, 10, red.id)[0], link: null };
          const L = { id: 'logoX', track: 'V2', media: logo.id, start: 12, in: 0, out: 4, speed: 1, rev: false, link: null, fit: 'auto', reframe: .5, color: {},
            fx: { scale: { v: 100, k: [{ t: 0, v: 50, e: 'lin' }, { t: 4, v: 150, e: 'lin' }] }, opacity: { v: 100, k: [{ t: 0, v: 100, e: 'lin' }, { t: 4, v: 30, e: 'lin' }] },
                  rot: { v: 0, k: [{ t: 0, v: 0, e: 'lin' }, { t: 4, v: 90, e: 'lin' }] }, pos: { v: [.5, .5], k: [{ t: 0, v: [.3, .5], e: 'lin' }, { t: 4, v: [.7, .5], e: 'lin' }] } } };
          const M = { id: 'bgmX', track: 'A2', media: bgm.id, start: 0, in: 0, out: 16, speed: 1, rev: false, link: null, fx: {}, gain: 0, fadeIn: 0, fadeOut: 0, mute: false };
          const q = { id: 'xseq', name: '내보내기 검사', format: 'long', v: 2, captionsOn: false, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)), titles: [], shapes: [],
            layout: { mode: 'fill' }, master: { volume: 1, normalize: false, lufs: -14 }, duck: { on: true, amount: -14 },
            items: [...A, R1, ...C, ...Dd, R2, L, M],
            trans: [{ id: 'x1', track: 'V1', a: A[0].id, b: R1.id, type: 'dissolve', dur: 1, align: 'center' }, { id: 'x2', track: 'V1', a: Dd[0].id, b: R2.id, type: 'push', dur: 1, align: 'center' }] };
          ensureSeq(q); P.sequences.push(q); switchSeq('xseq'); })()""")
        W(800)
        pct, eta, toast, took = run_export()
        out = WORK / "out" / f"{NAME.rsplit('.', 1)[0]}_내보내기 검사.mp4"
        check("내보내기: 진행률 %와 남은 시간 표시", len(pct) >= 2 and eta, f"{sorted(pct)[:6]} eta={eta}")
        check("내보내기 완료 알림", "내보냈어요" in toast, toast)
        print(f"   (16초 영상 내보내기 {took:.0f}초)")
        if out.exists():
            info = ffprobe(out)
            vs = [s for s in info["streams"] if s["codec_type"] == "video"][0]
            au = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
            dur = float(info["format"]["duration"])
            check("ffprobe: 16초 · H.264 1920×1080 30fps · AAC 48k 스테레오", approx(dur, 16.0, 0.08) and vs["codec_name"] == "h264" and vs["width"] == 1920 and vs["height"] == 1080
                  and vs["r_frame_rate"] == "30/1" and au["codec_name"] == "aac" and au["sample_rate"] == "48000" and au["channels"] == 2, f"{dur} {vs} {au}")
            src = WORK / "videos" / NAME
            fa = frame(src, 8.0, 1920, 1080)
            mid = frame(out, 3.0, 1920, 1080)
            redpx = np.array([254, 0, 0], np.float32)
            e_mix, e_a = np.abs(mid - (fa + redpx) / 2)[200:900, 300:1600].mean(), np.abs(mid - fa)[200:900, 300:1600].mean()
            check("디졸브 중간 프레임 = 두 장면 평균", e_mix < 4 and e_a > 30, f"|out-평균|={e_mix:.1f} |out-A|={e_a:.1f}")
            sp_ = frame(out, 7.0, 1920, 1080)
            e32, e31 = np.abs(sp_ - frame(src, 32.0, 1920, 1080))[200:900].mean(), np.abs(sp_ - frame(src, 31.0, 1920, 1080))[200:900].mean()
            check("2배속: 7초 지점 = 원본 32초 장면", e32 < 3 and e31 > e32 * 2, f"{e32:.1f} vs {e31:.1f}")
            g = frame(out, 9.0, 1920, 1080)
            check("색보정(채도 0) = 흑백", np.abs(g[..., 0] - g[..., 2]).mean() < 1.5, f"{np.abs(g[..., 0] - g[..., 2]).mean():.2f}")
            pu = frame(out, 10.0, 1920, 1080)
            check("밀어내기 중간: 오른쪽 절반 빨강, 왼쪽은 흑백 장면", pu[540, 1500][0] > 200 and pu[540, 1500][1] < 40 and abs(pu[540, 300][0] - pu[540, 300][2]) < 6, f"{pu[540, 1500]} {pu[540, 300]}")
            boxes = []
            for tt in (12.05, 14.0, 15.9):
                f = frame(out, tt, 1920, 1080)
                blue = (f[..., 2] > 40) & (f[..., 0] < 30) & (f[..., 1] < 30)
                ys, xs = np.nonzero(blue)
                boxes.append((int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()), float(f[..., 2].max())))
            w0, w1, h2 = boxes[0][1] - boxes[0][0], boxes[1][1] - boxes[1][0], boxes[2][3] - boxes[2][2]
            check("키프레임 로고: 크기 50%→100%→150%·회전·왼→오 이동·흐려짐", 190 < w0 < 240 and 400 < w1 < 450 and 560 < h2 < 640 and boxes[0][0] < boxes[1][0] < boxes[2][0]
                  and boxes[0][4] > boxes[1][4] > boxes[2][4], str(boxes))
            ev("seek(14.0)")
            W(500)
            pv = ev("""(() => { const f = $('frame').getBoundingClientRect(), e = [...POOL.values()].find(e => e.it && e.it.id === 'logoX'), r = e.el.getBoundingClientRect(), k = 1920 / f.width;
                       return [(r.left - f.left) * k, (r.right - f.left) * k, (r.top - f.top) * k, (r.bottom - f.top) * k]; })()""")
            ex = boxes[1]
            check("미리보기와 내보낸 영상이 같은 자리·크기 (14초 로고, 오차 12px 이내)", all(abs(a - b) < 12 for a, b in zip(pv, ex[:4])), f"미리보기 {[round(v) for v in pv]} / 내보내기 {list(ex[:4])}")
            # E2(feat_e2b): 받아쓴 말(자막 원본 6.5초~ = 타임라인 1.5초~) 자리만 줄임 → 그 안에서 잼 (예전 규칙은 소리 크기 · 0.5~2.5초)
            sp_lo = 1.8 if ev("typeof speechSpans === 'function'") else 0.5
            m_sp, m_no = tone(out, sp_lo, 2.9 if sp_lo > 1 else 2.5, 880), tone(out, 4.0, 5.5, 880)
            check("말할 때 배경음악 자동 줄이기 (약 -14dB)", 10 < m_no - m_sp < 18, f"말할 때 {m_sp:.1f}dB / 조용할 때 {m_no:.1f}dB")
            xmlp = WORK / "out" / f"{NAME.rsplit('.', 1)[0]}_내보내기 검사_premiere.xml"
            srtp = WORK / "out" / f"{NAME.rsplit('.', 1)[0]}_내보내기 검사.srt"
            root = ET.parse(xmlp).getroot()
            vtr = root.findall(".//video/track")
            check("프리미어 XML: 영상 트랙 3개 · 클립 · 2배속 정보", len(vtr) == 3 and len(root.findall(".//clipitem")) >= 10 and "<value>200.00</value>" in xmlp.read_text(encoding="utf-8"), f"tracks={len(vtr)}")
            check("SRT 파일 생성", srtp.exists())
        else:
            check("내보내기 파일 생성", False, str(out))
        # 목소리 보정: 웅웅거림(40Hz) 줄이기 → 내보낸 소리에서 40Hz 가 확 줄어야 함
        pg.set_input_files("#fileIn", [str(D / "media_src" / "hum.wav")])
        pg.wait_for_function("P.media.some(m => m.file.startsWith('hum'))", timeout=20000)
        ev("""(() => { const hm = P.media.find(m => m.file.startsWith('hum'));
          const q = { id: 'vseq', name: '목소리 보정 검사', format: 'long', v: 2, captionsOn: false, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)), titles: [], shapes: [],
            layout: { mode: 'fill' }, master: { volume: 1, normalize: false }, duck: { on: false, amount: -14 },
            items: [{ id: 'humA', track: 'A1', media: hm.id, start: 0, in: 0, out: 6, speed: 1, rev: false, link: null, fx: {}, gain: 0, fadeIn: 0, fadeOut: 0, mute: false }] };
          ensureSeq(q); P.sequences.push(q); switchSeq('vseq'); })()""")
        W(500)
        pg.click('[data-rt="audio"]')
        W(300)
        pg.click("#voHp")
        W(200)
        check("목소리 보정 켜기 (오디오 탭)", ev("S.voice && S.voice.hp === true"))
        run_export(preset="small")
        vout = WORK / "out" / f"{NAME.rsplit('.', 1)[0]}_목소리 보정 검사.mp4"
        hum_in = tone(D / "media_src" / "hum.wav", 1, 5, 40) - tone(D / "media_src" / "hum.wav", 1, 5, 1000)
        hum_out = tone(vout, 1, 5, 40) - tone(vout, 1, 5, 1000) if vout.exists() else 0
        check("목소리 보정: 저음 웅웅거림(40Hz) 이 크게 줄어듦", hum_in - hum_out > 10, f"40Hz-1kHz 차이: 원본 {hum_in:.1f}dB → 내보내기 {hum_out:.1f}dB")
        ev("switchSeq('xseq')")
        W(500)
        # 구간만 내보내기
        ev("markIn = 2; markOut = 6; renderTimeline()")
        pct, eta, toast, took = run_export(rng=True)
        outr = (glob.glob(str(WORK / "out" / f"{NAME.rsplit('.', 1)[0]}_내보내기 검사_구간_*.mp4")) or [""])[0]
        dur = float(ffprobe(outr)["format"]["duration"]) if outr else 0
        check("표시한 구간(I~O)만 내보내기 = 4초 (파일 이름에 구간 표시)", approx(dur, 4.0, 0.08) and "_구간_0m02s-0m06s" in outr, f"{dur} {Path(outr).name if outr else ''}")
        # 멈추기
        pct, eta, toast, took = run_export(cancel=True)
        check("내보내기 멈추기(✕)", "멈췄어요" in toast, toast)
        # 스타일 쇼츠 (박스 화면 + 줌 컷) 내보내기
        sid = ev("P.sequences.find(q => q.name.includes('빠른컷') && q.format === 'shorts').id")
        ev(f"switchSeq('{sid}')")
        W(500)
        zi = ev("(() => { const z = S.items.find(i => i.track === 'V1' && i.fx && i.fx.scale && i.fx.scale.v > 120), n = S.items.find(i => i.track === 'V1' && !(i.fx && i.fx.scale)); return [z.start + 0.5, n.start + 0.5, total()]; })()")
        ev(f"selI.clear(); selX = null; seek({zi[1]}); renderTimeline()")
        W(1200)
        fbox = pg.locator("#frame").bounding_box()
        pg.screenshot(path=str(SHOTS / "m2_shorts_preview.png"), clip=fbox)
        pct, eta, toast, took = run_export()
        outs = glob.glob(str(WORK / "out" / "*빠른컷 스타일 가편집 · 쇼츠*.mp4"))
        if outs:
            info = ffprobe(outs[0])
            vs = [s for s in info["streams"] if s["codec_type"] == "video"][0]
            check("쇼츠 내보내기: 1080×1920 · 길이 일치", vs["width"] == 1080 and vs["height"] == 1920 and approx(float(info["format"]["duration"]), zi[2], 0.08), f"{vs['width']}x{vs['height']} {info['format']['duration']} vs {zi[2]:.2f}")

            def band(t):
                f = frame(outs[0], t, 1080, 1920)
                col = f[:, 30, :].max(axis=1)
                return int((col > 25).sum())

            bz, bn = band(zi[0]), band(zi[1])
            check("쇼츠 줌 컷: 확대 구간 영상 띠가 약 1.25배", 1.15 < bz / max(1, bn) < 1.35, f"줌 {bz}px / 보통 {bn}px")
            # 제목(노란 글씨) 자리·크기: 미리보기 화면 = 내보낸 영상
            from PIL import Image
            pw, ph = int(fbox["width"]), int(fbox["height"])
            prev = np.asarray(Image.open(SHOTS / "m2_shorts_preview.png").convert("RGB").resize((pw, ph)), np.float32)
            expf = frame(outs[0], zi[1], pw, ph)

            def ybox(a):
                m = (a[..., 0] > 200) & (a[..., 1] > 170) & (a[..., 2] < 110)
                m[int(ph * 0.3):] = False
                ys, xs = np.nonzero(m)
                return (int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())) if len(xs) else None

            bp, be = ybox(prev), ybox(expf)
            ok = bp and be and all(abs(a - c) <= pw * 0.04 for a, c in zip(bp, be))
            check("제목 글씨 자리·크기: 미리보기 = 내보낸 영상 (오차 4% 이내)", ok, f"미리보기 {bp} / 내보내기 {be}")
        else:
            check("쇼츠 내보내기 파일", False)


    # ---------- N. 검수(감사) 반영 회귀 검사 · 편집실 화면 ----------
    print("   (편집실 화면 회귀 검사)", flush=True)
    pg.click('[data-lt="caps"]')
    ev("if (monMode !== 'prog') setMon('prog'); document.querySelectorAll('.modal').forEach(m => m.classList.remove('show'))")

    def reset_tseq():
        ev("""(() => { switchSeq('tseq'); commit('검사 초기화'); S.items = itemsFromCuts([{in:5,out:9},{in:20,out:24},{in:30,out:34},{in:40,out:44}]); S.trans = []; S.markers = [];
          S.tracks = defaultTracks(); S.titles = []; S.shapes = []; S.format = 'long'; S.captionsOn = true; selI.clear(); selX = null; selKf = null; markIn = markOut = null; linkOn = true;
          changed(); setZoom(40); $('tlscroll').scrollLeft = 0; renderTimeline(); snapOn = false; layoutFrame(); })()""")
        W(300)

    def spans(track):
        return [(round(i["start"], 3), round(i["end"], 3)) for i in items(track)]

    def near_spans(a, b, tol=0.02):
        return len(a) == len(b) and all(abs(x[0] - y[0]) <= tol and abs(x[1] - y[1]) <= tol for x, y in zip(a, b))

    def no_overlap(track):
        its = items(track)
        return all(b["start"] >= a["end"] - 1 / 60 for a, b in zip(its, its[1:]))

    # [4] 속도 바꾸기 + 잔물결: 연결된 영상·소리를 한 번만 밀고, 겹침·빈틈 없음 · 프리셋도 소리 같이
    reset_tseq()
    pg.click('[data-rt="fx"]')
    pg.mouse.click(x_of(6.0), y_of("V1"))
    W(200)
    pg.click('#rt-fx [data-spd="2"]')
    W(250)
    s2 = (spans("V1"), spans("A1"))
    undo()
    pg.mouse.click(x_of(6.0), y_of("V1"))
    W(200)
    pg.click('#rt-fx [data-spd="0.5"]')
    W(250)
    s05 = (spans("V1"), spans("A1"))
    undo()
    ev("applyFxPreset('2배 빠르게', [S.items.filter(i => i.track === 'V1').sort((a, b) => a.start - b.start)[1]])")
    W(200)
    sp_ = (spans("V1"), spans("A1"), [round(i["speed"], 2) for i in items("A1")])
    undo()
    want2, want05 = [(0, 4), (4, 6), (6, 10), (10, 14)], [(0, 4), (4, 12), (12, 16), (16, 20)]
    check("[4] 속도 2배·0.5배 + 잔물결: 영상·소리가 한 번만 밀리고 겹침·빈틈 없음 · '2배 빠르게'는 소리도 2배",
          near_spans(s2[0], want2) and near_spans(s2[1], want2) and near_spans(s05[0], want05) and near_spans(s05[1], want05)
          and near_spans(sp_[0], want2) and near_spans(sp_[1], want2) and sp_[2][1] == 2, f"{s2} {s05} {sp_}")

    # [1] 전환 뒤 클립을 줄이면 전환도 줄어듦 (미리보기)
    reset_tseq()
    ev("seek(4); selI.clear(); renderTimeline()")
    key("Control+d")
    ev("(() => { const b = S.items.filter(i => i.track === 'V1').sort((a, b) => a.start - b.start)[1]; commit('t'); edgeOp('ripple', linkedOf(b).map(x => x.id), 'r', 4.2 - iEnd(b)); changed(false); })()")
    W(200)
    trr = ev("trRange(S.trans.find(t => t.track === 'V1'))")
    st_in = ev("(() => { const s = trackState('V1', 4.3); return s && s.item ? s.item.in : -1; })()")
    check("[1] 전환에 붙은 클립을 0.2초로 줄이면 전환도 0.4초로 · 4.3초엔 다음 클립 (잘라낸 장면이 다시 안 보임)", approx(trr[0], 3.8, 0.01) and approx(trr[1], 4.2, 0.01) and st_in == 30, f"{trr} {st_in}")

    # [2] 소스 모니터에서 사진에 끝점(O) → 화면·재생이 멈추지 않음
    reset_tseq()
    pg.click('[data-bt="media"]')
    W(300)
    ev("openSource(P.media.find(m => m.kind === 'image'))")
    W(400)
    key("o")
    W(300)
    key("Escape")
    W(200)
    ev("seek(1)")
    pg.mouse.click(800, 20)
    key("Space")
    W(900)
    key("Space")
    t_img = ev("T")
    check("[2] 소스 모니터에서 사진에 끝점(O)을 찍어도 재생·화면이 계속 움직임", t_img > 1.4 and not any("pause is not a function" in e for e in errs), f"T={t_img:.2f}")

    # [3] 왼쪽 자막 목록에서 고친 뒤 오른쪽 '자막 수정' 칸에 이어 써도 앞의 수정이 사라지지 않음
    reset_tseq()
    pg.click('[data-lt="caps"]')
    pg.click('[data-rt="fx"]')
    W(200)
    ta = pg.locator("#capList textarea").first
    cid = ta.get_attribute("data-cap")
    ta.click()
    key("End")
    pg.keyboard.type(" 왼발")
    W(150)
    pg.click("#pCapText")
    key("End")
    pg.keyboard.type("!")
    W(150)
    ctext = ev(f"P.captions.find(c => c.id === '{cid}').text")
    pg.mouse.click(800, 20)
    check("[3] 자막: 왼쪽 목록에서 고친 글자가 오른쪽 칸에서 이어 써도 남음", ctext.endswith(" 왼발!"), ctext[-20:])
    undo()

    # [27] 키프레임을 눌러 고르고 Delete → 클립이 아니라 키프레임만 지움
    reset_tseq()
    pg.click('[data-rt="fx"]')
    pg.mouse.click(x_of(5.0), y_of("V1"))
    ev("seek(4.5)")
    W(200)
    pg.click('#rt-fx [data-sw="scale"]')
    ev("seek(7)")
    W(200)
    pg.fill('#rt-fx .num[data-k="scale"]', "150")
    pg.dispatch_event('#rt-fx .num[data-k="scale"]', "change")
    W(250)
    n_it = len(items())
    pg.locator('#rt-fx .kl[data-kl="scale"] .kd').nth(1).click()
    W(150)
    sel_on = pg.locator('#rt-fx .kl[data-kl="scale"] .kd.sel').count()
    key("Delete")
    W(200)
    nk = ev("prm(S.items.filter(i => i.track === 'V1').sort((a, b) => a.start - b.start)[1], 'scale').k.length")
    check("[27] 키프레임을 고르면 흰색 표시 · Delete 는 그 키프레임만 지움 (클립은 그대로)", sel_on == 1 and len(items()) == n_it and nk == 1, f"sel={sel_on} items {n_it}->{len(items())} kf={nk}")

    # [30·31] 잠긴 트랙: 오른쪽 클릭·자르기·전환·연결된 짝 선택 모두 막힘
    reset_tseq()
    pg.click('[data-lock="V1"]')
    W(150)
    pg.mouse.click(x_of(6.0), y_of("V1"), button="right")
    W(200)
    menu_vis = ev("$('cmenu').style.display")
    ev("razorAt('V1', 6, false)")
    tr_ok = ev("addTransAt(S.items.filter(i => i.track === 'V1').sort((a, b) => a.start - b.start)[1], 'r', null, true)")
    n_v1 = len(items("V1"))
    pg.click('[data-lock="V1"]')
    pg.click('[data-lock="A1"]')
    W(150)
    pg.mouse.click(x_of(6.0), y_of("V1"))
    W(150)
    sel_tr = sorted(set(ev("[...selI].map(id => byId(id).track)")))
    pg.click('[data-lock="A1"]')
    check("[30·31] 잠긴 트랙 클립: 오른쪽 클릭 메뉴·자르기·전환 안 됨 · 잠긴 짝(A1)은 같이 안 골라짐", menu_vis != "block" and n_v1 == 4 and tr_ok is False and sel_tr == ["V1"],
          f"menu={menu_vis} v1={n_v1} trans={tr_ok} sel={sel_tr}")

    # [32] 전환을 오른쪽 클릭 → Delete 는 전환만 (먼저 골라 둔 클립이 지워지지 않음)
    reset_tseq()
    ev("seek(4); selI.clear(); renderTimeline()")
    key("Control+d")
    pg.mouse.click(x_of(13.0), y_of("V1"))
    W(150)
    tb = pg.locator("#tracks .trn").first.bounding_box()
    pg.mouse.click(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2, button="right")
    W(150)
    key("Delete")
    W(150)
    check("[32] 전환 오른쪽 클릭 뒤 Delete → 전환만 지움 (클립 4개 그대로)", ev("S.trans.length") == 0 and len(items("V1")) == 4, f"trans={ev('S.trans.length')} v1={len(items('V1'))}")

    # [21] Q·W·' 는 대상 트랙에만 → 대상이 아닌 배경음악은 끊기지 않음
    reset_tseq()
    ev("""(() => { const bgm = P.media.find(m => m.kind === 'audio'); S.items.push({ id: 'bgQ', track: 'A2', media: bgm.id, start: 0, in: 0, out: 16, speed: 1, rev: false, link: null, fx: {}, gain: 0, fadeIn: 0, fadeOut: 0, mute: false }); changed(false); seek(10); })()""")
    pg.mouse.click(800, 20)
    key("q")
    a2q, vq = spans("A2"), items("V1")[-1]["end"]
    ev("seek(5)")
    key("w")
    a2w = spans("A2")
    ev("markIn = 1; markOut = 2; renderTimeline()")
    key("'")
    a2x = spans("A2")
    check("[21] Q·W·' 를 써도 대상이 아닌 A2 배경음악은 한 덩어리 그대로 (영상·대사만 당김)", a2q == a2w == a2x == [(0, 16)] and approx(vq, 14, 0.04), f"{a2q} {a2w} {a2x} v_end={vq}")

    # [20] 빈 곳을 골라 둔 뒤 다른 편집으로 내용이 바뀌면 Delete 가 겹침을 만들지 않음
    reset_tseq()
    pg.mouse.click(x_of(6.0), y_of("V1"))
    key("Delete")
    pg.mouse.click(x_of(6.0), y_of("V1"))
    W(150)
    ev("markIn = 1; markOut = 3; renderTimeline()")
    pg.mouse.click(800, 20)
    key("'")
    key("Delete")
    check("[20] 빈 곳 선택이 남아 있어도 내용이 바뀐 뒤 Delete → 겹치는 클립 없음", no_overlap("V1") and no_overlap("A1"), str(spans("V1")))

    # [36] 프리미어 단축키: Ctrl+Shift+A 선택 해제 · Shift+I/O 시작·끝점으로 · Ctrl+Shift+I 시작점 지우기 · Shift+A 는 도구를 안 바꿈
    reset_tseq()
    pg.mouse.click(x_of(1.0), y_of("V1"))
    key("Control+Shift+a")
    nsel = ev("selI.size")
    ev("markIn = 2; markOut = 6; seek(4); renderTimeline()")
    key("Shift+i")
    ti = ev("T")
    key("Shift+o")
    to = ev("T")
    mi_keep = ev("markIn")
    key("Control+Shift+i")
    mi_gone = ev("markIn")
    tool0 = ev("tool")
    key("Shift+a")
    # Shift+A: 앞으로 트랙 선택(A)으로 바뀌지 않음 — v2.21.1 부터는 프리미어처럼 '뒤로 트랙 선택'(tselb · D-334) · 예전 판은 도구 그대로
    tool1 = ev("tool")
    check("[36] Ctrl+Shift+A 선택 해제 · Shift+I/O 이동 · Ctrl+Shift+I 지우기 · Shift+A 는 앞으로 트랙 선택이 아님 (그대로 또는 뒤로 트랙 선택)", nsel == 0 and approx(ti, 2) and approx(to, 6) and mi_keep == 2 and mi_gone is None and tool1 in (tool0, "tselb"),
          f"sel={nsel} {ti} {to} {mi_keep} {mi_gone} tool={tool1}")
    ev(f"setTool({json.dumps(tool0)})")

    # [35] 슬라이더를 만진 뒤 Home/End 는 재생 위치만 (값을 몰래 바꾸지 않음)
    reset_tseq()
    pg.mouse.click(x_of(1.0), y_of("V1"))
    pg.click('[data-rt="color"]')
    W(200)
    pg.click("#col_sat")
    W(150)
    sat0 = ev("byId(primary).color.sat")
    ev("seek(5)")
    key("Home")
    sat1 = ev("byId(primary).color.sat")
    key("End")
    sat2 = ev("byId(primary).color.sat")
    check("[35] 색보정 슬라이더를 만진 뒤 Home/End → 값은 그대로, 재생 위치만 이동", sat0 == sat1 == sat2 and approx(ev("T"), 16), f"{sat0} {sat1} {sat2} T={ev('T')}")
    pg.click('[data-rt="fx"]')

    # [25] '가편집 다시 만들기'는 자동으로 만든 가편집만 바꿈 (복제·이름 바꾼·직접 만든 편집본은 그대로)
    reset_tseq()
    keep_ids = ev("P.sequences.map(q => q.id)")  # 지금 있는 것은 모두 예전·직접 만든·스타일 편집본 (같은 판정식을 쓰지 않고 고정 목록)
    rec_tab()
    pg.click("#recRebuild")
    pg.wait_for_function("S.auto === 'rough'", timeout=15000)
    W(300)
    rough1 = [i for i in ev("P.sequences.map(q => q.id)") if i not in keep_ids]
    bt0 = ev("btab")
    pg.click('[data-bt="seqs"]')  # 새로 만든 '쇼츠 1 · …'을 복제 → 이름이 '쇼츠 1 · … 복사본' 이어도 내 것
    W(150)
    pg.click(f'[data-seq="{ev("P.sequences.find(q => /^쇼츠 1 · /.test(q.name)).id")}"]')
    W(300)
    pg.click("#seqDup")
    W(300)
    dup_id = ev("S.id")
    rec_tab()
    pg.click("#recRebuild")
    pg.wait_for_function(f"!P.sequences.some(q => q.id === {json.dumps(rough1[0])})", timeout=15000)
    W(300)
    after_ids = ev("P.sequences.map(q => q.id)")
    check("[25] 가편집 다시 만들기: 내가 만든 편집본(자동 쇼츠의 복사본 포함)은 모두 그대로 · 자동 가편집만 바뀜", len(keep_ids) >= 3 and len(rough1) >= 2 and all(i in after_ids for i in keep_ids + [dup_id])
          and not any(i in after_ids for i in rough1) and "tseq" in after_ids, f"{len(keep_ids)}+1개 유지 · 지금 {len(after_ids)}개 · 복사본 {'있음' if dup_id in after_ids else '없어짐'}")
    pg.click('[data-lt="caps"]')
    pg.click(f'[data-bt="{bt0}"]')

    # [26·29] 효과 붙여넣기·볼륨 모두 적용: 키프레임을 클립 길이에 맞춰 옮김 (애니메이션·페이드 유지)
    reset_tseq()
    ev("""(() => { const v = S.items.filter(i => i.track === 'V1').sort((a, b) => a.start - b.start); applyFxPreset('서서히 확대', [v[0]]);
      selI = new Set([v[0].id]); primary = v[0].id; copySel(true); selI = new Set([v[2].id]); pasteAttrs(); })()""")
    kv = ev("(() => { const c = S.items.filter(i => i.track === 'V1').sort((a, b) => a.start - b.start)[2]; return [val(c, 'scale', 8.0), val(c, 'scale', 11.99)]; })()")
    ev("""(() => { const a = S.items.filter(i => i.track === 'A1').sort((x, y) => x.start - y.start); const p = prm(a[0], 'level');
      p.k = [{ t: mt(a[0], 3.5), v: 0, e: 'lin' }, { t: mt(a[0], 3.99), v: -60, e: 'lin' }]; a[0].fx.level = p; selI = new Set([a[0].id]); primary = a[0].id; renderPanels(); })()""")
    W(150)
    pg.click("#pVolAll")
    W(150)
    lv = ev("(() => { const a = S.items.filter(i => i.track === 'A1').sort((x, y) => x.start - y.start); return [val(a[1], 'level', 6), val(a[2], 'level', 10), val(a[3], 'level', 15.99)]; })()")
    check("[26·29] 효과 붙여넣기: 서서히 확대가 그대로 (100%→115%) · 볼륨 모두 적용: 페이드아웃이 각 클립 끝에만", approx(kv[0], 100, 0.5) and kv[1] > 113 and approx(lv[0], 0, 0.5) and approx(lv[1], 0, 0.5) and lv[2] < -50,
          f"scale {kv} level {lv}")

    # [23·24] 정지 화면: B롤의 연결된 소리(A2)도 같이 밀림 · 720p 영상에서 만든 정지 화면도 화면 크기 그대로
    reset_tseq()
    ev("(() => { const red = P.media.find(m => m.file.includes('REDBROLL')); const [vt, at] = autoDest(red, 2, 10, false); placeMedia(red, 2, vt, at, 'over', 0, 10); })()")
    W(300)
    ev("selI.clear(); seek(5); renderTimeline()")
    ev("freezeFrame()")
    pg.wait_for_function("S.items.some(i => MED[i.media] && MED[i.media].freeze)", timeout=15000)
    W(300)
    a2f = [(round(i["start"], 2), round(i["end"], 2), round(i["in"], 2), i["link"]) for i in items("A2")]
    v2f = [(round(i["start"], 2), round(i["end"], 2), round(i["in"], 2), i["link"]) for i in items("V2")]
    fz_bw = ev("(() => { const it = S.items.find(i => MED[i.media] && MED[i.media].freeze); return fitRect(it, MED[it.media]).bw; })()")
    linked = any(a[3] and a[3] == v[3] and approx(a[0], 7) and approx(v[0], 7) for a in a2f for v in v2f)
    check("[23] 정지 화면: B롤 소리(A2)도 영상과 같이 나뉘고 2초 밀림 (싱크 유지)", linked and any(approx(a[0], 7) and approx(a[2], 3) for a in a2f), f"A2 {a2f} V2 {v2f}")
    check("[24] 720p 영상의 정지 화면도 원래 영상처럼 화면 가득 (작아지지 않음)", approx(fz_bw, 1920, 1), str(fz_bw))

    # [33] 탐색기에서 파일 여러 개를 끌어다 놓으면 차례로 이어서 (앞 파일을 덮지 않음)
    reset_tseq()
    ev("""(async () => {
      const blob = await (await fetch('/api/edit/file?src=assets&f=logo.png')).blob();
      const dt = new DataTransfer(); dt.items.add(new File([blob], '장면 A.png', { type: 'image/png' })); dt.items.add(new File([blob], '장면 B.png', { type: 'image/png' }));
      const x = $('tracks').getBoundingClientRect().left + HD + 2 * zoom, r = document.querySelector('#tracks .row[data-tk=V3]').getBoundingClientRect();
      window.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, clientX: x, clientY: r.top + r.height / 2, bubbles: true, cancelable: true }));
    })()""")
    pg.wait_for_function("S.items.filter(i => i.track === 'V3').length >= 2", timeout=20000)
    W(300)
    check("[33] 파일 두 개를 V3 2초에 끌어 놓기 → 2~7초, 7~12초 차례로", near_spans(spans("V3"), [(2, 7), (7, 12)]), str(spans("V3")))

    # [63] 끼워 넣기(Ctrl+Shift+V)를 하면 뒤의 마커도 같이 밀림
    reset_tseq()
    ev("S.markers = [{ t: 10, name: '' }]; renderTimeline()")
    pg.mouse.click(x_of(1.0), y_of("V1"))
    key("Control+c")
    ev("seek(2)")
    key("Control+Shift+v")
    check("[63] 끼워 넣으면 마커도 같이 밀림 (10초 → 14초)", approx(ev("S.markers[0].t"), 14), str(ev("S.markers[0].t")))

    # [69] 여러 클립을 위로 옮길 때 잠긴 트랙이 있으면 다 같이 멈춤 (한 트랙에 두 클립이 겹치지 않음)
    reset_tseq()
    ev("""(() => { const lg = P.media.find(m => m.kind === 'image'); S.items.push({ id: 'lg69', track: 'V2', media: lg.id, start: 0, in: 0, out: 4, speed: 1, rev: false, link: null, fit: 'auto', reframe: .5, fx: {}, color: {} });
      TRK('V3').lock = true; changed(false); })()""")
    W(150)
    pg.mouse.click(x_of(1.0), y_of("V1"))
    pg.keyboard.down("Shift")
    pg.mouse.click(x_of(1.0), y_of("V2"))
    pg.keyboard.up("Shift")
    W(100)
    drag(x_of(1.5), y_of("V1"), x_of(1.5), y_of("V2"))
    check("[69] 잠긴 V3 위로는 못 감 → V2 에 클립이 겹쳐 쌓이지 않음", no_overlap("V2") and len(items("V2")) == 1, str(spans("V2")) + " " + str(spans("V1")))
    ev("TRK('V3').lock = false; renderTimeline()")

    # [70] 효과 컨트롤의 '복제'는 바로 뒤에 끼워 넣음 (다음 클립을 덮지 않음)
    reset_tseq()
    pg.mouse.click(x_of(6.0), y_of("V1"))
    pg.click('[data-rt="fx"]')
    W(200)
    pg.click("#pDup")
    W(200)
    ins = [round(i["in"]) for i in items("V1")]
    check("[70] 복제: 바로 뒤에 끼워 넣음 → 다음 클립(원본 30초)이 지워지지 않음", ins == [5, 20, 20, 30, 40], str(ins))

    # [72] 붙여넣기는 대상 트랙으로 (V2·A2 를 대상으로 하면 V1·A1 을 덮지 않음)
    reset_tseq()
    pg.mouse.click(x_of(1.0), y_of("V1"))
    key("Control+c")
    ev("TRK('V1').target = false; TRK('A1').target = false; TRK('V2').target = true; TRK('A2').target = true; seek(9); renderTimeline()")
    key("Control+v")
    pv = (len(items("V1")), spans("V2"), spans("A2"))
    ev("TRK('V1').target = true; TRK('A1').target = true; TRK('V2').target = false; TRK('A2').target = false; renderTimeline()")
    check("[72] 대상 트랙 V2·A2 로 붙여넣기 → V1 그대로, V2·A2 9~13초", pv[0] == 4 and near_spans(pv[1], [(9, 13)]) and near_spans(pv[2], [(9, 13)]), str(pv))

    # [74] 소스 모니터가 보일 때 J·K·L 은 소스 모니터를 움직임 (숨은 프로그램이 재생되지 않음)
    pg.click('[data-bt="media"]')
    W(300)
    pg.locator(f'#bt-media .item[data-name="videos|{RED}"]').dblclick()
    pg.wait_for_function("monMode === 'src' && SRC && SRC.el.readyState >= 1", timeout=15000)
    ev("srcSeek(0)")
    W(200)
    key("l")
    W(700)
    srcst = ev("[playing, SRC.el.paused, SRC.el.currentTime]")
    key("k")
    key("End")
    t_end_src = ev("srcT()")
    key("Escape")
    check("[74] 소스 모니터에서 L: 소스가 재생되고 프로그램은 그대로 · End: 소스 끝으로", srcst[0] is False and srcst[1] is False and srcst[2] > 0.2 and t_end_src > 9, f"{srcst} end={t_end_src}")

    # [64] 이름에 ' 가 있는 파일도 썸네일이 보임
    import tempfile as _tf
    tdir = Path(_tf.mkdtemp())
    shutil.copy(D / "media_src" / "logo.png", tdir / "Messi's 드리블.png")
    pg.set_input_files("#fileIn", [str(tdir / "Messi's 드리블.png")])
    pg.wait_for_function("P.media.some(m => m.file.startsWith('Messi'))", timeout=20000)
    reset_tseq()
    ev("dropOn('media', 'assets|' + P.media.find(m => m.file.startsWith('Messi')).file, null)")
    W(600)
    bgi = ev("(() => { const it = S.items.find(i => MED[i.media] && MED[i.media].file.startsWith('Messi')); const el = document.querySelector(`#tracks .clip[data-id='${it.id}']`); return getComputedStyle(el).backgroundImage; })()")
    check("[64] 이름에 ' 가 있는 사진도 타임라인 썸네일이 보임", bgi.startswith("url("), bgi[:60])

    # [19] 계속 잘라도 연결 표시(link)가 길어지지 않음
    reset_tseq()
    ev("(() => { commit('x'); S.items = itemsFromCuts([{ in: 0, out: 40 }]); changed(false); for (let k = 1; k <= 25; k++) splitAt(k * 1.5, true); })()")
    ml = ev("Math.max(...S.items.map(i => (i.link || '').length))")
    check("[19] 25번 잘라도 연결 표시가 짧게 유지 (프로젝트 파일이 커지지 않음)", ml <= 16 and len(items("V1")) == 26 and len(items("A1")) == 26 and ev("S.items.filter(i => i.track === 'V1').every(v => S.items.some(a => a.track === 'A1' && a.link === v.link))"), f"최대 {ml}자")

    # [18] 되돌리기 기록은 최대 120단계 (끄는 동작이 많아도 메모리가 끝없이 늘지 않음)
    hl = ev("(() => { const keep = hist.slice(), r = redoS.slice(); for (let i = 0; i < 300; i++) pushHist('검사', '{}'); const n = hist.length; hist = keep; redoS = r; return n; })()")
    check("[18] 되돌리기 기록 최대 120단계", hl == 120, str(hl))

    # [58·71] 기록 패널에서 여러 단계를 한 번에 되돌려도 백업 목록을 다시 받지 않음 (한 번만 그림)
    reset_tseq()
    for _ in range(15):
        key("m")
    reqs = []
    pg.on("request", lambda r: reqs.append(r.url) if "/api/edit/backups" in r.url else None)
    pg.click('[data-rt="hist"]')
    W(600)
    n_open = len(reqs)
    t0 = time.time()
    pg.locator('#rt-hist .hrow[data-h]').nth(max(0, ev("hist.length") - 15)).click()
    W(300)
    nm = ev("S.markers.length")
    jump_n = len(reqs) - n_open
    pg.locator('#rt-hist .hrow[data-h]').last.click()
    W(300)
    check("[58·71] 기록 패널: 15단계를 한 번에 되돌림 · 백업 목록은 탭을 열 때 한 번만", nm == 0 and n_open == 1 and jump_n == 0 and ev("S.markers.length") == 15, f"마커 {nm} · 요청 {n_open}/{jump_n}")
    pg.click('[data-rt="fx"]')

    # [28] 시퀀스 탭의 자막 스타일 조절이 (효과 컨트롤에서 자막을 본 뒤에도) 잘 됨
    reset_tseq()
    ev("selX = { k: 'caption', id: P.captions[0].id }; selI.clear(); setRtab('fx'); renderPanels()")
    W(150)
    pg.click('[data-rt="seq"]')
    W(200)
    pg.fill("#rt-seq #cs_size", "120")
    pg.dispatch_event("#rt-seq #cs_size", "input")
    pg.dispatch_event("#rt-seq #cs_size", "change")
    check("[28] 시퀀스 탭 자막 크기 조절이 실제로 바뀜 (같은 이름의 숨은 칸에 묶이지 않음)", ev("S.captionStyle.size") == 120 and pg.locator("#cs_size").count() == 1, str(ev("S.captionStyle.size")))
    undo()
    pg.click('[data-rt="fx"]')

    # [15] 다른 창이 먼저 저장했으면: 덮어쓰지 않고 알려 줌 → '이 창 내용으로 저장'
    n_err15 = len(errs)
    reset_tseq()
    W(1000)
    disk = json.loads((WORK / "projects" / (NAME.rsplit(".", 1)[0] + ".json")).read_text(encoding="utf-8"))
    code, _r = api("/api/edit/save", {"name": NAME, "project": disk, "rev": ev("REV")})
    key("m")
    pg.wait_for_function("$('cfModal').classList.contains('show')", timeout=5000)
    shot("n1_conflict")
    pg.click("#cfForce")
    W(800)
    errs[n_err15:] = [e for e in errs[n_err15:] if "409" not in e]  # 일부러 일으킨 충돌 응답
    check("[15] 다른 창이 저장한 뒤 이 창에서 고치면 덮어쓰지 않고 물어봄 → '이 창 내용으로 저장' 하면 저장됨", code == 200 and not ev("conflict") and "자동 저장됨" in pg.inner_text("#saved"), pg.inner_text("#saved"))

    # [37] 저장이 안 되면 '← 스튜디오'로 나가지 않음
    ev("window._f = window.fetch; window.fetch = (u, o) => String(u).includes('/api/edit/save') ? Promise.reject(new Error('x')) : window._f(u, o); 0")
    key("m")
    pg.click("#back")
    W(1500)
    stay = "/editor" in pg.url
    ev("window.fetch = window._f; saveNow()")
    W(800)
    check("[37] 저장이 실패하면 화면을 떠나지 않고 알려 줌", stay and "저장" in pg.inner_text("#saved"), pg.url)

    # [22] 디졸브 미리보기: 다음 장면이 화면을 다 덮지 않아도 앞 장면이 내보내기처럼 서서히 사라짐
    ev("""(() => { const red = P.media.find(m => m.file.includes('REDBROLL')); commit('dz');
      const A = { ...newPair(0, 2, 0, red.id)[0], link: null }, B = newPair(20, 22, 2)[0]; B.fx = { scale: { v: 80, k: [] } };
      const q = { id: 'dzseq', name: '디졸브 검사', format: 'long', v: 2, captionsOn: false, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)), titles: [], shapes: [], layout: { mode: 'fill' },
        master: { volume: 1, normalize: false }, items: [A, B], trans: [{ id: 'dz1', track: 'V1', a: A.id, b: B.id, type: 'dissolve', dur: 1, align: 'center' }] };
      ensureSeq(q); P.sequences.push(q); switchSeq('dzseq'); seek(2.25); })()""")
    W(800)
    lays = ev("[...POOL.values()].filter(e => e.lay && e.lay.style.display === 'block').map(e => [e.it.media === 'main' ? 'B' : 'A', +e.lay.style.opacity, e.lay.style.mixBlendMode])")
    fb = pg.locator("#frame").bounding_box()
    pg.screenshot(path=str(SHOTS / "n2_dissolve.png"), clip=fb)
    corner = np.asarray(Image.open(SHOTS / "n2_dissolve.png").convert("RGB"), np.float32)[4:12, 4:12].mean(axis=(0, 1))
    check("[22] 디졸브 미리보기: 80% 크기 다음 장면이어도 앞 장면이 25%로 (내보내기와 같은 섞기)", any(l[0] == "A" and approx(l[1], 0.25, 0.02) for l in lays) and any(l[0] == "B" and l[2] == "plus-lighter" for l in lays)
          and 45 < corner[0] < 85 and corner[1] < 20, f"{lays} 모서리 {corner.round()}")

    # [65] 쇼츠 '블러 배경' 화면에 사진을 넣어도 미리보기에 흐린 배경이 보임
    ev("""(() => { const lg = P.media.find(m => m.kind === 'image' && m.file.startsWith('logo')); commit('bl');
      const q = { id: 'blseq', name: '블러 사진', format: 'shorts', v: 2, captionsOn: false, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)), titles: [], shapes: [],
        layout: { mode: 'blur', zoom: 1, vpos: .5 }, master: { volume: 1, normalize: false },
        items: [{ id: 'blimg', track: 'V1', media: lg.id, start: 0, in: 0, out: 4, speed: 1, rev: false, link: null, fit: 'auto', reframe: .5, fx: {}, color: {} }] };
      ensureSeq(q); P.sequences.push(q); switchSeq('blseq'); seek(1); })()""")
    W(900)
    bgpx = ev("(() => { const e = [...POOL.values()].find(e => e.it && e.it.id === 'blimg'); const d = e.bgc.getContext('2d').getImageData(54, 20, 1, 1).data; return [d[0], d[1], d[2], d[3]]; })()")
    check("[65] 블러 배경 + 사진: 미리보기에도 흐린 배경이 그려짐 (내보내기와 같게)", bgpx[3] > 0 and sum(bgpx[:3]) > 30, str(bgpx))

    # [66·67] 거꾸로 재생 클립도 미리보기에서 소리가 남 (내보내기처럼 거꾸로)
    ev("""(() => { const bgm = P.media.find(m => m.kind === 'audio' && m.file.startsWith('bgm')); commit('rv');
      const q = { id: 'rvseq', name: '거꾸로 소리', format: 'long', v: 2, captionsOn: false, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)), titles: [], shapes: [], layout: { mode: 'fill' },
        master: { volume: 1, normalize: false }, duck: { on: false, amount: -14 },
        items: [{ id: 'rvA', track: 'A2', media: bgm.id, start: 0, in: 2, out: 8, speed: 1, rev: true, link: null, fx: {}, gain: 0, fadeIn: 0, fadeOut: 0, mute: false }] };
      ensureSeq(q); P.sequences.push(q); switchSeq('rvseq'); seek(1); })()""")
    W(500)
    pg.mouse.click(800, 20)
    key("Space")
    W(1500)
    rvl = ev("lvl(BUS['A2'] && BUS['A2'].an)")
    rve = ev("[...POOL.values()].filter(e => e.rv).map(e => [e.el.paused, e.el.src.includes('revaudio')])")
    key("Space")
    check("[66·67] 거꾸로 재생 클립: 미리보기에서도 (뒤집은) 소리가 들림", rvl > 0.01 and rve and rve[0][1], f"A2 {rvl:.3f} {rve}")

    # [68] 목소리 크기 고르게: 미리보기 압축기를 내보내기에 맞춘 값
    check("[68] 미리보기 압축기: 무릎 3dB · 8ms · 160ms · 뒤에 보정 소리 크기", ev("BUS['A1'] ? [BUS['A1'].cp.knee.value, +BUS['A1'].cp.attack.value.toFixed(3), +BUS['A1'].cp.release.value.toFixed(2), !!BUS['A1'].mk] : null") == [3, 0.008, 0.16, True])

    # [55·56] 점프 컷이 많은 타임라인을 훑어도 재생기(video)가 쌓이지 않음
    ev("""(() => { commit('jc'); const cuts = []; for (let k = 0; k < 40; k++) cuts.push({ in: k * 1.4, out: k * 1.4 + 1 });
      const q = { id: 'jcseq', name: '점프 컷', format: 'long', v: 2, captionsOn: true, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)), titles: [], shapes: [], layout: { mode: 'fill' },
        master: { volume: 1, normalize: false }, items: itemsFromCuts(cuts) };
      ensureSeq(q); P.sequences.push(q); switchSeq('jcseq'); })()""")
    W(500)
    for k in range(40):
        ev(f"seek({k + 0.5})")
        W(25)
    W(400)
    pool_n, vids_n = ev("POOL.size"), ev("document.querySelectorAll('#layers video').length")
    check("[55·56] 40컷을 훑어도 재생기는 몇 개만 (같은 영상 재생기를 다시 씀)", pool_n <= 5 and vids_n <= 5, f"POOL {pool_n} · video {vids_n}")

    # [57] 타임라인: 바뀐 줄만 다시 그림 (그대로면 금방)
    rt = ev("(() => { renderTimeline(); const a = performance.now(); for (let i = 0; i < 5; i++) renderTimeline(); return (performance.now() - a) / 5; })()")
    rows_same = ev("(() => { const r = document.querySelector('#tracks .row[data-tk=V1]'); renderTimeline(); return r === document.querySelector('#tracks .row[data-tk=V1]'); })()")
    check("[57] 타임라인: 바뀐 것이 없으면 줄을 다시 만들지 않음 (빠름)", rows_same and rt < 25, f"{rt:.1f}ms")

    # [48] 파일이 없어진 미디어는 빨간 점선 + 알림
    n_err48 = len(errs)
    ev("""(() => { const g = { id: 'ghost', kind: 'image', src: 'assets', file: '없어진 사진.png', dur: 0, w: 400, h: 300, audio: false };
      P.media.push(g); MED.ghost = g; S.items.push({ id: 'gh1', track: 'V3', media: 'ghost', start: 0, in: 0, out: 3, speed: 1, rev: false, link: null, fit: 'auto', reframe: .5, fx: {}, color: {} });
      changed(false); loadBundle(g); })()""")
    pg.wait_for_function("MED.ghost && MED.ghost.missing", timeout=10000)
    W(200)
    check("[48] 없어진 미디어: 타임라인에 빨간 점선 + 알림", ev("document.querySelector('#tracks .clip[data-id=gh1]').classList.contains('missing')") and "찾을 수 없어요" in pg.inner_text("#toast"))
    ev("S.items = S.items.filter(i => i.id !== 'gh1'); P.media = P.media.filter(m => m.id !== 'ghost'); changed(false)")
    errs[n_err48:] = [e for e in errs[n_err48:] if "%EC%97%86%EC%96%B4%EC%A7%84" not in e and "404 (Not Found)" not in e]  # 일부러 없앤 파일

    # [11·40] 미디어 탭: 보관함 영상은 작은 그림(poster)만, 한 번에 하나씩 → 저장·상태 요청이 막히지 않음
    preqs = []
    pg.on("request", lambda r: preqs.append(r.url) if "/api/edit/thumbs.jpg?src=videos" in r.url else None)
    ev("LIB = null")
    pg.click('[data-bt="seqs"]')
    pg.click('[data-bt="media"]')
    st_ms = ev("(async () => { const a = performance.now(); await fetch('/api/state?since=999999'); return performance.now() - a; })()")
    W(2000)
    th = ev("""(() => { const box = $('bt-media').getBoundingClientRect(); return [...document.querySelectorAll('#bt-media .mthumb[data-th]')].map(m => { const r = m.getBoundingClientRect();
      return [m.dataset.th.includes('poster.jpg'), m.style.backgroundImage.startsWith('url('), r.bottom > box.top && r.top < box.bottom]; }); })()""")
    vis = [x for x in th if x[0] and x[2]]
    hidden_loaded = [x for x in th if x[0] and not x[2] and x[1]]
    check("[11·40] 미디어 탭: 보관함 영상은 빠른 한 장면 그림(poster)만 · 보이는 줄만 하나씩 · 상태 요청도 바로", vis and all(x[1] for x in vis) and not preqs and st_ms < 1500,
          f"보이는 poster {len(vis)}개 · 안 보이는 줄 미리 받은 것 {len(hidden_loaded)} · 띠 요청 {len(preqs)} · state {st_ms:.0f}ms")

    if not NO_EXPORT:
        # [34·61] 자동 검수: 내보낼 때의 형식으로 검사 (내보내는 중 쇼츠 편집본을 열어도 '쇼츠 비율' 오류 없음)
        ev("switchSeq('xseq'); markIn = 1; markOut = 3; renderTimeline()")
        W(400)
        sid = ev("P.sequences.find(q => q.format === 'shorts').id")
        pct, eta, toast, took = run_export(preset="small", rng=True, after_go=lambda: ev(f"switchSeq('{sid}')"))
        check("[34] 내보내는 중 다른(쇼츠) 편집본을 열어도 검수는 내보낸 롱폼 기준 ('쇼츠 비율' 오류 없음)", QA and not any("쇼츠 비율" in q for q in QA), " | ".join(QA)[:200])

        # [41] 타이틀 글자 { } 도 내보낸 영상에 그대로 (미리보기와 같은 폭)
        ev("""(() => { commit('ass'); const q = { id: 'assq', name: '중괄호 제목', format: 'long', v: 2, captionsOn: false, captionStyle: JSON.parse(JSON.stringify(S.captionStyle)), shapes: [], layout: { mode: 'fill' },
            master: { volume: 1, normalize: false }, items: [],
            titles: [{ id: 'tb1', text: '{꿀팁} 대공개', start: 0, dur: 3, style: { ...baseTitle(), size: 100, y: 0.5, effect: 'none', fill: '#FFE14D', stroke: '#111111', strokeW: 6 } }] };
          ensureSeq(q); P.sequences.push(q); switchSeq('assq'); selI.clear(); selX = null; seek(1.5); renderTimeline(); })()""")
        W(1000)
        fb = pg.locator("#frame").bounding_box()
        pg.screenshot(path=str(SHOTS / "n3_title_braces.png"), clip=fb)
        ev("markIn = 1; markOut = 2; renderTimeline()")
        run_export(preset="youtube", rng=True)
        bout = glob.glob(str(WORK / "out" / "*중괄호 제목_구간*.mp4"))
        pw, ph = int(fb["width"]), int(fb["height"])

        def ybox2(a):
            m = (a[..., 0] > 200) & (a[..., 1] > 170) & (a[..., 2] < 110)
            m[: int(a.shape[0] * 0.3)] = False
            m[int(a.shape[0] * 0.7):] = False
            ys, xs = np.nonzero(m)
            return (int(xs.min()), int(xs.max())) if len(xs) else (0, 0)

        bp = ybox2(np.asarray(Image.open(SHOTS / "n3_title_braces.png").convert("RGB").resize((pw, ph)), np.float32))
        be = ybox2(frame(bout[0], 0.5, pw, ph)) if bout else (0, 0)
        wp, we = bp[1] - bp[0], be[1] - be[0]
        check("[41] 제목 '{꿀팁} 대공개': 내보낸 영상에도 중괄호 글자까지 그대로 (미리보기와 폭 6% 이내)", wp > 0 and abs(we - wp) <= 0.06 * wp, f"미리보기 {wp}px / 내보내기 {we}px")

    shot("z_final")
    check("자바스크립트 오류 0개", not errs, " | ".join(errs[:5]))
    b.close()

fails = [r for r in RESULTS if not r[1]]
print(f"\n결과: {len(RESULTS) - len(fails)}/{len(RESULTS)} 통과")
for n, _, i in fails:
    print("  실패:", n, i)
sys.exit(1 if fails else 0)
