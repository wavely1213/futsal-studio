"""스타일 배우기 정답 영상 만들기 — ffmpeg lavfi 만으로 '정답을 아는' 시험 영상을 만듦 (인터넷·실제 영상 없이).

정답:
- 컷: 서로 다른 장면(단색·무늬·컬러바)을 이어 붙인 시각 CUTS
- 확대 컷(펀치인): PUNCH_T 초에 바로 앞 장면을 1.2배 확대한 장면으로 바뀜
- 자막: 아래쪽 검은 띠(drawbox) 위에 #FFE14D 노란 글자 (CAPTIONS 구간)
  · 이 ffmpeg 에는 drawtext(글꼴 필터)가 없어서 글자는 libass 자막 필터(subtitles)로 그림 — 앱에 들어 있는 Pretendard 글꼴 사용
- 말 사이 공백: GAPS 의 (시작, 길이) 에서만 소리가 완전히 멈춤

직접 실행: python3 tests/make_fixture.py 정답.mp4  → 영상 + 정답.json
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402

W, H, RATE = 640, 360, 30
CAPTION_COLOR = "#FFE14D"
PUNCH_SCALE = 1.2
# (길이, 장면) — 장면: 단색 0xRRGGBB / pat1·pat2(부드러운 무늬) / pat1z(pat1 을 1.2배 확대) / bars(컬러바)
SHOTS = [(4.1, "0x1E3A8A"), (3.5, "pat1"), (3.5, "pat1z"), (3.0, "0xE8A33D"), (4.5, "bars"),
         (2.5, "0x2E7D32"), (4.0, "pat2"), (2.9, "0xC0C0C0")]
CUTS = [round(sum(d for d, _ in SHOTS[:k]), 3) for k in range(1, len(SHOTS))]  # 4.1, 7.6, 11.1, 14.1, 18.6, 21.1, 25.1
DURATION = round(sum(d for d, _ in SHOTS), 3)
PUNCH_T = CUTS[1]  # pat1 → pat1z
CAPTIONS = [(0.4, 3.6, "풋살 첫 터치는 발 안쪽으로"), (4.5, 7.2, "공을 받을 때 몸을 열어요"), (8.0, 10.8, "Ball Control 기본기 연습"),
            (11.6, 13.8, "시선은 먼저 앞을 봐요"), (14.6, 18.2, "패스 받고 바로 돌아서기"), (19.0, 20.8, "실수해도 괜찮아요"),
            (21.6, 24.6, "오늘의 꿀팁 정리해요"), (25.5, 27.6, "구독과 좋아요 부탁해요")]
NOTES = [(2.0, 6.0), (12.0, 16.0), (22.0, 25.0)]  # white_note=True: 아래 오른쪽 흰 글자(채널 이름) — 자막보다 덜 나옴
CAP_BOX = {"bottom": (0.80, 2, 22), "middle": (0.43, 5, 0), "top": (0.06, 8, 30)}  # 위치 → (검은 띠 위쪽 y, ASS 정렬, 여백)
GAPS = [(1.5, 0.3), (3.2, 0.5), (5.9, 0.2), (8.4, 0.8), (10.2, 0.4), (12.6, 1.2), (15.5, 0.25), (17.4, 0.6),
        (20.0, 0.35), (22.6, 0.7), (24.9, 0.45), (26.6, 0.3)]

# 강한 세로 경계(글자처럼 보이는 것)가 없도록 부드럽지만, 확대하면 확 달라 보이는 무늬
PATTERNS = {
    "pat1": ("128+80*sin(X/31+2*sin(Y/47))+40*sin(X/9.5)*cos(Y/11)",
             "128+80*sin(Y/23+1.7*cos(X/53))+35*cos(X/13+Y/17)",
             "128+90*cos((X+Y)/37)"),
    "pat2": ("128+90*cos(X/19-Y/41)",
             "128+70*sin(X/43)*cos(Y/15)+40*sin((X-Y)/10)",
             "128+80*sin(Y/27+2*cos(X/61))"),
}


def _ts(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _ass(pos="bottom", white_note=False):
    _, align, mv = CAP_BOX[pos]
    head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # ASS 색은 &H00BBGGRR → #FFE14D
            f"Style: Cap,Pretendard,30,&H004DE1FF,&H004DE1FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,1,0,{align},10,10,{mv},1",
            "Style: Note,Pretendard,22,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,3,16,16,12,1",
            "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = [f"Dialogue: 0,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{t}" for a, b, t in CAPTIONS]
    ev += [f"Dialogue: 1,{_ts(a)},{_ts(b)},Note,,0,0,0,,@풋살사관학교 최경진" for a, b in NOTES] if white_note else []
    return "\n".join(head + ev) + "\n"


def _audio_expr():
    gate = "*".join(f"not(between(t,{a},{a + n:.3f}))" for a, n in GAPS)
    return f"(0.30*sin(2*PI*220*t)+0.12*sin(2*PI*330*t))*(0.65+0.35*sin(2*PI*3*t))*{gate}"


def _ff(args, cwd):
    r = subprocess.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **core.NO_WINDOW)
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])


def truth(caption_pos="bottom"):
    """정답 (영상 없이도 쓸 수 있게)."""
    gaps = sorted(n for _, n in GAPS)
    return {"duration": DURATION, "cuts": CUTS, "punch": {"t": PUNCH_T, "scale": PUNCH_SCALE}, "captionPos": caption_pos,
            "captionColor": CAPTION_COLOR, "captions": [[a, b] for a, b, _ in CAPTIONS], "gaps": [list(g) for g in GAPS],
            "pauseP75": gaps[int(len(gaps) * 0.75)]}


def make_fixture(out, work=None, caption_pos="bottom", white_note=False):
    """정답 영상을 out 에 만들고 정답(dict)을 돌려줌. work: 중간 파일 폴더 (없으면 임시 폴더).
    caption_pos: 노란 자막 위치(bottom/middle/top) · white_note: 아래 오른쪽에 흰 글자도 가끔 (자막 색을 엉뚱한 띠에서 뽑지 않는지)."""
    out = Path(out)
    tmp = Path(work or tempfile.mkdtemp(prefix="스타일 정답 "))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        (tmp / "fonts").mkdir(exist_ok=True)
        shutil.copy2(core.APP_DIR / "fonts" / "Pretendard-Bold.otf", tmp / "fonts" / "Pretendard-Bold.otf")
        (tmp / "cap.ass").write_text(_ass(caption_pos, white_note), encoding="utf-8")
        for k, (r, g, b) in PATTERNS.items():
            _ff(["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d=1,format=rgb24,geq=r='{r}':g='{g}':b='{b}'",
                 "-frames:v", "1", f"{k}.png"], tmp)
        ins, chains = [], []
        for i, (d, kind) in enumerate(SHOTS):
            if kind.startswith("pat"):
                ins += ["-loop", "1", "-framerate", RATE, "-t", d, "-i", f"{kind[:4]}.png"]
            elif kind == "bars":
                ins += ["-f", "lavfi", "-i", f"smptehdbars=s={W}x{H}:r={RATE}:d={d}"]
            else:
                ins += ["-f", "lavfi", "-i", f"color=c={kind}:s={W}x{H}:r={RATE}:d={d}"]
            z = (f"crop=iw/{PUNCH_SCALE}:ih/{PUNCH_SCALE}:(iw-iw/{PUNCH_SCALE})/2:(ih-ih/{PUNCH_SCALE})/2,scale={W}:{H}:flags=bicubic,"
                 if kind == "pat1z" else "")
            chains.append(f"[{i}:v]{z}fps={RATE},trim=duration={d},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[s{i}]")
        n = len(SHOTS)
        fc = ";".join(chains) + ";" + "".join(f"[s{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat];" \
            + f"[cat]drawbox=x=0:y=ih*{CAP_BOX[caption_pos][0]}:w=iw:h=ih*0.14:color=black@0.75:t=fill,subtitles=cap.ass:fontsdir=fonts[v]"
        ins += ["-f", "lavfi", "-i", f"aevalsrc='{_audio_expr()}':s=48000:d={DURATION}"]
        _ff(ins + ["-filter_complex", fc, "-map", "[v]", "-map", f"{n}:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-r", RATE, "-c:a", "aac", "-b:a", "160k", "-t", DURATION, "fixture.mp4"], tmp)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp / "fixture.mp4"), str(out))
    finally:
        if work is None:
            shutil.rmtree(tmp, ignore_errors=True)
    return truth(caption_pos)


# ---- 한 자리에서 계속 찍은 촬영본 흉내 (점프 컷이 화면 분석에 안 보이는지) ----
STATIC_A, STATIC_B = 20.0, 14.0  # 앞: 밋밋한 벽(완만한 밝기 변화) · 뒤: 무늬 벽(pat1) — 둘 다 그 앞에서 사람(어두운 상자)이 천천히 움직임
GRAD = ("100+50*X/W", "110+40*X/W", "120+30*Y/H")


def make_static(out, work=None):
    """정답: 같은 장면 안의 점프 컷·밋밋한 벽 확대는 화면 분석에 컷으로 안 보이고, 벽이 바뀌거나 무늬 벽을 확대하면 보임.
    장면 전환은 STATIC_A 초 한 번뿐. 소리는 계속 남 (공백 없음)."""
    out = Path(out)
    tmp = Path(work or tempfile.mkdtemp(prefix="스타일 정답 "))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for k, (r, g, b) in (("grad", GRAD), ("pat1", PATTERNS["pat1"])):
            _ff(["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d=1,format=rgb24,geq=r='{r}':g='{g}':b='{b}'",
                 "-frames:v", "1", f"{k}.png"], tmp)
        dur = STATIC_A + STATIC_B
        ins = ["-loop", "1", "-framerate", RATE, "-t", STATIC_A, "-i", "grad.png", "-loop", "1", "-framerate", RATE, "-t", STATIC_B, "-i", "pat1.png",
               "-f", "lavfi", "-i", f"color=c=0x3A2A1A:s=60x90:r={RATE}:d={dur}", "-f", "lavfi", "-i", f"sine=f=220:sample_rate=48000:d={dur}"]
        fc = (f"[0:v]fps={RATE},trim=duration={STATIC_A},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[a];"
              f"[1:v]fps={RATE},trim=duration={STATIC_B},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[b];"
              f"[a][b]concat=n=2:v=1:a=0[bg];[bg][2:v]overlay=x='290+40*sin(t*0.7)':y='150+10*sin(t*1.3)':shortest=1[v]")
        _ff(ins + ["-filter_complex", fc, "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-r", RATE, "-c:a", "aac", "-b:a", "128k", "-t", dur, "static.mp4"], tmp)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp / "static.mp4"), str(out))
    finally:
        if work is None:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"duration": STATIC_A + STATIC_B, "cuts": [STATIC_A]}


def reencode(src, out, size="960x540", crf=30):
    """같은 영상을 다른 크기·화질로 다시 인코딩 (재인코딩한 복사본도 같은 스타일로 읽혀야 함)."""
    w, h = size.split("x")
    _ff(["-i", Path(src).resolve(), "-vf", f"scale={w}:{h}", "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", Path(out).resolve()], Path(out).resolve().parent)
    return Path(out)


if __name__ == "__main__":
    dst = Path(sys.argv[1] if len(sys.argv) > 1 else "style_fixture.mp4")
    t = make_fixture(dst)
    dst.with_suffix(".json").write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"만들었어요 · {dst} · 정답 {dst.with_suffix('.json')}")
