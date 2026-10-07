"""MSG 시험용 '재미없는 원본' (인터넷 없이 · 몇 초 만에): 말하는 장면 + 말 없는 시범 2번(공 움직임·공 차는 소리) + 웃음 + NG 다시 찍기.

make_msg_fixture(work) → (영상 이름, 정답 truth). 받아쓰기(transcript.json · 낱말 시각)와 analysis.json 을 함께 만든다
(실제 받아쓰기 모델 없이 · 정답 시각을 그대로). 웃음소리는 소리 모델 대신 truth["laughs"] 로 시험에서 끼워 넣는다.
"""
import json
import subprocess
from pathlib import Path

import core

W, H, FPS = 320, 180, 30
DUR = 34.0
NAME = "20261007_MSGTEST_첫 터치 레슨 [시험].mp4"
# (시작, 끝, 글) — 낱말 시각은 글자 수로 나눔
LINES = [
    (0.6, 3.6, "안녕하세요. 오늘은 퍼스트 터치를 해 볼게요."),
    (4.2, 6.6, "왜 다들 첫 터치에서 공을 놓칠까요?"),
    (8.4, 10.6, "자, 첫 번째 포인트는 고개 들기예요."),
    (11.0, 13.4, "이게 진짜 핵심이에요."),
    (13.8, 15.4, "그래서 인사이드로 받을 때"),          # NG 앞 테이크
    (15.6, 17.0, "아 잠깐만요, 다시 할게요."),          # 슬레이트
    (17.6, 20.4, "그래서 인사이드로 받을 때는 힘을 빼세요."),
    (20.8, 21.8, "보여 드릴게요."),
    # 시범 1: 22.2 ~ 26.6 (말 없음 · 공 소리 23.4)
    (27.0, 28.4, "나이스! 들어갔어요."),
    (28.8, 30.6, "제가 원래 잘 넘어지거든요."),      # 농담 → 웃음 30.9
    (32.0, 33.6, "오늘은 여기까지예요. 감사합니다."),
]
PLAYS = [(22.2, 26.6, [23.4, 25.2])]
LAUGHS = [(30.9, 32.2)]
TRUTH = {"plays": [{"a": a, "b": b, "kicks": k} for a, b, k in PLAYS], "punchlines": [{"a": 28.8, "b": 30.6}], "laughs": [{"a": a, "b": b} for a, b in LAUGHS],
         "emphasis": [{"a": 11.0, "b": 13.4}, {"a": 17.6, "b": 20.4}], "success": [{"a": 27.0, "b": 28.4}], "question": [{"a": 4.2, "b": 6.6}],
         "section": [{"a": 8.4, "b": 10.6}], "closing": [{"a": 32.0, "b": 33.6}], "junk": [{"a": 13.8, "b": 17.0}], "duration": DUR}


def _words(a, b, text):
    toks = text.split()
    tot = sum(len(t) for t in toks)
    out, t = [], a
    for tk in toks:
        d = (b - a) * len(tk) / tot
        out.append({"w": tk, "s": round(t, 3), "e": round(t + d - 0.04, 3), "p": 0.9})
        t += d
    return out


def _audio_expr():
    """말: 150~260Hz 웅웅 소리를 낱말마다 켰다 끔 · 공 소리: 짧고 큰 쿵 · 웃음: 잡음 덩어리."""
    talk = "+".join(f"between(t,{a},{b})" for a, b, _ in LINES)
    kicks = "+".join(f"between(t,{k},{k + 0.06})*exp(-abs(t-{k})*60)" for _, _, ks in PLAYS for k in ks)
    laugh = "+".join(f"between(t,{a},{b})" for a, b in LAUGHS)
    sp = f"0.25*({talk})*sin(2*PI*(170+40*sin(2*PI*3*t))*t)*(0.6+0.4*sin(2*PI*5*t))"
    kk = f"0.9*({kicks})*sin(2*PI*90*t)"
    lg = f"0.12*({laugh})*(random(0)-0.5)*(0.6+0.4*sin(2*PI*7*t))"
    return f"{sp}+{kk}+{lg}+0.002*(random(1)-0.5)"


def _frames():
    """말하는 장면은 거의 그대로, 시범 장면은 공(흰 원)이 빠르게 움직임."""
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W]
    base = np.zeros((H, W, 3), np.uint8)
    base[..., 0], base[..., 1], base[..., 2] = 40, 110, 60   # 초록 바닥
    base[60:180, 130:190] = (30, 60, 140)                     # 사람(파란 옷)
    face = (xx - 160) ** 2 + (yy - 45) ** 2 < 18 ** 2
    base[face] = (220, 180, 150)
    for f in range(int(DUR * FPS)):
        t = f / FPS
        im = base.copy()
        for a, b, ks in PLAYS:
            if a <= t <= b:
                x = int(40 + (t - a) / (b - a) * 240)
                y = int(150 - 80 * abs(((t - a) * 1.7) % 2 - 1))
                ball = (xx - x) ** 2 + (yy - y) ** 2 < 9 ** 2
                im[ball] = (250, 250, 250)
                im[:, :, :] = np.roll(im, int(6 * np.sin(t * 9)), axis=1) if any(abs(t - k) < 0.3 for k in ks) else im
        yield im.tobytes()


def make_msg_fixture(work):
    """work 작업 폴더(videos/·analysis/)에 시험 영상 + 받아쓰기를 만들고 (이름, 정답)을 돌려줌."""
    work = Path(work)
    vids, ana = work / "videos", work / "analysis" / Path(NAME).stem
    vids.mkdir(parents=True, exist_ok=True)
    ana.mkdir(parents=True, exist_ok=True)
    out = vids / NAME
    if not out.exists():
        cmd = [core.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
               "-f", "lavfi", "-i", f"aevalsrc='{_audio_expr()}':s=48000:d={DUR}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-ac", "1", "-shortest", str(out)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for fr in _frames():
            p.stdin.write(fr)
        p.stdin.close()
        err = p.stderr.read().decode("utf-8", "replace")
        p.stderr.close()
        assert p.wait() == 0, err[-400:]
    segs = [{"start": a, "end": b, "text": t, "words": _words(a, b, t)} for a, b, t in LINES]
    (ana / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    sil = []
    prev = 0.0
    for a, b, _ in LINES:
        if a - prev >= 0.4:
            sil.append({"start": round(prev, 2), "end": round(a, 2)})
        prev = b
    peaks = [{"time": k, "db": -6.0} for _, _, ks in PLAYS for k in ks]
    (ana / "analysis.json").write_text(json.dumps({"silences": sil, "loud_peaks": peaks}, ensure_ascii=False), encoding="utf-8")
    return NAME, json.loads(json.dumps(TRUTH))


if __name__ == "__main__":
    import sys
    print(make_msg_fixture(sys.argv[1]))


class MsgWork:
    """시험 작업 폴더(한글·띄어쓰기 이름) + core·editor·style 경로 바꾸기 + 시험 영상 · 웃음 소리 모델 흉내 · 얼굴 모델 끔."""

    def __init__(self):
        import tempfile
        from unittest import mock
        import editor
        import msg
        import style
        self.tmp = Path(tempfile.mkdtemp(prefix="MSG 시험 "))
        self.work = self.tmp / "풋살 작업 폴더"
        dirs = {"WORK": self.work, "VIDEOS": self.work / "videos", "ANALYSIS": self.work / "analysis", "OUT": self.work / "out"}
        for d in list(dirs.values()) + [self.work / "edit_media", self.work / "projects", self.work / "styles"]:
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [mock.patch.object(core, k, v) for k, v in dirs.items()]
        self.patches += [mock.patch.object(editor, "ASSETS", self.work / "edit_media"), mock.patch.object(editor, "PROJECTS", self.work / "projects"),
                         mock.patch.object(style, "STYLES", self.work / "styles"), mock.patch.object(msg, "_tags", self.fake_tags),
                         mock.patch.object(msg, "_faces_at", lambda name, ts: {})]
        for p in self.patches:
            p.start()
        self.name, self.truth = make_msg_fixture(self.work)

    def fake_tags(self, path, wave, name):
        """YAMNet 대신: 정답 웃음 구간만 0.8 (0.48초 칸)."""
        k = int(DUR / 0.48) + 1
        lg = [0.0] * k
        for x in self.truth["laughs"]:
            for i in range(int(x["a"] / 0.48), min(k, int(x["b"] / 0.48) + 1)):
                lg[i] = 0.8
        return {"laugh": lg, "cheer": [0.0] * k, "music": [0.0] * k, "speech": [0.0] * k}

    def close(self):
        import shutil
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
