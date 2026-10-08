"""올리기 키트: 유튜브에 올릴 제목 후보·설명·챕터·태그를 영상 내용으로 만들고 썸네일을 확인 → 완성본 폴더에 <이름>_올리기.txt/.json.

유튜브 규칙 (넘으면 저장이 안 되거나 무시됨)
- 제목 100자 · 설명 5000자 · 태그 합계 500자 · 해시태그 15개 · 제목·설명에 < > 못 씀
- 챕터: 첫 줄 00:00 · 3개 이상 · 각 10초 이상 (3분 안 되는 영상·쇼츠는 넣지 않음)
- 썸네일: 2MB 이하 · 1280×720 (쇼츠 1080×1920)
"""
import copy
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import core
import editor
import hooks

TITLE_MAX, DESC_MAX, TAGS_MAX, HASHTAG_MAX = 100, 5000, 500, 15
CH_MIN_VIDEO, CH_MIN_LEN, CH_MIN_COUNT = 180, 10, 3
SPLIT_LO, SPLIT_HI, SPLIT_TAIL = 90, 150, 30  # 90~150초마다 나누고, 끝에 30초도 안 남으면 안 나눔
CH_TITLE_MAX = 20
SHORTS_MAX = 180  # 쇼츠는 3분까지
THUMB_MAX = 2 * 1024 * 1024
THUMB_SIZES = {(1280, 720), (1080, 1920)}
IMG_EXTS = (".jpg", ".jpeg", ".png")
STUDIO_URL = "https://studio.youtube.com"
COACH = "최경진 감독"
BASE_TAGS = ["풋살", "풋살사관학교", "최경진 감독", "최경진", "풋살 레슨", "풋살 기술", "풋살 강좌", "축구", "futsal"]
SUFFIX = "_올리기"
_LOCK = threading.RLock()  # 만들기·고친 내용 저장이 겹쳐도 파일이 섞이지 않게

DEFAULT_TEMPLATE = """## 올리기 키트의 설명 틀이에요. 고쳐서 저장하면 다음에 만드는 키트부터 그대로 들어가요.
## '##'로 시작하는 줄은 설명에 들어가지 않아요 (안내용이에요).
## {훅} 영상 내용으로 만든 첫 두 줄 · {챕터} 목차 (3분 넘는 긴 영상만) · {해시태그} 해시태그 · {제목} 고른 제목 · {주제} 주제어
{훅}

▶ 출연
최경진 감독 (풋살사관학교)

{챕터}

{해시태그}
"""

# 문장 앞 군말: 띄어쓰기만 있어도 빼는 말 · '네'(넷)·'예'·'그'(그 공)는 바로 뒤에 쉼표·마침표가 있을 때만 ('네 번째' '그 공을'은 그대로)
_LEAD = ["자 이제", "그래서", "그리고", "그러면", "그럼", "근데", "이제", "여러분", "좋습니다", "좋아요", "오케이", "자", "어", "음", "아"]
_LEAD_RE = re.compile(r"(?:(?:" + "|".join(map(re.escape, _LEAD)) + r")[\s,.~!…]+|(?:네|예|그)\s*[,.~!…]+\s*|그\s+(?=(?:어|음|아)[\s,.~!…]))")
_SECTION = re.compile(r"^\s*(?:\d{1,2}\s*[.)]|\d{1,2}\s*(?:번|단계)|[①-⑳]|(?:첫|두|세|네|다섯)\s?(?:번째|째)|(?:step|part|chapter)\s?\d|#\d)", re.I)
# 편집 메모로 붙인 마커 이름 (챕터로 안 씀 · '방향 바꾸기' '공 빼기' '각 자르기' 같은 기술 이름은 걸리지 않게 좁게)
_MEMO = re.compile(r"TODO|FIXME|BGM|효과음|싱크|메모|삭제|지우기|자막\s?(?:수정|확인)|(?:수정|확인|체크)\s?(?:필요|요망)|(?:확인|체크)$|"
                   r"다시\s?(?:확인|찍|녹음)|^(?:여기|이\s?부분|요기)(?:\s|$)|\?\?|^\W*$", re.I)
_HASHTAG = re.compile(r"(?<![0-9A-Za-z가-힣_&#])#[0-9A-Za-z가-힣_]+")


# ---------- 공통 ----------

def _read_text(p):
    """메모장이 어떤 방식으로 저장했든 읽기 (UTF-8 · BOM · 옛 한글 cp949 · 유니코드 UTF-16)."""
    raw = Path(p).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _write_safe(path, text, encoding="utf-8"):
    """임시 파일에 다 쓴 뒤 바꿔 끼움. 다른 프로그램이 잡고 있어 못 바꾸면 ' (2)' 를 붙인 이름으로 저장."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    for i in range(20):  # Windows: 백신·OneDrive·미리보기가 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            time.sleep(0.05)
    for k in range(2, 50):
        alt = path.with_name(f"{path.stem} ({k}){path.suffix}")
        try:
            os.replace(tmp, alt)
            return alt
        except PermissionError:
            continue
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"{path.name} 파일을 저장하지 못했어요 · 다른 프로그램에서 열려 있으면 닫고 다시 해 주세요")


def stamp(t):
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _mtime(p):
    try:
        return p.stat().st_mtime
    except OSError:  # 그 사이 지워졌거나 잠김
        return 0.0


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _oneline(t, limit=None):
    t = hooks.NO_TITLE_CHARS.sub("", re.sub(r"\s+", " ", str(t or ""))).strip()
    return t[:limit].rstrip() if limit else t


# ---------- 자막 ----------

_SRT_T = r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
_SRT_LINE = re.compile(_SRT_T + r"\s*-->\s*" + _SRT_T)


def _srt_sec(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + float("0." + ms)


def parse_srt(text):
    """SRT 글 → [{"start", "end", "text"}] (번호·꾸밈 표시 무시, 깨진 칸은 건너뜀)."""
    out = []
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.split("\n")
        for k, ln in enumerate(lines):
            m = _SRT_LINE.search(ln)
            if not m:
                continue
            a, b = _srt_sec(*m.groups()[:4]), _srt_sec(*m.groups()[4:])
            txt = " ".join(x.strip() for x in lines[k + 1:] if x.strip())
            txt = re.sub(r"<[^>]*>|\{\\[^}]*\}", "", txt).strip()
            if txt:
                out.append({"start": round(a, 3), "end": round(max(a, b), 3), "text": txt})
            break
    return sorted(out, key=lambda s: s["start"])


def _segments(x):
    """대사 목록 · SRT 글 · SRT 파일 경로 모두 받음."""
    if isinstance(x, Path):
        x = _read_text(x)
    if isinstance(x, str):
        segs = parse_srt(x)
    else:
        segs = []
        for s in x or []:
            a = _num(s.get("start"))
            if a is None:
                continue
            b = _num(s.get("end"))
            segs.append({"start": a, "end": a if b is None else max(a, b), "text": re.sub(r"\s+", " ", str(s.get("text") or "")).strip()})
    return sorted((s for s in segs if s["text"]), key=lambda s: s["start"])


# ---------- 짧게 나눈 자막 → 문장 (#5 단어 단위 자막) ----------

_SENT_END = re.compile(r"(?:[.?!…]|(?:요|다|죠|까|니다|네|자|래)[~.?!]*)[\"'”’)]*$")
SENT_GAP, SENT_MAX = 1.2, 120  # 이 안에 이어지는 말만 · 이은 문장은 이 글자까지


def sentences(segs):
    """읽기 좋게 짧게 나눈 자막(쇼츠형·롱폼형 덩어리)을 다시 문장으로 이음 — 설명 첫 줄·챕터 제목·주제어가 반쪽 문장이 되지 않게.
    앞 자막이 문장 끝(마침표·'~요'·'~다' 등)이 아니고 사이가 1.2초 안이면 이어 붙임 (120글자까지)."""
    out = []
    for s in segs:
        p = out[-1] if out else None
        if (p and not _SENT_END.search(p["text"]) and s["start"] - p["end"] <= SENT_GAP
                and len(p["text"]) + 1 + len(s["text"]) <= SENT_MAX):
            p["text"] += " " + s["text"]
            p["end"] = max(p["end"], s["end"])
        else:
            out.append(dict(s))
    return out


# ---------- 챕터 ----------

def first_sentence(text, limit=CH_TITLE_MAX):
    """챕터 제목: 첫 문장에서 '자, 이제' 같은 군말을 빼고 limit 자 안으로 (낱말 중간에서 자르지 않음)."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.split(r"(?<=[.!?…])\s+", t)[0]
    while True:
        m = _LEAD_RE.match(t)
        if not m or m.end() >= len(t):
            break
        t = t[m.end():]
    t = hooks.NO_TITLE_CHARS.sub("", t).strip(" ,.~…!·-")
    if len(t) > limit:
        cut = t[:limit]
        sp = cut.rfind(" ")
        t = (cut[:sp] if sp >= limit // 2 else cut).rstrip(" ,.~…·-")
    return t


def _title_at(segs, t, k):
    """t 초에 시작하는 챕터 제목 (그 자리 첫 문장 · 인사말이면 '인트로')."""
    near = [s for s in segs if s["start"] >= t - 1.0][:3] or [s for s in segs if s["end"] > t][:1]
    if t == 0 and near and re.search(r"안녕하세요|반갑습니다|안녕하십니까", near[0]["text"]):
        return "인트로"
    for s in near:
        title = first_sentence(s["text"])
        if len(title) >= 2:
            return title
    return "인트로" if t == 0 else f"파트 {k + 1}"


def _auto_cuts(segs, a, b):
    """[a, b) 를 90~150초마다 가장 오래 쉰 곳(다음 말이 시작하는 곳)에서 나눈 시각들."""
    starts, prev_end = [], None
    for s in segs:
        pause = s["start"] - (prev_end if prev_end is not None else 0.0)
        prev_end = s["end"] if prev_end is None else max(prev_end, s["end"])
        if a < s["start"] < b:
            starts.append((s["start"], max(0.0, pause)))
    cuts, cur = [], a
    while b - cur > SPLIT_HI:
        win = [(p, s) for s, p in starts if cur + SPLIT_LO <= s <= min(cur + SPLIT_HI, b - SPLIT_TAIL)]
        if not win:  # 말이 없는 긴 구간(경기 장면 등) → 그 뒤 처음 말이 시작하는 곳
            win = [(p, s) for s, p in starts if s > cur + SPLIT_HI and b - s >= SPLIT_TAIL][:1]
            if not win:
                break
        mid = cur + (SPLIT_LO + SPLIT_HI) / 2
        _, s = max(win, key=lambda x: (round(x[0], 1), -abs(x[1] - mid)))
        cuts.append(s)
        cur = s
    return cuts


def _finalize(points, dur, segs):
    """유튜브 규칙에 맞게: 정수 초 · 첫 챕터 00:00 · 각 10초 이상 (이름 붙인 것 우선)."""
    items = sorted(((int(t), nm, named) for t, nm, named in points), key=lambda x: (x[0], not x[2]))
    if not items or items[0][0] >= CH_MIN_LEN:
        items.insert(0, (0, None, False))
    else:  # 10초 안쪽에서 시작하면 00:00 으로
        items[0] = (0,) + items[0][1:]
    out = []
    for t, nm, named in items:
        if dur - t < CH_MIN_LEN:  # 마지막 챕터도 10초 이상
            continue
        if out and t - out[-1][0] < CH_MIN_LEN:
            if named and not out[-1][2]:  # 바로 앞이 자동으로 나눈 곳이면 이름 붙인 쪽을 남김
                if len(out) == 1:
                    out[0] = (0, nm, True)
                elif t - out[-2][0] >= CH_MIN_LEN:
                    out[-1] = (t, nm, True)
            continue
        out.append((t, nm, named))
    res, seen = [], set()
    for k, (t, nm, named) in enumerate(out):
        title = nm or _title_at(segs, t, k)
        if title in seen:  # 같은 제목이 또 나오면 번호를 붙임
            title = f"{title} {sum(1 for x in res if x['title'].startswith(title)) + 1}"
        seen.add(title)
        res.append({"t": t, "time": stamp(t), "title": title, "named": bool(named)})
    return res


def _clean_name(v, limit=60):
    return _oneline(v, limit)


def chapters(segments_or_srt, markers=None, titles=None, dur=None, notes=None):
    """유튜브 챕터 [{"t", "time", "title", "named"}].
    이름 붙인 편집실 마커가 있으면 그 자리로 (없으면 '1. …'처럼 번호 붙은 그래픽 제목), 없으면 90~150초마다
    가장 오래 쉰 곳에서 나누고 그 자리 첫 문장(20자 이내)을 제목으로. 3분 미만이거나 규칙을 못 맞추면 []."""
    notes = [] if notes is None else notes
    segs = _segments(segments_or_srt)
    dur = _num(dur)
    if dur is None or dur <= 0:
        dur = max((s["end"] for s in segs), default=0.0)
    if dur < CH_MIN_VIDEO:
        notes.append("3분이 안 되는 영상이라 챕터는 넣지 않았어요")
        return []
    named, memo = [], []
    for m in markers or []:
        nm, t = _clean_name((m or {}).get("name")), _num((m or {}).get("t"))
        if nm and t is not None and 0 <= t < dur:
            (memo if _MEMO.search(nm) else named).append((t, nm))
    if memo:
        notes.append("편집 메모처럼 보이는 마커 이름은 챕터에서 뺐어요 · " + ", ".join(f"'{nm}'" for _, nm in memo[:5]))
    from_markers = bool(named)
    if not named:  # 번호 붙은 그래픽 제목('1. 퍼스트 터치')은 장 제목으로 봄
        for x in titles or []:
            nm, t = _clean_name((x or {}).get("text")), _num((x or {}).get("start"))
            if nm and t is not None and 0 <= t < dur and _SECTION.match(nm):
                named.append((t, nm))
    pts = [(t, nm, True) for t, nm in named]
    by_marker = "편집실 마커 이름으로 챕터를 만들었어요 · 메모로 붙인 이름이 있으면 설명에서 고쳐 주세요"
    if pts:
        out = _finalize(pts, dur, segs)
        if len(out) >= CH_MIN_COUNT:
            if from_markers:
                notes.append(by_marker)
            return out
        bounds = sorted({0.0, dur} | {float(int(t)) for t, _ in named})
        extra = [(s, None, False) for a, b in zip(bounds, bounds[1:]) for s in _auto_cuts(segs, a, b)]
        out = _finalize(pts + extra, dur, segs)
        if len(out) >= CH_MIN_COUNT:
            if from_markers:
                notes.append(by_marker)
            notes.append("이름 붙인 마커가 적어서 사이사이 쉬는 곳에서 챕터를 더 나눴어요")
            return out
    elif segs:
        out = _finalize([(0.0, None, False)] + [(s, None, False) for s in _auto_cuts(segs, 0.0, dur)], dur, segs)
        if len(out) >= CH_MIN_COUNT:
            return out
    notes.append("나눌 곳이 마땅치 않아 챕터는 넣지 않았어요 (챕터는 3개 이상, 각 10초 이상이어야 해요)"
                 if segs or pts else "받아쓰기가 없어 챕터를 만들지 못했어요")
    return []


def chapters_text(chs):
    return "\n".join(f"{c['time']} {c['title']}" for c in chs)


# ---------- 설명 · 태그 ----------

def template_path():
    return core.WORK / "upload_template.txt"


def load_template():
    """작업 폴더의 설명 틀 (없으면 기본 틀을 만들어 둠 · 사용자가 메모장으로 고침)."""
    p = template_path()
    if not p.exists():
        try:
            _write_safe(p, DEFAULT_TEMPLATE, "utf-8-sig")
        except (OSError, RuntimeError):
            return DEFAULT_TEMPLATE
    try:
        t = _read_text(p)
    except OSError:
        return DEFAULT_TEMPLATE
    return t if t.strip() else DEFAULT_TEMPLATE


def _sent_score(t):
    return sum(w for k, w in editor.KEYWORDS.items() if k in t) + min(len(t), 60) / 30


def _topic_hook(hook, topics):
    """편집실 쇼츠 훅('국가대표 꿀팁' · editor._hook 은 editor.KEYWORDS 로 주제를 고름)을 키트의 주제어로 다시 씀
    → 제목·해시태그·태그·설명이 같은 주제 ('팬텀 드리블 꿀팁'). 주제어가 풋살 용어가 아니면(자주 나온 낱말) 훅의 말이 풋살 용어일 때만 그대로."""
    if not hook:
        return hook
    tail = "꿀팁" if hook.endswith("꿀팁") else "!"
    word = hook[: -len(tail)].strip()
    terms = {re.sub(r"\s+", "", t) for t in hooks.topic_terms()}
    if topics and re.sub(r"\s+", "", topics[0]) in terms:
        return f"{topics[0]} {tail}" if tail == "꿀팁" else f"{topics[0]}!"
    return hook if re.sub(r"\s+", "", word) in terms else None


def hook_lines(segs, topics, fmt="long", flow=None, dur=None):
    """설명 첫 두 줄: 감독님이 한 말 중 가장 힘 있는 한 문장 + 무엇을 알려 주는지 (flow: 나오는 순서의 주제어).
    쇼츠는 3분까지라 '1분 안에'는 1분 이하일 때만."""
    topic = topics[0] if topics else "풋살"
    best, best_sc = None, 0.0
    for s in segs:
        t = first_sentence(s["text"], 70)
        if not 10 <= len(t) <= 70 or re.search(r"안녕하세요|구독|좋아요|알림", t):
            continue
        sc = _sent_score(t) + sum(2 for tp in topics[:3] if tp.replace(" ", "") in t.replace(" ", ""))
        if sc > best_sc:
            best, best_sc = t, sc
    line1 = f"“{best}”" if best else f"{topic}{hooks.josa(topic, '이')} 늘 제자리라면 이 영상으로 바로잡아 드릴게요."
    if fmt == "shorts":
        line2 = f"{COACH}의 {topic} 꿀팁을 {'1분 안에' if hooks.one_minute(dur) else '짧게'} 담았어요."
    else:
        names = "·".join(flow or topics[:3]) or "풋살 기본기"
        line2 = f"{COACH}이 {names}{hooks.josa(names, '을')} 차근차근 알려 드려요."
    return [line1, line2]


def hashtags(topics, fmt="long"):
    """해시태그 3개 (쇼츠는 맨 앞에 #shorts)."""
    pool = ["풋살", topics[0] if topics else None, "풋살사관학교", "풋살레슨", *topics[1:]]
    out = []
    for p in pool:
        h = "#" + re.sub(r"[^0-9A-Za-z가-힣_]", "", p or "")
        if len(h) > 1 and h not in out:
            out.append(h)
        if len(out) == 3:
            break
    return (["#shorts"] if fmt == "shorts" else []) + out


def tags_len(tags):
    """유튜브 스튜디오가 세는 방식: 띄어쓰기가 있는 태그는 따옴표 2자 + 태그 사이 쉼표."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def split_tags(text):
    return [t for t in (re.sub(r"\s+", " ", x).strip() for x in re.split(r"[,\n]", text or "")) if t]


def make_tags(topics, fmt="long", limit=TAGS_MAX):
    cand = (["shorts", "쇼츠", "풋살 쇼츠"] if fmt == "shorts" else []) + list(topics[:5]) + [f"풋살 {t}" for t in topics[:3]] + BASE_TAGS
    out = []
    for t in cand:
        t = re.sub(r"\s+", " ", re.sub(r"[<>,#\"]", "", t or "")).strip()
        if not t or len(t) > 30 or t.lower() in {x.lower() for x in out} or tags_len(out + [t]) > limit:
            continue
        out.append(t)
    return out


def _limit_hashtags(text, notes):
    found = list(_HASHTAG.finditer(text))
    if len(found) <= HASHTAG_MAX:
        return text
    for m in reversed(found[HASHTAG_MAX:]):
        text = text[:m.start()] + text[m.end():]
    notes.append(f"해시태그가 {HASHTAG_MAX}개를 넘으면 유튜브가 전부 무시해서 앞의 {HASHTAG_MAX}개만 남겼어요")
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def fit_description(text, notes=None):
    """유튜브 설명 규칙: < > 빼기 · 빈 줄 정리 · 해시태그 15개 · 5000자."""
    notes = [] if notes is None else notes
    text = text.replace("\r\n", "\n").replace("<", "〈").replace(">", "〉")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(ln.rstrip() for ln in text.split("\n"))).strip()
    text = _limit_hashtags(text, notes)
    if len(text) > DESC_MAX:
        cut = text[:DESC_MAX]
        text = cut[:cut.rfind("\n")] if cut.rfind("\n") > DESC_MAX * 0.8 else cut
        notes.append(f"설명이 {DESC_MAX}자를 넘어서 뒷부분을 잘랐어요")
    return text


def description(hook, chs, tags_h, title="", topics=(), template=None, notes=None):
    """설명 틀(upload_template.txt)의 {훅} {챕터} {해시태그} {제목} {주제} 를 채움."""
    notes = [] if notes is None else notes
    tpl = load_template() if template is None else template
    tpl = "\n".join(ln for ln in tpl.replace("\r\n", "\n").split("\n") if not ln.lstrip().startswith("##"))
    if chs and "{챕터}" not in tpl:
        tpl = tpl.rstrip() + "\n\n{챕터}"
        notes.append("설명 틀에 {챕터} 자리가 없어 맨 아래에 목차를 붙였어요")
    if tags_h and "{해시태그}" not in tpl:
        tpl = tpl.rstrip() + "\n\n{해시태그}"
    vals = {"{훅}": "\n".join(hook), "{챕터}": ("▶ 목차\n" + chapters_text(chs)) if chs else "", "{해시태그}": " ".join(tags_h),
            "{제목}": title, "{주제}": ", ".join(topics[:3])}
    text = re.sub("|".join(map(re.escape, vals)), lambda m: vals[m[0]], tpl)  # 한 번에 바꿈 (넣은 글 속 {…}는 그대로)
    return fit_description(text, notes)


# ---------- 썸네일 확인 ----------

def image_size(path):
    """PNG·JPG 머리 부분만 읽어 (가로, 세로)."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] != b"\xff\xd8":
                return None
            f.seek(2)
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                while b == b"\xff":
                    b = f.read(1)
                if not b:
                    return None
                mk = b[0]
                if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7:
                    continue
                ln = struct.unpack(">H", f.read(2))[0]
                if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                    f.read(1)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                f.seek(ln - 2, 1)
    except (OSError, struct.error):
        return None


def _images_of(name):
    """썸네일 편집기가 이 영상 이름으로 저장한 이미지들 (이름이 더 긴 다른 영상의 것은 빼고 · 최근 것부터)."""
    pre = core.adir(name).name + "_"
    try:
        longer = {core.adir(v.name).name + "_" for v in core.VIDEOS.iterdir() if core.is_video_file(v.name)}
        files = [p for p in core.OUT.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS and p.name.startswith(pre)]
    except OSError:
        return []
    longer = {o for o in longer if o != pre and o.startswith(pre)}
    files = [p for p in files if not any(p.name.startswith(o) for o in longer)]
    return sorted(files, key=_mtime, reverse=True)


def thumbnail_check(name, fmt="long"):
    """가장 최근에 저장한 썸네일 확인: 2MB 이하 · 1280×720(긴 영상) / 1080×1920(쇼츠)."""
    files = _images_of(name)
    if not files:
        return {"ok": False, "file": None, "problems": ["아직 저장한 썸네일이 없어요 · 아래 '썸네일 만들기'를 눌러 편집기에서 '이미지로 저장'을 눌러 주세요"]}
    sized = [(p, image_size(p)) for p in files[:30]]
    vertical = fmt == "shorts"
    pick = next(((p, s) for p, s in sized if s and (s[1] > s[0]) == vertical), sized[0])
    p, size = pick
    try:
        nbytes = p.stat().st_size
    except OSError:
        return {"ok": False, "file": None, "problems": ["썸네일 파일을 읽지 못했어요 · 다시 확인해 주세요"]}
    probs = []
    if nbytes > THUMB_MAX:
        probs.append(f"파일이 {nbytes / 1048576:.1f}MB예요 · 유튜브 썸네일은 2MB까지만 올라가요 (썸네일 편집기에서 JPG로 다시 저장해 주세요)")
    if not size:
        probs.append("이미지 크기를 읽지 못했어요 · JPG나 PNG로 다시 저장해 주세요")
    else:
        w, h = size
        if (w, h) not in THUMB_SIZES:
            probs.append(f"크기가 {w}×{h}예요 · 유튜브 권장 크기는 1280×720(긴 영상) · 1080×1920(쇼츠)이에요")
        if vertical and w > h:
            probs.append("가로 썸네일이에요 · 쇼츠에는 세로(1080×1920) 썸네일이 어울려요")
        if not vertical and h > w:
            probs.append("세로(쇼츠용) 썸네일이에요 · 긴 영상에는 가로 1280×720 썸네일을 올려 주세요")
    return {"ok": not probs, "file": p.name, "bytes": nbytes, "size_text": f"{nbytes / 1048576:.2f}MB",
            "w": size[0] if size else None, "h": size[1] if size else None, "problems": probs, "mtime": int(_mtime(p))}


# ---------- 프로젝트 (읽기만) ----------

def read_project(name):
    """편집실 프로젝트를 읽기만 함 (고치거나 새로 만들지 않음). 없거나 깨졌으면 None."""
    p = editor._ppath(name)
    for i in range(5):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            break
        except PermissionError:  # Windows: 편집실이 막 저장하는 중
            time.sleep(0.1)
        except (OSError, ValueError):
            return None
    else:
        return None
    if not isinstance(d, dict):
        return None
    seqs = []
    for s in d.get("sequences") or []:
        try:
            seqs.append(editor.migrate_seq(copy.deepcopy(s)))
        except Exception:
            continue
    d["sequences"] = seqs
    return d


def _seq_label(sq):
    """editor.export 와 같은 파일 이름 규칙."""
    fmt = sq.get("format", "shorts")
    return re.sub(r'[\\/:*?"<>|]', "", sq.get("name") or ("쇼츠" if fmt == "shorts" else "롱폼")).strip(" .")


def latest_export(name, sq):
    """이 편집본을 가장 최근에 내보낸 영상 (구간 내보내기는 뺌)."""
    rx = re.compile(rf"{re.escape(core.adir(name).name)}_{re.escape(_seq_label(sq))}(?: \(\d+\))*\.mp4", re.I)
    try:
        hits = [p for p in core.OUT.iterdir() if rx.fullmatch(p.name)]
    except OSError:
        return None
    return max(hits, key=_mtime, default=None)


def _target(name, seq, proj=None):
    """(편집본, 가장 최근에 내보낸 영상) · 원본 영상 그대로면 (None, None)."""
    if not seq:
        return None, None
    proj = read_project(name) if proj is None else proj
    sq = next((s for s in (proj or {}).get("sequences") or [] if s.get("id") == seq), None)
    if sq is None:
        raise LookupError("편집본을 찾지 못했어요 · 편집실에서 지웠다면 다시 골라 주세요")
    return sq, latest_export(name, sq)


def _kit_paths(stem):
    return core.OUT / f"{stem}{SUFFIX}.txt", core.OUT / f"{stem}{SUFFIX}.json"


_KIT_FILE = re.compile(re.escape(SUFFIX) + r"(?: \(\d+\))?\.(json|txt)$")


def _read_kit(p):
    for i in range(5):
        try:
            k = json.loads(Path(p).read_text(encoding="utf-8"))
            return k if isinstance(k, dict) else None
        except PermissionError:  # Windows: 막 저장하는 중
            time.sleep(0.05)
        except (OSError, ValueError):
            return None
    return None


def _owner(kit):
    src = kit.get("source") if isinstance(kit.get("source"), dict) else {}
    return kit.get("name"), src.get("id") or ""


def _find_kit(name, seq):
    """이 영상·편집본으로 저장해 둔 키트 중 가장 최근 것 (파일 이름이 바뀌었어도 안에 적힌 영상·편집본으로 찾음) → (경로, 키트)."""
    pre, want = core.adir(name).name + "_", (name, seq or "")
    try:
        files = [p for p in core.OUT.iterdir() if p.name.startswith(pre) and p.suffix == ".json" and _KIT_FILE.search(p.name)]
    except OSError:
        return None, None
    for p in sorted(files, key=_mtime, reverse=True):
        kit = _read_kit(p)
        if kit is not None and _owner(kit) == want:
            return p, kit
    return None, None


def _kit_stem(name, seq, sq):
    """키트 파일 이름 앞부분: 원본은 '<영상>' · 편집본은 '<영상>_<편집본 이름>'.
    내보낼 때마다 붙는 (2)·(3)과 상관없이 늘 같은 이름 · 다른 영상·편집본이 이미 쓰는 이름이면 (2), (3)…"""
    base = core.adir(name).name + (f"_{_seq_label(sq)}" if sq else "")
    for k in range(1, 100):
        stem = base if k == 1 else f"{base} ({k})"
        js = _kit_paths(stem)[1]
        kit = _read_kit(js) if js.exists() else None
        if kit is None or _owner(kit) == (name, seq or ""):
            return stem
    return base


def sources(name):
    """키트를 만들 수 있는 것: 원본 영상 그대로 + 편집실 편집본 (내보낸 영상이 있으면 표시 · 가장 최근에 내보낸 것을 기본으로)."""
    editor.video_path(name)
    proj = read_project(name)
    out, best, newest = [{"id": "", "label": "원본 영상 그대로", "format": None, "export": None}], "", 0.0
    for s in (proj or {}).get("sequences") or []:
        exp = latest_export(name, s)
        out.append({"id": s.get("id"), "label": s.get("name") or "편집본", "format": "shorts" if s.get("format") == "shorts" else "long",
                    "export": exp.name if exp else None})
        if exp and _mtime(exp) > newest:
            best, newest = s.get("id"), _mtime(exp)
    return {"sources": out, "default": best}


# ---------- 키트 ----------

def _transcript(d):
    tj = d / "transcript.json"
    try:
        return _segments(json.loads(tj.read_text(encoding="utf-8"))) if tj.exists() else []
    except (OSError, ValueError):
        return []


def summary(segs, chs, dur, limit=2500):
    """받아쓰기 요약 (AI 없이): 챕터(없으면 90초)마다 힘 있는 문장 3개."""
    if not segs:
        return "(받아쓰기가 없어요)"
    full = "\n".join(f"({stamp(s['start'])}) {s['text']}" for s in segs)
    if len(full) <= limit:
        return full
    bounds = [c["t"] for c in chs] or list(range(0, int(max(dur, segs[-1]["end"])) + 1, 90))
    parts, seen = [], set()
    for i, a in enumerate(bounds):
        b = bounds[i + 1] if i + 1 < len(bounds) else float("inf")
        inside = []
        for s in segs:  # 같은 말을 되풀이한 문장은 한 번만
            if a <= s["start"] < b and s["text"] not in seen:
                seen.add(s["text"])
                inside.append(s)
        if not inside:
            continue
        top = sorted(sorted(inside, key=lambda s: -_sent_score(s["text"]))[:3], key=lambda s: s["start"])
        head = f"[{stamp(a)}]" + (f" {chs[i]['title']}" if chs else "")
        parts.append(head + "\n" + "\n".join(f"- {s['text']}" for s in top))
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…"


def claude_prompt(kit, segs):
    """Claude 에게 붙여 넣을 질문 (무료 · 사용자가 직접 붙여 넣음)."""
    short = kit["format"] == "shorts"
    m, s = divmod(int(kit["duration"]), 60)
    lines = [f"유튜브 '풋살사관학교' 채널({COACH}의 풋살 레슨)에 영상을 올리려고 해요. 아래 내용을 보고 도와주세요.", "",
             "[영상]", f"- 형식: {'쇼츠 (세로 9:16)' if short else '긴 영상 (가로 16:9)'}", f"- 길이: {m}분 {s:02d}초",
             f"- 주제어: {', '.join(kit['topics']) or '(없음)'}", ""]
    if kit["chapters"]:
        lines += ["[목차]", chapters_text(kit["chapters"]), ""]
    lines += ["[대사 요약 (받아쓰기에서 뽑은 문장)]", summary(segs, kit["chapters"], kit["duration"]), "",
              "[지금 만든 초안]", *[f"- 제목 후보 {i}: {t}" for i, t in enumerate(kit["titles"], 1)],
              f"- 태그: {', '.join(kit['tags'])}", "",
              "[부탁해요]",
              f"1. 클릭하고 싶어지는 제목 5개 — {TITLE_MAX}자 이내, 과장·낚시 없이, < > 기호 없이",
              "2. 설명 첫 두 줄 2가지 — 검색 결과에 보이는 부분이라 핵심을 먼저",
              f"3. 해시태그 3개{' (#shorts 포함해서 4개)' if short else ''}",
              f"4. 태그 15개 — 쉼표로 구분, 다 합쳐 {TAGS_MAX}자 이내",
              "5. 썸네일에 넣을 짧은 문구 3개 — 8자 이내",
              "한국어로, 바로 복사해서 쓸 수 있게 번호만 붙여 답해 주세요."]
    return "\n".join(lines)


def counts(kit):
    return {"title": len(kit.get("title") or ""), "description": len(kit.get("description") or ""), "tags": tags_len(kit.get("tags") or []),
            "hashtags": len(_HASHTAG.findall(kit.get("description") or ""))}


def build_kit(name, seq=None, save=True):
    """올리기 키트 만들기. seq: 편집실 편집본 id (없으면 원본 영상 그대로)."""
    editor.video_path(name)
    d, notes = core.adir(name), []
    proj = read_project(name) if seq else None
    if seq and proj is None:
        raise LookupError("편집실 프로젝트를 읽지 못했어요 · 편집실을 한 번 연 뒤 다시 해 주세요")
    sq, exp = _target(name, seq, proj)
    if sq:
        fmt = "shorts" if sq.get("format") == "shorts" else "long"
        segs = _segments(exp.with_suffix(".srt")) if exp and exp.with_suffix(".srt").exists() else []
        if not segs:  # 내보낸 자막이 없으면 편집본 자막(없으면 받아쓰기)을 편집본 시간으로 옮김
            caps = [c for c in (proj.get("captions") or []) if c.get("text")] or _transcript(d)
            try:
                segs = _segments(editor.timeline_captions(dict(sq, captions=caps))) if caps else []
            except Exception:
                segs = []
            notes.append("내보낸 자막 파일(.srt)이 없어서 편집본 자막으로 만들었어요" if segs else
                         "이 편집본에는 자막이 없어요 · 편집점 찾기를 했는지 확인해 주세요")
        dur = (editor.probe(exp)["duration"] if exp else 0.0) or editor.seq_total(sq)
        markers, overlays = sq.get("markers"), sq.get("titles")
        source = {"id": sq.get("id"), "label": sq.get("name") or "편집본", "export": exp.name if exp else None}
        if not exp:
            notes.append("이 편집본은 아직 내보내지 않았어요 · 편집실에서 내보낸 영상과 함께 올려 주세요")
    else:
        info = editor.media_info(name)
        dur = info["duration"]
        fmt = "shorts" if info["height"] > info["width"] and dur <= SHORTS_MAX else "long"
        srt = d / "subtitles.srt"
        segs = _segments(srt) if srt.exists() else []
        if not segs:
            segs = _transcript(d)
            if segs:
                notes.append("자막 파일(subtitles.srt)이 없어서 받아쓰기 기록(transcript.json)으로 만들었어요")
            else:
                notes.append("아직 받아쓰기가 없어요 · 보관함에서 '편집점 찾기'를 하면 챕터·설명이 영상 내용으로 채워져요")
        markers = overlays = None
        source = {"id": "", "label": "원본 영상 그대로", "export": None}

    segs = sentences(segs)  # 자막 덩어리(반쪽 문장)가 아니라 문장 단위로
    texts = [s["text"] for s in segs]
    topics = hooks.topic_keywords(texts)
    flow = hooks.in_order(topics[:3], texts)
    hook = None
    if sq and fmt == "shorts":  # 쇼츠 편집본의 큰 제목 글자가 곧 훅
        hook = next((_oneline(t.get("text"), 40) for t in overlays or [] if _oneline(t.get("text"))), None)
    if not hook and segs:
        try:
            rec = editor.recommend(name)
            h = editor._hook(rec["shorts"][0]) if rec["shorts"] else ""
            hook = h if h.endswith(("꿀팁", "!") if fmt == "shorts" else "꿀팁") else None  # 첫 문장을 자른 것이면 제목으로 안 씀
            hook = _topic_hook(hook, topics)
        except Exception:
            hook = None
    if fmt == "shorts":
        chs = []
        notes.append("쇼츠는 챕터를 넣지 않아요")
    else:
        chs = chapters(segs, markers, overlays, dur, notes)
    titles = hooks.title_candidates(topics, hook, fmt, flow=flow, dur=dur)
    tags_h = hashtags(topics, fmt)
    kit = {"version": 1, "made": time.strftime("%Y-%m-%d %H:%M"), "name": name, "source": source, "format": fmt,
           "duration": round(float(dur or 0), 1), "topics": topics, "titles": titles, "title": titles[0],
           "chapters": chs, "hashtags": tags_h, "tags": make_tags(topics, fmt)}
    kit["description"] = description(hook_lines(segs, topics, fmt, flow, dur), chs, tags_h, kit["title"], topics, notes=notes)
    kit["thumbnail"] = thumbnail_check(name, fmt)
    kit["prompt"] = claude_prompt(kit, segs)
    kit["notes"] = notes
    kit["counts"] = counts(kit)
    kit["limits"] = {"title": TITLE_MAX, "description": DESC_MAX, "tags": TAGS_MAX, "hashtags": HASHTAG_MAX}
    if save:
        _store(kit, name, seq, sq)
    return kit


def kit_text(kit):
    """사람이 읽는 .txt (메모장으로 열어 복사해도 되게)."""
    c, th = counts(kit), kit.get("thumbnail") or {}
    src = kit["source"]["label"] + (f" · {kit['source']['export']}" if kit["source"].get("export") else "")
    out = ["풋살사관학교 스튜디오 · 올리기 키트", f"영상: {kit['name']} ({src})", f"만든 시각: {kit['made']}", "",
           f"■ 제목 (지금 {c['title']}/{TITLE_MAX}자)", kit["title"], "", "■ 제목 후보",
           *[f"{i}. {t}" for i, t in enumerate(kit["titles"], 1)], "",
           f"■ 설명 (지금 {c['description']}/{DESC_MAX}자)", kit["description"], "",
           f"■ 태그 (지금 {c['tags']}/{TAGS_MAX}자 · 그대로 복사해서 붙여 넣으세요)", ", ".join(kit["tags"]), ""]
    if kit["chapters"]:
        out += ["■ 챕터 (설명에도 들어 있어요)", chapters_text(kit["chapters"]), ""]
    out += ["■ 썸네일", (f"{th['file']} · {th.get('w')}×{th.get('h')} · {th.get('size_text')} · " if th.get("file") else "")
            + ("문제없어요" if th.get("ok") else " / ".join(th.get("problems") or []))]
    if kit.get("notes"):
        out += ["", "■ 알림", *[f"- {n}" for n in kit["notes"]]]
    return "\n".join(out) + "\n"


def save_kit(kit, stem):
    txt, js = _kit_paths(stem)
    kit["counts"] = counts(kit)
    with _LOCK:
        t = _write_safe(txt, kit_text(kit), "utf-8-sig")  # BOM: 옛 메모장에서도 한글이 안 깨지게
        kit["files"] = {"txt": t.name, "json": js.name}
        keep = {k: v for k, v in kit.items() if k != "alerts"}  # 알림(alerts)은 열 때마다 새로 봄
        j = _write_safe(js, json.dumps(keep, ensure_ascii=False, indent=1))
        kit["files"]["json"] = j.name
    return kit


def _store(kit, name, seq, sq):
    """편집본 이름으로 저장 (다시 내보내도 같은 파일). 편집본 이름을 바꿨거나 파일이 잠겨 다른 이름으로 저장됐던
    예전 키트 파일은 새 파일을 저장한 뒤 지움 (같은 키트가 두 개로 보이지 않게)."""
    with _LOCK:
        old, prev = _find_kit(name, seq)
        save_kit(kit, _kit_stem(name, seq, sq))
        if old is None:
            return kit
        now = set(kit["files"].values())
        for f in {old.name, (prev.get("files") or {}).get("txt") or ""} - now:
            p = core.OUT / f
            if f and _KIT_FILE.search(f) and p.name.startswith(core.adir(name).name + "_"):
                try:
                    p.unlink(missing_ok=True)
                except OSError:  # 메모장 등에서 잡고 있으면 그대로 둠 (최근 것을 먼저 읽음)
                    pass
    return kit


def load_kit(name, seq=None):
    """저장해 둔 키트 (썸네일 확인은 새로). 없으면 None.
    키트를 만든 뒤 편집본을 다시 내보냈으면 고친 제목·설명·태그는 그대로 두고 alerts 에 알림."""
    sq, exp = _target(name, seq)
    _, kit = _find_kit(name, seq)
    if kit is None:
        return None
    if not isinstance(kit.get("source"), dict):  # 손으로 고쳐 깨진 파일
        kit["source"] = {"id": seq or "", "label": "원본 영상 그대로", "export": None}
    alerts, src = [], kit["source"]
    if sq:
        src["label"] = sq.get("name") or "편집본"  # 편집실에서 이름을 바꿨으면 새 이름으로
        was = src.get("export")
        if exp and exp.name != was:
            alerts.append(f"키트를 만든 뒤 편집본을 {'다시 ' if was else ''}내보냈어요 ({exp.name}) · 고친 제목·설명·태그는 그대로 두었어요. "
                          "길이나 챕터 자리가 바뀌었으면 '다시 만들기'를 눌러 주세요")
            kit["notes"] = [n for n in kit.get("notes") or [] if "아직 내보내지 않았어요" not in n]
    kit["alerts"] = alerts
    kit["thumbnail"] = thumbnail_check(name, kit.get("format", "long"))
    kit["counts"] = counts(kit)
    return kit


def save_edits(name, seq, title=None, desc=None, tags=None):
    """화면에서 고친 제목·설명·태그를 키트 파일에 저장 (규칙을 넘으면 알림만 · 글은 그대로)."""
    with _LOCK:
        sq, _ = _target(name, seq)
        kit = load_kit(name, seq)
        if kit is None:
            raise LookupError("저장된 키트가 없어요 · '올리기 키트 만들기'를 먼저 눌러 주세요")
        if title is not None:  # 쓴 그대로 저장 (< > 같은 건 화면에서 경고)
            kit["title"] = re.sub(r"\s+", " ", str(title)).strip()
        if desc is not None:
            kit["description"] = str(desc).replace("\r\n", "\n").strip()
        if tags is not None:
            kit["tags"] = split_tags(tags) if isinstance(tags, str) else [str(t).strip() for t in tags if str(t).strip()]
        kit["edited"] = time.strftime("%Y-%m-%d %H:%M")
        return _store(kit, name, seq, sq)


# ---------- 열기 ----------

def reveal(path):
    """파일 위치 열기: Windows 탐색기에서 그 파일을 골라 둔 채로 (없으면 폴더만)."""
    p = Path(os.path.abspath(path))
    if sys.platform == "win32":
        if p.exists():
            subprocess.Popen(f'explorer /select,"{p}"')  # 목록이 아닌 글자 하나로 넘겨야 띄어쓰기·한글 경로가 안 깨짐
        else:
            os.startfile(str(p.parent if p.parent.exists() else core.OUT))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if p.exists() else ["open", str(p.parent)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])


def open_template():
    p = template_path()
    load_template()  # 없으면 기본 틀을 만들어 둠
    if sys.platform == "win32":
        try:
            os.startfile(str(p))  # noqa
        except OSError:
            subprocess.Popen(["notepad.exe", str(p)])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
    return p


def open_studio():
    webbrowser.open(STUDIO_URL)


# ---------- 유튜브에 바로 올리기 (youtube_upload) 가 쓰는 파일 찾기 ----------

def files_for(name, seq=None):
    """올릴 파일 (읽기만): 편집본이면 가장 최근에 내보낸 영상과 그 옆의 .srt · 원본이면 보관함 영상과 analysis/<이름>/subtitles.srt.
    → {"video": 경로 또는 None(아직 안 내보냄), "srt": 경로 또는 None, "srtWhere": "out"|"analysis"|None, "format": 편집본 형식|None, "export": 파일 이름|None}"""
    path = editor.video_path(name)
    sq, exp = _target(name, seq)
    if sq:
        srt = exp.with_suffix(".srt") if exp else None
        ok = bool(srt and srt.is_file())
        return {"video": exp, "srt": srt if ok else None, "srtWhere": "out" if ok else None,
                "format": "shorts" if sq.get("format") == "shorts" else "long", "export": exp.name if exp else None}
    srt = core.adir(name) / "subtitles.srt"
    ok = srt.is_file()
    return {"video": path, "srt": srt if ok else None, "srtWhere": "analysis" if ok else None, "format": None, "export": None}


# ---------- 스튜디오 4단계 카드의 '다음 할 일' (D-075) ----------

def progress(names):
    """영상마다 어디까지 했는지 {seqs, rough, exported, thumb, kit}: 편집본 수 · 가편집이 있음 · 내보낸 편집본 수 ·
    저장한 썸네일 그림 · 만든 올리기 키트 (읽기만 · 완성본 폴더·보관함 목록은 한 번만 읽음 · 이름이 더 긴 다른 영상의 파일은 뺌)."""
    try:
        out_files = [p for p in core.OUT.iterdir() if p.is_file()]
        lib = [v.name for v in core.VIDEOS.iterdir() if core.is_video_file(v.name)]
    except OSError:
        out_files, lib = [], []
    pres = {n: core.adir(n).name + "_" for n in set(lib) | set(names)}
    res = {}
    for n in names:
        pre = pres[n]
        longer = [o for o in set(pres.values()) if o != pre and o.startswith(pre)]
        mine = [p for p in out_files if p.name.startswith(pre) and not any(p.name.startswith(o) for o in longer)]
        seqs = (read_project(n) or {}).get("sequences") or []
        mp4 = [p.name for p in mine if p.suffix.lower() == ".mp4"]
        exported = sum(1 for sq in seqs  # latest_export 와 같은 이름 규칙 (구간 내보내기는 뺌)
                       if any(re.fullmatch(rf"{re.escape(pre)}{re.escape(_seq_label(sq))}(?: \(\d+\))*\.mp4", x, re.I) for x in mp4))
        kit = any(p.suffix == ".json" and _KIT_FILE.search(p.name) and _owner(_read_kit(p) or {})[0] == n for p in mine)
        res[n] = {"seqs": len(seqs), "rough": bool(seqs), "exported": exported,
                  "thumb": any(p.suffix.lower() in IMG_EXTS for p in mine), "kit": kit}
    return res
