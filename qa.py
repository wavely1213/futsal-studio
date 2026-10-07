"""내보낸 영상 자동 검수 — 올리기 전에 흔한 실수를 잡아 준다 (ffmpeg만 사용).

- 크기·비율 (롱폼 16:9 / 쇼츠 9:16), 쇼츠 길이
- 검은 화면, 멈춘 화면, 소리가 끊긴 구간
- 소리 크기(LUFS)·최대 피크(클리핑)
"""
import re

import core

# 소리 크기 기준 (유튜브: -14 LUFS 로 맞춰 틀고, 작은 소리는 키워 주지 않음 · 최대 피크는 -1 dBTP 이하 권장)
QUIET, LOUD, PEAK_MAX = -16.0, -10.0, -1.0


def _t(s):
    s = max(0, int(round(s)))
    return f"{s // 60:02d}:{s % 60:02d}"


def check_video(path, fmt=None, master=None, run=None):
    """fmt: 내보낸 편집본 형식 (내보내기를 누를 때의 값) · master: 그때의 소리 크기 설정 {normalize, lufs}
    run: 멈추기(✕)로 끌 수 있는 실행 함수 (편집실은 editor.run_killable)."""
    path = str(path)
    items = []

    def add(lv, title, msg, t=None):
        items.append({"lv": lv, "title": title, "msg": msg, "t": t})

    core.set_progress(label="영상 검수 중", pct=None, detail="화면·소리 살펴보는 중")
    r = (run or core.run)([core.ffmpeg(), "-hide_banner", "-i", path,
                           "-vf", "blackdetect=d=0.4:pix_th=0.08,freezedetect=n=-55dB:d=2.5",
                           "-af", "silencedetect=n=-45dB:d=1.2,ebur128=peak=true", "-f", "null", "-"])
    err = r.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    v = re.search(r"Stream #\d+:\d+.*Video:.*?(\d{3,5})x(\d{3,5})", err)
    w, h = (int(v[1]), int(v[2])) if v else (0, 0)
    fps = re.search(r"([\d.]+) fps", err)
    has_audio = bool(re.search(r"Stream #\d+:\d+.*Audio:", err))
    fmt = fmt or ("shorts" if h > w else "long")

    if not v:
        add("bad", "영상 없음", "영상 화면을 읽지 못했어요. 파일이 깨졌을 수 있어요.")
    elif fmt == "shorts":
        if abs(w / h - 9 / 16) > 0.02:
            add("bad", "쇼츠 비율", f"{w}×{h}예요. 쇼츠는 세로 9:16(1080×1920)이어야 해요.")
        elif h < 1920:
            add("warn", "화질", f"{w}×{h} — 1080×1920이면 더 선명해요.")
        else:
            add("ok", "크기", f"{w}×{h} 쇼츠 규격이에요.")
        if dur > 180:
            add("bad", "쇼츠 길이", f"{_t(dur)} — 쇼츠는 3분 이하여야 해요.")
        elif dur > 60:
            add("warn", "쇼츠 길이", f"{_t(dur)} — 1분 안쪽이 끝까지 보는 사람이 많아요.")
        else:
            add("ok", "길이", f"{_t(dur)}")
    else:
        if abs(w / h - 16 / 9) > 0.02:
            add("warn", "화면 비율", f"{w}×{h} — 유튜브 일반 영상은 16:9가 꽉 차요.")
        elif h < 1080:
            add("warn", "화질", f"{w}×{h} — 1920×1080 이상이면 'HD'로 보여요.")
        else:
            add("ok", "크기", f"{w}×{h} · {fps[1] if fps else '?'}fps")
        add("ok", "길이", f"{_t(dur)}")

    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    blacks = [(a, b) for a, b in blacks if not (a < 0.3 or b > dur - 1.2)]  # 맨 앞뒤 페이드는 괜찮음
    for a, b in blacks[:5]:
        add("bad", "검은 화면", f"{_t(a)}부터 {b - a:.1f}초 동안 화면이 까매요 (빈 구간이나 끊긴 클립일 수 있어요).", a)
    freezes = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", err)]
    for a in freezes[:3]:
        add("warn", "멈춘 화면", f"{_t(a)}쯤 화면이 2초 넘게 멈춰 있어요 (사진·정지 화면이면 괜찮아요).", a)
    if not has_audio:
        add("bad", "소리 없음", "소리 트랙이 없어요.")
    else:
        sils = [(float(a), float(b)) for a, b in re.findall(r"silence_start: ([\d.]+)[\s\S]*?silence_end: ([\d.]+)", err)]
        sils = [(a, b) for a, b in sils if b - a >= 1.2 and not (b > dur - 0.8)]
        for a, b in sils[:5]:
            add("warn", "소리가 끊겨요", f"{_t(a)}부터 {b - a:.1f}초 동안 거의 소리가 없어요.", a)
        I = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
        P = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)
        lufs, peak = (float(I[-1]) if I else None), (float(P[-1]) if P else None)
        norm = bool(master) and master.get("normalize", True)
        tgt = min(-9.0, max(-24.0, float(master.get("lufs") or -14.0))) if norm else None
        if lufs is None:
            pass
        elif lufs < QUIET:  # 목표와 상관없이: 유튜브는 작은 소리를 키워 주지 않음 → 다른 영상보다 작게 들림
            how = (f"편집실 '소리 크기 맞추기' 목표가 {tgt:g} LUFS예요. -14로 바꿔 다시 내보내세요." if tgt is not None and tgt < QUIET
                   else "편집실에서 '소리 크기 맞추기'를 켜고(목표 -14) 다시 내보내세요." if tgt is None
                   else f"목표({tgt:g})까지 키우지 못했어요. 다시 내보내 보세요.")
            add("warn", "소리가 작아요", f"{lufs:.1f} LUFS — 유튜브는 작은 소리를 키워 주지 않아서 다른 영상보다 작게 들려요. {how}")
        elif lufs > LOUD:
            add("warn", "소리가 커요", f"{lufs:.1f} LUFS — 유튜브가 -14 근처로 줄여서 틀어요(그만큼 소리가 납작해져요). -14 근처가 좋아요.")
        elif tgt is not None and abs(lufs - tgt) > 2:
            add("warn", "소리 크기", f"{lufs:.1f} LUFS — 목표 {tgt:g} LUFS와 달라요.")
        elif tgt is not None:
            add("ok", "소리 크기", f"{lufs:.1f} LUFS — 목표({tgt:g})대로 맞췄어요.")
        else:
            add("ok", "소리 크기", f"{lufs:.1f} LUFS — 적당해요.")
        if peak is not None and peak > PEAK_MAX:
            add("warn", "소리 깨짐 가능", f"최대 피크 {peak:.1f} dBTP — 유튜브 권장(-1 dBTP 이하)보다 커서 소리가 찢어질 수 있어요.")
    bad = sum(i["lv"] == "bad" for i in items)
    warn = sum(i["lv"] == "warn" for i in items)
    order = {"bad": 0, "warn": 1, "ok": 2}
    items.sort(key=lambda i: order[i["lv"]])
    return {"file": path, "duration": round(dur, 2), "width": w, "height": h, "score": max(0, 100 - bad * 20 - warn * 7),
            "bad": bad, "warn": warn, "items": items}
