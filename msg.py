"""MSG 자동 편집 (D-023): 재미없는 원본 → 예능 자막·효과음·확대·다시 보기·배경음악을 넣은 편집본 후보.

흐름 (모두 PC에서 규칙으로 · AI 사용료 없음):
1. signals(name)  — 원본 신호: 받아쓰기 낱말 · 정리할 곳 · 소리 세기 · 움직임 · 공 차는 소리(순간 큰 소리) · YAMNet 웃음/환호 · 얼굴
                    (분석 폴더의 msg_signals.json 에 남김 · 영상이 그대로면 다시 안 봄)
2. moments(sig)   — 재미 순간: 시범·펀치라인·강조·놀람·성공·실패·숫자 세기·질문·장 나눔·훅·마무리·리액션
3. resolve(...)   — 스타일 다섯 부분(인트로·컷 리듬·자막·재미·음악/소리)을 기본 스타일·배운 스타일·섞은 스타일에서 가져와 합침
4. plan_events    — 스타일·양(담백·보통·듬뿍)에 맞춰 사건을 고름 (간격·동시에 보이는 글자 수·말 자막 자리 피하기 · 시드 고정)
5. compile_seq    — 사건 → 편집실 재료(titles·shapes·items·trans·markers) + seq.msg 기록 (사건마다 만든 재료 id)
build_variants(...) 가 위를 묶어 새 편집본 후보 여러 개를 돌려준다 (사용자 편집본은 건드리지 않음).
"""
import hashlib
import json
import math
import os
import random
import re
import shutil
import threading
import time
from pathlib import Path

import core
import editor
import sfxlib
import takes

SIG_VER = 11
MIX_DIR_NAME = "섞기"           # styles/섞기/<이름>.json — 배운 스타일 목록(list_styles)에 섞이지 않게 따로
DRAFT_NAME = "풋살사관학교 스타일(초안)"
ASPECTS = ("intro", "rhythm", "captions", "fun", "sound")
ASPECT_KO = {"intro": "인트로", "rhythm": "컷 리듬", "captions": "자막", "fun": "재미(MSG)", "sound": "음악·소리"}
INTENSITY = {"담백": 0.45, "보통": 1.0, "듬뿍": 1.7}
SPACING = {"담백": 5.0, "보통": 3.0, "듬뿍": 1.5}    # MSG 사건끼리 최소 간격(초)
LABEL = "재미 요소 찾는 중"
FACE_SAMPLES = 30               # 얼굴은 이만큼의 장면만 봄 (느린 PC)
DEMO_GAP = editor.DEMO_GAP      # 단어 사이가 이보다 길면 말 대신 보여 주는 중
ONSET_DB = 9.0                  # 0.1초 안에 이만큼 커지면 순간 큰 소리 (공 차는 소리·부딪힘)

# ---- 낱말 사전 (말을 듣고 판단) ----
PRAISE = re.compile(r"좋아요|좋습니다|나이스|이거죠|그렇죠|그렇지|들어갔|완벽|잘했|훌륭|오케이|굿")
SUCCESS = re.compile(r"성공|됐다|됐어요|들어갔|골인|나이스|완벽|좋아요")
FAIL = re.compile(r"아깝|놓쳤|실수|안 ?돼|아이고|아이구|빗나|안 ?들어|아쉽|틀렸|망했")
SURPRISE = re.compile(r"(?:^|[\s,.!?])(?:와|우와|와우|대박|헐|미쳤|오오+|어\?!|어머)(?:[\s,.!?~]|$)")
JOKE = re.compile(r"농담|ㅋㅋ|웃기|장난(?:이|입|이에|이고)|제가 원래|저도 .{0,12}못|저도 몰라")
LAUGH_THR, CHEER_THR = 0.12, 0.1   # YAMNet 웃음·환호 점수: 현장 웃음은 말소리에 섞여 낮게 나옴 (말만 있는 곳은 0.01 안팎)
EMPH_MORE = ("무조건", "절대", "생명", "핵심", "중요", "차이", "비밀", "비결", "꼭", "달라", "완벽", "정확", "제일", "가장")  # 앞의 것부터
COUNT = re.compile(r"하나[,\s]+둘[,\s]+셋(?![가-힣])")   # 실제로 세는 말 ('하나, 둘, 셋!') · '셋에 맞춰서'처럼 예고하는 말은 아님
SECTION = re.compile(r"^(?:자[,\s]*)?(?:(?:마지막,?\s*)?(?:첫|두|세|네|다섯|여섯|일곱) ?번째|다음은|다음으로|이번엔|이번에는|그 ?다음|마지막으로|자 이제)")
CLOSING = re.compile(r"오늘은 여기까지|오늘 영상은 여기|감사합니다|구독|다음 시간|다음 영상")
DEMO_W = re.compile(r"보여 ?드릴|시범|한 ?번 (?:해 ?)?볼게요|다시 (?:한 ?번 )?해 ?볼게요|직접 해 ?볼게요|해 ?보겠습니다")
HOOK_Q = re.compile(r"\?|(?<!니)까요|을까|할까|일까|될까|있을까|없을까|왜 ")
ROUTINE_Q = re.compile(r"(해|배워|가|시작해|알아|살펴|만나|들어가|차|연습해|보러 가)\s?(볼까요|봅시다|볼게요|보죠|보겠습니다)|시작할게요|시작합니다")
HOOK_W = re.compile(r"하나면|만 ?알면|달라(집니다|져요)|바뀝니다|바뀌어요|무조건|절대|이것만|핵심|비결|비밀|왜 ")
# 속마음·효과 자막은 감독님을 깎아내리지 않는 정해 둔 문구만 (외모·몸 농담 없음 · 실수를 놀리지 않음)
INNER_TEXTS = {"punchline": ["(머쓱)", "(민망)"], "proud": ["(뿌듯)", "(자신감 뿜뿜)"], "confess": ["(솔직 고백)", "(공감 100%)"], "confident": ["(자신감 뿜뿜)", "(각오 완료)"],
               "fail": ["(아까비…)", "(다음엔 꼭!)"], "question": ["(생각 중…)"], "demo": ["(집중)"], "success": ["(뿌듯)"]}
FX_TEXTS = {"kick": ["뻥!", "슛!"], "goal": ["골인!", "들어갔다!", "꽂혔다!", "깔끔하게 골!"], "success": ["나이스!!", "깔끔!", "완벽!", "굿!"], "surprise": ["두둥!", "오오!"],
            "fail": ["아깝다…", "앗!"]}
CONFESS = re.compile(r"못했|못 했|실수|넘어지|틀렸|헷갈")      # 스스로 낮추는 농담 → 공감 쪽 속마음
CONFIDENT = re.compile(r"안 넘어질|좋네요|자신|문제없|걱정 ?마")
SHOOTING = re.compile(r"슛|슈팅|골대|골인|골키퍼|차서|킥|득점|넣으면|넣는지")  # 이 말이 앞뒤에 있어야 공 소리에 '뻥!'·'골인!


LEAD = re.compile(r"^(?:자|어|음|그|아|네|예|그래서|그리고)[,.\s]+")  # 화면 글자에서 뺄 군말


class MsgError(RuntimeError):
    """화면에 그대로 보여 줄 안내."""


def _np():
    import numpy as np
    return np


def _cancelled():
    return editor.CANCEL.is_set()


def _check():
    if _cancelled():
        raise MsgError("MSG 후보 만들기를 멈췄어요")


def mmss(t):
    t = max(0, int(round(t)))
    return f"{t // 60:02d}:{t % 60:02d}"


# ---------- 0. 받아쓰기 다시 듣기·다듬기 ----------
RELISTEN_LABEL = "말 다시 듣는 중"
ALIGN_VER = 2   # 단어 시각을 소리에 맞추는 규칙 판 (asr.json 'aligned' · 규칙을 고치면 올림 · 2: 뭉개진 낱말·잡음 덩어리까지 늘여 적은 낱말)
ORIG_TRANSCRIPT = "transcript.원본.json"   # MSG 가 다시 듣기 전 받아쓰기 (한 번만 남김)


def ensure_transcript(name, log=print, proofread=False):
    """MSG 앞: 받아쓰기가 예전 방식(v2.0.0 까지 · 말 찾기로 거른 것)이면 지금 방식(core.ASR_VER)으로 한 번 다시 들음 —
    짧은 감탄·작은 말이 빠지고 같은 말이 되풀이되던 받아쓰기는 MSG 의 컷·자막·점수판을 틀리게 함.
    proofread: 사용자가 '클로드로 자막 오타 고치기'를 켰을 때만 클로드로 잘못 들은 글자를 고침 (같은 받아쓰기는 한 번만).
    → 받아쓰기가 바뀌었으면 True (실패하면 예전 받아쓰기로 계속)."""
    d = core.adir(name)
    tr = d / "transcript.json"
    if not tr.exists():
        raise MsgError("받아쓰기가 없어요. 스튜디오의 보관함에서 '편집점 찾기'를 먼저 해 주세요")
    info = core.asr_info(name)
    changed = False
    if int(info.get("v") or 0) < core.ASR_VER:
        bak = d / ORIG_TRANSCRIPT
        if not bak.exists():
            shutil.copy2(tr, bak)
        log("  받아쓰기를 새 방식으로 한 번 더 들어요 (빠진 말·되풀이를 고침 · 처음 받아쓰기는 transcript.원본.json 에 둠)")
        try:
            core.analyze(name, log, info.get("model") or "large-v3-turbo", label=RELISTEN_LABEL)
            changed = True
        except Exception as e:  # noqa: BLE001 — 받아쓰기 모델을 못 쓰는 PC: 예전 받아쓰기로 계속
            log(f"  다시 듣지 못해 예전 받아쓰기로 만들어요 · {str(e)[:120]}")
        _check()
        info = core.asr_info(name)
    if proofread and not info.get("proof"):
        changed = _proofread(name, log, info) or changed
        info = core.asr_info(name)
    if int(info.get("aligned") or 0) < ALIGN_VER:
        changed = _align(name, log, info) or changed
    return changed


def align_to_sound(segs, blobs):
    """받아쓰기 단어 시각을 실제 소리 덩어리에 맞춤 → (고친 구간 목록, 옮긴 낱말 수).
    받아쓰기(whisper)는 단어 시작을 0.1~0.5초 일찍 적거나, 앞의 추임새('자…'·'음…') 소리까지 다음 낱말에 붙여 적음 —
    그대로 자르면 컷 앞에 NG·추임새 끝이 남고, 소리로 자르면 자막에서 낱말이 빠짐.
    · 조용한 틈에서 시작한 낱말은 바로 다음 소리 덩어리 시작으로 (0.6초 안일 때만)
    · 추임새 낱말이 든 덩어리에서 시작한 내용 낱말은 다음 덩어리 시작으로 (추임새 소리를 붙여 적은 것) — 앞 말과 0.3초 넘게 떨어져
      시작한 낱말은 덩어리 전체가 길어도(잡음·공 소리와 이어진 추임새) 낱말이 걸친 부분만 보고 옮김
    · 뭉개진 낱말(한 글자에 0.06초도 안 됨 · '그리고' 0.02초)은 그 낱말이 든 소리 덩어리만큼 늘림 (앞뒤 낱말과 안 겹치게) —
      그대로 두면 그 소리를 낱말 없는 추임새로 보고 잘라 말이 빠짐."""
    if not blobs:
        return segs, 0
    moved = 0
    out = []
    prev_s, prev_e = -1.0, -9.0
    for sg in segs:
        ws = [dict(w) for w in sg.get("words") or []]
        filler_blob = None   # 바로 앞 추임새 낱말이 든 소리 덩어리
        for k, w in enumerate(ws):
            s0, e0 = float(w["s"]), float(w["e"])
            if INTERJ.match(str(w["w"]).strip()):
                filler_blob = max((b for b in blobs if min(b[1], e0) - max(b[0], s0) > 0), key=lambda b: min(b[1], e0) - max(b[0], s0), default=None)
                prev_s, prev_e = s0, e0
                continue
            new_s = s0
            bl = _blob_at(blobs, s0)
            nxt = next((b for b in blobs if b[0] > s0 + 0.03), None)
            if bl is None and nxt is not None and nxt[0] - s0 <= 0.8:  # 조용한 틈에서 시작 → 다음 소리에서
                new_s, bl = nxt[0], nxt
                nxt = next((b for b in blobs if b[0] > nxt[0] + 0.03), None)
            syl = len(re.findall(r"[가-힣]", str(w["w"]))) or 1
            short_part = bl is not None and (bl[1] - bl[0] <= 0.9 or (bl[1] - new_s <= 0.9 and new_s - prev_e >= 0.3))
            if bl is not None and nxt is not None and nxt[0] - bl[1] >= 0.12 and nxt[0] - new_s <= 1.4 and (
                    bl is filler_blob  # 추임새 낱말이 든 덩어리
                    or (e0 >= nxt[0] and short_part and e0 - new_s > 0.15 + 0.2 * syl + 0.5 * (nxt[0] - bl[1]))):  # 앞 덩어리까지 늘여 적은 낱말
                new_s = nxt[0]
            new_s = max(new_s, prev_s + 0.05)
            if new_s > s0 + 0.02:
                w["s"] = round(new_s, 2)
                w["e"] = round(max(e0, new_s + min(0.3, max(0.12, e0 - s0))), 2)
                moved += 1
            elif e0 - s0 < 0.06 * syl:  # 뭉개진 낱말 → 그 소리 덩어리만큼
                hb = _blob_at(blobs, s0 + 0.01)
                nx_s = float(ws[k + 1]["s"]) if k + 1 < len(ws) else 1e9
                if hb is not None:
                    a2 = max(prev_e + 0.02, min(s0, hb[0]))
                    b2 = min(nx_s, max(e0, hb[1]))
                    if b2 - a2 > e0 - s0 + 0.05:
                        w["s"], w["e"] = round(a2, 2), round(b2, 2)
                        moved += 1
            prev_s, prev_e = float(w["s"]), float(w["e"])
            filler_blob = None
        moved += _spread_runs(ws, blobs)
        for a, b in zip(ws, ws[1:]):  # 순서·겹침 정리
            if float(a["e"]) > float(b["s"]):
                a["e"] = round(max(float(a["s"]) + 0.05, float(b["s"])), 2)
        if ws:
            out.append(dict(sg, words=ws, start=round(float(ws[0]["s"]), 2), end=round(max(float(sg["end"]), float(ws[-1]["e"])), 2)))
        else:
            out.append(sg)
    return out, moved


def _syl(w):
    return len(re.findall(r"[가-힣]", str(w))) or 1


def _spread_runs(ws, blobs):
    """뭉개진 낱말이 이어진 묶음('자리에 서 있으면 왜'를 0.2초에 몰아 적음)을 앞 낱말 뒤의 받아 적지 않은 소리로 펼침 (글자 수 비례) →
    옮긴 낱말 수. 앞 낱말이 든 소리 덩어리가 그 낱말 끝보다 0.3초 넘게 이어지고(그 소리가 이 묶음) 묶음이 0.3초 넘게 떨어져 있을 때만
    · 앞 낱말이 추임새('자,')면 그 꼬리가 1초 넘고 묶음이 세 글자 넘을 때만 ('자… 첫 번째'의 '첫'은 그대로)."""
    n = 0
    k = 1
    while k < len(ws):
        w = ws[k]
        if float(w["e"]) - float(w["s"]) >= 0.06 * _syl(w["w"]):
            k += 1
            continue
        j = k
        while j + 1 < len(ws) and float(ws[j + 1]["e"]) - float(ws[j + 1]["s"]) < 0.06 * _syl(ws[j + 1]["w"]):
            j += 1
        p_e = float(ws[k - 1]["e"])
        bl = _blob_at(blobs, p_e - 0.02)
        a, b = p_e + 0.02, float(ws[j]["e"])
        pw = str(ws[k - 1]["w"]).strip()
        interj = INTERJ.match(pw) and (re.search(r"[,.…~]$", pw) or k == 1 or re.search(r"[.?!]$", str(ws[k - 2]["w"]).strip()))
        # 앞 낱말이 추임새('자,')면 이어진 소리는 보통 추임새 꼬리 ('자…' 첫 번째) — 꼬리라기엔 길고(1초 넘게) 묶음이 세 글자 넘을 때만
        tail = (bl[1] - p_e) if bl is not None else 0.0
        if interj and not (tail >= 1.0 and sum(_syl(x["w"]) for x in ws[k:j + 1]) >= 3):
            k = j + 1
            continue
        if bl is not None and bl[1] - p_e >= 0.3 and float(w["s"]) - p_e >= 0.3 \
                and b - a >= 0.12 * sum(_syl(x["w"]) for x in ws[k:j + 1]):
            tot = sum(_syl(x["w"]) for x in ws[k:j + 1])
            t = a
            for x in ws[k:j + 1]:
                d = (b - a) * _syl(x["w"]) / tot
                x["s"], x["e"] = round(t, 2), round(t + d, 2)
                t += d
                n += 1
        k = j + 1
    return n


def _align(name, log, info):
    """받아쓰기 단어 시각을 소리에 맞춰 받아쓰기 파일에 씀 (한 번만 · asr.json 'aligned')."""
    import plan
    tr = core.adir(name) / "transcript.json"
    try:
        segs = json.loads(tr.read_text(encoding="utf-8"))
        wave = plan._audio16(editor.video_path(name))
    except (OSError, ValueError, plan.PlanCancelled):
        return False
    words = _words([x for x in segs if str(x.get("text") or "").strip()])
    new, n = align_to_sound(segs, _blobs(wave, _speech_peak(wave, words)))
    core.write_transcript(name, new, asr=dict(info, aligned=ALIGN_VER))
    if n:
        log(f"  받아쓰기 단어 {n}개의 시각을 실제 소리에 맞췄어요")
    return n > 0


def _title_of(name):
    """영상 파일 이름 → 제목 ('20261007_MSGRAW01_퍼스트 터치 레슨.mp4' → '퍼스트 터치 레슨')."""
    return re.sub(r"^\d{8}_[A-Za-z0-9-]+_", "", Path(name).stem).strip()


def _proofread(name, log, info):
    """클로드로 받아쓰기 오타 고치기 (사용자 계정 · 대사 글만 보냄) → 고쳤으면 True."""
    import captions
    import claude_cli
    import proofread as pr
    if claude_cli.status().get("state") != "ready":
        log("  클로드 로그인이 안 돼 있어 자막 오타 고치기는 건너뛰어요 (스튜디오의 '클로드 계정으로 쓰기')")
        return False
    tr = core.adir(name) / "transcript.json"
    try:
        segs = json.loads(tr.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    core.set_progress(label=LABEL, item=name, pct=3, detail="클로드가 자막 오타를 고치는 중")
    terms = captions.load_dict(core.dict_path())["terms"]
    try:
        new, n, model = pr.proofread(segs, _title_of(name), terms, cancel=editor.CANCEL)
    except claude_cli.ClaudeError as e:
        if e.kind == "cancel":
            raise MsgError("MSG 후보 만들기를 멈췄어요") from None
        log(f"  자막 오타 고치기를 건너뛰었어요 · {e}")
        return False
    except pr.ProofError as e:
        log(f"  {e}")
        return False
    core.write_transcript(name, new, asr=dict(info, proof={"by": "claude", "model": model, "lines": n, "t": time.strftime("%Y-%m-%d %H:%M")}))
    log(f"  클로드로 자막 {n}줄의 오타를 고쳤어요" if n else "  클로드가 본 자막에 고칠 오타가 없었어요")
    return n > 0


# ---------- 1. 신호 ----------

def _sig_file(name):
    return core.adir(name) / "msg_signals.json"


def _file_sig(path):
    s = os.stat(path)
    return [s.st_size, s.st_mtime_ns]


def _words(segs):
    """받아쓰기 → 낱말 [(시작, 끝, 낱말, 구간 번호)] (낱말 시각이 없으면 구간을 글자 수로 나눠 어림)."""
    out = []
    for k, s in enumerate(segs):
        ws = [w for w in s.get("words") or () if str(w.get("w") or "").strip()]
        if ws:
            out += [(float(w["s"]), float(w["e"]), str(w["w"]).strip(), k) for w in ws]
            continue
        toks = str(s.get("text") or "").split()
        a, b = float(s["start"]), float(s["end"])
        tot = sum(len(x) for x in toks) or 1
        t = a
        for x in toks:
            d = (b - a) * len(x) / tot
            out.append((t, t + d, x, k))
            t += d
    return sorted(out)


LINE_GAP = 0.7   # 낱말 사이가 이만큼 비면 다른 문장


def lines_of(segs):
    """받아쓰기 → 문장 [{start, end, text, words}] — 받아쓰기 구간이 여러 문장을 한데 묶어도(작은 모델·빠른 말) 문장 끝·쉼에서 나눔."""
    out = []
    for s in segs:
        ws = [w for w in s.get("words") or () if str(w.get("w") or "").strip()]
        if not ws:
            out.append({"start": float(s["start"]), "end": float(s["end"]), "text": str(s.get("text") or "").strip(), "words": []})
            continue
        cur = []
        for i, w in enumerate(ws):
            cur.append(w)
            nxt = ws[i + 1] if i + 1 < len(ws) else None
            end = re.search(r"[.?!…]$", str(w["w"]).strip()) or nxt is None or float(nxt["s"]) - float(w["e"]) > LINE_GAP
            if end:
                out.append({"start": float(cur[0]["s"]), "end": float(cur[-1]["e"]), "text": " ".join(str(x["w"]).strip() for x in cur),
                            "words": [dict(x) for x in cur]})
                cur = []
    return [x for x in out if x["text"]]


RESTART_GAP = 0.6   # 짧은 말을 끊고 이만큼 안에 같은 말로 다시 시작하면 앞 말은 버림


def _plain_ko(text):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def _latin_junk(text):
    """한국어 받아쓰기에 섞인 영어 찌꺼기 ('functioning.' · 'paced...Pacific...') — 글자의 70% 넘게 로마자이고 한글이 3자 이하."""
    p = _plain_ko(text)
    lat = len(re.findall(r"[A-Za-z]", p))
    return len(p) >= 4 and lat / len(p) > 0.7 and len(p) - lat <= 3


def _split_slates(lines):
    """한 문장 끝에 붙은 촬영용 말('… 옆으로, 아니다, 다시 할게요.' · '… 발목에, 아 잠깐만요.')을 따로 떼어 냄 —
    받아쓰기가 끊긴 말과 슬레이트 말을 한 문장으로 묶으면 NG 테이크를 못 찾음 (떼어 내야 다시 찍은 테이크와 짝이 맞음).
    떼는 자리는 슬레이트·고쳐 말하기·추임새 낱말에서만 ('옆으로'처럼 내용 낱말은 앞 문장에 남김)."""
    ed = editor
    out = []
    for ln in lines:
        ws = ln.get("words") or []
        cut = None
        for k in range(1, len(ws)):
            w0 = ed._norm(str(ws[k]["w"]))
            if not (w0 in ed.FILLERS or takes.FIX_WORDS.fullmatch(w0) or takes.OOPS.fullmatch(w0) or any(rx.match(w0) for rx, _ in takes.SLATE)
                    or w0 in ("다시", "잠깐만요", "잠시만요", "잠깐만", "잠시만")):
                continue
            if takes.is_slate(" ".join(str(w["w"]) for w in ws[k:])):
                cut = k
                break
        if cut is None:
            out.append(ln)
            continue
        for part in (ws[:cut], ws[cut:]):
            out.append({"start": float(part[0]["s"]), "end": float(part[-1]["e"]), "text": " ".join(str(w["w"]).strip() for w in part),
                        "words": [dict(w) for w in part]})
    return out


PRED_END = re.compile(r"(?:요|다|죠|까|네|지|고|서|면|데|며|게|니|라|자|야|어|아|봐|해|돼|줘|군|걸|래|냐|나|구나|거든|는데|지만|은|는|을|를|도)$")


def _fragment(text):
    """서술어 없이 끊긴 짧은 말 조각인지 ('오늘 진짜' · '근데 이거') — 낱말 2개 이하·한글 6자 이하·문장 부호 없음·끝이 서술어·이음말이 아님.
    추임새·숫자 세기·장 나눔 말은 아님."""
    t = str(text or "").strip()
    ws = t.split()
    if not ws or len(ws) > 2 or re.search(r"[.,?!…~]$", t) or len(re.findall(r"[가-힣]", t)) > 6 or not re.search(r"[가-힣]", t):
        return False
    if all(INTERJ.match(w) for w in ws) or COUNT.search(t) or SECTION.search(t) or re.search(r"하나|둘|셋|넷", t):
        return False
    return not PRED_END.search(re.sub(r"[^가-힣]", "", ws[-1]))


def clean_lines(segs, blobs=None):
    """문장으로 나눈 말 + 편집에서 뺄 말 [(a, b, 이유)].
    받아쓰기 구간 단위로는 못 잡던 것: 한 구간에 묶인 말 고쳐 다시 하기('이렇게 패스와 동작. 이렇게 패스와 동시에 …')·
    쉼으로 쪼개진 슬레이트 말('다시' … '해볼게요.')·영어 찌꺼기. 나머지(다시 찍기·추임새·말더듬)는 editor.recommend 가 문장 단위로 봄."""
    lines = _split_slates(lines_of(segs))
    drop = {}
    for k, ln in enumerate(lines):
        if _latin_junk(ln["text"]):
            drop[k] = "알아듣지 못한 말"
    for k in range(len(lines) - 1):
        a, b = lines[k], lines[k + 1]
        if b["start"] - a["end"] > RESTART_GAP:
            continue
        wa, wb = a["text"].split(), b["text"].split()
        same = 0
        while same < min(len(wa), len(wb)) and _plain_ko(wa[same]) == _plain_ko(wb[same]):
            same += 1
        # 앞 두 낱말이 같거나, 첫 낱말이 같고 다음 낱말 첫 글자도 같음('동작' → '동시에') · '패스하고 앞으로.' → '패스하고 옆으로 …' 같은 나란한 설명은 그대로
        slip = same == 1 and len(wa) > 1 and _plain_ko(wa[1])[:1] and _plain_ko(wa[1])[:1] == _plain_ko(wb[1])[:1]
        if 1 <= len(wa) <= 4 and len(wb) > len(wa) and same < len(wa) and (same >= 2 or slip):
            drop.setdefault(k, "고쳐 다시 한 말")
    for k in range(len(lines) - 1):  # 끊긴 말: 서술어 없이 끝난 짧은 말 조각 뒤 쉬었다가 새 문장 ('오늘 진짜' … '물 좀 마시고 할게요.')
        a, b = lines[k], lines[k + 1]
        if _fragment(a["text"]) and 0.7 <= b["start"] - a["end"] <= 3.0 and len(b["text"].split()) >= 3 \
                and not (blobs and (_blob_at(blobs, a["end"] - 0.02) or [0, 0])[1] - a["end"] > 0.3):  # 말소리가 이어지면 낱말 시각이 틀린 것
            drop.setdefault(k, "끊긴 말")
    for k in range(len(lines) - 1):  # 쉼으로 쪼개진 슬레이트 말
        a, b = lines[k], lines[k + 1]
        if b["start"] - a["end"] <= 2.5 and len(_plain_ko(a["text"])) <= 4 and not takes.is_slate(a["text"]) \
                and takes.is_slate(a["text"] + " " + b["text"]):
            drop.setdefault(k, "슬레이트 말")
            drop.setdefault(k + 1, "슬레이트 말")
    junk = [(round(lines[k]["start"], 2), round(lines[k]["end"], 2), why) for k, why in sorted(drop.items())]
    return [ln for k, ln in enumerate(lines) if k not in drop], junk


def _recommend(name, segs, keep_pause=None, sig=None):
    """editor.recommend 를 문장 단위·깨진 말 뺀 받아쓰기로 → 정리 컷(tidy)·군더더기(junk_list).
    sig 에 소리 덩어리(blobs)가 있으면 받아쓰기가 다음 낱말에 붙여 버린 추임새('음…' 뒤 '이 세 가지만')도 뺌."""
    kept, extra = clean_lines(segs, (sig or {}).get("blobs"))
    rec = editor.recommend(name, keep_pause=keep_pause, segs=kept if segs else None)
    if sig and sig.get("onsets") and kept:
        # '다시 해 볼게요' 뒤에 말보다 공 소리(시범)가 먼저 오면 다시 찍기 신호가 아니라 '한 번 더 보여 줄게요' 안내 → 남김
        starts = sorted(float(x["start"]) for x in kept)
        keep_back = []
        for j in rec.get("junk_list") or []:
            if j["why"] != "슬레이트 말":
                continue
            nxt_talk = next((t for t in starts if t > j["b"] + 0.3), 1e9)
            if any(j["b"] <= o <= min(j["b"] + 6.0, nxt_talk) for o in sig["onsets"]):
                keep_back.append(j)
        if keep_back:
            rec["junk_list"] = [j for j in rec["junk_list"] if j not in keep_back]
            rec["tidy"] = keep_cuts(rec["tidy"] + [{"in": max(0.0, j["a"] - 0.15), "out": j["b"] + 0.25} for j in keep_back], [])
    if sig and sig.get("blobs"):
        old = [(j["a"], j["b"]) for j in rec.get("junk_list") or []] + [(a, b) for a, b, _ in extra]
        fill = [f for f in blob_fillers(sig["blobs"], _words(segs), sig.get("onsets") or ())
                if not any(a <= (f[0] + f[1]) / 2 <= b for a, b in old)]
        if fill:
            rec["tidy"] = editor._minus(rec["tidy"], fill)
            extra = extra + fill
    rec["junk_list"] = sorted((rec.get("junk_list") or []) + [{"a": a, "b": b, "why": w} for a, b, w in extra], key=lambda j: (j["a"], -j["b"]))
    return rec


def _onsets(wave, words, sr=16000):
    """순간 큰 소리 (공 차는 소리·부딪힘): 10ms 소리 세기가 0.1초 전보다 ONSET_DB 넘게 커졌다가 0.15초 안에 다시 줄어듦(탁 하고 끝나는 소리)
    · 말하는 중이 아닐 때 (말 첫소리는 이어지므로 빠짐)."""
    np = _np()
    hop = sr // 100
    n = len(wave) // hop
    if n < 20:
        return []
    x = wave[:n * hop].astype(np.float32).reshape(n, hop) / 32768.0
    db = 20 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-6)
    base = np.array([db[max(0, i - 10):i].min() if i else db[0] for i in range(n)])
    hit = np.where((db - base >= ONSET_DB) & (db > -38))[0]
    talk = [(s - 0.25, e + 0.2) for s, e, _, _ in words]
    out, last = [], -9.0
    for i in hit:
        t = i / 100.0
        if t - last < 0.25:
            continue
        pk = float(db[i:i + 4].max())
        tail = db[i + 12:i + 20]
        if len(tail) and float(tail.mean()) > pk - 8.0:  # 소리가 이어짐 (말·음악) → 공 소리 아님
            continue
        if any(a <= t <= b for a, b in talk):
            continue
        out.append(round(float(t), 2))
        last = t
    return out


def _speech_peak(wave, words, sr=16000):
    """말소리 최대 크기(dBFS): 낱말이 들리는 곳의 0.1초 조각 최대값 중 99% 지점 — 효과음을 말보다 조금 작게 맞추는 기준.
    (영상 전체의 최대값은 공 차는 소리·박수가 섞여 말보다 크게 나옴) · 낱말이 없으면 None."""
    np = _np()
    if not len(wave) or not words:
        return None
    hop = sr // 10
    x = np.abs(wave.astype(np.float32)) / 32768.0
    n = len(x) // hop
    if n < 1:
        return None
    pk = x[:n * hop].reshape(n, hop).max(axis=1)
    on = np.zeros(n, bool)
    for s, e, _, _ in words:
        on[max(0, int(s * 10)):min(n, int(math.ceil(e * 10)))] = True
    if not on.any():
        return None
    return round(float(20 * np.log10(np.percentile(pk[on], 99) + 1e-6)), 1)


BLOB_GAP = 0.12      # 소리 덩어리: 이보다 짧은 조용한 틈은 이어진 것으로 봄 (낱말 안의 닫힌 소리·추임새 꼬리)
INTERJ = re.compile(r"^(?:자|어|음|아|에|그|흠|으|엄)(?:[,.…~]|\.\.\.)*$")   # 홀로 쓰인 추임새 낱말 (물음표·느낌표가 붙으면 진짜 감탄이라 뺌)


def _blobs(wave, speech_pk=None, sr=16000):
    """소리 덩어리 [[시작, 끝]] (0.01초 단위 소리 세기가 잡음 바닥 +10dB · 말 최대 -35dB 넘는 곳) — 추임새·컷 가장자리를 소리로 맞추려고."""
    np = _np()
    hop = sr // 100
    n = len(wave) // hop
    if n < 10:
        return []
    x = wave[:n * hop].astype(np.float32).reshape(n, hop) / 32768.0
    db = 20 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-6)
    floor = float(np.percentile(db, 10))
    thr = max(floor + 10.0, (float(speech_pk) - 35.0) if speech_pk is not None else floor + 10.0)
    on = db > thr
    out = []
    i = 0
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j < n and on[j]:
            j += 1
        a, b = i / 100.0, j / 100.0
        if out and a - out[-1][1] < BLOB_GAP:
            out[-1][1] = b
        else:
            out.append([a, b])
        i = j
    return [[round(a, 2), round(b, 2)] for a, b in out if b - a >= 0.05]


def _blob_at(blobs, t):
    return next((b for b in blobs if b[0] - 0.01 <= t <= b[1] + 0.01), None)


def blob_fillers(blobs, words, onsets=()):
    """받아쓰기와 소리 덩어리로 찾는 추임새 [(a, b, '추임새')]: 말이 쉬었다가 시작하는 곳의 짧은 소리 덩어리(0.2~0.9초)가
    바로 다음 말 덩어리와 조용한 틈(0.15초 넘게)으로 떨어져 있고, 그 안에서 끝나는 낱말이 없거나(받아쓰기가 다음 낱말에 붙여 버림)
    추임새 낱말('어…'·'음…'·'자,')뿐일 때. 공 소리(순간 큰 소리)가 든 덩어리는 뺌 ('음…' 같은 콧소리는 소리 모델이 말로 안 들어서 말소리 점수는 안 봄)."""
    out = []
    for k in range(len(blobs) - 1):
        a, b = blobs[k]
        na = blobs[k + 1][0]
        before = a - (blobs[k - 1][1] if k else -9.0)
        if not (0.2 <= b - a <= 0.9 and 0.15 <= na - b <= 1.0 and before >= 0.25):
            continue
        if any(a - 0.05 <= o <= b + 0.05 for o in onsets or ()):
            continue
        # 덩어리에 걸친 낱말: 추임새 낱말이거나, 다음 덩어리까지 이어진 낱말(받아쓰기가 추임새 소리를 다음 낱말 앞에 붙임)만 있어야 함
        # (단어 시각은 align_to_sound 로 소리에 맞춘 뒤라 낱말 시작이 이 덩어리 안이면 그 낱말 소리)
        over = [(s0, e, w) for s0, e, w, _ in words if min(b, e) - max(a, s0) > 0.03]
        if any(not INTERJ.match(w.strip()) and (s0 >= a - 0.05 or e < na - 0.02) for s0, e, w in over):
            continue
        if not any(e >= na - 0.02 for s0, e, w, _ in words):  # 뒤에 말이 이어지는 곳만
            continue
        out.append((round(max(0.0, a - 0.03), 2), round(na - 0.06, 2), "추임새"))
    return out


def _collapsed(w):
    """뭉개진 낱말 (한 글자에 0.06초도 안 됨) — 받아쓰기가 시각을 모르고 몰아 적은 낱말 (s, e, 낱말, …)."""
    return w[1] - w[0] < 0.06 * (len(re.findall(r"[가-힣]", str(w[2]))) or 1)


def snap_edges(cuts, blobs, words, junk):
    """컷 가장자리를 실제 말소리에 맞춤: 말로 시작하는 컷은 첫 낱말 소리 덩어리 바로 앞에서 (앞에 붙은 추임새·숨소리·NG 끝은 뺌) ·
    정리할 곳(NG·군말) 바로 앞에서 끝나는 컷은 마지막 낱말 소리 덩어리 바로 뒤에서."""
    if not blobs or not words:
        return cuts
    out = []
    for c in cuts:
        a, b = float(c["in"]), float(c["out"])
        first = next((w for w in words if a - 0.05 <= w[0] <= a + 0.5), None)
        bl = _blob_at(blobs, a)
        if first is not None and _collapsed(first) and bl is not None and bl[0] < a - 0.1:
            # 뭉개진 낱말(받아쓰기가 '하나, 둘, 셋에'를 0.2초에 몰아 적음)로 시작하는 컷: 진짜 말소리는 더 앞 → 이어진 소리 덩어리 안에서
            # 앞 낱말이 끝난 뒤까지 당김 (정리할 곳·앞 컷은 넘지 않음 · 1.5초까지)
            prev_end = max([w[1] for w in words if w[1] <= first[0] - 0.02] or [-9.0])
            lo = max(bl[0], prev_end + 0.05, a - 1.5, out[-1]["out"] + 0.05 if out else 0.0)
            lo = max([lo] + [float(j[1]) for j in junk if float(j[0]) < a and float(j[1]) > lo])
            if lo < a - 0.05:
                a = lo
        elif first is not None:
            bl = _blob_at(blobs, first[0] + 0.02) or _blob_at(blobs, first[1] - 0.02)
            if bl is not None and a + 0.02 < bl[0] < first[1] and bl[0] - a < 1.5:
                a = max(a, bl[0] - 0.06)
        if any(abs(float(j[0]) - b) < 0.6 or float(j[0]) <= b <= float(j[1]) for j in junk):
            last = next((w for w in reversed(words) if b - 0.6 <= w[1] <= b + 0.05), None)
            if last is not None:
                bl = _blob_at(blobs, last[1] - 0.02)
                if bl is not None and bl[1] + 0.08 < b:
                    b = bl[1] + 0.08
        if b - a >= 0.2:
            out.append(dict(c, **{"in": round(a, 3), "out": round(b, 3)}))
    return out


def _chan_levels(path, segs, words, sr=16000):
    """말 크기 (채널 하나 기준 · dBFS): (말할 때 평균 크기 중앙값, 전체 최대, 말소리 최대) — 효과음·배경음악 레벨의 기준.
    흑백으로 섞은 소리(plan._audio16 · 두 채널을 더함)는 같은 소리가 양쪽에 있는 보통 촬영본에서 3dB 크게 나와
    효과음(스테레오 -3 dBFS)·배경음악을 그만큼 크게 맞추게 됨 → 두 채널을 그대로 받아 채널 기준으로 잼. 못 받으면 None."""
    np = _np()
    try:
        r = core.run_bytes([core.ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", str(sr), "-f", "s16le", "-"])
    except Exception:  # noqa: BLE001
        return None
    raw = r or b""
    if len(raw) < 4 * sr:
        return None
    x = np.frombuffer(raw[:len(raw) // 4 * 4], np.int16).reshape(-1, 2).astype(np.float32) / 32768.0
    pk = (np.abs(x).max(axis=1) * 32768.0).astype(np.float32)
    rm = (np.sqrt((x * x).mean(axis=1)) * 32768.0).astype(np.float32)
    rms_db, _ = _levels(rm, segs, sr)
    peak_db = round(float(20 * np.log10(np.percentile(pk / 32768.0, 99.9) + 1e-6)), 1)
    return rms_db, peak_db, _speech_peak(pk, words, sr)


def _levels(wave, segs, sr=16000):
    """말할 때 소리 크기(dBFS, 중앙값)와 최대 크기 — 효과음·배경음악 레벨을 이 원본에 맞추려고."""
    np = _np()
    if not len(wave):
        return -24.0, -6.0
    x = wave.astype(np.float32) / 32768.0
    vals = []
    for s in segs:
        a, b = int(float(s["start"]) * sr), int(float(s["end"]) * sr)
        if b - a > sr // 4:
            seg = x[a:b]
            vals.append(float(20 * np.log10(np.sqrt((seg * seg).mean()) + 1e-6)))
    rms = float(np.median(vals)) if vals else float(20 * np.log10(np.sqrt((x * x).mean()) + 1e-6))
    pk = float(20 * np.log10(np.percentile(np.abs(x), 99.9) + 1e-6))
    return round(rms, 1), round(pk, 1)


def _tags(path, wave, name):
    """YAMNet 소리 종류 (웃음·환호·음악) · 모델을 못 쓰면 None (웃음 순간은 말로만 찾음)."""
    import avmodels
    import plan
    try:
        ok = avmodels.ensure("audio", item=name, label=LABEL, cancel=_cancelled)
    except Exception:  # noqa: BLE001 — 받기 실패·멈춤: 소리 종류 없이 계속
        ok = False
    if not ok:
        return None

    def prog(pct, detail):
        core.set_progress(label=LABEL, item=name, pct=min(60, 30 + pct // 3), detail="웃음소리·환호 듣는 중")
    try:
        r = plan._audio_tags(wave, prog)
    except plan.PlanCancelled:
        raise MsgError("MSG 후보 만들기를 멈췄어요") from None
    if r is None:
        return None
    return {k: [round(float(v), 2) for v in r[k]] for k in ("laugh", "cheer", "music", "speech")}


def _faces_at(name, ts):
    """장면 몇 개에서 얼굴 [{box, emo}] (모델이 없으면 {})."""
    import face
    import thumb
    try:
        if not face.ensure(item=name, label=LABEL):
            return {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for t in ts[:FACE_SAMPLES]:
        _check()
        try:
            p = thumb.grab(name, t, 640)
            fs = face.faces(p) or []
        except Exception:  # noqa: BLE001 — 장면 하나가 안 돼도 계속
            continue
        out[f"{t:.2f}"] = [{"box": f["box"], "happy": round(f["emo"].get("happiness", 0), 2), "surprise": round(f["emo"].get("surprise", 0), 2)}
                           for f in fs[:2]]
    return out


def signals(name, log=print, use_faces=True):
    """원본 신호 모으기 (캐시: analysis/<영상>/msg_signals.json · 영상 지문·판이 같으면 다시 안 봄)."""
    import plan
    import style
    path = editor.video_path(name)
    fsig = _file_sig(path)
    cf = _sig_file(name)
    tr = core.adir(name) / "transcript.json"
    tsig = _file_sig(tr) if tr.exists() else None
    try:
        old = json.loads(cf.read_text(encoding="utf-8"))
        if old.get("v") == SIG_VER and old.get("sig") == fsig and old.get("tsig") == tsig:
            return old
    except (OSError, ValueError):
        pass
    if not tr.exists():
        raise MsgError("받아쓰기가 없어요. 스튜디오의 보관함에서 '편집점 찾기'를 먼저 해 주세요")
    t0 = time.time()
    log(f"재미 요소 찾기 · {name}")
    core.set_progress(label=LABEL, item=name, pct=1, detail="화면 움직임 살펴보는 중")
    info = editor.media_info(name)
    segs = editor._segments_of(name)
    words = _words(segs)
    try:
        ev = style.extract_events(name, lambda m: None, label=LABEL)
    except style.StyleCancelled:
        raise MsgError("MSG 후보 만들기를 멈췄어요") from None
    _check()
    core.set_progress(label=LABEL, item=name, pct=30, detail="소리 듣는 중")
    try:
        wave = plan._audio16(path)
    except plan.PlanCancelled:
        raise MsgError("MSG 후보 만들기를 멈췄어요") from None
    rms_db, peak_db = _levels(wave, segs)
    speech_pk = _speech_peak(wave, words)
    blobs = _blobs(wave, speech_pk)  # 소리 덩어리는 같은 흑백 소리 기준으로
    lv = _chan_levels(path, segs, words)
    if lv:  # 효과음·배경음악 레벨은 채널 하나 기준 크기로
        rms_db, peak_db, sp = lv
        speech_pk = sp if sp is not None else speech_pk
    tags = _tags(path, wave, name)
    noisy = _spans((tags or {}).get("laugh"), 0.48, LAUGH_THR) + _spans((tags or {}).get("cheer"), 0.48, CHEER_THR)
    noisy += _spans((tags or {}).get("speech"), 0.48, 0.6, 0.3)  # 받아쓰기가 놓친 말(짧은 말·작은 소리)의 첫소리도 공 소리가 아님
    onsets = [t for t in _onsets(wave, words) if not any(a - 0.2 <= t <= b + 0.2 for a, b in noisy)]  # 웃음·환호 소리는 공 소리가 아님
    _check()
    rec = _recommend(name, segs, sig={"blobs": blobs, "onsets": onsets, "tags": tags, "tagStep": 0.48})
    sig = {"v": SIG_VER, "sig": fsig, "tsig": tsig, "name": name, "duration": float(info["duration"]),
           "w": info["width"], "h": info["height"], "fps": info.get("fps", 30.0),
           "motion": ev.get("motion") or [], "motionStep": 1.0 / float(ev.get("fps") or 2), "cuts": ev.get("cuts") or [],
           "onsets": onsets, "dialogDb": rms_db, "peakDb": peak_db, "speechPk": speech_pk, "blobs": blobs, "tags": tags, "tagStep": 0.48,
           "junk": [[j["a"], j["b"], j["why"]] for j in rec.get("junk_list") or []], "tidy": rec.get("tidy") or [],
           "faces": {}}
    sig["demo"] = demo_windows(sig, words)
    sig["faces"] = _faces_at(name, _face_times(sig, segs, words)) if use_faces else {}
    tmp = cf.with_name(cf.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(sig, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cf)
    except OSError:
        tmp.unlink(missing_ok=True)
    log(f"  재미 요소 신호 · 공 차는 소리 {len(onsets)}번 · " + ("웃음·환호 들음" if tags else "소리 모델 없이") + f" · {time.time() - t0:.0f}초")
    return sig


def demo_windows(sig, words):
    """말 없는 틈 가운데 화면이 움직이거나 공 소리가 나는 곳(시범) → 남길 구간 [[a, b]].
    말로만 정리하는 가편집(tidy)은 말 없는 곳을 다 지우지만, 풋살 레슨에서는 시범이 가장 중요한 장면이라 살림.
    움직임도 소리도 없는 쉼(카메라만 켜진 곳)은 그대로 지움."""
    dur = sig["duration"]
    mz = _motion_z(sig)
    junk = sig.get("junk") or []
    voice = _spans((sig.get("tags") or {}).get("speech"), sig.get("tagStep", 0.48), 0.6, 0.3)
    out, prev = [], 0.0
    for s, e, _, _ in sorted(words) + [(dur, dur, "", -1)]:
        if s - prev >= DEMO_GAP:
            a, b = prev + 0.1, s - 0.1
            act = [t for t, z in mz if a <= t <= b and z >= 1.0] + [t for t in sig.get("onsets") or () if a <= t <= b]
            if act and not any(_in_spans((a + b) / 2, [j]) for j in junk):
                lo, hi = max(a, min(act) - 0.8), min(b, max(act) + 1.2)
                for sa, sb in voice:  # 가장자리의 받아쓰지 못한 목소리(추임새 '어…'·작은 말)는 시범이 아님 → 그 앞뒤에서 끊음
                    if sa < hi and lo < sb:  # (소리 모델 칸이 0.48초라 목소리 시작은 0.3초 넉넉히 앞으로)
                        if sb >= hi - 0.1:
                            hi = min(hi, sa - 0.3)
                        elif sa <= lo + 0.1:
                            lo = max(lo, sb + 0.1)
                if hi - lo >= 1.2:
                    out.append([round(float(lo), 2), round(float(hi), 2)])
        prev = max(prev, e)
    return out


def keep_cuts(tidy, demo):
    """말 정리 구간 + 시범 구간을 합친 컷 목록 (겹치거나 0.3초 안으로 붙으면 하나로)."""
    ivs = sorted([(float(c["in"]), float(c["out"])) for c in tidy or []] + [(float(a), float(b)) for a, b in demo or []])
    out = []
    for a, b in ivs:
        if out and a <= out[-1][1] + 0.3:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [{"in": round(a, 3), "out": round(b, 3)} for a, b in out if b - a >= 0.2]


def _face_times(sig, segs, words):
    """얼굴을 볼 장면: 말하는 장면 고르게 12곳 + 웃음·놀람 말 직후."""
    dur = sig["duration"]
    talk = [((float(s["start"]) + float(s["end"])) / 2) for s in segs]
    even = [talk[int(k * (len(talk) - 1) / 11)] for k in range(12)] if len(talk) >= 12 else talk
    hot = [float(s["end"]) + 0.3 for s in segs if JOKE.search(s["text"]) or SURPRISE.search(" " + s["text"] + " ") or SUCCESS.search(s["text"])]
    ts = sorted({round(min(dur - 0.2, max(0.1, t)), 2) for t in even + hot})
    return ts[:FACE_SAMPLES]


# ---------- 2. 재미 순간 ----------

def _in_spans(t, spans):
    return any(a <= t < b for a, b, *_ in spans)


def _kept(t, tidy):
    return any(float(c["in"]) <= t < float(c["out"]) for c in tidy)


def _spans(arr, step, thr, gap=1.0):
    out, a, last = [], None, None
    for i, v in enumerate(arr or ()):
        if v >= thr:
            if a is None:
                a = i
            last = i
        elif a is not None and (i - last) * step > gap:
            out.append((round(a * step, 2), round((last + 1) * step, 2)))
            a = None
    if a is not None:
        out.append((round(a * step, 2), round((last + 1) * step, 2)))
    return out


def _motion_z(sig):
    """움직임 → 강도(중앙값 기준 z) 목록 [(시각, z)]."""
    np = _np()
    m = np.asarray(sig.get("motion") or [0.0], np.float64)
    st = float(sig.get("motionStep") or 0.5)
    med = float(np.median(m))
    mad = float(np.median(np.abs(m - med))) * 1.4826 + 1e-3
    return [(i * st, (float(v) - med) / mad) for i, v in enumerate(m)]


BOUND = re.compile(r"^(?:번째|째|개|번|거|것|수|때|데|건|게|지)(?:\s|$|[.!?,])")
INTENS = ("진짜", "정말", "완전", "제일", "가장", "무조건", "꼭")


def _norm_txt(t):
    return re.sub(r"[\s.,!?~…]+", "", str(t or ""))


def _emph_short(lab, line):
    """강조 글자가 말 한 줄을 통째로 옮긴 것이면(아래 말 자막과 같은 글이 두 번) 핵심 낱말만 ('진짜 핵심!' · '인사이드!')."""
    if len(_norm_txt(lab)) < 0.8 * max(1, len(_norm_txt(line))):
        return lab
    toks = re.findall(r"[가-힣A-Za-z0-9%]+", str(line or ""))
    found = editor._find_terms(toks, editor._emph_terms())
    for w in EMPH_MORE + tuple(editor.EMPH_WORDS):
        for i, tk in enumerate(toks):
            if tk.startswith(w) and w not in INTENS:
                pre = toks[i - 1] if i and toks[i - 1] in INTENS else ""
                head = f"{found[0][2]} " if found and found[0][2] not in (w,) else ""
                # 이름씨(핵심·차이)는 낱말만 · 움직씨·그림씨(달라져요·정확하게)는 낱말 전체 ('달라!' 같은 조각을 만들지 않음)
                word = w if editor._EMPH_TAIL.match(tk[len(w):]) else tk
                return (head + (pre + " " if pre and not head else "") + word + "!").strip()
    if found and len(found[0][2].replace(" ", "")) >= 2:
        return f"{found[0][2]}!"
    return lab


BOUND_N = re.compile(r"^(?:쪽|곳|때|데|것|거|수|번|쯤|만큼|정도|뒤|앞|옆|위|밑|안|밖|사이)(?:[가-힣]{0,3})$")  # 홀로 못 서는 말 (앞 낱말이 있어야 뜻이 됨)


def _emph_clause(txt):
    """기술 이름은 없지만 강조 말(무조건·핵심·생명…)이 든 말 → 그 말부터 서술어까지 짧은 구절 ('터치가 무조건 길어져요!').
    서술어(~요·~다)로 끝나지 않거나 14글자 넘으면 None (말 조각을 띄우지 않게)."""
    toks = re.findall(r"\S+", str(txt or ""))
    for w in EMPH_MORE:
        for i, tk in enumerate(toks):
            if not tk.startswith(w):
                continue
            a = i - 1 if i > 0 and not re.search(r"[,.!?]$", toks[i - 1]) and not LEAD.match(toks[i - 1] + " ") else i
            most = 3
            if BOUND_N.match(toks[a]):  # '쪽으로 정확하게…'처럼 앞 낱말에 기대는 말로 시작하면 그 앞 낱말까지 ('발 쪽으로 정확하게…')
                if a == 0 or re.search(r"[,.!?]$", toks[a - 1]):
                    continue
                a, most = a - 1, 4
            b = i
            while b < len(toks) and b - a <= most and not re.search(r"(요|다|죠)[.!?~]*$", toks[b]):
                b += 1
            if b >= len(toks) or b - a > most:
                continue
            out = re.sub(r"[,.~…]+$", "", " ".join(toks[a:b + 1])).strip()
            if 4 <= len(out.replace(" ", "")) <= 14:
                return (out if out.endswith(("!", "?")) else out + "!"), 1
    return None, 0


def _word_at(words, pat, k):
    """구간 k 안에서 pat 이 처음 나오는 낱말의 시각."""
    for s, e, w, j in words:
        if j == k and re.search(pat, w):
            return s
    return None


def moments(sig, segs=None):
    """재미 순간 목록 [{kind, a, b, t, score(0~1), text, why}] — 정리할 곳(NG·추임새) 안의 것은 뺌."""
    segs = lines_of(segs if segs is not None else editor._segments_of(sig["name"]))
    words = _words(segs)
    tidy = keep_cuts(sig.get("tidy") or [{"in": 0.0, "out": sig["duration"]}], sig.get("demo"))
    junk = sig.get("junk") or []
    dur = sig["duration"]
    out = []

    def add(kind, a, b, t, score, text="", why=""):
        if t is None or _in_spans(t, junk) or not _kept(t, tidy):
            return
        out.append({"kind": kind, "a": round(a, 2), "b": round(b, 2), "t": round(t, 2), "score": float(score), "text": text, "why": why})

    # 시범(플레이): 말 없는 틈(DEMO_GAP 넘게) 안의 움직임 봉우리·순간 큰 소리
    mz = _motion_z(sig)
    onsets = sig.get("onsets") or []
    laughs = _spans((sig.get("tags") or {}).get("laugh"), sig.get("tagStep", 0.48), LAUGH_THR)
    cheers = _spans((sig.get("tags") or {}).get("cheer"), sig.get("tagStep", 0.48), CHEER_THR)
    gaps = []
    prev = 0.0
    for s, e, _, _ in words + [(dur, dur, "", -1)]:
        if s - prev >= DEMO_GAP:
            gaps.append((prev, s))
        prev = max(prev, e)
    for a, b in gaps:
        a2, b2 = a + 0.15, b - 0.1
        ons = [t for t in onsets if a2 <= t <= b2]
        mot = [(t, z) for t, z in mz if a2 <= t <= b2]
        peak = max(mot, key=lambda x: x[1]) if mot else (None, 0.0)
        if not ons and peak[1] < 1.5:
            continue
        t = ons[0] if ons else peak[0]
        if ons:  # 공 소리가 여러 번이면 화면이 가장 크게 움직이는 때의 소리
            zat = lambda o: max([z for tm, z in mot if abs(tm - o) <= 0.75] or [0.0])  # noqa: E731
            t = max(ons, key=lambda o: (zat(o), -o))
        score = max(0.0, peak[1]) * 0.4 + 1.0 * min(3, len(ons))
        after = [w for s, e, w, _ in words if b <= s <= b + 3.0]
        praise = bool(PRAISE.search(" ".join(after)))
        react = any(b - 0.5 <= x <= b + 2.0 for x, _ in laughs + cheers)
        score += (1.5 if praise else 0) + (1.0 if react else 0)
        lo, hi = max(a2, t - 2.5), min(b2, t + 3.5)
        add("play", lo, hi, t, score, "", f"말 없는 {b - a:.1f}초 · 공 소리 {len(ons)}번" + (" · 칭찬" if praise else "") + (" · 반응" if react else ""))
        for o in ons[:6]:
            add("kick", o, o + 0.3, o, 1.0, "", "공 차는 소리")
    # 낱말로 찾는 순간
    first_q = None
    for k, s in enumerate(segs):
        txt = str(s.get("text") or "").strip()
        a, b = float(s["start"]), float(s["end"])
        sp = " " + txt + " "
        lab, sc = editor.emphasis_label(txt)
        if not lab:
            lab, sc = _emph_clause(txt)
        if lab:
            lab = _emph_short(LEAD.sub("", lab).strip() or lab, txt)
        if lab and (BOUND.match(lab) or SECTION.search(txt) or _norm_txt(lab) in ("포인트", "팁")):
            lab = None  # '번째 슈팅.' 같은 잘린 조각 · 장 나눔 말('첫 번째 포인트')은 상황 자막이 맡음 · '포인트!'만은 뜻이 없음
        if lab:
            key = lab.rstrip("!").split()[0]
            t = next((w0 for w0, _, w, j in words if j == k and w.replace(" ", "").startswith(key[:2])), a)
            add("emphasis", a, b, t, 1.0 + sc, lab, "기술 이름·강조 낱말")
        if SURPRISE.search(sp):
            add("surprise", a, b, _word_at(words, SURPRISE.pattern, k) or a, 1.5, txt, "놀람 말")
        if SUCCESS.search(txt):
            add("success", a, b, _word_at(words, SUCCESS.pattern, k) or a, 2.0, txt, "성공 말")
        if FAIL.search(txt):
            add("fail", a, b, _word_at(words, FAIL.pattern, k) or a, 2.0, txt, "아쉬움 말")
        if COUNT.search(txt):
            add("count", a, b, a, 1.0, txt, "숫자 세기")
        if SECTION.search(txt):
            add("section", a, b, a, 1.0, txt, "다음 순서로 넘어감")
        tt = TOTAL.search(txt)
        if tt and a < dur * 0.3:  # 챌린지 전체 횟수 ('다섯 번 차서')
            add("total", a, b, a, 1.0, tt[1], "챌린지 횟수")
        if CLOSING.search(txt) and a > dur * 0.6:
            add("closing", a, b, a, 1.0, txt, "마무리 말")
        if DEMO_W.search(txt):
            add("demo_call", a, b, a, 1.0, txt, "시범 예고")
        if HOOK_Q.search(txt) and not ROUTINE_Q.search(txt) and len(txt.replace(" ", "")) >= 6:
            add("question", a, b, a, 1.5 + (1.0 if HOOK_W.search(txt) else 0.0), txt, "질문")
            if first_q is None and a < dur * 0.3:
                first_q = (a, b, txt)
        if JOKE.search(txt):
            add("punchline", a, b, b - 0.1, 2.0, txt, "농담 말")  # (말 끝 바로 앞 · 정리 컷이 말 끝에서 끝나도 남음)
        for la, lb in laughs:  # 말이 끝나고 1.5초 안에 웃음 → 그 말이 펀치라인
            if 0 <= la - b <= 1.5:
                add("punchline", a, b, b - 0.1, 3.0, txt, "말 끝에 웃음소리")
                break
    # 훅 문장: 앞 30% 안의 첫 질문, 없으면 강한 낱말이 든 말
    if first_q:
        add("hook_line", first_q[0], first_q[1], first_q[0], 3.0, first_q[2], "앞부분 질문")
    else:
        for s in segs:
            if float(s["start"]) < dur * 0.3 and HOOK_W.search(str(s["text"])):
                add("hook_line", float(s["start"]), float(s["end"]), float(s["start"]), 2.0, str(s["text"]).strip(), "앞부분 강한 말")
                break
    # 리액션: 크게 웃거나 놀란 얼굴
    for ts, fs in (sig.get("faces") or {}).items():
        for f in fs[:1]:
            if f["box"][3] >= 0.15 and max(f["happy"], f["surprise"]) >= 0.6:
                t = float(ts)
                add("reaction", t - 0.5, t + 1.0, t, 1.0 + max(f["happy"], f["surprise"]), "", "웃거나 놀란 얼굴")
    # 같은 종류가 겹치면 점수 높은 것만 · 종류마다 점수 0~1 로
    out.sort(key=lambda m: (m["kind"], m["t"]))
    dedup = []
    for m in out:
        if dedup and dedup[-1]["kind"] == m["kind"] and abs(dedup[-1]["t"] - m["t"]) < {"closing": 6.0, "section": 3.0, "question": 3.0}.get(m["kind"], 1.0):
            if m["score"] > dedup[-1]["score"]:
                dedup[-1] = m
            continue
        dedup.append(m)
    by = {}
    for m in dedup:
        by.setdefault(m["kind"], []).append(m)
    for ms in by.values():
        hi = max(m["score"] for m in ms) or 1.0
        for m in ms:
            m["score"] = round(m["score"] / hi, 3)
    return sorted(dedup, key=lambda m: m["t"])


def face_center(sig):
    """말하는 장면의 주인공 얼굴 가운데 (0~1) — 확대할 때 기준점 · 모르면 None."""
    np = _np()
    pts = [(f["box"][0] + f["box"][2] / 2, f["box"][1] + f["box"][3] / 2) for fs in (sig.get("faces") or {}).values() for f in fs[:1] if f["box"][3] >= 0.1]
    if len(pts) < 3:
        return None
    a = np.median(np.array(pts), axis=0)
    return [round(float(a[0]), 3), round(float(a[1]), 3)]


def face_box(sig):
    """말하는 장면의 주인공 얼굴 자리 [x0, y0, x1, y1] (0~1 · 여러 장면의 가운데값) — 화면 글자가 얼굴을 가리지 않게 · 모르면 None."""
    np = _np()
    bs = [f["box"] for fs in (sig.get("faces") or {}).values() for f in fs[:1] if f["box"][3] >= 0.1]
    if len(bs) < 3:
        return None
    x, y, w, h = (float(v) for v in np.median(np.array(bs, np.float64), axis=0))
    return [round(x, 3), round(y, 3), round(x + w, 3), round(y + h, 3)]


# ---------- 3. 스타일 (다섯 부분) ----------
# 사건 1분당 기준 개수 (보통 · 무게 1) — 무게·양(INTENSITY)을 곱해 이 영상의 예산이 됨
BASE_PER_MIN = {"emphasis": 2.0, "situ": 1.0, "inner": 1.0, "fx": 1.5, "punch_zoom": 2.0, "slowmo_replay": 0.67, "freeze": 0.33,
                "shake": 0.5, "sfx": 3.0, "reaction": 1.0}

PRESETS = {
    "담백 레슨형": {
        "desc": "첫 질문으로 시작 → 작은 제목 · 상자 말 자막 + 초록 상자 강조 자막 조금 · 딩동·휙 정도 · 잔잔한 배경음악",
        "intro": {"type": "hook_line", "clips": 1, "teaserSec": 3.0, "titleCard": True, "flash": False, "letterbox": False},
        "rhythm": {"keepPause": 0.5, "splitShot": 0, "curve3": [0, 0, 0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.08},
        "captions": {"on": True, "pos": "bottom", "color": "#FFFFFF", "karaoke": False, "box": True, "emphColor": "#FFFFFF", "emphFont": "Pretendard",
                     "emphBox": "#0F7A3D", "emphEffect": "fade", "situLook": "box", "perMin": {"emphasis": 1.6, "situ": 0.8, "inner": 0.0, "fx": 0.0}},
        "fun": {"punch_zoom": 0.6, "slowmo_replay": 0.5, "freeze": 0.0, "shake": 0.0, "montage": 0.0, "sfx": 0.45, "reaction": 0.3, "replayLook": "plain"},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "잔잔", "lesson": "잔잔", "demo": "경쾌", "outro": "잔잔"}, "duck": -14.0, "bgmDb": -10.0,
                  "palette": "clean"},
    },
    "예능 MSG형": {
        "desc": "명장면 3개 티저 + 번쩍 → 제목 카드 · 여러 색 예능 자막 · 확대·효과음·슬로모 다시 보기·정지 화면 · 분위기 따라 바뀌는 배경음악",
        "intro": {"type": "teaser", "clips": 3, "teaserSec": 4.5, "titleCard": True, "flash": True, "letterbox": False},
        "rhythm": {"keepPause": 0.35, "splitShot": 6.0, "curve3": [4.0, 6.0, 5.0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.12},
        "captions": {"on": True, "pos": "bottom", "color": "#FFFFFF", "karaoke": False, "emphColor": "#FFE14D", "emphFont": "Black Han Sans",
                     "emphEffect": "stamp", "situLook": "box", "capWeight": "Black", "capSize": 62, "capStroke": 9,
                     "perMin": {"emphasis": 2.4, "situ": 1.2, "inner": 1.0, "fx": 1.4}},
        "fun": {"punch_zoom": 1.0, "slowmo_replay": 1.0, "freeze": 1.0, "shake": 1.0, "montage": 1.0, "sfx": 1.0, "reaction": 1.0, "replayLook": "plain"},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "신남", "lesson": "잔잔", "demo": "경쾌", "outro": "신남"}, "duck": -13.0, "bgmDb": -8.0,
                  "palette": "variety"},
    },
    "쇼츠 하이텐션형": {
        "desc": "2.5초마다 컷 + 번갈아 확대 · 가운데 노래방 자막 · 효과 글자 흔들기 · 처음부터 신나는 배경음악",
        "intro": {"type": "teaser", "clips": 2, "teaserSec": 3.0, "titleCard": False, "flash": True, "letterbox": False},
        "rhythm": {"keepPause": 0.25, "splitShot": 2.5, "curve3": [2.0, 2.5, 2.5], "tempo": 0, "zoomEvery": 3.0, "zoomScale": 1.15},
        "captions": {"on": True, "pos": "middle", "color": "#FFFFFF", "karaoke": True, "emphColor": "#FFD400", "emphFont": "Black Han Sans",
                     "emphEffect": "pop", "situLook": "box", "perMin": {"emphasis": 2.0, "situ": 0.8, "inner": 0.8, "fx": 2.2}},
        "fun": {"punch_zoom": 1.3, "slowmo_replay": 0.8, "freeze": 0.6, "shake": 1.5, "montage": 1.0, "sfx": 1.4, "reaction": 1.0, "replayLook": "plain"},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "신남", "lesson": "신남", "demo": "신남", "outro": "신남"}, "duck": -13.0, "bgmDb": -7.0,
                  "palette": "variety"},
    },
    "다큐 감성형": {
        "desc": "제목 화면으로 시작 + 위아래 검은 띠 · 왼쪽 위 상황 자막 · 명장면 슬로모·흑백 정지 · 효과음은 묵직하게 조금 · 감성 배경음악",
        "intro": {"type": "title_first", "clips": 1, "teaserSec": 3.0, "titleCard": True, "flash": False, "letterbox": True},
        "rhythm": {"keepPause": 0.6, "splitShot": 0, "curve3": [0, 0, 0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.06},
        "captions": {"on": True, "pos": "bottom", "color": "#F5F1E8", "karaoke": False, "emphColor": "#FFFFFF", "emphFont": "Do Hyeon",
                     "emphEffect": "fade", "situLook": "plain", "capSize": 52, "capStroke": 5,
                     "perMin": {"emphasis": 1.0, "situ": 1.2, "inner": 0.0, "fx": 0.0}},
        "fun": {"punch_zoom": 0.4, "slowmo_replay": 1.4, "freeze": 1.2, "shake": 0.0, "montage": 0.6, "sfx": 0.35, "reaction": 0.3, "replayLook": "letterbox"},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "감성", "lesson": "감성", "demo": "감성", "outro": "감성"}, "duck": -14.0, "bgmDb": -8.0,
                  "palette": "cinematic"},
    },
}
# 배운 값을 묶어 둘 범위 (이상한 레퍼런스 하나가 터무니없는 양을 만들지 않게) — 기본 스타일 범위의 바깥쪽 조금까지
BANDS = {"keepPause": (0.2, 0.8), "splitShot": (0.0, 12.0), "zoomEvery": (0.0, 20.0), "zoomScale": (1.0, 1.3), "tempo": (0.0, 9.0),
         "perMin": (0.0, 3.0), "weight": (0.0, 1.6), "duck": (-20.0, -12.0), "lufs": (-15.0, -13.0), "bgmDb": (-13.0, -7.0)}
CAP_Y = {"bottom": 0.9, "middle": 0.62, "top": 0.16}


def _clamp(v, band):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return band[0]
    return round(min(band[1], max(band[0], v)), 3) if math.isfinite(v) else band[0]


def _copy(d):
    return json.loads(json.dumps(d, ensure_ascii=False))


def styles_dir():
    import style
    return style.STYLES


def mix_dir():
    return styles_dir() / MIX_DIR_NAME


def _learned(name):
    """배운 스타일 하나 → list_styles 의 한 줄 (없으면 None)."""
    import style
    return next((s for s in style.list_styles() if s["name"] == name), None)


def learned_aspects(st):
    """배운 스타일(list_styles 한 줄) → 다섯 부분 값 (기본 스타일 범위로 묶음)."""
    p = st.get("params") or {}
    pl = st.get("plan") or {}
    base = _copy(PRESETS["예능 MSG형"])
    intro = pl.get("intro") or {}
    it = intro.get("type") if intro.get("type") in ("teaser", "hook_line", "title_first", "greeting", "cold_open") else "cold_open"
    base["intro"] = {"type": it, "clips": 3 if it == "teaser" else 1, "teaserSec": _clamp(intro.get("teaserSec") or 4, (3.0, 6.0)),
                     "titleCard": bool(intro.get("titleCard")) or it == "title_first", "flash": it == "teaser", "letterbox": False}
    every = _clamp(p.get("zoomEvery") or 0, BANDS["zoomEvery"])
    base["rhythm"] = {"keepPause": _clamp(p.get("keepPause") or 0.4, BANDS["keepPause"]), "splitShot": _clamp(p.get("splitShot") or 0, BANDS["splitShot"]),
                      "curve3": [_clamp(x, BANDS["splitShot"]) for x in (p.get("curve3") or [0, 0, 0])][:3],
                      "tempo": _clamp(p.get("tempo") or 0, BANDS["tempo"]), "zoomEvery": every if every >= 2 else 0,
                      "zoomScale": _clamp(p.get("zoomScale") or 1.1, BANDS["zoomScale"])}
    cj = pl.get("captions") or {}
    pm = cj.get("perMin") or {}
    colors = [c for c in (cj.get("colors") or []) if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(c)) and str(c).upper() not in ("#FFFFFF", "#111111", "#000000")]
    sty = cj.get("style") or "speech"
    base["captions"] = {"on": bool(p.get("captions", True)), "pos": p.get("captionPos") if p.get("captionPos") in CAP_Y else "bottom",
                        "color": str(p.get("captionColor") or "#FFFFFF").upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(p.get("captionColor") or "")) else "#FFFFFF",
                        "karaoke": False, "emphColor": colors[0].upper() if colors else "#FFE14D", "emphFont": "Black Han Sans",
                        "emphEffect": "stamp" if sty in ("variety", "mixed") else "pop", "situLook": "box",
                        "perMin": {"emphasis": _clamp(cj.get("keywordPerMin") or pm.get("emph") or (1.0 if sty in ("variety", "mixed") else 0.6), BANDS["perMin"]),
                                   "situ": _clamp(pm.get("situ") or 0.5, BANDS["perMin"]), "inner": _clamp(pm.get("inner") or 0, BANDS["perMin"]),
                                   "fx": _clamp(pm.get("sfx") or 0, BANDS["perMin"])}}
    fun = {x["key"]: float(x.get("perMin") or 0) for x in ((pl.get("fun") or {}).get("items") or []) if isinstance(x, dict) and x.get("key")}
    w = lambda k, base_pm: _clamp(fun.get(k, 0) / base_pm, BANDS["weight"])  # noqa: E731
    base["fun"] = {"punch_zoom": max(w("punch_zoom", 1.0), 0.3), "slowmo_replay": max(w("slowmo_replay", 0.3), w("replay", 0.3)), "freeze": w("freeze", 0.2),
                   "shake": w("shake", 0.3), "montage": 1.0 if fun.get("montage") else 0.0, "sfx": max(w("sfx", 1.5), 0.3),
                   "reaction": w("reaction_cut", 0.5), "replayLook": "plain"}
    snd = _copy(PRESETS["예능 MSG형"]["sound"])
    snd["lufs"] = _clamp(p.get("lufs") if p.get("lufs") is not None else -14, BANDS["lufs"])
    if not intro.get("bgmAtStart"):
        snd["moods"]["intro"] = "잔잔"
    base["sound"] = snd
    base["desc"] = st.get("desc") or ""
    return base


def preset_list():
    return [{"name": k, "desc": v["desc"], "kind": "preset"} for k, v in PRESETS.items()]


def _mix_path(name):
    n = re.sub(r'[\\/:*?"<>|]', "", str(name or "")).strip(" .")
    if not n:
        raise ValueError("이름을 적어 주세요")
    return mix_dir() / f"{n}.json"


def load_mix(name):
    """섞은 스타일 파일 → {name, aspects{부분: {kind, name}}, intensity, history[]} (없으면 기본: 모든 부분 '예능 MSG형')."""
    p = _mix_path(name)
    d = {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    asp = d.get("aspects") if isinstance(d.get("aspects"), dict) else {}
    out = {"name": p.stem, "aspects": {}, "intensity": d.get("intensity") if d.get("intensity") in INTENSITY else "보통",
           "history": [h for h in (d.get("history") or []) if isinstance(h, dict)][-20:], "exists": p.exists()}
    for a in ASPECTS:
        src = asp.get(a) if isinstance(asp.get(a), dict) else {}
        kind, nm = src.get("kind"), str(src.get("name") or "")
        out["aspects"][a] = {"kind": kind, "name": nm} if kind in ("preset", "style") and nm else {"kind": "preset", "name": "예능 MSG형"}
    return out


def save_mix(name, aspects=None, intensity=None, pick=None):
    """섞은 스타일 저장 (임시 파일 → 바꿔 끼우기). pick=(부분, {kind, name}) 이면 그 부분만 바꾸고 '최근 바꾼 것'에 남김."""
    m = load_mix(name)
    if aspects:
        for a in ASPECTS:
            src = aspects.get(a)
            if isinstance(src, dict) and src.get("kind") in ("preset", "style") and src.get("name"):
                m["aspects"][a] = {"kind": src["kind"], "name": str(src["name"])}
    if intensity in INTENSITY:
        m["intensity"] = intensity
    if pick:
        a, src = pick
        if a not in ASPECTS or not isinstance(src, dict) or src.get("kind") not in ("preset", "style") or not src.get("name"):
            raise ValueError("고를 수 없는 부분이에요")
        m["aspects"][a] = {"kind": src["kind"], "name": str(src["name"])}
        m["history"] = (m["history"] + [{"t": time.strftime("%Y-%m-%d %H:%M"), "aspect": a, "source": src["name"]}])[-20:]
    p = _mix_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"v": 1, "name": p.stem, "aspects": m["aspects"], "intensity": m["intensity"], "history": m["history"]}
    tmp = p.with_name(p.name + f".{os.getpid()}_{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    for k in range(10):  # Windows: 백신·OneDrive 가 잠깐 잡고 있으면 조금 뒤 다시
        try:
            os.replace(tmp, p)
            break
        except PermissionError:
            if k == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)
    return load_mix(p.stem)


def list_mixes():
    d = mix_dir()
    if not d.is_dir():
        return []
    return [load_mix(p.stem) for p in sorted(d.glob("*.json"))]


def sources_listing():
    """스타일 고르기 목록: 기본 스타일 4개 · 섞은 스타일 · 배운 스타일 (부분마다 한 줄 설명)."""
    import style
    pre = [{"kind": "preset", "name": k, "desc": v["desc"], "aspects": {a: describe_aspect(a, v[a]) for a in ASPECTS}} for k, v in PRESETS.items()]
    learned = []
    for st in style.list_styles():
        asp = learned_aspects(st)
        learned.append({"kind": "style", "name": st["name"], "desc": st.get("plan_desc") or st.get("desc") or "", "aspects": {a: describe_aspect(a, asp[a]) for a in ASPECTS}})
    mixes = [{"kind": "mix", "name": m["name"], "desc": " · ".join(f"{ASPECT_KO[a]} {m['aspects'][a]['name']}" for a in ASPECTS), "intensity": m["intensity"]}
             for m in list_mixes()]
    return {"presets": pre, "learned": learned, "mixes": mixes, "aspects": ASPECT_KO, "draft": DRAFT_NAME, "intensities": list(INTENSITY)}


def mix_view(name):
    """섞은 스타일 + 부분마다 설명 (원래 스타일이 지워졌으면 그 표시)."""
    m = load_mix(name)
    notes = []
    rows = {}
    for a in ASPECTS:
        val, used = _aspect_from(m["aspects"][a], a, notes)
        rows[a] = {"source": m["aspects"][a], "used": used, "desc": describe_aspect(a, val),
                   "fallback": used != m["aspects"][a]["name"]}
    return {"name": m["name"], "exists": m["exists"], "intensity": m["intensity"], "aspects": rows, "history": m["history"][-5:][::-1], "notes": notes}


def _aspect_from(src, aspect, notes):
    """{kind, name} 의 한 부분 값. 배운 스타일이 지워졌으면 기본 스타일 값 + 알림."""
    kind, nm = (src or {}).get("kind"), (src or {}).get("name")
    if kind == "preset" and nm in PRESETS:
        return _copy(PRESETS[nm][aspect]), nm
    if kind == "style":
        st = _learned(nm)
        if st:
            return learned_aspects(st)[aspect], nm
        notes.append(f"{ASPECT_KO[aspect]}: '{nm}' 스타일이 지워져 기본값(예능 MSG형)으로 했어요")
    return _copy(PRESETS["예능 MSG형"][aspect]), "예능 MSG형"


def resolve(spec):
    """후보 스타일 {kind: preset|style|mix, name} → 다섯 부분을 합친 MSG 스타일."""
    kind, nm = spec.get("kind"), str(spec.get("name") or "")
    notes = []
    if kind == "mix":
        m = load_mix(nm)
        out = {"label": m["name"], "kind": "mix", "sources": {}, "notes": notes}
        for a in ASPECTS:
            out[a], out["sources"][a] = _aspect_from(m["aspects"][a], a, notes)
        return out
    if kind == "style":
        st = _learned(nm)
        if not st:
            raise MsgError(f"'{nm}' 스타일을 찾지 못했어요")
        asp = learned_aspects(st)
        return dict({a: asp[a] for a in ASPECTS}, label=nm, kind="style", sources={a: nm for a in ASPECTS}, notes=notes)
    if nm not in PRESETS:
        raise MsgError(f"'{nm}' 스타일을 찾지 못했어요")
    return dict({a: _copy(PRESETS[nm][a]) for a in ASPECTS}, label=nm, kind="preset", sources={a: nm for a in ASPECTS}, notes=notes)


def describe_aspect(aspect, val):
    """부분 값 → 한 줄 설명 (스타일 섞기 화면)."""
    if aspect == "intro":
        t = {"teaser": f"명장면 {val.get('clips', 3)}개 미리 보기", "hook_line": "첫 질문(훅)으로 시작", "title_first": "제목 화면으로 시작",
             "greeting": "인사로 시작", "cold_open": "바로 본론"}.get(val.get("type"), "바로 본론")
        return t + (" → 제목 카드" if val.get("titleCard") else "") + (" · 번쩍 전환" if val.get("flash") else "") + (" · 위아래 검은 띠" if val.get("letterbox") else "")
    if aspect == "rhythm":
        s = f"말 사이 {val.get('keepPause', 0.4)}초 넘게 쉬면 자름"
        if val.get("splitShot"):
            s += f" · 긴 말은 {val['splitShot']}초마다 나눠 번갈아 확대"
        if val.get("zoomEvery"):
            s += f" · {val['zoomEvery']}초마다 확대 컷"
        return s
    if aspect == "captions":
        pm = val.get("perMin") or {}
        parts = [f"강조 1분 {pm.get('emphasis', 0):g}개"]
        if pm.get("situ"):
            parts.append(f"상황 {pm['situ']:g}")
        if pm.get("inner"):
            parts.append(f"속마음 {pm['inner']:g}")
        if pm.get("fx"):
            parts.append(f"효과 글자 {pm['fx']:g}")
        return ("노래방 말 자막 · " if val.get("karaoke") else f"{'상자 ' if val.get('box') else ''}말 자막 {({'bottom': '아래', 'middle': '가운데', 'top': '위'}).get(val.get('pos'), '아래')} · ") + " · ".join(parts)
    if aspect == "fun":
        names = {"punch_zoom": "확대", "slowmo_replay": "슬로모 다시 보기", "freeze": "정지 화면", "shake": "흔들기", "montage": "몽타주", "sfx": "효과음"}
        on = [f"{v}" + ("↑" if float(val.get(k) or 0) >= 1.2 else "") for k, v in names.items() if float(val.get(k) or 0) >= 0.3]
        return " · ".join(on) or "재미 요소 거의 없음"
    m = val.get("moods") or {}
    return (f"배경음악 {m.get('intro', '')}→{m.get('lesson', '')}·{m.get('demo', '')}" if val.get("bgm") else "배경음악 없음") + \
        f" · 말할 때 {abs(int(val.get('duck', -14)))}dB 줄임 · 효과음 {({'clean': '깔끔', 'variety': '예능', 'cinematic': '묵직'}).get(val.get('palette'), '예능')}"


# ---------- 4. 사건 고르기 ----------
SFX_PALETTE = {  # 사건 → 효과음 (없으면 소리 없음)
    "clean": {"success": "딩동", "section": "딸깍", "replay": "휙", "title": "짠", "count": "틱", "question": "물음표", "teaser": "휙", "emphasis": "팡",
              "freeze": "찰칵", "montage": "휙", "end": "맑은 짧은 음악"},
    "variety": {"success": "레벨업", "fail": "띠로리", "surprise": "두둥", "punchline": "바둠츠", "kick": "뻥", "emphasis": "뽁", "question": "물음표",
                "section": "휙", "count": "틱", "freeze": "찰칵", "replay": "휙", "title": "짠", "teaser": "휙", "inner": "띠용", "montage": "휙",
                "end": "경쾌 짧은 음악"},
    "cinematic": {"kick": "퍽", "replay": "라이저", "freeze": "찰칵", "title": "쿵", "success": "맑은 짧은 음악", "teaser": "휙", "section": "딸깍",
                  "end": "맑은 짧은 음악"},
}
CLEAN_ONLY = {"휙", "딩동", "짠", "틱", "딸깍", "팡", "맑은 짧은 음악", "찰칵", "물음표"}   # 담백: 이 소리들만
TEXT_KINDS = ("emphasis", "situ", "inner", "fx", "count", "score")
ORD = {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7}
TOTAL = re.compile(r"(다섯|열|세|네|여섯|일곱|여덟|아홉|[3-9]|10) ?(?:번|개|회) ?(?:차|도전|던지|해 ?보|시도|슛|슈팅)")


def _seed(*parts):
    return int(hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:8], 16)


def _situ_text(m, ctx=""):
    """상황 자막 글: 말한 이름 그대로 ('두 번째 동작' → 동작 · '두 번째 슛' → 슛) · 이름 없이 '첫 번째 갑니다' 면 앞뒤 말로 도전/포인트."""
    txt = m.get("text") or ""
    mm = re.search(r"(첫|두|세|네|다섯|여섯|일곱) ?번째\s*([가-힣A-Za-z]+)?", txt)
    if mm:
        noun = re.sub(r"(은|는|이|가|을|를|도|부터|에서|으로|로)$", "", mm[2] or "")
        if not noun or len(noun) > 4 or re.search(r"(다|요|죠|게|고|서|면)$", noun):
            noun = "도전" if re.search(r"갑니다|가볼게요|갈게요|차|슛|슈팅", txt) or SHOOTING.search(ctx) else "포인트"
        return ("마지막! " if re.search(r"마지막", txt) else "") + f"{mm[1]} 번째 {noun}"
    if re.search(r"마지막", txt):
        return "마지막 포인트"
    if m["kind"] == "demo_call":
        return "다시 도전" if re.search(r"다시|한 ?번 더", txt) else "시범 들어갑니다"
    if m["kind"] == "play":
        return "실전 시범"
    return "다음 순서"


def _scoreboard(moms):
    """챌린지(첫 번째 슛·두 번째 슛…) → 시도마다 결과(성공·실패 말) 뒤 '2번째 · 1골' 점수판 (시도가 3번 넘을 때만).
    전체 횟수를 말했으면('다섯 번 차서') '2/5 · 1골'."""
    tries = []
    for m in moms:
        if m["kind"] != "section":
            continue
        mm = re.search(r"(첫|두|세|네|다섯|여섯|일곱) ?번째", m.get("text") or "")
        if mm:
            tries.append((m["t"], ORD[mm[1]]))
        elif re.search(r"마지막", m.get("text") or "") and tries:
            tries.append((m["t"], tries[-1][1] + 1))
    if len(tries) < 3:
        return []
    total = None
    x = next((m["text"] for m in moms if m["kind"] == "total"), None)
    if x:
        total = {"다섯": 5, "열": 10, "세": 3, "네": 4, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}.get(x) or int(x)
    res = sorted([m for m in moms if m["kind"] in ("success", "fail")], key=lambda m: m["t"])
    first = {}
    for t, n in tries:  # 같은 시도를 두 번 말함 (받아쓰기 반복) → 처음 것만
        first.setdefault(n, t)
    ann = sorted((t, n) for n, t in first.items())
    got, last, guessed = {}, None, set()
    for r in res:
        before = [(t, n) for t, n in ann if t < r["t"]]
        if not before:
            continue
        t0, n0 = before[-1]
        said = re.search(r"(첫|두|세|네|다섯|여섯|일곱) ?번째", r.get("text") or "")
        if said:  # 결과 말에 번호가 있으면 그 시도 ('네 번째 빗나갔어요' 를 두 번 말해도 한 번만)
            if ORD[said[1]] in got:
                continue
            n0, t0 = ORD[said[1]], r["t"]
        if n0 not in got and r["t"] - t0 < 15.0:
            n = n0
        else:
            # 말로 안 알린 시도 ('세 번째'를 받아쓰기가 놓침 · 마지막 시도를 '하나 둘 셋'으로만): 앞 결과와 4초 넘게 떨어진 새 결과만,
            # 뒤에 알린 번호 사이 빈자리이거나 전체 횟수 안에서
            if last is None or r["t"] - last["t"] < 4.0:
                continue
            n = max(got) + 1
            later = [m for t, m in ann if t > r["t"]]
            if not ((later and n < min(later)) or (not later and total and n <= total)):
                continue
            guessed.add(n)
        got[n] = r
        last = r
    out, goals = [], 0
    for n in sorted(got):
        if n != len(out) + 1:  # 결과를 모르는 시도가 끼면 골 수가 틀릴 수 있어 거기서 멈춤 (틀린 점수보다 없는 게 나음)
            break
        r = got[n]
        goals += r["kind"] == "success"
        txt = (f"{n}/{total}" if total and n <= total else f"{n}번째") + f" · {goals}골"
        # 결과 말과 같은 때 (효과 글자와 함께 바뀜 · 따로 띄우면 화면이 그만큼 더 바쁨)
        out.append({"kind": "score", "t": round(r["t"], 2), "a": r["a"], "b": r["b"], "score": 1.0, "text": txt, "why": "챌린지 점수판", "n": n,
                    "guessed": n in guessed, "total": total})
    return out


ORD_KO = {v: k for k, v in ORD.items()}


def _unsaid_tries(board, plays):
    """받아쓰기가 놓친 시도 알림('세 번째' 말이 안 들림) → 그 시도 시범 장면 앞에 '세 번째 도전' 상황 자막 [(시범, 글자)] —
    판정: 알림 말 없이 점수판만 바뀌면 갑자기 나온 것처럼 보임. 결과 6초 안에 끝나는 시범이 있을 때만."""
    out = []
    for c in board:
        if not c.get("guessed") or c["n"] not in ORD_KO:
            continue
        p = max([p for p in plays if p["a"] < c["t"] and c["t"] - p["b"] <= 6.0], key=lambda p: p["a"], default=None)
        if p is not None:
            last = c.get("total") and c["n"] == c["total"]
            out.append((p, ("마지막! " if last else "") + f"{ORD_KO[c['n']]} 번째 도전"))
    return out


def caption_y(st, fmt):
    pos = st["captions"].get("pos") or "bottom"
    if st["captions"].get("karaoke"):
        return 0.62 if fmt == "long" else 0.6
    return {"bottom": 0.9 if fmt == "long" else 0.8, "middle": 0.62 if fmt == "long" else 0.6, "top": 0.16}[pos]


def text_looks(st, fmt, cap_y):
    """사건별 글자 모양 (위치는 말 자막 자리에서 0.15 넘게 떨어뜨림)."""
    c = st["captions"]
    sh = fmt == "shorts"
    low = cap_y < 0.5  # 말 자막이 위쪽이면 MSG 글자는 아래쪽으로
    T = editor.TITLE_STYLE
    emph = dict(T, font=c.get("emphFont") or "Black Han Sans", weight="Black", size=110 if sh else 96, fill=c.get("emphColor") or "#FFE14D",
                stroke="#111111", strokeW=9, y=(0.66 if low else 0.3) if not sh else (0.66 if low else 0.36), x=0.5, align="center",
                effect=c.get("emphEffect") or "stamp")
    if c.get("emphBox"):  # 깔끔한 레슨: 색 상자 위 흰 글씨 (예능 노란 글씨와 다른 모양)
        emph.update(bgOn=True, bg=c["emphBox"], bgOpacity=0.92, stroke=c["emphBox"], strokeW=14, size=84 if not sh else 96)
    if c.get("situLook") == "plain":  # 다큐: 흰 상자에 검은 글씨 (차분하게)
        situ = dict(T, weight="Bold", size=58 if sh else 50, fill="#111111", stroke="#FFFFFF", strokeW=10, bgOn=True, bg="#FFFFFF", bgOpacity=0.85,
                    x=0.06, y=(0.86 if low else 0.14) if not sh else 0.18, align="left", effect="fade", font="Do Hyeon")
    else:
        situ = dict(T, weight="Bold", size=58 if sh else 54, fill="#FFFFFF", stroke="#000000", strokeW=10, bgOn=True, bg="#0F7A3D", bgOpacity=0.9,
                    x=0.06, y=(0.86 if low else 0.14) if not sh else 0.18, align="left", effect="slide")
    inner = dict(T, font="Do Hyeon", weight="Bold", size=70 if sh else 62, fill="#9AD7FF", stroke="#000000", strokeW=6, x=0.74 if not sh else 0.7,
                 y=0.45 if not low else 0.6, align="center", effect="fade", rot=-4)
    fx = dict(T, font="Black Han Sans", weight="Black", size=150 if sh else 132, fill="#FF9F1C", stroke="#111111", strokeW=10, x=0.5,
              y=(0.5 if not low else 0.6) if not sh else 0.48, align="center", effect="shake", rot=0)
    score = dict(situ, bgOn=True, bg="#111111", bgOpacity=0.75, fill="#FFE14D", x=0.94, y=0.14 if not low else 0.86, align="right", effect="pop",
                 font="Black Han Sans", weight="Black", size=56 if not sh else 60)
    if sh:
        score.update(y=0.27)
    return {"emphasis": emph, "situ": situ, "inner": inner, "fx": fx, "score": score,
            "count": dict(fx, fill="#FFFFFF", size=140 if sh else 120, effect="stamp")}


# ---------- 양(담백·보통·듬뿍)에 맞춘 빠르기 ----------
# 화면이 바뀌는 사건(점프 컷·확대가 바뀜·끼워 넣은 장면·글자·도형이 나타남)의 1분 개수 범위와, 아무것도 안 바뀌는 가장 긴 시간(초).
# 담백 = 깔끔한 레슨(긴 쉼만 자르고 글자는 꼭 필요한 것만) · 보통 = 적당히 · 듬뿍 = 예능처럼 자주.
DENSITY = {"담백": (3.3, 5.8, 14.6), "보통": (6.5, 11.3, 9.85), "듬뿍": (10.8, 18.8, 5.85)}
# 효과음 + 배경음악 곡 수의 1분 개수 범위 (스타일의 효과음 무게로 범위 안 자리를 정함)
AUDIO_DENSITY = {"담백": (1.2, 2.8), "보통": (3.3, 6.6), "듬뿍": (6.3, 11.5)}
PACE = {"담백": {"cuts": 1.0, "rhythm": 0.0}, "보통": {"cuts": 1.5, "rhythm": 0.0}, "듬뿍": {"cuts": 2.5, "rhythm": 0.0}}
PAUSE_MUST = 2.5     # 이보다 긴 쉼은 양과 상관없이 자름


def pace_pauses(cuts, junk, cuts_per_min, blobs=()):
    """말 정리 컷 사이의 쉼 중 자를 곳 고르기 — NG·군말이 든 쉼과 PAUSE_MUST 넘는 쉼은 꼭 자르고, 나머지는 긴 쉼부터
    1분에 cuts_per_min 곳까지만 (점프 컷이 너무 잦으면 담백한 레슨이 산만해짐) · 안 자른 쉼은 그대로 이어 붙임.
    blobs(소리 덩어리)가 쉼 전체를 덮으면 쉼이 아님 (받아쓰기가 낱말 시각을 뭉개 생긴 가짜 틈 · '하나, 둘, 셋에'를 0.2초에 몰아 적음) →
    NG·군말이 없으면 자르지 않음 (자르면 말 중간이 잘림)."""
    if len(cuts) < 2:
        return [dict(c) for c in cuts]
    minutes = max(0.5, sum(float(c["out"]) - float(c["in"]) for c in cuts) / 60.0)
    gaps = []
    for k in range(len(cuts) - 1):
        a, b = float(cuts[k]["out"]), float(cuts[k + 1]["in"])
        jk = any(float(j[0]) < b + 0.05 and a - 0.05 < float(j[1]) for j in junk)
        if not jk and b - a > 0.1 and any(x[0] <= a + 0.05 and b - 0.05 <= x[1] for x in blobs or ()):
            gaps.append((k, b - a, None))  # 소리가 이어짐 → 안 자름
            continue
        gaps.append((k, b - a, b - a >= PAUSE_MUST or jk))
    keep = {k for k, _, m in gaps if m}
    gaps = [g for g in gaps if g[2] is not None]
    budget = max(len(keep), int(cuts_per_min * minutes + 0.5))
    for k, _, _ in sorted([g for g in gaps if not g[2]], key=lambda g: -g[1]):
        if len(keep) >= budget:
            break
        keep.add(k)
    out = [dict(cuts[0])]
    for k in range(len(cuts) - 1):
        if k in keep:
            out.append(dict(cuts[k + 1]))
        else:
            out[-1]["out"] = cuts[k + 1]["out"]
    return out


SLIVER = 1.6   # 앞뒤가 다 잘린 이보다 짧은 조각은 뺌 (1초 남짓 사이에 점프 컷 두 번은 끊겨 보임)
SLIVER_KEEP = ("play", "punchline", "emphasis", "success", "fail", "surprise", "section", "count", "total", "question", "hook_line", "closing")


def drop_slivers(cuts, moms):
    """앞뒤가 다 잘린 짧은 조각(SLIVER 초 미만) 빼기 — 재미 순간·장 나눔·마무리 말이 든 조각은 남김 ('물 좀 마시고 할게요' 같은 혼잣말 조각)."""
    out = []
    for k, c in enumerate(cuts):
        a, b = float(c["in"]), float(c["out"])
        cut_before = k > 0 and a - float(cuts[k - 1]["out"]) > 0.05
        cut_after = k + 1 < len(cuts) and float(cuts[k + 1]["in"]) - b > 0.05
        if b - a < SLIVER and cut_before and cut_after and not any(m["kind"] in SLIVER_KEEP and a - 0.1 <= m["t"] <= b + 0.1 for m in moms):
            continue
        out.append(c)
    return out


def _zoom_of(it):
    sc = ((it.get("fx") or {}).get("scale") or {})
    return float(sc.get("v", 100.0)), sc.get("k") or [], (((it.get("fx") or {}).get("pos") or {}).get("k") or [])


def visual_events(items, titles, shapes, total):
    """화면이 바뀌는 때 [(시각, 종류)] — 점프 컷 · 확대가 바뀜(키프레임·흔들기 포함) · 끼워 넣은 장면(V2·V3) · 글자·도형이 나타남(0.3초 안은 하나)."""
    v1 = sorted([it for it in items if it["track"] == "V1"], key=lambda x: x["start"])
    ev = []
    for a, b in zip(v1, v1[1:]):
        cont = (a["media"] == b["media"] and abs(float(a["out"]) - float(b["in"])) < 0.06 and not a.get("rev") and not b.get("rev")
                and abs(editor.i_sp(a) - editor.i_sp(b)) < 1e-3)
        if not cont:
            ev.append((b["start"], "cut"))
        elif abs(_zoom_of(a)[0] - _zoom_of(b)[0]) > 0.5:
            ev.append((b["start"], "zoom"))
    for it in v1 + [it for it in items if it["track"] in ("V2", "V3")]:
        _, ks, pk = _zoom_of(it)
        if len(ks) >= 2 and max(k["v"] for k in ks) - min(k["v"] for k in ks) > 0.5:
            tt = editor.i_tl(it, ks[0]["t"]) if it["media"] == "main" else it["start"] + ks[0]["t"]
            ev.append((max(it["start"], min(tt, editor.i_end(it))), "zoom"))
        elif len(pk) >= 2:
            ev.append((it["start"], "zoom"))
    ev += [(it["start"], "insert") for it in items if it["track"] in ("V2", "V3")]
    ons = sorted([(t["start"], "text") for t in titles if t["start"] < total] + [(x["start"], "shape") for x in shapes if not x.get("full") and x["start"] < total])
    last = None
    for t, k in ons:
        if last is not None and t - last < 0.3:
            continue
        ev.append((t, k))
        last = t
    return sorted(ev)


def _drop(B, title_ids=(), event_ids=()):
    """글자·사건 빼기 (그 사건의 효과음·확대도 함께)."""
    title_ids, event_ids = set(title_ids), set(event_ids)
    by = {it["id"]: it for it in B.items}
    keep = []
    for e in B.events:
        r = e.get("refs") or {}
        if r.get("titles"):
            r["titles"] = [x for x in r["titles"] if x not in title_ids]
        if e["id"] in event_ids or (e["kind"] in TEXT_KINDS and "titles" in r and not r["titles"]):
            for k in r.get("sfx") or []:
                B.sfx[k] = None
            if e["kind"] in ("punch", "shake"):
                for iid in r.get("items") or []:
                    it = by.get(iid)
                    if it is not None:
                        it["fx"] = {k: v for k, v in (it.get("fx") or {}).items() if k not in ("scale", "pos", "anchor")}
            title_ids |= set(r.get("titles") or [])
            continue
        keep.append(e)
    B.events = keep
    B.titles = [t for t in B.titles if t["id"] not in title_ids]


DROP_ORDER = ("inner", "fx", "shake", "punch", "emphasis", "situ")   # 사건이 너무 많을 때 먼저 빼는 순서


def _split_main(B, it, t, fc):
    """본편 V1 클립(과 짝 A1)을 타임라인 t 에서 나누고 뒤쪽 크기를 바꿈 (원래 크기 ↔ 살짝 확대 · 카메라 두 대 느낌)."""
    if not (it["start"] + 0.6 <= t <= editor.i_end(it) - 0.6):
        return None
    m = editor.i_mt(it, t)
    pair = [x for x in B.items if x["track"] == "A1" and x.get("link") and x.get("link") == it.get("link")]
    link = editor._nid()
    new = dict(it, id=editor._nid(), start=round(t, 4), link=link, **{"in": round(m, 4)})
    it["out"] = round(m, 4)
    v = _zoom_of(it)[0]
    new["fx"] = {} if v > 103.0 else {"scale": {"v": round(editor.SOFT_ZOOM * 100, 1), "k": []}, "anchor": {"v": fc, "k": []}}
    new["color"] = dict(it.get("color") or {})
    B.items.append(new)
    for tr in B.trans:  # 이 클립에서 나가는 전환은 뒤쪽 조각이 이어받음
        if tr.get("a") == it["id"]:
            tr["a"] = new["id"]
    # 바로 이어지는 같은 크기 조각도 다음 컷까지 같은 크기로 (카메라를 바꾼 것처럼 · 조각 끝마다 크기가 되돌아가 화면이 한 번 더 바뀌지 않게)
    prev, end = new, editor.i_end(new)
    for nx in sorted([x for x in B.items if x["track"] == "V1" and x["start"] >= end - 0.02], key=lambda x: x["start"]):
        if abs(nx["start"] - end) > 0.02 or nx["media"] != it["media"] or abs(float(nx["in"]) - float(prev["out"])) > 0.06 or abs(editor.i_sp(nx) - editor.i_sp(prev)) > 1e-3 \
                or _zoom_of(nx)[1] or _zoom_of(nx)[2] or abs(_zoom_of(nx)[0] - v) > 0.5:
            break
        nx["fx"] = json.loads(json.dumps(new["fx"]))
        prev, end = nx, editor.i_end(nx)
    for a in pair:
        na = dict(a, id=editor._nid(), start=round(t, 4), link=link, **{"in": round(editor.i_mt(a, t), 4)})
        a["out"] = round(editor.i_mt(a, t), 4)
        B.items.append(na)
    if it in B.main:
        B.main.insert(B.main.index(it) + 1, new)
    return new


MOMENT_KINDS = ("play", "punchline", "emphasis", "success", "fail", "surprise")


def govern(B, intensity, fc, total, moms=()):
    """양에 맞춰 화면 사건 수 맞추기 (DENSITY):
    1) 너무 많으면 덜 중요한 것부터 뺌 — 속마음·효과 글자·흔들기 → 컷 리듬으로 번갈아 바꾼 확대 → 확대 → 강조·상황 자막
    2) 오래(한도 초 넘게) 아무것도 안 바뀌는 곳은 본편 클립을 나눠 살짝 확대/원래 크기로 (그 안에 재미 순간이 있으면 그 자리에서)
    3) 보통·듬뿍: 아직 아무 사건도 안 붙은 재미 순간(시범·펀치라인·강조·성공·실패)에 살짝 확대 (범위 위쪽을 넘지 않게)
    4) 그래도 범위 아래쪽보다 적으면 가장 긴 곳부터 더 나눔. 나눈 곳은 '확대' 사건으로 남김 (MSG 목록에서 뺄 수 있게).
    5) 나눈 뒤 범위 위쪽을 넘으면 1)을 한 번 더 (멈춘 화면이 새로 생기지 않는 것만)."""
    lo, hi, still = DENSITY[intensity]
    mins = max(0.25, total / 60.0)
    kind_rank = {k: i for i, k in enumerate(DROP_ORDER)}

    def evs():
        return visual_events(B.items, B.titles, B.shapes, total)

    def count():
        return len(evs())

    def rhythm_pairs():
        v1 = sorted([x for x in B.items if x["track"] == "V1" and x["media"] == "main"], key=lambda x: x["start"])
        made = {iid for e in B.events for iid in (e.get("refs") or {}).get("items") or []}
        return [(x, y) for x, y in zip(v1, v1[1:]) if abs(editor.i_end(x) - y["start"]) < 0.02 and abs(float(x["out"]) - float(y["in"])) < 0.06
                and not _zoom_of(x)[1] and not _zoom_of(y)[1] and not _zoom_of(x)[2] and not _zoom_of(y)[2]
                and abs(_zoom_of(x)[0] - _zoom_of(y)[0]) > 0.5 and y["id"] not in made]
    def need_of(ev):  # 멈춘 화면 한도를 지키려면 더 나눠야 하는 수
        ts = [0.0] + [t for t, _ in ev] + [total]
        return sum(max(0, math.ceil((b - a) / still) - 1) for a, b in zip(ts, ts[1:]))

    def need_fill():
        return need_of(evs())

    def over(ev):  # 멈춘 화면 한도를 넘는 구간들
        ts = [0.0] + [t for t, _ in ev] + [total]
        return {(round(a, 2), round(b, 2)) for a, b in zip(ts, ts[1:]) if b - a > still}

    def removable(items=None, titles=None):
        """이렇게 바꾸면(사건 하나를 뺀 글자·클립) 화면 사건이 실제로 줄고 한도를 넘는 멈춘 화면이 새로 생기거나 길어지지 않는지 —
        빼고 나서 다시 나눠야 하면 뺀 보람이 없고, 끼운 화면처럼 나눌 수 없는 곳이면 멈춘 채 남음.
        실제로 빼 보고 셈 (같은 때의 다른 컷·확대는 그대로 남으니 시각만으로 짐작하지 않음)."""
        now = evs()
        ev = visual_events(B.items if items is None else items, B.titles if titles is None else titles, B.shapes, total)
        return len(ev) < len(now) and over(ev) <= over(now)

    def without(e):
        r = e.get("refs") or {}
        tids = set(r.get("titles") or [])
        iids = set(r.get("items") or []) if e["kind"] in ("punch", "shake") else set()
        items = [dict(x, fx={k: v for k, v in (x.get("fx") or {}).items() if k not in ("scale", "pos", "anchor")}) if x["id"] in iids else x
                 for x in B.items]
        return removable(items, [x for x in B.titles if x["id"] not in tids])

    def flat(x, y):  # 번갈아 확대한 뒤 조각을 앞 조각 크기로
        return removable([dict(it, fx=dict(x.get("fx") or {})) if it is y else it for it in B.items])
    def trim():
        while count() + need_fill() > hi * mins:
            cand = sorted([e for e in B.events if e["kind"] in kind_rank], key=lambda e: (kind_rank[e["kind"]], -e["t"]))
            e = next((e for e in cand if without(e)), None)
            rp = [(x, y) for x, y in rhythm_pairs() if flat(x, y)]
            if e is not None and (kind_rank[e["kind"]] < kind_rank["punch"] or not rp):
                _drop(B, event_ids=[e["id"]])
                continue
            if rp:  # 번갈아 확대한 조각을 앞 조각 크기로 (컷 리듬보다 양 범위가 먼저)
                x, y = rp[len(rp) // 2]
                y["fx"] = {k: v for k, v in (x.get("fx") or {}).items()}
                continue
            break
    trim()

    def splittable(t):
        return next((x for x in B.items if x["track"] == "V1" and x["media"] == "main" and x["start"] + 0.6 <= t <= editor.i_end(x) - 0.6
                     and not _zoom_of(x)[1] and not _zoom_of(x)[2]), None)
    mom_tl = []
    for m in moms or ():
        if m["kind"] in MOMENT_KINDS:
            t = B.src_to_tl(m["t"])
            if t is not None:
                mom_tl.append((t, m))

    def split_at(t, why, m=None):
        it = splittable(t)
        if it is None:
            return False
        src = editor.i_mt(it, t)
        new = _split_main(B, it, t, fc)
        if not new:
            return False
        B.event("punch", t, "", why, src=m["t"] if m else src, refs={"items": [new["id"]], "fx": "zoom"})
        return True

    def stretches():
        ts = [0.0] + [t for t, _ in evs()] + [total]
        return sorted(((b - a, a, b) for a, b in zip(ts, ts[1:]) if b - a > 1.2), reverse=True)

    def fill(gap_lim, need=None):
        """gap_lim 초 넘게 아무것도 안 바뀌는 곳을 나눔 (긴 곳부터 · need 개까지) — 그 안의 재미 순간 자리를 먼저."""
        done = 0
        for _ in range(400):
            if need is not None and done >= need:
                break
            got = False
            for g, a, b in stretches():
                if g <= gap_lim:
                    break
                n = max(1, math.ceil(g / gap_lim) - 1)
                t0 = a + g / (n + 1)
                tries = [(t, m) for t, m in sorted(mom_tl, key=lambda x: abs(x[0] - t0)) if a + 1.0 <= t <= b - 1.0 and t - a <= gap_lim and b - t <= gap_lim * n]
                tries += [(t0 + d, None) for d in (0.0, 0.7, -0.7, 1.4, -1.4, 2.1, -2.1, 2.8, -2.8)]
                for t, m in tries:
                    if not (a + 1.0 <= t <= b - 1.0) or t - a > gap_lim:
                        continue
                    if split_at(t, ("재미 순간 살짝 확대 · " + m.get("why", "")) if m else "오래 멈춰 보이지 않게 살짝 확대", m):
                        done += 1
                        got = True
                        break
                if got:
                    break
            if not got:
                break
        return done
    fill(still)
    if intensity != "담백":  # 사건이 안 붙은 재미 순간에 살짝 확대
        for t, m in sorted(mom_tl, key=lambda x: -x[1].get("score", 0)):
            if count() >= hi * mins - 1:
                break
            if any(abs(t2 - t) <= 1.0 for t2, _ in evs()):
                continue
            split_at(t, "재미 순간 살짝 확대 · " + m.get("why", ""), m)
    short = int(lo * mins + 0.999) - count()
    if short > 0:
        fill(2.5, short)
    fill(still)  # 순간 확대로 크기가 바뀌며 사라진 경계가 있으면 한 번 더 (멈춘 화면 한도는 꼭 지킴)
    trim()  # 나누기가 재미 순간 자리를 고르느라 꼭 필요한 것보다 많이 나눴으면, 멈춘 화면이 다시 생기지 않는 것만 덜어 냄


def _audio_target(intensity, w):
    """효과음 무게 w(스타일) → 이 양에서 1분 효과음 수 목표 (AUDIO_DENSITY 범위 안 · 배경음악 곡 수 약 1개/분은 뺌)."""
    lo, hi = AUDIO_DENSITY[intensity]
    return max(0.3, lo + (hi - lo) * min(1.0, max(0.0, float(w or 0)) / 1.4) - 1.0)


DEMO_TXT = ("실전 시범", "한 번 더!")   # 시범 상황 자막 (시간 순서대로 · 다 쓰면 더 붙이지 않음 — 같은 글자 되풀이·예고 말을 시범 중간에 붙이지 않게)


def _demo_labels(plays, moms):
    """시범마다 상황 자막 글자 {id(시범): 글자}: 바로 앞(앞 시범이 끝난 뒤 · 20초 안)에 실패가 있었을 때만 '다시 도전' —
    실패 없이 이어지는 시범에 '다시 도전'을 붙이면 엉뚱함(판정: 리듬 시범에 '다시 도전'). 나머지는 DEMO_TXT 를 차례로."""
    out, n_plain, prev_b = {}, 0, -1e9
    for m in sorted(plays, key=lambda m: m["t"]):
        if any(x["kind"] == "fail" and prev_b - 1.0 <= x["t"] <= m["a"] + 0.5 and m["a"] - x["t"] <= 20.0 for x in moms):
            out[id(m)] = "다시 도전"
        else:
            out[id(m)] = DEMO_TXT[n_plain] if n_plain < len(DEMO_TXT) else None
            n_plain += 1
        prev_b = m["b"]
    return out


BODY_TERMS = ("디딤발", "발목", "무릎", "골반", "시선", "고개", "첫 터치", "타이밍")   # 정지 화면 '잠깐! ○○ 주목'에 쓰는 몸·동작 낱말 (기술 이름 말고도)


def _term_near(words, t, span=15.0):
    """t 앞뒤 span 초 말에서 가장 가까운 기술 이름 ('디딤발' · '퍼스트 터치') — 정지 화면에 '무엇을 볼지' 적으려고 · 없으면 ''."""
    best = None
    near = [(s0, w) for s0, e0, w, _ in words if t - span <= s0 <= t + span]
    toks = [re.sub(r"[^가-힣A-Za-z0-9%]", "", w) for _, w in near]
    for i, j, name in editor._find_terms(toks, editor._emph_terms() + list(BODY_TERMS)):
        d = abs(near[i][0] - t)
        if len(name.replace(" ", "")) <= 6 and (best is None or d < best[0]):
            best = (d, name)
    return best[1] if best else ""


def plan_events(sig, moms, st, intensity, fmt, seed, kept, words, knobs=None, avoid=()):
    """재미 순간 → 사건 (원본 시각 기준) · 예산·간격·동시에 보이는 글자 수 지키기 · 시드 고정.
    knobs: 양 범위에 맞추려고 줄인 구성 (다시 보기·정지 화면 최대 수 · 숫자 세기 여부) · avoid: 티저에 쓴 원본 구간 (다시 보기로 또 쓰지 않게)."""
    knobs = knobs or {}
    rng = random.Random(seed)
    mult = INTENSITY[intensity]
    gap = SPACING[intensity]
    minutes = max(0.5, sum(float(c["out"]) - float(c["in"]) for c in kept) / 60.0)
    if fmt == "shorts":
        mult *= 1.5
    cap, fun = st["captions"], st["fun"]
    pal = SFX_PALETTE.get(st["sound"].get("palette"), SFX_PALETTE["variety"])
    mild = intensity == "담백"

    def kept_at(t):
        return any(float(c["in"]) + 0.05 <= t < float(c["out"]) - 0.05 for c in kept)

    def budget(per_min, cap_n=None):
        n = int(per_min * minutes * mult + 0.5)
        return min(n, cap_n) if cap_n is not None else n

    pm = cap.get("perMin") or {}
    B = {"emphasis": budget(pm.get("emphasis", 0)), "situ": budget(pm.get("situ", 0)),
         "inner": 0 if mild else budget(pm.get("inner", 0)), "fx": 0 if mild else budget(pm.get("fx", 0)),
         "punch": budget(BASE_PER_MIN["punch_zoom"] * fun.get("punch_zoom", 0), int(minutes + 0.99) if mild else None),
         "replay": budget(BASE_PER_MIN["slowmo_replay"] * fun.get("slowmo_replay", 0) / 1.0,
                          1 if mild else max(1, int(minutes / (2.0 if intensity == "보통" else 1.5))) if fun.get("slowmo_replay", 0) > 0 else 0),
         "freeze": 0 if mild else budget(BASE_PER_MIN["freeze"] * fun.get("freeze", 0), (1 if intensity == "보통" else max(1, int(minutes / 3)))
                                        if fun.get("freeze", 0) > 0 else 0),
         "shake": 0 if mild else budget(BASE_PER_MIN["shake"] * fun.get("shake", 0), 1 if intensity == "보통" else None),
         "sfx": int(_audio_target(intensity, fun.get("sfx", 0)) * minutes + 0.5), "count": 99, "score": 99}
    if fun.get("slowmo_replay", 0) > 0 and B["replay"] == 0 and any(m["kind"] == "play" for m in moms):
        B["replay"] = 1
    if mild and fmt == "long":  # 담백: 다시 보기 없음 (끼워 넣은 장면 앞뒤로 컷이 둘 늘어 담백한 레슨이 바빠짐) · 확대는 오래 멈춘 곳만 (govern)
        B["replay"] = 0
        B["punch"] = 0
    for k in ("replay", "freeze"):
        if knobs.get(k) is not None:
            B[k] = min(B[k], knobs[k])
    if knobs.get("count") is False:
        B["count"] = 0
    cands = []

    def cand(kind, m, pri, **kw):
        t = kw.pop("t", m["t"])
        if not kept_at(t) or ("text" in kw and kw["text"] is None):
            return
        cands.append(dict({"kind": kind, "t": round(t, 2), "pri": pri, "src": m["kind"], "why": m.get("why", ""), "mt": m.get("text", "")}, **kw))

    plays = sorted([m for m in moms if m["kind"] == "play"], key=lambda m: -m["score"])
    onsets = sig.get("onsets") or []
    used_txt = {}

    def pick(opts, avoid=""):
        """정해 둔 문구 중 이 편집본에서 아직 안 쓴 것 (같은 개그 글자가 되풀이되면 과함 · 판정: '골인!' 두 번) · 말 자막과 같은 낱말은 피함.
        다 썼으면 None (그 글자는 안 띄움)."""
        av = _norm_txt(avoid)
        ok = [o for o in opts if not (_norm_txt(o).rstrip("!") and _norm_txt(o).rstrip("!") in av)] or list(opts)
        least = min(used_txt.get(o, 0) for o in ok)
        if least > 0:
            return None
        o = rng.choice([o for o in ok if used_txt.get(o, 0) == least])
        used_txt[o] = used_txt.get(o, 0) + 1
        return o

    def near_words(t, span=10.0):
        return " ".join(w for s0, e0, w, _ in words if t - span <= s0 <= t + span)

    def acted(t):
        """성공·실패 말 바로 앞에 실제 동작(시범 장면·공 소리)이 있었는지 — '좋아요' 같은 설명 말에 '나이스!!'를 붙이지 않게."""
        return any(p["a"] - 0.5 <= t <= p["b"] + 4.0 for p in plays) or any(0.0 <= t - o <= 4.0 for o in onsets)

    fails = [m["t"] for m in moms if m["kind"] == "fail"]
    wins = [m["t"] for m in moms if m["kind"] == "success"]
    for m in moms:
        k, sc = m["kind"], m["score"]
        txt = m.get("text") or ""
        if k == "emphasis":
            cand("emphasis", m, 2.0 + sc, text=m["text"], dur=1.6)
        elif k == "section" or (k == "demo_call" and any(0.0 <= p["a"] - m["b"] <= 8.0 for p in plays)):  # 시범 예고는 곧 시범이 이어질 때만
            cand("situ", m, 1.6 + sc, text=_situ_text(m, near_words(m["t"], 30.0)), dur=2.2)
        elif k == "count" and re.search(r"하나[,\s]+둘", txt):
            cand("count", m, 1.5, text="하나 둘 셋", dur=0.7)
        elif k == "punchline":
            after_fail = any(0 <= m["t"] - f <= 10.0 for f in fails)  # 실수 뒤 위로하는 말: 놀리는 효과음 없이 공감 쪽
            after_win = any(0 <= m["t"] - w <= 12.0 for w in wins)  # 성공 뒤 웃음: 머쓱이 아니라 뿌듯
            key = "confess" if CONFESS.search(txt) or after_fail else "proud" if after_win else "confident" if CONFIDENT.search(txt) else "punchline"
            cand("inner", m, 1.8 + sc, text=pick(INNER_TEXTS[key]), dur=1.6, t=m["t"], nosfx=after_fail or key == "confess")
            cand("punch", m, 1.7 + sc, dur=2.2, t=max(m["a"], m["b"] - 1.2))
        elif k == "fail" and acted(m["t"]):
            cand("fx", m, 1.6 + sc, text=pick(FX_TEXTS["fail"], txt), dur=1.0, look="fail")
            cand("inner", m, 1.4 + sc, text=pick(INNER_TEXTS["fail"]), dur=1.5, t=m["t"] + 0.6)
        elif k == "success" and acted(m["t"]):
            goal = re.search(r"들어갔|골인|넣었", txt) and SHOOTING.search(near_words(m["t"], 20.0))
            cand("fx", m, 1.9 + sc, text=pick(FX_TEXTS["goal" if goal else "success"], txt), dur=1.1, look="success")
        elif k == "surprise":
            cand("fx", m, 1.3 + sc, text=pick(FX_TEXTS["surprise"], txt), dur=1.0, look="surprise")
            cand("shake", m, 1.0 + sc, dur=0.35)
        elif k == "reaction":
            cand("punch", m, 1.5 + sc, dur=2.0, t=m["a"])
        elif k == "question":
            cand("inner", m, 1.0 + sc, text=INNER_TEXTS["question"][0], dur=1.5, t=m["b"] + 0.1)
        if k == "emphasis" and not mild:
            cand("punch", m, 1.2 + sc, dur=2.4, t=m["t"])
    demo_label = _demo_labels(plays, moms)
    for i, m in enumerate(plays):
        ons = [o for o in onsets if m["a"] <= o <= m["b"]]
        if ons and SHOOTING.search(near_words(ons[0])):  # 공 차는 소리 글자는 슛 이야기를 할 때만 (트래핑·패스에 '뻥!' 은 엉뚱함)
            cand("fx", m, 1.2 + m["score"], text=pick(FX_TEXTS["kick"]), dur=0.9, look="kick", t=ons[0])
        if ons and i < 3:
            cand("shake", m, 0.9 + m["score"], dur=0.35, t=ons[0])
        # 다시 보기: 공 소리가 난 시범을 먼저 · 티저에 쓴 장면은 뒤로 (판정: 티저와 같은 컷을 다시 보여 줌)
        in_teaser = any(min(m["b"], y) - max(m["a"], x) > 0.3 for x, y in avoid)
        cand("replay", m, 2.0 + m["score"] + (0.8 if ons else 0.0) - (1.5 if in_teaser else 0.0), a=m["a"], b=m["b"], at=m["b"])
        after = [x for x in moms if x["kind"] in ("fail", "success") and m["b"] - 0.5 <= x["t"] <= m["b"] + 5.0]
        if after:
            retry = any(x["kind"] == "fail" and 0.0 <= m["a"] - x["t"] <= 20.0 for x in moms)
            cand("freeze", m, 1.8 + m["score"], at=m["a"], turn=after[0]["kind"], t=m["a"] + 0.05, retry=retry, term=_term_near(words, m["t"]))
        if i < 4 and demo_label[id(m)] and not any(x["kind"] in ("demo_call", "section") and abs(x["t"] - m["a"]) < 6 for x in moms):
            cand("situ", m, 0.8 + m["score"] * 0.5, text=demo_label[id(m)], dur=2.0, t=max(m["a"], m["t"] - 1.0))
    board = _scoreboard(moms)
    for c in board:  # 챌린지: 'N번째' 시도 뒤 결과마다 점수판 (다른 글자보다 먼저 자리를 잡음)
        cand("score", c, 9.0, text=c["text"], dur=2.4, t=c["t"])
    for p, txt in _unsaid_tries(board, plays):
        cand("situ", p, 8.0, text=txt, dur=2.0, t=max(p["a"], p["t"] - 1.5), must=True)
    fin = re.match(r"(\d+)/(\d+) · (\d+)골", board[-1]["text"]) if board else None
    if fin and fin[1] == fin[2]:  # 마지막 시도까지 다 셌으면 최종 결과 (챌린지의 결말)
        last = next((c for c in reversed(cands) if c["kind"] == "score"), None)
        if mild and last is not None:  # 담백: 마지막 점수판 글자를 최종 결과로 (글자 하나 덜 띄움)
            last.update(text=f"최종 결과 · {fin[2]}번 중 {fin[3]}골!", dur=3.4)
        else:
            cand("situ", board[-1], 8.5, text=f"최종 결과 · {fin[2]}번 중 {fin[3]}골!", dur=2.6, t=board[-1]["t"] + 2.5, must=True)
    cands.sort(key=lambda c: (-c["pri"], c["t"]))
    used = {k: 0 for k in B}
    picked = []
    texts = []  # (a, b, 자리)

    def free_text(a, b, slot):
        on = [x for x in texts if x[0] < b and a < x[1]]
        return len(on) < 2 and not any(x[2] == slot for x in on)

    for c in cands:
        k = c["kind"]
        bk = "fx" if k == "fx" else k
        if used.get(bk, 0) >= B.get(bk, 0) and not c.get("must"):
            continue
        t = c["t"]
        if k in TEXT_KINDS:
            a, b = t, t + c["dur"]
            if not free_text(a, b, k):
                continue
            near = [p for p in picked if p["kind"] in TEXT_KINDS and p["kind"] != "score" and abs(p["t"] - t) < gap]
            if near and k != "score":  # 점수판은 제 자리(오른쪽 위)에만 · 다른 글자 간격에 안 셈
                continue
        elif k in ("punch", "shake"):
            if any(p["kind"] in ("punch", "shake") and abs(p["t"] - t) < max(gap, 3.0) for p in picked):
                continue
            # 강조 글자와 확대가 매번 같이 나오면 과함 → 보통은 글자만, 듬뿍도 2번까지만 겹침
            if c["src"] == "emphasis" and any(p["kind"] == "emphasis" and abs(p["t"] - t) < 1.0 for p in picked):
                if intensity != "듬뿍" or used.get("_pair", 0) >= 2:
                    continue
                used["_pair"] = used.get("_pair", 0) + 1
        elif k in ("replay", "freeze"):
            if any(p["kind"] in ("replay", "freeze") and abs(p["t"] - t) < 20.0 for p in picked):
                continue
        picked.append(c)
        used[bk] = used.get(bk, 0) + 1
        if k in TEXT_KINDS:
            texts.append((t, t + c["dur"], k))
    # 효과음: 사건에 붙임 (담백은 깔끔한 소리만 · 간격 1.2초)
    sfx_budget = B["sfx"]
    order = sorted(picked, key=lambda c: -c["pri"])
    sfx_t = []
    for c in order:
        key = {"fx": c.get("look") or "kick", "inner": "inner", "situ": "section", "emphasis": "emphasis", "count": "count",
               "replay": "replay", "freeze": "freeze", "shake": None, "punch": None}.get(c["kind"])
        if c["kind"] == "fx" and c.get("look") == "kick":
            key = "kick"
        if c["kind"] == "inner" and c["src"] == "punchline":
            key = None if c.get("nosfx") else "punchline"
        name = pal.get(key) if key else None
        if not name or (mild and name not in CLEAN_ONLY):
            continue
        if c["kind"] not in ("replay", "freeze", "count"):
            if sfx_budget <= 0:
                continue
            sfx_budget -= 1
        if any(abs(c["t"] - x) < 1.2 for x in sfx_t) and c["kind"] not in ("replay", "freeze"):
            continue
        c["sfx"] = name
        sfx_t.append(c["t"])
    # 몽타주: 시범이 4개 넘을 때 (담백 제외)
    mont = None
    if knobs.get("montage", MONTAGE_N.get(intensity, 0)) and fmt == "long" and fun.get("montage", 0) >= 0.5 and len(plays) >= 4:
        mont = sorted(plays[:8], key=lambda m: m["t"])
    return sorted(picked, key=lambda c: c["t"]), mont


# ---------- 5. 편집본 만들기 ----------
TEASER_FLASH = 0.24     # 티저 사이 흰 번쩍 길이(초)
TITLE_CARD = 2.0        # 제목 카드 길이
END_SEC = 8.0           # 엔드 화면 (유튜브 최종 화면 5~20초)
REPLAY_SPEED = 0.5
REPLAY_SRC = 3.0        # 다시 보기에 쓰는 원본 길이(초)
FREEZE_SEC = 1.7
MONTAGE_CLIP = 0.65
MONTAGE_N = {"듬뿍": 4}   # 몽타주 장면 수 (듬뿍만 · 담백·보통은 몽타주 없음)
TEASER_N = {"담백": 1, "보통": 2, "듬뿍": 3}  # 티저 장면 수 (양이 적을수록 짧게)
GREEN = "#0F7A3D"


def _freeze(name, t):
    """정지 화면 PNG (영상 끝 근처라 장면을 못 뽑으면 조금씩 앞에서 다시)."""
    last = None
    for back in (0.0, 0.5, 1.5, 3.0):
        try:
            return editor.freeze_frame("videos", name, round(max(0.0, t - back), 2))
        except RuntimeError as e:
            last = e
    raise last


def _mid(file):
    return "msg_" + hashlib.sha1(file.encode("utf-8")).hexdigest()[:8]


class _Build:
    """타임라인을 앞에서부터 차례로 쌓는 도우미 (V1/A1 쌍·정지 화면·전환·효과음·사건 기록)."""

    def __init__(self, name, info, fmt):
        self.name, self.info, self.fmt = name, info, fmt
        self.items, self.trans, self.titles, self.shapes, self.markers = [], [], [], [], []
        self.pos = 0.0
        self.media = {}
        self.sfx = []      # (타임라인 시각, 이름, 더 줄일 dB(None=0), 사건)
        self.events = []
        self.main = []     # 본편 V1 클립 (원본 → 타임라인 바꾸기용)

    def clip(self, a, b, speed=1.0, fx=None, nocaps=False, mute=False, vol_db=0.0, main=False, color=None):
        v, au = editor._pair(a, b, start=round(self.pos, 4))
        if fx:
            v["fx"] = fx
        if color:
            v["color"] = color
        if abs(speed - 1) > 1e-6:
            v["speed"] = au["speed"] = round(speed, 4)
        if nocaps:
            v["noCaps"] = au["noCaps"] = True
        if mute:
            au["mute"] = True
        if abs(vol_db) > 0.05:
            au["fx"] = {"level": {"v": round(vol_db, 2), "k": []}}
        self.items += [v, au]
        if main:
            self.main.append(v)
        self.pos = editor.i_end(v)
        return v, au

    def still(self, mid, dur, zoom=(100.0, 106.0), color=None):
        fx = {"scale": {"v": zoom[0], "k": [{"t": 0.0, "v": zoom[0], "e": "lin"}, {"t": round(dur, 3), "v": zoom[1], "e": "lin"}]}} if zoom[1] != zoom[0] else {}
        v = {"id": editor._nid(), "track": "V1", "media": mid, "start": round(self.pos, 4), "in": 0.0, "out": round(dur, 3), "speed": 1.0, "rev": False,
             "link": None, "reframe": 0.5, "fit": "auto", "color": color or {}, "fx": fx}
        self.items.append(v)
        self.pos += dur
        return v

    def flash(self, a, b, typ="white", dur=TEASER_FLASH):
        if a and b:
            tr = {"id": editor._nid(), "track": "V1", "a": a["id"], "b": b["id"], "type": typ, "dur": round(dur, 3), "align": "center"}
            self.trans.append(tr)
            return tr
        return None

    def title(self, text, start, dur, look, **extra):
        t = {"id": editor._nid(), "text": text, "start": round(start, 3), "dur": round(dur, 3), "style": dict(look, **extra), "msg": True}
        self.titles.append(t)
        return t

    def shape(self, x, y, w, h, color, start, dur, opacity=1.0, radius=0, name="도형"):
        s = {"id": editor._nid(), "name": name, "x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4), "color": color,
             "opacity": opacity, "radius": radius, "start": round(start, 3), "dur": round(dur, 3), "full": False}
        self.shapes.append(s)
        return s

    def event(self, kind, t, text="", why="", src=None, refs=None, ins=None):
        e = {"id": editor._nid(), "kind": kind, "t": round(t, 2), "text": text, "why": why, "src": None if src is None else round(src, 2),
             "refs": refs or {}}
        if ins:
            e["ins"] = ins
        self.events.append(e)
        return e

    def src_to_tl(self, t):
        for it in self.main:
            if float(it["in"]) <= t < float(it["out"]):
                return float(it["start"]) + (t - float(it["in"])) / editor.i_sp(it)
        return None


def _split(pieces, t, min_len=0.25):
    """조각 목록에서 원본 t 초를 경계로 나눔 (너무 짧은 조각이 생기면 안 나눔) → 그 경계 뒤 조각 번호 또는 None."""
    for i, p in enumerate(pieces):
        if p["in"] + min_len <= t <= p["out"] - min_len:
            a, b = dict(p, out=round(t, 3)), dict(p, **{"in": round(t, 3)})
            for k in ("punch", "shake"):
                a.pop(k, None)
            pieces[i:i + 1] = [a, b]
            return i + 1
        if abs(p["in"] - t) < min_len:
            return i
    return None


def _shake_keys(t0, speed, seed):
    rng = random.Random(seed)
    ks = []
    for k in range(9):
        dx = 0.0 if k in (0, 8) else rng.uniform(-0.012, 0.012)
        dy = 0.0 if k in (0, 8) else rng.uniform(-0.01, 0.01)
        ks.append({"t": round(t0 + k * 0.045 * speed, 4), "v": [round(0.5 + dx, 4), round(0.5 + dy, 4)], "e": "lin"})
    return ks


def _teaser_windows(moms, n, dur, junk):
    """티저에 쓸 장면: 시범(점수 순) → 펀치라인 → 성공 → 놀람 → 리액션 (서로 6초 넘게 떨어진 곳 · NG 밖)."""
    pri = {"play": 0, "punchline": 1, "success": 2, "surprise": 3, "reaction": 4}
    cands = sorted([m for m in moms if m["kind"] in pri], key=lambda m: (pri[m["kind"]], -m["score"]))
    out = []
    for m in cands:
        if m["kind"] == "play":
            a, b = m["t"] - 0.6, m["t"] + 1.1
        elif m["kind"] == "punchline":
            a, b = m["b"] - 1.0, m["b"] + 0.8
        else:
            a, b = m["t"] - 0.4, m["t"] + 1.3
        a, b = max(0.0, a), min(dur, b)
        if b - a < 1.0 or any(_in_spans(x, junk) for x in (a, b, (a + b) / 2)):
            continue
        if all(abs(a - o[0]) > 6.0 for o in out):
            out.append((round(a, 2), round(b, 2), m))
        if len(out) >= n:
            break
    order = {"play": 1, "success": 2, "surprise": 0, "punchline": 3, "reaction": 0}
    return sorted(out, key=lambda x: order.get(x[2]["kind"], 0))


def _topic(segs, name=None):
    """제목 카드·훅의 주제: 대사에서 가장 많이 나온 주제어 · 영상 제목(파일 이름)에 그 주제어가 들어 있고 짧으면 제목 그대로
    ('패스' → '패스 앤 무브 드릴' · 판정: 주제어 한 낱말만 있는 제목 카드는 너무 짧음)."""
    import hooks
    tk = hooks.topic_keywords([s.get("text", "") for s in segs], 3)
    if not tk:
        return "오늘의 레슨"
    title = _title_of(name) if name else ""
    if title and re.search(r"[가-힣]", title) and len(title.replace(" ", "")) <= 12 and tk[0].replace(" ", "") in title.replace(" ", ""):
        return title
    return tk[0]


def _hook_text(moms, segs, fmt, name=None):
    """훅 자막: 앞부분 질문을 짧게, 없으면 '주제어 + 이것만 알면!'."""
    h = next((m for m in moms if m["kind"] == "hook_line"), None)
    if h:
        t = re.sub(r"^(자|어|음|그|네|아)[,\s]+", "", h["text"].strip())
        t = re.sub(r"\s+", " ", t)
        if len(t.replace(" ", "")) <= (14 if fmt == "shorts" else 18):
            return t
        m = re.search(r"([^,.]*\?)", t)
        if m and len(m[1].replace(" ", "")) <= 18:
            return m[1].strip()
    return f"{_topic(segs, name)}, 이것만 알면 달라져요!"


def _short_text(t, limit):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    if len(t.replace(" ", "")) <= limit:
        return t
    cut, n = "", 0
    for w in t.split():
        if n + len(w) > limit:
            break
        cut, n = (cut + " " + w).strip(), n + len(w)
    return (cut or t[:limit]).rstrip(" ,.") + "…"


SFX_GAP = 3.0        # 효과음 최대 크기 = 말 최대 크기 - 이만큼 (말보다 조금 작게 · 1~6dB 범위 가운데)
SFX_SOFT = 4.0       # 글자에 붙는 작은 소리·짧은 음악은 조금 더 작게
STINGS = ("짠", "짠2", "짠3", "경쾌 짧은 음악", "경쾌 짧은 음악2", "경쾌 짧은 음악3", "맑은 짧은 음악", "맑은 짧은 음악2", "맑은 짧은 음악3",
          "라이저", "두구두구")


def _sfx_level(sig, name, kind, file_pk=None):
    """효과음 레벨(dB) = 목표 최대 크기(말 최대 크기 - 3~4dB) - 이 파일의 실제 최대 크기.
    파일 크기를 재서 맞춤 (예전 판처럼 '모든 효과음 파일은 -3 dBFS' 라고 가정하면 작은 파일은 안 들리고 큰 파일은 말보다 튐)."""
    sp = sig.get("speechPk")
    if sp is None:  # 예전 신호 (말 최대 크기를 안 잰 것): 전체 최대 크기에서 조금 뺌
        sp = float(sig.get("peakDb") or -6.0) - 2.0
    gap = SFX_SOFT if kind in ("emphasis", "count", "section", "inner") or name in STINGS else SFX_GAP
    pk = sfxlib.PEAK_DB if file_pk is None else float(file_pk)
    return round(min(18.0, max(-30.0, float(sp) - gap - pk)), 1)


# 양 범위(DENSITY)를 넘으면 차례로 줄이는 구성: 쉼 자르기 → 몽타주 → 정지 화면 → 숫자 세기 → 다시 보기 → 티저 장면
def _knobs(intensity):
    return {"cuts": PACE[intensity]["cuts"], "montage": MONTAGE_N.get(intensity, 0), "teaser": TEASER_N[intensity], "freeze": None, "replay": None,
            "count": True}


def _reduce(k):
    """한 단계 줄임 → 줄였으면 True."""
    if k["cuts"] > 0.75:
        k["cuts"] = round(max(0.7, k["cuts"] * 0.7), 2)
    elif k["montage"]:
        k["montage"] = 0
    elif k["freeze"] is None or k["freeze"] > 0:
        k["freeze"] = 0
    elif k["count"]:
        k["count"] = False
    elif k["replay"] is None or k["replay"] > 1:
        k["replay"] = 1
    elif k["teaser"] > 1:
        k["teaser"] -= 1
    else:
        return False
    return True


def compile_seq(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log=print, caps=None):
    """스타일·양 → 새 편집본 하나 (+ 필요한 효과음·배경음악·정지 화면 미디어). caps: 말 자막 (글자 자리를 피하려고 · 없으면 받아쓰기로 만듦).
    롱폼은 화면 사건 수가 양 범위(DENSITY) 위쪽을 넘으면 구성을 한 단계씩 줄여 다시 만듦 (재미 글자보다 구조를 먼저 줄임)."""
    knobs = _knobs(intensity)
    for _ in range(8):
        seq, media, n_ev = _compile_once(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log, caps, knobs)
        tot = editor.seq_total(seq)
        if fmt != "long" or n_ev <= DENSITY[intensity][1] * max(0.25, tot / 60.0) or not _reduce(knobs):
            break
    return seq, media


def _compile_once(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log, caps, knobs):
    dur = float(info["duration"])
    words = _words(segs)
    rh = st["rhythm"]
    rec = _recommend(name, segs, rh.get("keepPause") or None, sig)
    junk = [(j["a"], j["b"], j["why"]) for j in rec.get("junk_list") or []]
    pace = dict(PACE[intensity], cuts=knobs["cuts"])
    if fmt == "shorts":
        base = _shorts_window(rec, moms, dur, sig.get("demo") or [])
    else:
        base = keep_cuts(rec["tidy"], sig.get("demo")) or [{"in": 0.0, "out": dur}]
        base = editor._minus(base, junk) or base  # 시범 장면으로 살린 곳에 걸친 군말·NG 도 뺌
        base = snap_edges(pace_pauses(base, junk, pace["cuts"], sig.get("blobs") or ()), sig.get("blobs") or [], words, junk)
        base = drop_slivers(base, moms)
    rf = pace["rhythm"]  # 양이 적을수록 긴 말을 덜 나누고 확대도 덜 바꿈 (담백은 안 나눔)
    every = float(rh.get("zoomEvery") or 0) * rf
    zoom = min(1.6, max(1.0, float(rh.get("zoomScale") or 1.0)))
    if every <= 0:
        zoom = min(zoom, editor.SOFT_ZOOM)
    c3 = [float(x or 0) * rf for x in (rh.get("curve3") or [0, 0, 0])][:3]
    cuts = editor._rhythm([dict(c) for c in base], segs, {"splitShot": float(rh.get("splitShot") or 0) * rf, "curve3": c3, "tempo": rh.get("tempo") or 0},
                          every)
    intro = st["intro"]
    mild = intensity == "담백"
    # 담백: 원본 순서를 바꾸는 티저·첫 질문 장면 없이 본편 첫 장면 위에 질문 글자와 작은 제목만 (화면 사건을 양에 맞게 적게)
    teaser_on = fmt == "long" and intro.get("type") == "teaser" and not mild
    wins = _teaser_windows(moms, min(int(intro.get("clips") or 3), knobs["teaser"]), dur, junk) if teaser_on else []
    picked, mont = plan_events(sig, moms, st, intensity, fmt, seed, base, words, knobs, avoid=[(a, b) for a, b, _ in wins])
    fc = face_center(sig) or [0.5, 0.38]
    B = _Build(name, info, fmt)
    cap_y = caption_y(st, fmt)
    looks = text_looks(st, fmt, cap_y)
    snd = dict(st["sound"], moods=_moods_for(st["sound"].get("moods") or {}, moms))
    pal = SFX_PALETTE.get(snd.get("palette"), SFX_PALETTE["variety"])
    sections = []   # 배경음악 구간 (타임라인 시작, 끝, 분위기)
    topic = _topic(segs, name)
    hook = _hook_text(moms, segs, fmt, name)
    hook_look = dict(looks["emphasis"], y=0.2 if cap_y > 0.5 else 0.7, size=84, fill="#FFFFFF", effect="pop")

    # --- 인트로 ---
    if teaser_on:
        prev = None
        made = []
        for a, b, m in wins:
            v, au = B.clip(a, b, nocaps=True, vol_db=-3.0)
            if prev is not None and intro.get("flash"):
                tr = B.flash(prev, v)
                B.sfx.append((v["start"] - 0.12, pal.get("teaser", "휙"), None, "teaser"))
                made.append(tr["id"]) if tr else None
            prev = v
            made += [v["id"], au["id"]]
        if wins:
            hk = B.title(hook, 0.0, min(B.pos, 3.2), hook_look)
            B.event("teaser", 0.0, hook, f"명장면 {len(wins)}개 미리 보기", refs={"items": made, "titles": [hk["id"]]}, ins={"start": 0.0, "len": round(B.pos, 3)})
    elif fmt == "long" and intro.get("type") == "hook_line" and not mild:
        h = next((m for m in moms if m["kind"] == "hook_line"), None)
        if h and h["b"] - h["a"] <= 6.0:
            v, au = B.clip(max(0.0, h["a"] - 0.1), h["b"] + 0.2, nocaps=True)
            hk = B.title(_short_text(h["text"], 18), 0.0, B.pos, dict(hook_look, size=80))
            B.event("teaser", 0.0, hk["text"], "첫 질문으로 시작", refs={"items": [v["id"], au["id"]], "titles": [hk["id"]]}, ins={"start": 0.0, "len": round(B.pos, 3)})
    shorts_hook = fmt == "shorts"  # 쇼츠: 티저 없이 맨 위에 훅 자막을 처음 2.5초 (본편을 다 만든 뒤 넣음)
    first_main = cuts[0]["in"] if cuts else 0.0
    overlay_title = fmt == "long" and (intro.get("titleCard") or mild) and intensity != "듬뿍"  # 담백·보통: 정지 화면 카드 대신 본편 첫 장면 왼쪽 위에 작은 제목 (컷 없이)
    if fmt == "long" and intro.get("titleCard") and not overlay_title:
        ff = _freeze(name, first_main + 0.2)
        ff["id"] = _mid(ff["file"])
        B.media[ff["id"]] = ff
        s0 = B.pos
        prev = B.items[-2] if len(B.items) >= 2 and B.items[-2]["track"] == "V1" else None
        v = B.still(ff["id"], TITLE_CARD, (100.0, 100.0), {"exp": -0.8, "sat": 85})
        refs = {"items": [v["id"]], "titles": [], "shapes": []}
        if prev is not None:
            tr = B.flash(prev, v, "white" if intro.get("flash") else "dissolve", 0.3)
            refs["trans"] = [tr["id"]] if tr else []
        sh = B.shape(0.06, 0.6, 0.5, 0.25, GREEN, s0, TITLE_CARD, 0.92, 24, "제목 카드") if fmt == "long" else None  # 왼쪽 아래 (얼굴을 가리지 않게)
        if intro.get("letterbox"):
            refs["shapes"] += [B.shape(0, 0, 1, 0.1, "#000000", s0, TITLE_CARD, 1.0, 0, "검은 띠")["id"], B.shape(0, 0.9, 1, 0.1, "#000000", s0, TITLE_CARD, 1.0, 0, "검은 띠")["id"]]
        if sh:
            refs["shapes"].append(sh["id"])
        t1 = B.title("오늘의 주제", s0 + 0.1, TITLE_CARD - 0.1, dict(editor.TITLE_STYLE, weight="Bold", size=44, fill="#FFFFFF", strokeW=0, x=0.09, y=0.69,
                                                                align="left", effect="fade"))
        t2 = B.title(_short_text(topic, 10), s0 + 0.25, TITLE_CARD - 0.25, dict(editor.TITLE_STYLE, font="Black Han Sans", size=112, fill="#FFE14D", stroke="#111111",
                                                                               strokeW=8, x=0.09, y=0.82, align="left", effect="stamp"))
        refs["titles"] += [t1["id"], t2["id"]]
        B.sfx.append((s0 + 0.2, pal.get("title", "짠"), None, "title"))
        B.event("title", s0, topic, "제목 카드", refs=refs, ins={"start": round(s0, 3), "len": TITLE_CARD})
    intro_end = B.pos

    # --- 본편 조각 (확대·흔들기 표시 · 끼워 넣을 곳) ---
    pieces = [{"in": float(c["in"]), "out": float(c["out"]), "zoom": bool(c.get("zoom")), "speed": float(c.get("speed") or 1.0)} for c in cuts]
    inserts = {}   # 조각 번호 → 그 앞에 넣을 것 [(종류, 사건)]
    for c in picked:
        if c["kind"] == "punch":
            i = _split(pieces, c["t"])
            if i is None:
                continue
            _split(pieces, min(pieces[i]["out"], c["t"] + c["dur"]))
            pieces[i]["punch"] = c
        elif c["kind"] == "shake":
            i = _split(pieces, c["t"] - 0.03, 0.15)
            if i is None:
                continue
            _split(pieces, c["t"] + 0.4, 0.15)
            pieces[i]["shake"] = c
    lines = lines_of(segs)
    for c in picked:
        if c["kind"] == "replay":  # 시범 뒤 감독님 반응 말(나이스!)이 끝난 다음 쉬는 틈에 (말 중간에 끊지 않게)
            c["at"] = _after_reaction(lines, c["b"])
    for c in picked:
        if c["kind"] in ("replay", "freeze"):
            at = c["at"]
            i = _split(pieces, at, 0.2)
            if i is None:
                i = next((k for k, p in enumerate(pieces) if p["in"] >= at), None)
            if i is not None:
                inserts.setdefault(i, []).append(c)
    # 몽타주(오늘의 명장면)는 마무리 인사('오늘은 여기까지') 바로 앞에 · 마무리 말이 없으면 본편 뒤
    mont_done = []
    closing = [m["t"] for m in moms if m["kind"] == "closing" and m["t"] >= dur * 0.5]
    mont_at = None
    if mont and fmt == "long" and closing and any(p["in"] - 0.2 <= closing[0] < p["out"] for p in pieces):
        mont_at = closing[0]
        _split(pieces, mont_at, 0.2)

    def _montage():
        s0 = B.pos
        made, prev = [], [x for x in B.items if x["track"] == "V1"][-1] if B.items else None
        for k, m in enumerate(mont[:knobs["montage"]]):
            a = max(0.0, m["t"] - 0.2)
            v, au = B.clip(a, min(dur, a + MONTAGE_CLIP), nocaps=True, vol_db=-6.0)
            tr = B.flash(prev, v, "white", 0.14) if prev is not None else None
            made += [v["id"], au["id"]] + ([tr["id"]] if tr else [])
            if k % 2 == 0:  # 휙 소리는 한 컷 걸러 (번쩍 전환마다 다 넣으면 시끄러움)
                B.sfx.append((v["start"], pal.get("montage", "휙"), -1.5, "montage"))
            prev = v
        tt = B.title("오늘의 명장면", s0, B.pos - s0, dict(looks["emphasis"], y=0.2 if cap_y > 0.5 else 0.75, size=80, fill="#FFFFFF", effect="pop"))
        B.event("montage", s0, "오늘의 명장면", f"시범 {len(mont[:knobs['montage']])}개를 빠르게", refs={"items": made, "titles": [tt["id"]]},
                ins={"start": round(s0, 3), "len": round(B.pos - s0, 3)})
        mont_done[:] = [s0, B.pos]

    # 고친 조각 번호가 바뀌었으므로 다시 찾기: 끼워 넣을 곳은 원본 시각으로
    ins_at = sorted(((c["at"], c) for cs in inserts.values() for c in cs), key=lambda x: x[0])
    main_start = B.pos
    prev_v = None
    for p in pieces:
        while ins_at and ins_at[0][0] <= p["in"] + 0.21:
            _, c = ins_at.pop(0)
            _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y)
        if mont_at is not None and not mont_done and p["in"] >= mont_at - 0.21:
            _montage()
        fx = None
        if p.get("punch"):
            c = p["punch"]
            S = 1.18 if mild else 1.25 if intensity == "보통" else 1.3
            anc = {"v": fc, "k": []}
            if mild:
                fx = {"scale": {"v": round(S * 100, 1), "k": []}, "anchor": anc}
            else:
                sp = p["speed"]
                fx = {"scale": {"v": 100.0, "k": [{"t": round(p["in"], 4), "v": 100.0, "e": "ease"}, {"t": round(p["in"] + 0.25 * sp, 4), "v": round(S * 100, 1), "e": "lin"}]},
                      "anchor": anc}
        elif p.get("shake"):
            c = p["shake"]
            # 크기는 키프레임으로 (기본값은 앞뒤 조각과 같은 100 · 흔들기 한 번이 화면 사건 세 번(커짐·흔들림·돌아옴)으로 세지지 않게)
            fx = {"scale": {"v": 100.0, "k": [{"t": round(p["in"], 4), "v": 106.0, "e": "lin"}, {"t": round(p["out"], 4), "v": 106.0, "e": "lin"}]},
                  "pos": {"v": [0.5, 0.5], "k": _shake_keys(p["in"], p["speed"], seed + int(p["in"] * 100))}}
        elif p["zoom"] and zoom > 1.001:
            fx = {"scale": {"v": round(zoom * 100, 1), "k": []}, "anchor": {"v": fc, "k": []}}
        v, au = B.clip(p["in"], p["out"], p["speed"], fx, main=True)
        if prev_v is None and B.items and len(B.items) > 2:
            before = [x for x in B.items[:-2] if x["track"] == "V1"]
            if before and abs(editor.i_end(before[-1]) - v["start"]) < 0.02 and intro.get("titleCard"):
                B.flash(before[-1], v, "dissolve", 0.4)
        prev_v = v
        if p.get("punch"):
            c = p["punch"]
            B.event("punch", v["start"], "", c.get("why") or "확대", src=p["in"], refs={"items": [v["id"]], "fx": "punch"})
        if p.get("shake"):
            B.event("shake", v["start"], "", "화면 흔들기", src=p["in"], refs={"items": [v["id"]], "fx": "shake"})
    for _, c in ins_at:
        _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y)
    main_end = B.pos
    hook_over = fmt == "long" and mild and main_start < 0.05 and main_end - main_start > 6.0   # 담백: 첫 질문 글자를 본편 첫 장면 위에
    if overlay_title and main_end - main_start > TITLE_CARD + 1.0:
        # 제목으로 시작하는 스타일(다큐)·담백은 첫 장면부터 (첫 1초 안에 화면이 바뀌어야 함) · 나머지는 첫 컷과 떨어뜨려 (같은 때 바뀌면 화면만 바쁨)
        s0 = main_start if intro.get("type") == "title_first" or hook_over else main_start + min(3.0, (main_end - main_start) / 4)
        ln = TITLE_CARD + (1.0 if hook_over else 0.6)
        sh = B.shape(0.04, 0.06, 0.34, 0.2, GREEN, s0, ln, 0.92, 24, "작은 제목")
        ts = []
        if not hook_over:  # 질문 글자와 함께 뜰 때는 '오늘의 주제' 줄을 빼서 화면 글자를 2개까지
            ts.append(B.title("오늘의 주제", s0, ln, dict(editor.TITLE_STYLE, weight="Bold", size=36, fill="#FFFFFF", strokeW=0, x=0.065, y=0.125,
                                                       align="left", effect="fade")))
        ts.append(B.title(_short_text(topic, 10), s0, ln, dict(editor.TITLE_STYLE, weight="Black", size=72 if not hook_over else 64, fill="#FFFFFF",
                                                              strokeW=0, x=0.065, y=0.235 if not hook_over else 0.19, align="left", effect="fade")))
        B.sfx.append((s0, pal.get("title", "짠"), None, "title"))
        B.event("title", s0, topic, "작은 제목 (본편 위)", refs={"shapes": [sh["id"]], "titles": [t["id"] for t in ts]})
    if hook_over and hook:
        h = next((m for m in moms if m["kind"] == "hook_line"), None)
        txt = _short_text(h["text"], 18) if h else hook
        hk = B.title(txt, main_start, min(3.2, main_end - main_start), dict(hook_look, size=76, y=0.72 if cap_y > 0.5 else 0.3))
        B.event("teaser", main_start, txt, "첫 장면 위 질문 글자", refs={"titles": [hk["id"]]})

    # --- 글자·효과음 사건 (본편 시각으로 옮김) ---
    for c in picked:
        if c["kind"] not in TEXT_KINDS and not (c.get("sfx") and c["kind"] not in ("replay", "freeze")):
            continue
        tl = B.src_to_tl(c["t"])
        if tl is None:
            continue
        refs = {"titles": []}
        text = c.get("text") or ""
        if c["kind"] in TEXT_KINDS:
            look = dict(looks[c["kind"]])
            if c["kind"] == "fx":
                look["rot"] = random.Random(seed + int(c["t"] * 10)).choice((-8, -6, 6, 8))
                look["fill"] = {"fail": "#4DA3FF", "success": "#FFE14D", "surprise": "#FF4D4D"}.get(c.get("look"), "#FF9F1C")
            if c["kind"] == "emphasis":
                tl -= 0.12  # 낱말이 들리기 바로 앞에 (효과음이 말 첫소리를 가리지 않게)
            d = c["dur"]
            if c["kind"] == "count":
                # 숫자가 하나씩 옆으로 쌓임 ('1' → '1 2' → '1 2 3') · 마지막 숫자 뒤 0.6초까지 · 겹치지 않게 자리를 나눔 · 효과음은 첫 숫자에만
                ws = [x for x in words if c["t"] - 0.1 <= x[0] <= c["t"] + 3.0 and re.fullmatch(r"(하나|둘|셋|넷)[,.!]*", x[2])][:4]
                nums = {"하나": "1", "둘": "2", "셋": "3", "넷": "4"}
                tls = [(B.src_to_tl(x[0]), nums[re.sub(r"[,.!]", "", x[2])]) for x in ws]
                tls = [(t2, n) for t2, n in tls if t2 is not None]
                if tls:
                    # 숫자마다 다음 숫자가 나올 때까지(적어도 MIN_TEXT) · 그다음 숫자가 나오기 전엔 사라짐 (화면 글자 2개까지)
                    xs = [0.5 + 0.12 * (k - (len(tls) - 1) / 2) for k in range(len(tls))]
                    for k, ((t2, n), x) in enumerate(zip(tls, xs)):
                        end = max(t2 + MIN_TEXT, tls[k + 1][0] if k + 1 < len(tls) else t2 + MIN_TEXT)
                        if k + 2 < len(tls):
                            end = min(end, tls[k + 2][0] - 0.02)
                        tt = B.title(n, t2, max(0.3, end - t2), dict(look, x=round(x, 3)))
                        refs["titles"].append(tt["id"])
                    B.sfx.append((tls[0][0], c.get("sfx") or "틱", None, "count"))
                    refs["sfx"] = [len(B.sfx) - 1]
                    B.event("count", tl, "하나 둘 셋", c["why"], src=c["t"], refs=refs)
                continue
            tt = B.title(text, max(0.0, tl), d, look)
            refs["titles"].append(tt["id"])
        if c.get("sfx"):
            st_t = tl - 0.12 if c["kind"] == "emphasis" else tl
            st_t = _gap_time(B, words, c["t"], st_t)
            B.sfx.append((st_t, c["sfx"], None, c["kind"]))
            refs["sfx"] = [len(B.sfx) - 1]
        B.event(c["kind"] if c["kind"] in TEXT_KINDS else "sfx", tl, text or c.get("sfx") or "", c["why"], src=c["t"], refs=refs)

    if shorts_hook and B.pos > 3.0:
        hk = B.title(_short_text(hook, 14), 0.0, 2.5, dict(looks["emphasis"], y=0.22, size=96, fill="#FFFFFF", effect="pop"))
        B.sfx.append((0.0, pal.get("title", "짠"), None, "title"))
        B.event("hook", 0.0, hk["text"], "쇼츠 첫 2.5초 훅 자막", refs={"titles": [hk["id"]], "sfx": [len(B.sfx) - 1]})

    # --- 몽타주 (마무리 말이 없으면 끝나기 전) ---
    if mont and fmt == "long" and not mont_done:
        _montage()
        sections.append((mont_done[0], mont_done[1], snd.get("moods", {}).get("outro", "신남")))

    # --- 엔드 화면 ---
    if fmt == "long" and B.pos >= 25.0 and not _end_overlay(B, moms, intensity, pal, dur, face_box(sig)):
        _end_screen(B, name, sig, pieces, intensity, pal, dur)
    total = B.pos

    # --- 배경음악 구간 ---
    moods = snd.get("moods") or {}
    if snd.get("bgm", True):
        if intro_end > 0.5:
            sections.append((0.0, intro_end, moods.get("intro", "신남")))
        sections += _body_sections(B, picked, moms, main_start, main_end, moods)
        if total - main_end > 0.5 and not any(s[0] >= main_end - 0.01 for s in sections):
            sections.append((main_end, total, moods.get("outro", "신남")))
        elif total - main_end > 0.5:
            sections.append((max(s[1] for s in sections), total, moods.get("outro", "신남")))
        # 곡 수도 양에 맞춤 (소리 사건 = 효과음 + 곡 · 짧은 영상은 2곡까지)
        sections = _merge_sections(sorted(sections), total, max(2, min(MAX_SECTIONS, int(AUDIO_DENSITY[intensity][1] * total / 60.0 * 0.45))))
    if caps is None:
        caps = editor._captions_of(segs, info)
    caps_tl = editor.timeline_captions({"items": B.items, "captions": caps, "format": fmt}) if st["captions"].get("on", True) else []
    layout_titles(B, caps_tl, _caption_style(st, fmt, cap_y), total, face_box(sig))
    if fmt == "long":
        govern(B, intensity, fc, total, moms)
    seq_items_audio = _audio_items(B, sig, sections, seed, snd, intensity, words)

    # --- 챕터 (3분 넘을 때) · 썸네일 장면 ---
    if fmt == "long" and total >= 180:
        _chapters(B, moms, topic)
    thumbs = _thumb_picks(moms, hook, B)

    tracks = editor.default_tracks()
    for tr in tracks:
        if tr["id"] == "A2":
            tr.update(role="dialog", name="효과음")
        if tr["id"] == "A3":
            tr["name"] = "배경음악"
    if any(it["track"] == "A4" for it in B.items):
        tracks.append({"id": "A4", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0.0, "role": "music", "name": "배경음악"})
    if any(it["track"] == "A5" for it in B.items):
        tracks.append({"id": "A5", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0.0, "role": "dialog", "name": "효과음"})
    capst = _caption_style(st, fmt, cap_y)
    layout = {"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}
    if fmt == "shorts":
        layout = {"mode": "blur", "bar": "#000000", "zoom": 1.35, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}
    seq = editor._new_seq(label, fmt, B.items, trans=B.trans, titles=B.titles, shapes=B.shapes, markers=B.markers, captionStyle=capst,
                          captionsOn=bool(st["captions"].get("on", True)), layout=layout,
                          master={"volume": 1.0, "normalize": True, "lufs": float(snd.get("lufs") or -14.0)},
                          duck={"on": True, "amount": float(snd.get("duck") or -14.0)},
                          # 목소리 보정: 웅웅거림 컷 + 크기 고르게 (외치는 말·감탄이 튀지 않아 소리 크기 -14 LUFS·순간 최대를 함께 지킴)
                          voice={"hp": True, "comp": True})
    seq["tracks"] = tracks
    seq["auto"] = "msg"
    summ = summary_of(B.events, sections, total, seq_items_audio)
    seq["msg"] = {"v": 1, "style": {"label": st.get("label"), "kind": st.get("kind"), "sources": st.get("sources")}, "intensity": intensity, "seed": seed,
                  "events": B.events, "thumb": thumbs, "summary": summ, "notes": st.get("notes") or [], "hook": hook, "topic": topic,
                  "bgm": [{"a": round(a, 2), "b": round(b, 2), "mood": md} for a, b, md in sections]}
    return seq, list(B.media.values()), len(visual_events(B.items, B.titles, B.shapes, total))


# ---------- 글자 자리 정리 (안전 영역 · 말 자막 줄 · 서로 겹침) ----------
SAFE = 0.05          # 화면 가장자리 5% 안쪽(유튜브 플레이어·휴대폰에서 잘리지 않는 자리)에만 글자
MIN_TEXT = 0.8       # 화면 글자는 적어도 이만큼(초) 보여 줌 (읽을 틈)
_FONTS = {}
# 먼저 자리를 잡는 순서 (앞일수록 그대로 두고, 뒤의 것을 옮기거나 줄이거나 뺌)
LAYOUT_PRI = {"title": 0, "teaser": 0, "end": 0, "hook": 0, "montage": 1, "score": 2, "replay": 3, "freeze": 3, "situ": 4, "count": 5,
              "emphasis": 6, "fx": 7, "inner": 8}


def _font(style):
    """(PIL 글꼴 또는 None, 줄 높이 비율) — editor.build_ass 와 같은 글꼴·비율."""
    f = style.get("font")
    if f == "Black Han Sans":
        fn, k = "BlackHanSans-Regular.ttf", 1.02
    elif f == "Do Hyeon":
        fn, k = "DoHyeon-Regular.ttf", 1.0
    else:
        fn, k = ("Pretendard-Black.otf" if style.get("weight") == "Black" else "Pretendard-Bold.otf"), editor.FONT_K
    key = (fn, int(round(float(style.get("size") or 50))))
    if key not in _FONTS:
        try:
            from PIL import ImageFont
            _FONTS[key] = ImageFont.truetype(str(editor.FONTS / fn), key[1])
        except Exception:  # noqa: BLE001 — 글꼴을 못 읽으면 글자 수로 어림
            _FONTS[key] = None
    return _FONTS[key], k


def text_box(text, style, W, H):
    """화면 글자 상자 [x0, y0, x1, y1] — editor.build_ass 와 같은 자리 (왼쪽·가운데·오른쪽 아래 기준점 + \\pos · 좌우 여백 40 · 기울기)."""
    fo, k = _font(style)
    size = float(style.get("size") or 50)
    rows = []
    for r in str(text).replace("\r", "").split("\n"):
        w = fo.getlength(r) if fo is not None else 0.95 * size * len(r)
        n = max(1, math.ceil(w / (W - 80))) if w > 0 else 1
        rows += [w / n] * n
    w = max(rows) if rows else 0.0
    h = size * k * len(rows)
    pad = max(6, style.get("strokeW", 0)) if style.get("bgOn") else style.get("strokeW", 0)
    x, y = W * (style.get("x") if style.get("x") is not None else 0.5), H * float(style["y"])
    al = style.get("align", "center")
    x0 = x if al == "left" else x - w if al == "right" else x - w / 2
    box = [x0 - pad, y - h - pad, x0 + w + pad, y + pad]
    rot = float(style.get("rot") or 0)
    if rot:
        a = math.radians(rot)
        pts = [(px, py) for px in (box[0], box[2]) for py in (box[1], box[3])]
        rp = [(x + (px - x) * math.cos(a) - (py - y) * math.sin(a), y + (px - x) * math.sin(a) + (py - y) * math.cos(a)) for px, py in pts]
        box = [min(q[0] for q in rp), min(q[1] for q in rp), max(q[0] for q in rp), max(q[1] for q in rp)]
    return box


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(0.0, w) * max(0.0, h)
    return inter / max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


FACE_FREE = ("end", "title", "montage", "freeze")   # 얼굴을 가려도 되는 글자 (엔드·제목 카드·정지 화면 위)


def layout_titles(B, caps_tl, cap_style, total, face=None):
    """MSG 화면 글자 자리 정리: 안전 영역(5%) 안으로 · 말 자막과 같은 줄·같은 때는 피함 · 같은 때 겹치는 글자는 위아래로 옮기고,
    자리가 없으면 겹치지 않게 늦게 띄우거나 뺌 (점수판·상황 자막처럼 앞 순서인 것은 그대로) · 적어도 MIN_TEXT 초.
    face: 주인공 얼굴 자리 [x0, y0, x1, y1] (0~1 · 롱폼만) — 되도록 얼굴을 안 가리는 자리로 (판정: 티저 글자 상자가 이마·눈을 가림 ·
    얼굴을 피할 자리가 없으면 가리더라도 띄움).
    → 뺀 글자 id 집합."""
    W, H = (1080, 1920) if B.fmt == "shorts" else (1920, 1080)
    fpx = None
    if face and B.fmt == "long":
        fx0, fy0, fx1, fy1 = face
        fpx = [fx0 * W, fy0 * H, fx1 * W, fy1 * H]
    sx0, sy0, sx1, sy1 = SAFE * W, SAFE * H, (1 - SAFE) * W, (1 - SAFE) * H
    cboxes = [(c["start"], c["end"], text_box(c["text"], cap_style, W, H)) for c in caps_tl or ()]
    band = (min(b[1] for _, _, b in cboxes), max(b[3] for _, _, b in cboxes)) if cboxes else None
    kind_of = {}
    for e in B.events:
        for tid in (e.get("refs") or {}).get("titles") or []:
            kind_of.setdefault(tid, e["kind"])
    placed, gone = [], set()
    for t in sorted(B.titles, key=lambda t: (LAYOUT_PRI.get(kind_of.get(t["id"]), 9), t["start"])):
        st = t["style"]
        t["dur"] = round(max(t["dur"], min(MIN_TEXT, total - t["start"])), 3)
        box = text_box(t["text"], st, W, H)
        dx = sx0 - box[0] if box[0] < sx0 else sx1 - box[2] if box[2] > sx1 else 0.0
        if dx:
            st["x"] = round(float(st.get("x") if st.get("x") is not None else 0.5) + dx / W, 4)
        y0 = float(st["y"])
        on_caps = band is not None and any(min(b, t["start"] + t["dur"]) - max(a, t["start"]) > 0.02 for a, b, _ in cboxes)

        avoid_face = fpx is not None and kind_of.get(t["id"]) not in FACE_FREE

        def free(y, a, b, face_ok=False):
            bx = text_box(t["text"], dict(st, y=y), W, H)
            if bx[1] < sy0 - 0.5 or bx[3] > sy1 + 0.5:
                return False
            if avoid_face and not face_ok and _overlap(bx, fpx) > 0.12:
                return False
            if on_caps and min(bx[3], band[1]) - max(bx[1], band[0]) > 0:
                return False
            same = [(p_a, p_b, pb) for p_a, p_b, pb in placed if min(b, p_b) - max(a, p_a) > 1e-3]
            if len(same) >= 2 and any(sum(1 for q_a, q_b, _ in same if q_a <= m < q_b) >= 2 for m in [a] + [x for q in same for x in (q[0], q[1]) if a <= x < b]):
                return False  # 화면 글자는 한꺼번에 2개까지
            return not any(_overlap(bx, pb) > 0.05 for p_a, p_b, pb in same)
        a, b = t["start"], t["start"] + t["dur"]
        ys = [y0] + [round(y0 + d * sg, 3) for d in (0.07, 0.14, 0.21, 0.28, 0.35, 0.42, 0.5) for sg in ((-1, 1) if y0 >= 0.5 else (1, -1))]
        y = next((yy for yy in ys if 0.0 < yy <= 1.0 and free(yy, a, b)), None)
        if y is None and avoid_face:  # 얼굴을 피할 자리가 없으면 가리더라도
            y = next((yy for yy in ys if 0.0 < yy <= 1.0 and free(yy, a, b, True)), None)
        if y is None:  # 자리가 없으면 앞 글자가 사라진 뒤로 늦춤 (남는 시간이 MIN_TEXT 넘을 때만)
            for later in sorted({p_b for p_a, p_b, pb in placed if p_a < b and a < p_b}):
                if b - later < MIN_TEXT:
                    break
                if free(y0, later + 0.02, b, True):
                    t["start"], t["dur"], y = round(later + 0.02, 3), round(b - later - 0.02, 3), y0
                    a = t["start"]
                    break
        if y is None:
            gone.add(t["id"])
            continue
        st["y"] = y
        placed.append((a, a + t["dur"], text_box(t["text"], st, W, H)))
    if gone:
        B.titles = [t for t in B.titles if t["id"] not in gone]
        keep = []
        for e in B.events:
            r = e.get("refs") or {}
            if r.get("titles"):
                r["titles"] = [x for x in r["titles"] if x not in gone]
                if not r["titles"] and e["kind"] in TEXT_KINDS:  # 글자가 다 빠진 사건은 그 효과음도 뺌
                    for k in r.get("sfx") or []:
                        B.sfx[k] = None
                    continue
            keep.append(e)
        B.events = keep
    return gone


def _shorts_window(rec, moms, dur, sig_demo=()):
    """쇼츠: 추천 구간 중 재미 순간이 가장 많은 곳 (없으면 앞쪽 정리 구간 50초)."""
    best, bs = None, -1.0
    for r in rec.get("shorts") or []:
        sc = sum(m["score"] for m in moms if r["start"] <= m["t"] <= r["end"] and m["kind"] in ("play", "punchline", "success", "emphasis", "surprise"))
        if sc > bs:
            best, bs = r, sc
    if best:  # 그 구간 안의 시범 장면도 살림 (말 정리만 하면 시범이 빠짐)
        demo = [d for d in sig_demo if best["start"] <= d[0] and d[1] <= best["end"]]
        return keep_cuts(best["cuts"], demo)
    out, tot = [], 0.0
    for c in rec.get("tidy") or [{"in": 0.0, "out": min(dur, 50.0)}]:
        if tot >= 50:
            break
        b = min(float(c["out"]), float(c["in"]) + 50 - tot)
        out.append({"in": float(c["in"]), "out": b})
        tot += b - float(c["in"])
    return out


def _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y):
    """슬로모 다시 보기 · 정지 화면을 지금 자리에 끼워 넣기."""
    prev = [x for x in B.items if x["track"] == "V1"]
    prev = prev[-1] if prev else None
    s0 = B.pos
    refs = {"items": [], "titles": [], "shapes": [], "trans": []}
    if c["kind"] == "replay":
        a, b = max(0.0, c["a"] - 0.3), min(float(sig["duration"]), c["b"] + 0.3)
        if b - a > REPLAY_SRC:  # 다시 보기는 원본 3초까지 (공 차는 순간 조금 앞부터 · 느리게 6초)
            a = max(a, c["t"] - 1.2)
            b = min(b, a + REPLAY_SRC)
        fx = None
        if intensity == "듬뿍":  # 다른 각도 느낌: 살짝 당겨서
            fx = {"scale": {"v": 118.0, "k": []}, "anchor": {"v": [0.5, 0.55], "k": []}}
        v, au = B.clip(a, b, REPLAY_SPEED, fx, nocaps=True, mute=True)
        refs["items"] += [v["id"], au["id"]]
        tr = B.flash(prev, v, "white", 0.2) if prev is not None and st["sound"].get("palette") != "cinematic" else B.flash(prev, v, "dissolve", 0.3)
        if tr:
            refs["trans"].append(tr["id"])
        ln = B.pos - s0
        lt = dict(editor.TITLE_STYLE, weight="Black", size=44, fill="#FFFFFF", stroke="#000000", strokeW=10, bgOn=True, bg="#000000", bgOpacity=0.6,
                  x=0.93, y=0.13 if cap_y > 0.5 else 0.8, align="right", effect="fade")
        if B.fmt == "shorts":
            lt.update(size=50, y=0.2)
        refs["titles"].append(B.title("다시 보기 ▶", s0 + 0.15, ln - 0.15, lt)["id"])
        if st["fun"].get("replayLook") == "letterbox":
            refs["shapes"] += [B.shape(0, 0, 1, 0.1, "#000000", s0, ln, 1.0, 0, "검은 띠")["id"], B.shape(0, 0.9, 1, 0.1, "#000000", s0, ln, 1.0, 0, "검은 띠")["id"]]
        B.sfx.append((s0 - 0.1, pal.get("replay", "휙"), None, "replay"))
        k = pal.get("kick")
        if k:
            for o in [o for o in sig.get("onsets") or () if a <= o <= b][:1]:
                B.sfx.append((s0 + (o - a) / REPLAY_SPEED, k, None, "kick"))
        B.event("replay", s0, "다시 보기", f"{mmss(c['t'])} 시범을 느리게 한 번 더", src=c["a"], refs=refs, ins={"start": round(s0, 3), "len": round(ln, 3)})
    else:
        ff = _freeze(name, c["at"] + 0.05)
        ff["id"] = _mid(ff["file"])
        B.media[ff["id"]] = ff
        v = B.still(ff["id"], FREEZE_SEC, (100.0, 100.0), {"sat": 0, "exp": -0.3})
        refs["items"].append(v["id"])
        # 무엇을 볼지 알려 줌 (판정: '잠깐! 여기 주목'은 무엇을 봐야 하는지 모름) · 실수 뒤 다시 하는 시범은 결과를 궁금하게
        txt = ("이때까지만 해도…" if c.get("turn") == "fail" else "이번엔 과연…?" if c.get("retry")
               else f"잠깐! {c['term']} 주목" if c.get("term") else "잠깐! 여기 주목")
        lt = dict(editor.TITLE_STYLE, font="Do Hyeon", size=76 if B.fmt == "long" else 84, fill="#FFFFFF", stroke="#000000", strokeW=12, bgOn=True, bg="#000000",
                  bgOpacity=0.6, x=0.5, y=0.26 if cap_y > 0.5 else 0.7, align="center", effect="fade")
        refs["titles"].append(B.title(txt, s0 + 0.1, FREEZE_SEC - 0.1, lt)["id"])
        B.sfx.append((s0, pal.get("freeze", "찰칵"), None, "freeze"))
        if intensity == "듬뿍" and pal.get("fail"):
            B.sfx.append((s0 + 0.35, "긁기", -1.0, "freeze"))
        B.event("freeze", s0, txt, f"{mmss(c['at'])} 결정적 순간 직전 멈춤", src=c["at"], refs=refs, ins={"start": round(s0, 3), "len": FREEZE_SEC})


END_DARK = {"exp": -1.1, "sat": 70}   # 엔드 화면 배경 (어둡게 · 글자가 잘 읽히게)
END_ZOOM = (100.0, 110.0)             # 움직임이 적은 배경은 천천히 당김 (8초 동안 멈춘 화면으로 보이지 않게)


def _end_window(sig, pieces, used, dur, ln=END_SEC):
    """엔드 화면 배경으로 쓸 원본 구간 [a, a+ln]: 본편에 쓴 곳 가운데 화면이 가장 많이 움직이는 곳 (티저·다시 보기에 쓴 곳은 피함).
    → (a, 움직임 z 평균) · 본편이 짧으면 None."""
    mz = _motion_z(sig)
    if not pieces or dur < ln + 1.0:
        return None
    best = None
    a = 0.0
    while a + ln <= dur - 0.3:
        b = a + ln
        inside = sum(max(0.0, min(b, p["out"]) - max(a, p["in"])) for p in pieces)
        if inside >= 0.7 * ln and not any(min(b, y) - max(a, x) > 0.5 for x, y in used):
            zs = [z for t, z in mz if a <= t < b]
            sc = sum(zs) / len(zs) if zs else 0.0
            if best is None or sc > best[1]:
                best = (round(a, 2), round(sc, 2))
        a += 1.0
    return best


END_MIN, END_MAX = 5.0, 20.0         # 유튜브 최종 화면 길이 (5~20초)
END_DIM = {"exp": -0.5, "sat": 85}   # 마무리 인사 위 엔드 화면: 본편을 조금 어둡게 (글자가 잘 읽히게)


def _cut_main(B, t):
    """본편 V1 클립(과 짝 A1)을 타임라인 t 에서 나눔 (크기·색은 그대로 · 화면은 이어짐) → t 부터 시작하는 V1 클립 (없으면 None)."""
    it = next((x for x in B.main if x["start"] - 0.02 <= t < editor.i_end(x) - 0.02), None)
    if it is None:
        return None
    if t - it["start"] < 0.1:
        return it
    m = editor.i_mt(it, t)
    link = editor._nid()
    new = dict(it, id=editor._nid(), start=round(t, 4), link=link, fx=json.loads(json.dumps(it.get("fx") or {})), color=dict(it.get("color") or {}),
               **{"in": round(m, 4)})
    for a in [x for x in B.items if x["track"] == "A1" and x.get("link") and x.get("link") == it.get("link")]:
        B.items.append(dict(a, id=editor._nid(), start=round(t, 4), link=link, **{"in": round(editor.i_mt(a, t), 4)}))
        a["out"] = round(editor.i_mt(a, t), 4)
    it["out"] = round(m, 4)
    B.items.append(new)
    B.main.insert(B.main.index(it) + 1, new)
    for tr in B.trans:
        if tr.get("a") == it["id"]:
            tr["a"] = new["id"]
    return new


def _end_overlay(B, moms, intensity, pal, dur, face=None):
    """엔드 화면을 마무리 인사('오늘은 여기까지…') 위에 (유튜브 최종 화면 5~20초 · 판정: 따로 붙인 8초 정지 화면은 멈춘 화면·빈 상자) —
    본편을 조금 어둡게 하고 '다음 영상도 같이 봐요!' + 구독 글자. 마무리 말부터 끝까지 5초가 안 되면 원본 뒤를 조금 더 이어 붙임.
    마무리 말이 없거나 끝에서 너무 멀면 False (따로 붙이는 엔드 화면으로)."""
    if not B.main or [x for x in B.items if x["track"] == "V1"][-1] is not B.main[-1]:
        return False  # 본편 뒤에 끼운 장면(몽타주 등)으로 끝나면 따로
    cl = [m for m in moms if m["kind"] == "closing"]
    s0 = None
    for m in cl:
        t = B.src_to_tl(m["a"]) if B.src_to_tl(m["a"]) is not None else B.src_to_tl(m["t"])
        if t is not None and B.pos - t <= END_MAX:
            s0 = t
            break
    if s0 is None:
        return False
    need = END_MIN - (B.pos - s0)
    last = B.main[-1]
    if need > 0:  # 원본 뒤를 조금 더 (같은 장면이 이어짐 · 컷 없음)
        more = need + 0.3
        if float(last["out"]) + more * editor.i_sp(last) > dur - 0.05:
            return False
        nout = round(float(last["out"]) + more * editor.i_sp(last), 4)
        for a in [x for x in B.items if x["track"] == "A1" and x.get("link") and x.get("link") == last.get("link")]:
            a["out"] = nout
        last["out"] = nout
        B.pos = editor.i_end(last)
    first = _cut_main(B, s0)
    if first is None:
        return False
    for x in B.main[B.main.index(first):]:
        x["color"] = dict(x.get("color") or {}, **END_DIM)
    ln = B.pos - s0
    low = face is not None and (face[1] + face[3]) / 2 < 0.45  # 얼굴이 위쪽이면 글자는 아래쪽에 (말 자막 줄 위)
    y1, y2 = (0.58, 0.7) if low else (0.25, 0.36)
    t1 = B.title("다음 영상도 같이 봐요!", s0, ln, dict(editor.TITLE_STYLE, font="Black Han Sans", size=84, fill="#FFFFFF", stroke="#111111", strokeW=8, y=y1,
                                                    effect="pop"))
    t2 = B.title("구독 · 좋아요 부탁해요!", s0, ln, dict(editor.TITLE_STYLE, weight="Black", size=44, fill="#FFFFFF", stroke="#E5484D", strokeW=0, bgOn=True,
                                                     bg="#E5484D", bgOpacity=1.0, y=y2, effect="pop"))
    B.sfx.append((s0, pal.get("end", "경쾌 짧은 음악"), None, "end"))
    # 본편 위에 얹은 것이라 빼도 빈자리를 당기지 않음(ins.overlay) · 어둡게 한 본편은 빼면 원래 밝기로(refs.dim · 편집실 msgRemove)
    B.event("end", s0, "다음 영상도 같이 봐요!", "엔드 화면 (마무리 인사 위 · 유튜브 최종 화면 자리)", refs={"titles": [t1["id"], t2["id"]], "dim": dict(END_DIM)},
            ins={"start": round(s0, 3), "len": round(ln, 3), "overlay": True})
    return True


def _end_screen(B, name, sig, pieces, intensity, pal, dur):
    """엔드 화면 (유튜브 최종 화면 자리 · END_SEC 초): 본편에서 많이 움직이는 장면을 소리 없이 어둡게 깔고 '다음 영상도 같이 봐요!' + 구독 버튼.
    정지 사진을 깔면 8초 동안 멈춘 화면이 되어 자동 검수(멈춘 화면)에 걸리고, 빈 상자(최종 화면 요소 자리)는 덜 만든 그림처럼 보여서 둘 다 안 씀.
    움직임이 적은 장면은 천천히 당김(END_ZOOM)."""
    used = [(e["src"], e["src"] + 4.0) for e in B.events if e["kind"] in ("replay", "teaser") and e.get("src") is not None]
    used += [(float(x["in"]), float(x["out"])) for x in B.items if x["track"] == "V1" and x["media"] == "main"
             and x["start"] < (B.main[0]["start"] if B.main else 0.0)]  # 티저에 쓴 장면
    win = _end_window(sig, pieces, used, dur)
    s0 = B.pos
    prev = [x for x in B.items if x["track"] == "V1"][-1] if B.items else None
    if win is not None:
        a, mot = win
        fx = None
        if mot < 1.0:
            fx = {"scale": {"v": END_ZOOM[0], "k": [{"t": round(a, 3), "v": END_ZOOM[0], "e": "lin"}, {"t": round(a + END_SEC, 3), "v": END_ZOOM[1], "e": "lin"}]}}
        v, au = B.clip(a, a + END_SEC, 1.0, fx, nocaps=True, mute=True, color=dict(END_DARK))
        made = [v["id"], au["id"]]
    else:  # 아주 짧은 원본: 마지막 장면 정지 + 천천히 당김
        last = min(dur - 0.6, pieces[-1]["out"] - 0.4) if pieces else dur - 0.6
        ff = _freeze(name, max(0.0, last))
        ff["id"] = _mid(ff["file"])
        B.media[ff["id"]] = ff
        v = B.still(ff["id"], END_SEC, END_ZOOM, dict(END_DARK))
        made = [v["id"]]
    tr = B.flash(prev, v, "dissolve", 0.6) if prev is not None else None
    # 엔드 화면 글자·도형은 한꺼번에 (0.3초 안 · 하나씩 나타나면 화면이 그만큼 더 바쁨)
    sh = [B.shape(0.445, 0.79, 0.11, 0.11 * 16 / 9 * 9 / 16, "#E5484D", s0 + 0.3, END_SEC - 0.3, 1.0, 100, "구독 버튼")]
    busy = intensity == "듬뿍"  # 듬뿍: 엔드 화면 가운데쯤 글을 바꿈
    t1 = B.title("다음 영상도 같이 봐요!", s0 + 0.3, (END_SEC / 2 if busy else END_SEC) - 0.3, dict(editor.TITLE_STYLE, font="Black Han Sans", size=92,
                                                                                                 fill="#FFFFFF", stroke="#111111", strokeW=8, y=0.25,
                                                                                                 effect="pop"))
    t2 = B.title("구독", s0 + 0.3, END_SEC - 0.3, dict(editor.TITLE_STYLE, weight="Black", size=40, fill="#FFFFFF", strokeW=0, y=0.875, effect="pop"))
    t3 = B.title("좋아요·구독 부탁해요!", s0 + END_SEC / 2, END_SEC / 2, dict(editor.TITLE_STYLE, font="Black Han Sans", size=92, fill="#FFE14D",
                                                                          stroke="#111111", strokeW=8, y=0.25, effect="pop")) if busy else None
    B.sfx.append((s0 + 0.3, pal.get("end", "경쾌 짧은 음악"), None, "end"))
    B.event("end", s0, "다음 영상도 같이 봐요!", "엔드 화면 (유튜브 최종 화면 자리)", refs={"items": made, "trans": [tr["id"]] if tr else [], "shapes": [x["id"] for x in sh],
                                                                     "titles": [t1["id"], t2["id"]] + ([t3["id"]] if t3 else [])},
            ins={"start": round(s0, 3), "len": END_SEC})


CHALLENGE_MOODS = {"감성": "경쾌", "잔잔": "경쾌"}   # 챌린지(점수판이 있는 영상)에서 시범·설명 음악을 신나게


def _moods_for(moods, moms):
    """스타일의 배경음악 분위기를 영상 내용에 맞춤: 슛 챌린지처럼 시도·결과를 세는 영상(점수판)은 시범·설명 장면을 '경쾌'로
    (판정: 신나는 슈팅 챌린지에 '감성' 음악이 처음부터 끝까지 깔려 분위기가 어긋남). 인트로·마무리는 스타일 그대로."""
    out = dict(moods or {})
    if _scoreboard(moms):
        for k in ("lesson", "demo"):
            if out.get(k) in CHALLENGE_MOODS:
                out[k] = CHALLENGE_MOODS[out[k]]
    return out


def _after_reaction(lines, b):
    """시범이 끝난 b 뒤에 바로 반응 말이 있으면 그 말이 끝난 뒤, 없으면 b+0.3 (늘 말과 말 사이)."""
    for k, ln in enumerate(lines):
        if b - 0.3 <= ln["start"] <= b + 2.0 or ln["start"] <= b < ln["end"]:  # 반응 말 (또는 시범 끝이 말 중간)
            end = ln["end"]
            for nx in lines[k + 1:]:  # 숨 안 쉬고 이어지는 말까지 ('나이스! 들어갔어요.')
                if nx["start"] - end > 0.35:
                    break
                end = nx["end"]
            return round(end + 0.15, 3)
    return round(b + 0.3, 3)


def _gap_time(B, words, src_t, tl):
    """효과음이 낱말 첫소리(0.15초)를 가리지 않게 — 그 안이면 낱말 바로 앞 틈으로."""
    for s, e, w, j in words:
        if s - 0.02 <= src_t <= s + 0.15:
            prev_end = max([e2 for s2, e2, w2, j2 in words if e2 <= s] or [s - 1.0])
            if s - prev_end >= 0.1:
                return tl - (src_t - s) - 0.08
            return tl
    return tl


def _body_sections(B, picked, moms, a0, a1, moods):
    """본편 배경음악 구간: 시범이 몰린 곳은 '시범' 분위기, 나머지는 '설명' 분위기 (15초 안 되는 구간은 합침)."""
    if a1 - a0 < 1.0:
        return []
    demo = []
    for m in moms:
        if m["kind"] != "play":
            continue
        t = B.src_to_tl(m["t"])
        if t is None:
            continue
        lo, hi = max(a0, t - 6.0), min(a1, t + 8.0)
        if demo and lo <= demo[-1][1] + 6.0:
            demo[-1][1] = hi
        else:
            demo.append([lo, hi])
    for e in B.events:  # 다시 보기·정지 화면은 시범 분위기 안으로
        if e["kind"] in ("replay", "freeze", "montage") and e.get("ins"):
            lo, hi = e["ins"]["start"], e["ins"]["start"] + e["ins"]["len"]
            demo.append([max(a0, lo - 3), min(a1, hi + 3)])
    demo.sort()
    merged = []
    for lo, hi in demo:
        if merged and lo <= merged[-1][1] + 6.0:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    merged = [d for d in merged if d[1] - d[0] >= 15.0]
    out, t = [], a0
    for lo, hi in merged:
        if lo - t >= 1.0:
            out.append((t, lo, moods.get("lesson", "잔잔")))
        out.append((lo, hi, moods.get("demo", "경쾌")))
        t = hi
    if a1 - t >= 1.0:
        out.append((t, a1, moods.get("lesson", "잔잔")))
    return out


MAX_SECTIONS = 5   # 배경음악은 한 영상에 3~5곡 (너무 자주 바뀌면 산만함)


def _merge_sections(secs, total, most=MAX_SECTIONS):
    """같은 분위기·짧은 구간(8초 미만)은 앞 구간에 합치고, 많으면 가장 짧은 본편 구간부터 이웃과 합침 (첫 인트로 음악은 그대로)."""
    out = []
    for a, b, m in secs:
        if b - a <= 0.05:
            continue
        if out and (out[-1][2] == m or b - a < 8.0) and a <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], b), out[-1][2])
        else:
            out.append((a, b, m))
    while len(out) > max(2, most):
        k = min(range(1, len(out) - 1), key=lambda i: out[i][1] - out[i][0])  # 첫(인트로)·끝(엔드) 음악은 남김
        j = k - 1 if k > 1 and out[k - 1][1] - out[k - 1][0] <= out[k + 1][1] - out[k + 1][0] else k + 1
        if j < k:
            out[j] = (out[j][0], out[k][1], out[j][2])
        else:
            out[j] = (out[k][0], out[j][1], out[j][2])
        del out[k]
        merged = []
        for x in out:
            if merged and merged[-1][2] == x[2]:
                merged[-1] = (merged[-1][0], x[1], x[2])
            else:
                merged.append(x)
        out = merged
    return [(round(a, 3), round(min(b, total), 3), m) for a, b, m in out]


SFX_MIN_GAP = 1.25   # 효과음 사이 최소 간격(초)
SFX_PRI = {"title": 9, "end": 9, "replay": 8, "freeze": 8, "score": 8, "fx": 7, "teaser": 6, "kick": 6, "hook": 6, "inner": 5, "count": 5,
           "situ": 4, "emphasis": 3, "montage": 2}


TOPUP_GAP = 2.5      # 모자란 소리를 더할 때 다른 효과음과 이만큼은 떨어뜨림 (몰려 있으면 과해 보임)


def _top_up_sfx(B, snd, intensity, n_bgm, kept_t, words=()):
    """보통·듬뿍인데 소리 사건(효과음 + 곡 수)이 양 범위(AUDIO_DENSITY) 아래쪽보다 적으면 — 효과음을 아끼는 스타일(다큐)·사건이 적은 원본 —
    소리 없이 지나가는 확대·상황 자막·강조 자막 순간에 그 스타일의 가벼운 소리(휙·딸깍)를 조금 작게 더함.
    다른 효과음과 TOPUP_GAP 초 넘게 떨어진 곳부터 고르게 · 낱말 첫소리는 가리지 않게. 더한 소리는 그 사건의 소리라 MSG 목록에서 함께 빠짐."""
    if intensity == "담백":
        return 0
    need = int(math.ceil(AUDIO_DENSITY[intensity][0] * max(0.25, B.pos / 60.0) - 1e-6)) - n_bgm - len(kept_t)
    if need <= 0:
        return 0
    pal = SFX_PALETTE.get(snd.get("palette"), SFX_PALETTE["variety"])
    soft = {"punch": (pal.get("teaser") or "휙", "punch"), "situ": (pal.get("section") or "딸깍", "section"),
            "emphasis": (pal.get("emphasis") or pal.get("section") or "딸깍", "emphasis")}
    by_title = {x["id"]: x for x in B.titles}
    cands = []
    for e in B.events:
        r = e.get("refs") or {}
        if e["kind"] not in soft or any(B.sfx[k] for k in r.get("sfx") or () if k < len(B.sfx)):
            continue
        ts = [by_title[i]["start"] for i in r.get("titles") or () if i in by_title]
        t = min(ts) if ts else float(e["t"])
        if e.get("src") is not None:
            t = _gap_time(B, words, float(e["src"]) + (t - float(e["t"])), t)
        if 0.3 <= t <= B.pos - 0.5:
            cands.append((t, e))
    added = 0
    while added < need and cands:
        far = lambda c: min([abs(c[0] - x) for x in kept_t] + [1e9])  # noqa: E731
        t, e = max(cands, key=far)
        if far((t, e)) < TOPUP_GAP:
            break
        name, kind = soft[e["kind"]]
        B.sfx.append((t, name, -2.0, kind))
        e.setdefault("refs", {}).setdefault("sfx", []).append(len(B.sfx) - 1)
        kept_t.append(t)
        cands = [c for c in cands if c[1] is not e]
        added += 1
    return added


def _audio_items(B, sig, sections, seed, snd, intensity, words=()):
    """효과음(A2·겹치면 A5) · 배경음악(A3/A4 번갈아 · 1초 겹쳐 바꿈) 클립."""
    assets = editor.ASSETS

    def media_for(fn):
        mid = _mid(fn)
        if mid not in B.media:
            e = editor.media_entry(fn, "assets", mid)
            B.media[mid] = e
        return mid, B.media[mid]

    def aitem(track, mid, start, a, b, lvl, fi=0.0, fo=0.0):
        it = {"id": editor._nid(), "track": track, "media": mid, "start": round(start, 4), "in": round(a, 4), "out": round(b, 4), "speed": 1.0, "rev": False,
              "link": None, "fx": {"level": {"v": round(lvl, 2), "k": []}} if abs(lvl) > 0.05 else {}, "gain": 0.0, "fadeIn": round(fi, 3),
              "fadeOut": round(fo, 3), "mute": False}
        B.items.append(it)
        return it

    # 효과음끼리 SFX_MIN_GAP 초 안으로 붙으면 중요한 것만 (다닥다닥 붙은 효과음은 시끄럽고 과해 보임)
    # 양에 맞는 소리 사건 수 (효과음 + 배경음악 곡 수 · 1분 AUDIO_DENSITY 범위 위쪽을 넘지 않게 덜 중요한 것부터 뺌)
    cap_n = int(AUDIO_DENSITY[intensity][1] * max(0.25, B.pos / 60.0)) - len(sections)
    kept_t = []
    for k in sorted([k for k, x in enumerate(B.sfx) if x], key=lambda k: (-SFX_PRI.get(B.sfx[k][3], 4), B.sfx[k][0])):
        if len(kept_t) >= max(0, cap_n) or any(abs(B.sfx[k][0] - t) < SFX_MIN_GAP for t in kept_t):
            B.sfx[k] = None
            continue
        kept_t.append(B.sfx[k][0])
    _top_up_sfx(B, snd, intensity, len(sections), kept_t, words)
    n_sfx = 0
    ends = {"A2": -1.0, "A5": -1.0}
    sfx_ids = []
    for t, name, lvl, kind in sorted([x for x in B.sfx if x], key=lambda x: x[0]):
        t = max(0.0, t)
        fn = sfxlib.ensure_sfx(name, assets)
        mid, md = media_for(fn)
        d = float(md.get("dur") or 0.5)
        tr = "A2" if t >= ends["A2"] else "A5" if t >= ends["A5"] else None
        if tr is None:
            sfx_ids.append(None)
            continue
        it = aitem(tr, mid, t, 0.0, d, _sfx_level(sig, name, kind, sfxlib.peak_db(assets / fn)) + (lvl or 0.0))  # lvl: 이 소리만 더 작게(dB)
        ends[tr] = t + d
        sfx_ids.append(it["id"])
        n_sfx += 1
    # 사건 기록의 효과음 번호 → 클립 id
    order = sorted([k for k in range(len(B.sfx)) if B.sfx[k]], key=lambda k: B.sfx[k][0])
    idmap = {k: sfx_ids[i] for i, k in enumerate(order)}
    for e in B.events:
        if "sfx" in e["refs"]:
            e["refs"]["items"] = e["refs"].get("items", []) + [idmap[k] for k in e["refs"].pop("sfx") if idmap.get(k)]
    # 배경음악
    # 말이 없을 때 배경음악 평균 크기 = 말 평균 크기(채널 기준) + bgmDb (말할 때는 duck 만큼 더 줄어 말보다 20dB 넘게 작음)
    music_db = float(sig.get("dialogDb") or -24.0) + float(snd.get("bgmDb") or -8.0)
    lvl = round(min(6.0, max(-30.0, music_db - sfxlib.BGM_RMS_DB)), 1)
    tracks = ("A3", "A4")
    seeds = {}
    for k, (a, b, mood) in enumerate(sections):
        sd = seeds.setdefault(mood, _seed(sig.get("sig"), mood) % 1000)  # 같은 영상이면 후보들이 같은 음악을 씀 (파일 하나 · 비교하기 쉽게)
        fn, L = sfxlib.ensure_bgm(mood, sd, assets)
        mid, md = media_for(fn)
        if mood == "코믹":
            continue
        tr = tracks[k % 2]
        x0 = max(0.0, a - (0.5 if k else 0.0))
        x1 = b + (0.5 if k < len(sections) - 1 else 0.0)
        t, first = x0, True
        mine = []
        while t < x1 - 0.05:
            ln = min(L, x1 - t)
            it = aitem(tr, mid, t, 0.0, ln, lvl, 1.0 if first and k else (0.4 if first else 0.0), 0.0)
            mine.append(it)
            first = False
            t += ln
        if mine:
            mine[-1]["fadeOut"] = 1.0 if k < len(sections) - 1 else 2.5
        B.event("bgm", a, mood, f"배경음악 {mood} {mmss(a)}~{mmss(b)}", refs={"items": [x["id"] for x in mine]})
    if intensity == "듬뿍":  # 펀치라인 바로 앞에서 음악을 0.4초 멈춤 (웃음 포인트를 살리는 예능 편집)
        for e in [e for e in B.events if e["kind"] == "inner" and re.search(r"웃음|농담", e.get("why") or "")][:2]:
            _music_gap(B, e["t"] - 0.45, 0.45)
    return {"sfx": n_sfx, "bgm": len(sections), "musicDb": lvl}


def _music_gap(B, t, ln):
    """배경음악 클립을 t 에서 ln 초 비움 (앞은 짧게 줄이고 뒤는 서서히 다시)."""
    for it in [x for x in B.items if x["track"] in ("A3", "A4") and x["start"] + 0.3 < t < editor.i_end(x) - ln - 0.3]:
        cut = t - it["start"]
        tail = dict(it, id=editor._nid(), start=round(t + ln, 4), **{"in": round(it["in"] + cut + ln, 4)}, fadeIn=0.3)
        it["out"] = round(it["in"] + cut, 4)
        it["fadeOut"] = 0.12
        B.items.append(tail)
        for e in B.events:
            if e["kind"] == "bgm" and it["id"] in e["refs"].get("items", []):
                e["refs"]["items"].append(tail["id"])


def _chapters(B, moms, topic):
    """챕터용 마커: 미리 보기 · 장 나눔 말 · 시범 묶음 · 마무리 (이름 20자 안)."""
    import upload
    pts = []
    first = next((e for e in B.events if e["kind"] == "title"), None)
    if B.main:
        pts.append((0.0, "미리 보기" if B.main[0]["start"] > 1 else f"{topic} 시작"))
        pts.append((first["t"] if first else B.main[0]["start"], f"{topic} 레슨"))
    for m in moms:
        if m["kind"] not in ("section", "closing"):
            continue
        t = B.src_to_tl(m["a"])
        if t is None:
            continue
        nm = "마무리" if m["kind"] == "closing" else (_situ_text(m) if re.search(r"번째|마지막", m["text"]) else upload.first_sentence(m["text"], 20))
        pts.append((t, nm))
    for e in B.events:
        if e["kind"] in ("montage", "end"):
            pts.append((e["t"], "오늘의 명장면" if e["kind"] == "montage" else "다음 영상"))
    pts.sort()
    out = []
    for t, nm in pts:
        if not nm or (out and t - out[-1][0] < 12.0):
            continue
        out.append((t, nm))
    for t, nm in out:
        B.markers.append({"t": round(t, 3), "name": nm[:20], "msg": True})
    if out:
        B.event("chapters", 0.0, f"챕터 {len(out)}개", "유튜브 챕터용 마커", refs={"markers": [round(t, 3) for t, _ in out]})


def _thumb_picks(moms, hook, B):
    """썸네일 추천 장면 (원본 시각): 최고 시범 +0.2초 · 웃거나 놀란 얼굴 · 강조 말."""
    out = []
    for kind, why, dt in (("play", "최고 시범 장면", 0.2), ("reaction", "웃거나 놀란 얼굴", 0.0), ("success", "성공한 순간", 0.3),
                          ("emphasis", "강조하는 말", 0.2), ("punchline", "웃음 터진 순간", 0.4)):
        for m in sorted([m for m in moms if m["kind"] == kind], key=lambda m: -m["score"])[:2]:
            t = round(m["t"] + dt, 2)
            if all(abs(t - o["t_src"]) > 3 for o in out):
                out.append({"t_src": t, "why": why, "text": hook})
    return out[:6]


def _caption_style(st, fmt, cap_y):
    c = st["captions"]
    base = dict(editor.LONG_STYLE if fmt == "long" else editor.SHORTS_STYLE)
    base["y"] = cap_y
    if fmt == "long":  # 휴대폰으로 보는 사람이 많아 가편집(52)보다 조금 크게 · 스타일마다 굵기·크기·테두리 (예능 굵고 크게 · 다큐 가늘고 차분하게)
        base.update(size=int(c.get("capSize") or 58), strokeW=int(c.get("capStroke") or 7))
        if c.get("capWeight") in ("Bold", "Black"):
            base["weight"] = c["capWeight"]
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(c.get("color") or "")):
        base["fill"] = c["color"].upper()
    if c.get("box") and not c.get("karaoke"):  # 반투명 검은 상자 자막 (편집실 '박스형'과 같은 모양)
        base.update(bgOn=True, bg="#000000", bgOpacity=0.6, strokeW=10, effect="fade")
    if c.get("karaoke"):
        base.update(weight="Black", size=76 if fmt == "long" else 80, fill="#FFFFFF", stroke="#000000", strokeW=8, effect="karaoke",
                    highlight=c.get("emphColor") or "#FFD400")
    return base


def summary_of(events, sections, total, audio):
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    caps = sum(kinds.get(k, 0) for k in ("emphasis", "situ", "inner", "fx", "count"))
    s = {"captions": caps, "sfx": audio.get("sfx", 0), "punch": kinds.get("punch", 0) + kinds.get("shake", 0), "replay": kinds.get("replay", 0),
         "freeze": kinds.get("freeze", 0), "montage": kinds.get("montage", 0), "bgm": len(sections), "length": round(total, 2), "kinds": kinds}
    s["text"] = (f"예능 자막 {caps} · 효과음 {s['sfx']} · 확대 {s['punch']} · 다시 보기 {s['replay']}" + (f" · 정지 {s['freeze']}" if s["freeze"] else "")
                 + f" · 배경음악 {len(sections)}곡 · 길이 {mmss(total)}")
    return s


# ---------- 6. 후보 여러 개 ----------

def build_variants(name, specs, intensity="보통", kinds=("long",), log=print, proofread=False):
    """스타일 여러 개(최대 3) × 형식 → 새 편집본 후보 + 필요한 미디어. 같은 원본·스타일·양이면 늘 같은 결과.
    proofread: '클로드로 자막 오타 고치기'를 켰을 때 (사용자 클로드 계정으로 대사 글만 보냄).
    후보마다 지금 받아쓰기로 만든 자막(msgCaps)을 함께 가짐 — 편집실이 프로젝트 자막에 반영하고 지움 (msgAdd).
    captionsOld: MSG 가 다시 듣기 전 받아쓰기로 만든 자막 (프로젝트 자막을 사용자가 고쳤는지 보려고 · 다시 들은 적이 없으면 None)."""
    if intensity not in INTENSITY:
        raise ValueError("MSG 양은 담백·보통·듬뿍 중에서 골라 주세요")
    specs = [s for s in (specs or []) if isinstance(s, dict) and s.get("name")][:3]
    if not specs:
        raise ValueError("스타일을 하나 이상 골라 주세요")
    info = editor.media_info(name)
    ensure_transcript(name, log, proofread)
    sig = signals(name, log)
    segs = editor._segments_of(name)
    caps = editor._captions_of(segs, info)
    old = None
    bak = core.adir(name) / ORIG_TRANSCRIPT
    if bak.exists():
        try:
            old = editor._captions_of([x for x in json.loads(bak.read_text(encoding="utf-8")) if str(x.get("text") or "").strip()], info)
        except (OSError, ValueError):
            old = None
    moms = moments(sig, segs)
    seqs, media = [], {}
    letters = "ABC"
    for k, spec in enumerate(specs):
        _check()
        st = resolve(spec)
        for fmt in kinds:
            core.set_progress(label=LABEL, item=name, pct=70 + int(28 * k / len(specs)), detail=f"후보 만드는 중 ({k + 1}/{len(specs)})")
            label = f"MSG 후보 {letters[k]} · {st['label']} · {intensity}" + (" · 쇼츠" if fmt == "shorts" else "")
            seed = _seed(sig["sig"], st["label"], intensity, fmt)
            seq, md = compile_seq(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log, caps)
            seq["msgCaps"] = caps
            seqs.append(seq)
            for m in md:
                media[m["id"]] = m
            log(f"  {label} · {seq['msg']['summary']['text']}")
    return {"sequences": seqs, "media": list(media.values()), "moments": len(moms), "captions": caps, "captionsOld": old}
