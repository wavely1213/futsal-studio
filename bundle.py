"""촬영본 묶음 — 휴대폰·카메라가 여러 파일로 나눠 찍은 촬영본을 찍은 순서대로 이어 한 영상으로.

- 순서: 파일 속 촬영 시각(creation_time, 아이폰은 creationdate) → 없으면 파일 수정 시각 → 같으면 파일 이름
- 화면 규격(코덱·크기·fps·회전)이 모두 같으면 화면은 화질 손실 없이 그대로 이어 붙임 (concat -c:v copy)
  소리는 파일마다 그 파일 길이에 딱 맞춰 다시 만듦 (그대로 붙이면 이음새마다 조금씩 늦어짐)
- 다르면 가장 많은 fps·방향으로 맞춰 H.264 고정 fps 로 다시 만든 뒤 이어 붙임
- 소리 없는 파일은 무음을 채움
- 결과: videos/묶음_YYYYMMDD_<제목>.mp4 + analysis/<이름>/bundle.json [{file, start, dur}] · 원본은 그대로 둠
ffprobe 가 없으므로 정보는 ffmpeg -i 의 출력으로 읽는다.
"""
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core

LABEL = "한 영상으로 묶는 중"
TMP_PREFIX = ".render_bundle_"  # 완성본 폴더 안 임시 폴더 (앱이 갑자기 꺼져 남으면 다음 실행 때 정리됨)
COPY_VCODECS = {"h264", "hevc", "av1", "vp9"}  # mp4 에 그대로 담을 수 있는 영상 코덱
FPS_TOL = 1.01  # 기준 fps 가 이만큼(1%) 안에서 같으면 같은 fps (29.97 ↔ 30)
STD_FPS = [(24000, 1001), (24, 1), (25, 1), (30000, 1001), (30, 1), (48, 1), (50, 1), (60000, 1001), (60, 1),
           (100, 1), (120000, 1001), (120, 1), (240, 1)]
SR = 48000
MAX_LONG = 3840  # 다시 만들 때 최대 4K


# ---------- 파일 정보 (ffmpeg -i) ----------

def _split_top(s):
    """쉼표로 나누되 괄호 안 쉼표는 무시: 'yuv420p(tv, bt709), 1920x1080' → ['yuv420p(tv, bt709)', '1920x1080']"""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _num(s):
    """'29.97' · '90k' → 숫자"""
    m = re.fullmatch(r"([\d.]+)(k?)", s.strip())
    if not m:
        return 0.0
    try:
        return float(m[1]) * (1000 if m[2] else 1)
    except ValueError:
        return 0.0


def _parse_time(s):
    """촬영 시각 문자열 → epoch 초 (시간대 표시가 없으면 UTC — mp4 의 creation_time 규칙). 엉터리 값은 None."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?", s or "")
    if not m:
        return None
    try:
        tz = timezone.utc
        if m[7] and m[7] != "Z":
            off = m[7].replace(":", "")
            sign = -1 if off[0] == "-" else 1
            tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
        t = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]), tzinfo=tz).timestamp()
    except (ValueError, OverflowError):
        return None
    if t < datetime(1995, 1, 1, tzinfo=timezone.utc).timestamp() or t > time.time() + 2 * 86400:
        return None  # 1904·1970 같은 빈 값, 시계가 틀린 카메라
    return t


def _snap_fps(f):
    """29.98(가변 fps 평균) → 30000/1001 처럼 표준 fps 로. 표준에서 멀면 소수 둘째 자리까지."""
    if not f or f <= 0:
        return None
    best = min(STD_FPS, key=lambda r: abs(r[0] / r[1] - f))
    if abs(best[0] / best[1] - f) <= max(0.06, f * 0.004):
        return best
    return (int(round(f * 100)), 100)


def _nominal_fps(avg, tbr, interlaced=False):
    """기준 fps. 휴대폰은 가변 fps 라 평균(29.90·29.98, 어두운 실내면 더 낮음)이 파일마다 달라서 tbr(기준 fps)을 씀.
    인터레이스(tbr 이 필드 수라 2배)·엉터리 tbr(90k 등)이면 평균."""
    ok = tbr and 1 <= tbr <= 240
    if ok and not interlaced and (not avg or avg <= tbr * 1.02):
        return tbr
    return avg or (tbr if ok else 0.0)


def _hms(sec):
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _size_txt(b):
    return f"{b / 1e9:.1f}GB" if b >= 1e9 else f"{b / 1e6:.0f}MB" if b >= 1e7 else f"{max(0.1, b / 1e6):.1f}MB"


def _duration_by_reading(path):
    """길이 정보가 없는 파일: 화면 데이터를 끝까지 읽어(복사만, 빠름) 길이를 잼."""
    r = core.run([core.ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"])
    ts = re.findall(r"time=(\d+):(\d+):([\d.]+)", r.stderr or "")
    return int(ts[-1][0]) * 3600 + int(ts[-1][1]) * 60 + float(ts[-1][2]) if ts else 0.0


def probe(path):
    """영상 한 개의 정보: 길이·코덱·크기·fps·회전·소리·촬영 시각."""
    path = Path(path)
    err = core.run([core.ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)]).stderr or ""
    lines = err.splitlines()
    streams, head, cur = [], [], None
    for ln in lines:
        m = re.match(r"\s*Stream #\d+:\d+\S*: (Video|Audio|Data|Subtitle|Attachment): (.*)", ln)
        if m:
            cur = {"kind": m[1], "desc": m[2], "lines": []}
            streams.append(cur)
        elif re.match(r"\s*(Input|Output) #", ln) or ln.startswith("At least one output"):
            cur = None
        elif cur is not None:
            cur["lines"].append(ln)
        elif not streams:
            head.append(ln)
    v = next((s for s in streams if s["kind"] == "Video" and "attached pic" not in s["desc"]), None)
    # 소리: 풀 수 있는 첫 트랙 (새 아이폰의 공간 음향 트랙처럼 ffmpeg 가 모르는 코덱은 'none' 으로 나옴)
    auds = [s for s in streams if s["kind"] == "Audio"]
    ai = next((k for k, s in enumerate(auds) if not s["desc"].startswith("none")), None)
    a = auds[ai] if ai is not None else None
    if not v:
        raise RuntimeError(f"'{path.name}'에서 영상 화면을 읽지 못했어요 (파일이 깨졌거나 소리만 있는 파일일 수 있어요). 이 파일은 빼고 다시 묶어 주세요")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    st0 = re.search(r"Duration: [^\n]*?start: (-?[\d.]+)", err)  # 파일 시작 시각 (캠코더 .mts 는 1.4초 등)
    if dur <= 0:
        dur = _duration_by_reading(path)
    if dur <= 0:
        raise RuntimeError(f"'{path.name}'의 길이를 알 수 없어요 (파일이 깨졌을 수 있어요). 이 파일은 빼고 다시 묶어 주세요")
    vf = _split_top(v["desc"])
    sz = re.search(r", (\d{2,5})x(\d{2,5})", v["desc"])
    w, h = (int(sz[1]), int(sz[2])) if sz else (0, 0)
    sar = re.search(r"\[SAR (\d+):(\d+)", v["desc"])
    sar = (int(sar[1]), int(sar[2])) if sar and int(sar[1]) and int(sar[2]) else (1, 1)
    fps = re.search(r"([\d.]+k?) fps", v["desc"])
    tbr = re.search(r"([\d.]+k?) tbr", v["desc"])
    tbn = re.search(r"([\d.]+k?) tbn", v["desc"])
    f = _num(fps[1]) if fps else 0.0
    interlaced = bool(re.search(r"(top|bottom)( coded)? first", vf[1] if len(vf) > 1 else ""))
    f_nom = _nominal_fps(f, _num(tbr[1]) if tbr else 0.0, interlaced)
    rot = 0
    for ln in v["lines"]:
        r = re.search(r"rotation of (-?[\d.]+)", ln)
        if r:
            rot = int(round(float(r[1]))) % 360
    if not rot:  # 예전 ffmpeg 로 만든 파일의 rotate 표시
        r = next((re.search(r"^\s*rotate\s*:\s*(-?\d+)", ln) for ln in v["lines"] if re.search(r"^\s*rotate\s*:", ln)), None)
        if r:
            rot = (-int(r[1])) % 360
    rot = min((0, 90, 180, 270), key=lambda x: min(abs(x - rot), 360 - abs(x - rot)))
    dw, dh = (h, w) if rot in (90, 270) else (w, h)
    if sar != (1, 1) and dw and dh:  # 네모가 아닌 화소 → 화면에 보이는 비율로
        if rot in (90, 270):
            dh = int(round(dh * sar[0] / sar[1]))
        else:
            dw = int(round(dw * sar[0] / sar[1]))
    hdr = "pq" if "smpte2084" in v["desc"] else "hlg" if "arib-std-b67" in v["desc"] else None
    au = None
    if a:
        af = _split_top(a["desc"])
        rate = re.search(r"(\d+) Hz", a["desc"])
        au = {"codec": af[0].split()[0] if af else "", "desc": af[0] if af else "", "rate": int(rate[1]) if rate else 0,
              "layout": af[2] if len(af) > 2 else "", "idx": ai}
    # 촬영 시각: 아이폰 creationdate(현지 시각+시간대) → 파일 전체 creation_time → 영상 트랙 creation_time
    created = None
    apple = re.search(r"com\.apple\.quicktime\.creationdate\s*:\s*(\S+)", err)
    if apple:
        created = _parse_time(apple[1])
    if created is None:
        g = next((re.search(r"creation_time\s*:\s*(.+)", ln) for ln in head if "creation_time" in ln), None)
        created = _parse_time(g[1].strip()) if g else None
    if created is None:
        g = next((re.search(r"creation_time\s*:\s*(.+)", ln) for ln in v["lines"] if "creation_time" in ln), None)
        created = _parse_time(g[1].strip()) if g else None
    st = path.stat()
    return {"path": path, "name": path.name, "duration": round(dur, 3), "start": float(st0[1]) if st0 else 0.0,
            "w": w, "h": h, "dw": dw, "dh": dh, "rot": rot,
            "sar": sar, "vcodec": vf[0].split()[0] if vf else "", "vdesc": vf[0] if vf else "", "pix": vf[1] if len(vf) > 1 else "",
            "size_desc": re.sub(r"\s+", " ", vf[2]) if len(vf) > 2 else "", "fps": f or f_nom, "fps_r": _snap_fps(f_nom),
            "tbn": tbn[1] if tbn else "", "hdr": hdr, "audio": au, "created": created, "mtime": st.st_mtime,
            "bytes": st.st_size}


# ---------- 순서·규격 ----------

def _natural(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def sort_clips(clips):
    """찍은 순서: 촬영 시각(없으면 파일 수정 시각) → 같으면 파일 이름(숫자는 숫자 크기대로)."""
    return sorted(clips, key=lambda c: (c["created"] if c["created"] is not None else c["mtime"], _natural(c["name"])))


def _signature(c):
    """화면 규격 (소리는 어차피 파일마다 다시 만드니 보지 않음 · fps 는 따로 오차를 두고 비교)."""
    return (c["vdesc"], c["pix"], c["size_desc"], c["tbn"], c["rot"])


def can_copy(clips):
    """모든 파일의 화면 규격이 같고 mp4 에 그대로 담을 수 있으면 True (화질 손실 없이 이어 붙이기)."""
    if any(c["vcodec"] not in COPY_VCODECS or not c["fps_r"] for c in clips):
        return False
    rates = [c["fps_r"][0] / c["fps_r"][1] for c in clips]
    return len({_signature(c) for c in clips}) == 1 and max(rates) <= min(rates) * FPS_TOL


def target_format(clips):
    """다시 만들 때의 규격: 가장 많은(길이 기준) 방향·크기·fps. → (W, H, (fps 분자, 분모))"""
    wt = lambda c: max(0.1, c["duration"] or 0.1)  # noqa: E731
    ori = defaultdict(float)
    for c in clips:
        ori["portrait" if c["dh"] > c["dw"] else "landscape"] += wt(c)
    portrait = ori["portrait"] > ori["landscape"]
    sizes = defaultdict(float)
    for c in clips:
        if (c["dh"] > c["dw"]) == portrait and c["dw"] and c["dh"]:
            sizes[(c["dw"], c["dh"])] += wt(c)
    W, H = max(sizes, key=lambda s: (sizes[s], s[0] * s[1])) if sizes else ((1080, 1920) if portrait else (1920, 1080))
    if max(W, H) > MAX_LONG:
        k = MAX_LONG / max(W, H)
        W, H = W * k, H * k
    W, H = max(2, int(round(W / 2)) * 2), max(2, int(round(H / 2)) * 2)
    rates = defaultdict(float)
    for c in clips:
        if c["fps_r"]:
            rates[c["fps_r"]] += wt(c)
    R = max(rates, key=lambda r: (rates[r], r[0] / r[1])) if rates else (30, 1)
    if R[0] / R[1] > 60.5:  # 슬로모션(120·240fps) 이 대부분이면 60fps 로
        R = (60000, 1001) if R[1] == 1001 else (60, 1)
    return W, H, R


# ---------- 이름·공간 ----------

def clean_title(title):
    """파일 이름에 못 쓰는 글자를 빼고 Windows 규칙(끝의 점·공백 금지)에 맞춤."""
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", str(title or ""))
    t = re.sub(r"\s+", " ", t).strip()[:60].strip(" .")
    return t or "촬영본"


def _unique_name(ymd, title):
    base = f"묶음_{ymd}_{title}"
    projects = core.WORK / "projects"
    for k in range(1, 1000):
        name = f"{base}.mp4" if k == 1 else f"{base} ({k}).mp4"
        if not (core.VIDEOS / name).exists() and not core.adir(name).exists() and not (projects / f"{core.adir(name).name}.json").exists():
            return name
    return f"{base}_{uuid.uuid4().hex[:6]}.mp4"


def _need_bytes(clips, copy, W=0, H=0, R=(30, 1)):
    if copy:
        return int(sum(c["bytes"] for c in clips) * 1.02) + (200 << 20)
    total = sum(c["duration"] for c in clips)
    vbps = max(W * H * R[0] / R[1] * 0.15, 2e6)  # 다시 만든 영상 대략 크기
    # 파일별로 맞춘 조각(영상 + 무손실 소리) + 합친 완성본이 잠깐 함께 있음
    return int(total * (vbps * 2 + SR * 2 * 16 + 192e3) / 8) + (300 << 20)


def _check_space(need):
    try:
        free = shutil.disk_usage(core.OUT).free
    except OSError:
        return
    if free < need:
        raise RuntimeError(f"저장 공간이 부족해요 · 묶은 영상을 만드는 데 약 {_size_txt(need)}가 필요한데 {_size_txt(free)}만 남아 있어요. "
                           "필요 없는 파일을 지운 뒤 다시 해 주세요")


# ---------- ffmpeg 실행 (진행률은 -progress 파일을 옆에서 읽음) ----------

def _read_progress(p):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    m = re.findall(r"out_time_us=(\d+)", tail)
    return int(m[-1]) / 1e6 if m else None


def _run_watch(cmd, prog, on_time, runner):
    stop = threading.Event()

    def watch():
        while not stop.wait(0.5):
            t = _read_progress(prog)
            if t is not None:
                try:
                    on_time(t)
                except Exception:
                    pass

    th = threading.Thread(target=watch, daemon=True)
    th.start()
    try:
        return runner(cmd)
    finally:
        stop.set()
        th.join(2)


def _killable():
    """앱을 닫거나 멈추면 같이 꺼지는 실행 함수 (편집실 것) — 없으면 보통 실행."""
    try:
        import editor
        return editor.run_killable
    except Exception:
        return core.run


def _cancelled():
    try:
        import editor
        return editor.CANCEL.is_set()
    except Exception:
        return False


def _fail(r, what):
    msg = (r.stderr or "").strip()
    if re.search(r"No space left|not enough space|disk full|ENOSPC", msg, re.I):
        return RuntimeError("저장 공간이 부족해서 묶지 못했어요. 작업 폴더가 있는 드라이브의 공간을 비운 뒤 다시 해 주세요")
    return RuntimeError(f"{what} · {msg[-400:] or '알 수 없는 오류'}")


def _q(p):
    """concat 목록용 따옴표 (경로 속 ' 처리)."""
    return "'" + str(p).replace("'", "'\\''") + "'"


def _ff_base(prog):
    return [core.ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y", "-progress", str(prog)]


# ---------- 하드웨어 인코더 (편집실이 확인해 둔 것) ----------

def _venc(enc, R):
    g = str(max(1, int(round(R[0] / R[1] * 2))))  # 2초마다 키프레임 (편집실에서 빨리 찾아감)
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", "22", "-b:v", "0", "-pix_fmt", "yuv420p", "-g", g]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-preset", "medium", "-global_quality", "23", "-pix_fmt", "nv12", "-g", g]
    if enc == "h264_amf":
        return ["-c:v", enc, "-quality", "balanced", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24", "-pix_fmt", "yuv420p", "-g", g]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-g", g]


def _hw_encoder(W, H, R):
    try:
        import editor
        enc = editor.hw_encoder() if hasattr(editor, "hw_encoder") else None
    except Exception:
        enc = None
    if enc not in ("h264_nvenc", "h264_qsv", "h264_amf"):
        return None
    r = core.run([core.ffmpeg(), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={R[0]}/{R[1]}:d=0.3"]
                 + _venc(enc, R) + ["-f", "null", "-"])
    return enc if r.returncode == 0 else None


# ---------- 이어 붙이기 ----------

_FC = {}


def _fc_opt():
    """필터 그래프를 파일로 넘기는 옵션 (파일이 많아도 명령줄이 길어지지 않게 · ffmpeg 7.1 부터 -/filter_complex)."""
    if "o" not in _FC:
        h = core.run([core.ffmpeg(), "-hide_banner", "-h", "long"]).stdout or ""
        _FC["o"] = "-filter_complex_script" if "filter_complex_script" in h else "-/filter_complex"
    return _FC["o"]


def _copy_concat(clips, tmp, out, created_iso, progress):
    """화면 규격이 같은 파일: 화면은 다시 만들지 않고 그대로 이어 붙임.
    소리는 파일마다 풀어서 그 파일 길이에 딱 맞춘 뒤 한 번에 AAC 로 — 그대로 붙이면 이음새마다 AAC 앞뒤 여백이 쌓여
    뒤로 갈수록 소리가 늦어짐 (받아쓰기·자막·컷 위치가 어긋남)."""
    lst, fcf = tmp / "list.txt", tmp / "audio.txt"
    # 파일마다 길이를 적어 둠 → 화면·소리·bundle.json 이 모두 같은 시각표를 따름
    lst.write_bytes(("ffconcat version 1.0\n" + "".join(
        f"file {_q(os.path.abspath(c['path']))}\nduration {c['duration']:.6f}\n" for c in clips)).encode("utf-8"))
    total = sum(c["duration"] for c in clips)
    prog = tmp / "prog_copy.txt"
    cmd = _ff_base(prog) + ["-f", "concat", "-safe", "0", "-i", str(lst)]
    fc, k_in = [], 0
    for k, c in enumerate(clips):
        ns = int(round(c["duration"] * SR))
        if c["audio"]:
            k_in += 1
            cmd += ["-i", str(c["path"])]
            # 파일 시작 시각을 빼서 0초부터 (이어 붙이는 쪽도 파일마다 똑같이 뺌)
            sh = f"asetpts=PTS-({c['start']:.6f})/TB," if c.get("start") else ""
            src = f"[{k_in}:a:{c['audio'].get('idx', 0)}]{sh}aresample={SR}:async=1:first_pts=0"
        else:  # 소리 없는 파일 → 편집점 찾기(받아쓰기)가 되도록 무음
            src = f"anullsrc=r={SR}:cl=stereo:d={c['duration'] + 1:.3f}"
        # 길이를 꼭 끝이 있게 채우고 자름 (끝없이 채우면 ffmpeg 7 이 가끔 멈춰 버림)
        fc.append(f"{src},aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts=stereo,"
                  f"apad=whole_len={ns},atrim=end_sample={ns}[a{k}]")
    fc.append("".join(f"[a{k}]" for k in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1[a]")
    fcf.write_text(";\n".join(fc), encoding="utf-8")
    # -copyts: 새로 만든 AAC 의 앞 여백만큼 화면이 늦게 밀리지 않게 (화면 첫 장면 = 0초 = 소리 첫 소리)
    cmd += [_fc_opt(), str(fcf), "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-copyts"]
    if created_iso:
        cmd += ["-metadata", f"creation_time={created_iso}"]
    cmd += [str(out)]
    progress(None, "그대로 이어 붙이는 중 (화질 그대로)")
    r = _run_watch(cmd, prog, lambda t: progress(min(99, int(t * 100 / max(total, 0.1))),
                                                  f"그대로 이어 붙이는 중 · {_hms(t)} / {_hms(total)}"), core.run)
    if _cancelled():
        raise RuntimeError("묶기를 멈췄어요")
    if r.returncode or not out.exists():
        raise _fail(r, "파일을 이어 붙이지 못했어요")


def _clip_cmd(c, part, prog, W, H, R, enc):
    """파일 한 개를 공통 규격(크기·fps·방향·소리 48kHz 스테레오)으로 맞춘 조각을 만드는 명령."""
    fr = f"{R[0]}/{R[1]}"
    n = max(1, int(round(c["duration"] * R[0] / R[1])))
    samples = int(round(n * R[1] * SR / R[0]))
    vf = []
    if re.search(r"(top|bottom)( coded)? first", c["pix"]):  # 캠코더 인터레이스 영상 → 줄무늬 없이
        vf.append("bwdif=mode=send_frame:deint=interlaced")
    vf.append(f"fps={fr}:start_time=0")  # 고정 fps, 늦게 시작하는 화면은 첫 장면으로 채움 (회전은 ffmpeg 가 자동으로 세움)
    if c["hdr"]:
        try:
            import editor
            vf += editor.tonemap_chain(c["hdr"], W)  # 아이폰 HDR → 일반 색 (허옇게 뜨지 않게)
        except Exception:
            pass
    if c["sar"] != (1, 1):
        vf += ["scale='trunc(iw*sar/2)*2':ih", "setsar=1"]
    vf += [f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2:out_color_matrix=bt709:out_range=tv",
           f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1", "format=yuv420p",
           "tpad=stop_mode=clone:stop_duration=3"]  # 화면이 소리보다 짧으면 마지막 장면을 늘림
    # 늘리는 길이는 꼭 끝이 있게 (끝없이 채우면 ffmpeg 7 이 가끔 멈춰 버림)
    cmd = _ff_base(prog) + ["-i", str(c["path"])]
    if c["audio"]:
        af = f"[0:a:{c['audio'].get('idx', 0)}]aresample={SR}:async=1:first_pts=0,"
    else:  # 소리 없는 파일 → 무음
        cmd += ["-f", "lavfi", "-t", f"{n * R[1] / R[0] + 1:.3f}", "-i", f"anullsrc=r={SR}:cl=stereo"]
        af = "[1:a:0]"
    af += f"aformat=sample_fmts=s16:sample_rates={SR}:channel_layouts=stereo,apad=whole_len={samples},atrim=end_sample={samples}[a]"
    cmd += ["-filter_complex", f"[0:v:0]{','.join(vf)}[v];{af}", "-map", "[v]", "-map", "[a]",
            "-frames:v", str(n), "-r", fr] + _venc(enc, R) + [
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "pcm_s16le", "-map_metadata", "-1", "-map_chapters", "-1", "-f", "mov", str(part)]
    return cmd, n * R[1] / R[0]


def _encode_concat(clips, tmp, out, fmt, created_iso, progress, log):
    """규격이 다른 파일: 파일마다 같은 규격의 조각으로 다시 만든 뒤 이어 붙임 → 길이 [초]."""
    W, H, R = fmt
    total = sum(c["duration"] for c in clips) or 0.1
    enc = _hw_encoder(W, H, R)
    run = _killable()
    for attempt in ([enc, None] if enc else [None]):
        durs, done = [], 0.0
        try:
            for k, c in enumerate(clips):
                part, prog = tmp / f"c{k:04d}.mov", tmp / f"prog_{k:04d}.txt"
                cmd, d = _clip_cmd(c, part, prog, W, H, R, attempt)
                step = f"{k + 1}/{len(clips)}"
                progress(int(done * 95 / total), f"규격 맞추는 중 · {c['name']}", step)
                r = _run_watch(cmd, prog, lambda t, s=step, n=c["name"], base=done, cd=c["duration"]: progress(
                    min(95, int((base + min(t, cd)) * 95 / total)), f"규격 맞추는 중 · {n}", s), run)
                if _cancelled():
                    raise RuntimeError("묶기를 멈췄어요")
                if r.returncode or not part.exists():
                    if attempt:
                        raise _HwFail(r.stderr or "")
                    raise _fail(r, f"'{c['name']}'을(를) 맞추지 못했어요")
                durs.append(d)
                done += c["duration"]
            break
        except _HwFail:
            log("  그래픽카드로 만들기가 안 돼서 일반 방식으로 처음부터 다시 만들어요 (조금 더 걸려요)")
            for p in tmp.glob("c*.mov"):
                p.unlink(missing_ok=True)
    # 조각을 이어 붙임: 화면은 그대로, 소리는 한 번에 AAC 로 (조각 사이에 틈이 생기지 않게)
    # -copyts: 새로 만든 AAC 의 앞 여백만큼 화면이 늦게 밀리지 않게
    lst = tmp / "list.txt"
    lst.write_bytes(("ffconcat version 1.0\n" + "".join(f"file 'c{k:04d}.mov'\nduration {d:.6f}\n" for k, d in enumerate(durs)))
                    .encode("utf-8"))
    prog = tmp / "prog_join.txt"
    cmd = _ff_base(prog) + ["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v:0", "-map", "0:a:0",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2", "-copyts"]
    if created_iso:
        cmd += ["-metadata", f"creation_time={created_iso}"]
    cmd += [str(out)]
    joined = sum(durs)
    progress(95, "하나로 합치는 중")
    r = _run_watch(cmd, prog, lambda t: progress(min(99, 95 + int(t * 4 / max(joined, 0.1))), "하나로 합치는 중"), run)
    if _cancelled():
        raise RuntimeError("묶기를 멈췄어요")
    if r.returncode or not out.exists():
        raise _fail(r, "조각을 하나로 합치지 못했어요")
    return durs, enc if enc and attempt else None


class _HwFail(Exception):
    pass


def _replace_retry(src, dst, tries=40):
    """Windows: 백신·탐색기가 막 만든 파일을 잠깐 잡고 있으면 옮기기가 실패 → 잠시 뒤 다시."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.25)
        except OSError as e:
            if getattr(e, "errno", None) == 18 or getattr(e, "winerror", None) == 17:  # 다른 드라이브
                shutil.move(str(src), str(dst))
                return
            raise


def _rmtree(d):
    for _ in range(8):
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            return
        time.sleep(0.25)


def _sweep_old():
    """앱이 묶는 도중 꺼져 남은 임시 폴더 정리 (작업은 한 번에 하나라 지금 남은 것은 모두 예전 것)."""
    try:
        for d in core.OUT.iterdir():
            if d.is_dir() and d.name.startswith(TMP_PREFIX):
                _rmtree(d)
    except OSError:
        pass


# ---------- 본체 ----------

def make_bundle(names, title, log):
    """보관함의 여러 영상(names)을 찍은 순서대로 이어 한 영상으로. 원본은 그대로 둔다.
    → {"name", "size_mb", "added", "mode": "copy"|"encode", "clips", "duration"}"""
    seen, files = set(), []
    for n in names or []:
        n = Path(str(n)).name
        if n and n.lower() not in seen:
            seen.add(n.lower())
            files.append(n)
    if len(files) < 2:
        raise RuntimeError("묶으려면 영상을 2개 이상 골라 주세요")
    missing = [n for n in files if not (core.VIDEOS / n).is_file()]
    if missing:
        raise RuntimeError(f"보관함에서 찾지 못한 영상이 있어요 · {', '.join(missing[:3])}")
    title = clean_title(title)
    log(f"한 영상으로 묶는 중 · {len(files)}개 파일")

    def progress(pct, detail, step=None):
        core.set_progress(label=LABEL, item=title, pct=pct, detail=detail, **({"step": step} if step else {}))

    clips = []
    for k, n in enumerate(files, 1):
        progress(None, f"파일 정보 읽는 중 · {k}/{len(files)}")
        clips.append(probe(core.VIDEOS / n))
    clips = sort_clips(clips)
    for k, c in enumerate(clips, 1):
        when = datetime.fromtimestamp(c["created"] if c["created"] is not None else c["mtime"]).strftime("%m-%d %H:%M:%S")
        log(f"  {k}. {c['name']} · {_hms(c['duration'])} · {when}{'' if c['created'] is not None else ' (파일 수정 시각)'}")
    first = clips[0]["created"] if clips[0]["created"] is not None else clips[0]["mtime"]
    ymd = datetime.fromtimestamp(first).strftime("%Y%m%d")
    created_iso = datetime.fromtimestamp(clips[0]["created"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z") \
        if clips[0]["created"] is not None else None
    copy = can_copy(clips)
    fmt = None if copy else target_format(clips)
    if copy:
        log("  모든 파일의 화면 규격이 같아서 화질 손실 없이 그대로 이어 붙여요")
    else:
        W, H, R = fmt
        log(f"  파일마다 규격(코덱·크기·fps·방향)이 달라서 {W}×{H} · {R[0] / R[1]:.3g}fps 로 맞춰 다시 만들어요 (시간이 좀 걸려요)")
    _sweep_old()  # 공간 확인 전에: 예전에 멈춘 묶기가 남긴 조각이 자리를 차지하고 있을 수 있음
    _check_space(_need_bytes(clips, copy, *(fmt or ())))
    tmp = core.OUT / f"{TMP_PREFIX}{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    try:
        part = tmp / "bundle.mp4"
        hw = None
        if copy:
            _copy_concat(clips, tmp, part, created_iso, progress)
            durs = [c["duration"] for c in clips]
        else:
            durs, hw = _encode_concat(clips, tmp, part, fmt, created_iso, progress, log)
        progress(99, "마무리하는 중")
        starts, t = [], 0.0
        for d in durs:
            starts.append(t)
            t += d
        meta = [{"file": c["name"], "start": round(s, 3), "dur": round(d, 3)} for c, s, d in zip(clips, starts, durs)]
        name = _unique_name(ymd, title)  # 다 만든 뒤에 정함 (그사이 같은 이름 파일이 생겨도 덮어쓰지 않게)
        dest, adir = core.VIDEOS / name, core.adir(name)
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "bundle.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            _replace_retry(part, dest)
        except OSError:
            _rmtree(adir)
            raise
    finally:
        _rmtree(tmp)
    size = dest.stat().st_size
    total = sum(durs)
    # 다 만든 파일 확인 (길이가 크게 다르면 기록에 남김)
    try:
        got = probe(dest)["duration"]
        if abs(got - total) > max(1.0, total * 0.01):
            log(f"  확인 필요 · 묶은 영상 길이({_hms(got)})가 원본 합계({_hms(total)})와 달라요")
    except Exception as e:
        log(f"  확인 필요 · 묶은 영상을 다시 읽지 못했어요 · {e}")
    log(f"  완성 · {name} · 총 {_hms(total)} · 저장 공간 {_size_txt(size)} 더 씀 · 원본 {len(clips)}개는 그대로 있어요"
        + (" · 그래픽카드로 빠르게 만들었어요" if hw else ""))
    return {"name": name, "size_mb": round(size / 1e6, 1), "added": _size_txt(size), "mode": "copy" if copy else "encode",
            "clips": len(clips), "duration": round(total, 3)}
