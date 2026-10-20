"""MSG 자동 편집 (D-140): 재미없는 원본 → 예능 자막·효과음·확대·다시 보기·배경음악을 넣은 편집본 후보.

흐름 (모두 PC에서 규칙으로 · AI 사용료 없음):
1. signals(name)  — 원본 신호: 받아쓰기 낱말 · 정리할 곳 · 소리 세기 · 움직임 · 공 차는 소리(순간 큰 소리) · YAMNet 웃음/환호 · 얼굴
                    (분석 폴더의 msg_signals.json 에 남김 · 영상이 그대로면 다시 안 봄)
2. moments(sig)   — 재미 순간: 시범·펀치라인·강조·놀람·성공·실패·숫자 세기·질문·장 나눔·훅·마무리·리액션
3. resolve(...)   — 스타일 다섯 부분(인트로·컷 리듬·자막·재미·음악/소리)을 기본 스타일·배운 스타일·섞은 스타일에서 가져와 합침
4. plan_events    — 스타일·양(담백·보통·듬뿍)에 맞춰 사건을 고름 (간격·동시에 보이는 글자 수·말 자막 자리 피하기 · 시드 고정)
5. compile_seq    — 사건 → 편집실 재료(titles·shapes·items·trans·markers · 보통·듬뿍은 시범 장면 전술 그림 tactics) + seq.msg 기록 (사건마다 만든 재료 id)
build_variants(...) 가 위를 묶어 새 편집본 후보 여러 개를 돌려준다 (사용자 편집본은 건드리지 않음).
"""
import bisect
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

import core
import editor
import sfxlib
import tactic
import takes

SIG_VER = 16   # 16: 말하는 중이라도 탁 하고 끝나는 소리(hits) — 공 소리를 군말로 보지 않게 (D-268) · 15: 장면 바뀜으로 찾는 시범(_cutaway) · 정리 전 시범 구간으로 시범 예고 · 13: 목소리 보정을 거친 말소리 최대 크기(voicePk) — 효과음 크기 기준 · 14: 소리 크기를 한 번에 조금씩 읽어 잼(48kHz)
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
DEMO_PRE = 2.0                  # 시범 구간: 첫 공 소리 앞 준비 동작(초)까지 살림

# ---- 낱말 사전 (말을 듣고 판단) ----
PRAISE = re.compile(r"좋아요|좋습니다|나이스|이거죠|그렇죠|그렇지|들어갔|완벽|잘했|훌륭|오케이|굿")
SUCCESS = re.compile(r"성공|됐다|됐어요|들어갔|골인|나이스|완벽|좋아요")
WEAK_WIN = re.compile(r"좋아요|좋습니다|완벽")   # 설명하다가도 쓰는 칭찬 — 바로 앞 공 소리 + 반응으로 시작하는 말일 때만 성공 (D-376)
KICK_REACT = 3.0     # 약한 칭찬('좋아요')이 성공 반응이려면 반응 말 시작 이만큼(초) 안 앞에 실제 공 소리 (D-376)
CUE = re.compile(r"(?:^|[\s,.!?…])(?:시작|출발|스타트|레디 ?고|고고|가자|갑니다)[!.~…]*$")   # 시작 신호 말 ('혼난다. 시작!' · '준비, 시작!' · D-373)
CUE_STRONG = re.compile(r"시작|출발|스타트|레디")   # 분명한 시작 말 (가자·갑니다·고고는 앞에 준비·경고 말이 있어야)
CUE_CTX = re.compile(r"준비|레디|하면|혼난|카운트|휘슬|호루라기|하나[,\s]+둘|둘[,\s]+셋|(?:^|\s)셋[,.!]")   # 시작 신호 앞 준비·경고·숫자 세기 말
CUE_FREE = re.compile(r"(?:준비|레디|하나|둘|셋|자|그럼|이제)[,.!~]*")   # 신호 구절 낱말 수에 안 세는 말 ('준비, 시작!' · '자 시작!')
CUE_PLAY = 3.0   # 분명한 시작 말 뒤 이만큼(초) 안에 공 소리·시범이 이어지면 시작 신호 (앞에 준비 말이 없어도)
FAIL = re.compile(r"아깝|놓쳤|실수|안 ?돼|아이고|아이구|빗나|안 ?들어|아쉽|틀렸|망했")
SURPRISE = re.compile(r"(?:^|[\s,.!?])(?:와|우와|와우|대박|헐|미쳤|오오+|어\?!|어머)(?:[\s,.!?~]|$)")
JOKE = re.compile(r"농담|ㅋㅋ|웃기|장난(?:이|입|이에|이고)|제가 원래|저도 .{0,12}못|저도 몰라")
LAUGH_THR, CHEER_THR = 0.12, 0.1   # YAMNet 웃음·환호 점수: 현장 웃음은 말소리에 섞여 낮게 나옴 (말만 있는 곳은 0.01 안팎)
LAUGH_JOKE = 0.08    # 말이 끝나고 1.5초 안 웃음 (농담 뒤 웃음 · 효과음은 같이 웃는 쪽 · D-373) — 말 끝 웃음은 짧고 작게 잡혀 더 낮게 봄
LAUGH_NOISE = 0.06   # 이 웃음·환호 점수 안의 '순간 큰 소리'는 공 소리가 아님 (웃음 터지는 소리 · D-376)
EMPH_MORE = ("무조건", "절대", "생명", "핵심", "중요", "차이", "비밀", "비결", "꼭", "달라", "완벽", "정확", "제일", "가장")  # 앞의 것부터
COUNT = re.compile(r"하나[,\s]+둘[,\s]+셋(?![가-힣])")   # 실제로 세는 말 ('하나, 둘, 셋!') · '셋에 맞춰서'처럼 예고하는 말은 아님
# (앞의 '자'·'그리고'·'그럼'·'이제'는 건너뜀 — '그리고 마지막으로 …'가 다음 포인트인데 못 찾던 것 · editor.SECTION 과 같게 · D-373)
SECTION_LEAD = re.compile(r"(?:자|그리고|그럼|이제|어|음|아|그|네)[,.…\s]*")   # 다음 포인트 말 앞 군말 낱말 (순간 시각은 그 뒤 · D-379)
SECTION = re.compile(r"^(?:(?:자|그리고|그럼|이제)[,\s]*)?(?:(?:마지막,?\s*)?(?:첫|두|세|네|다섯|여섯|일곱) ?번째|다음은|다음으로|이번엔|이번에는|그 ?다음|마지막으로|자 이제)")
CLOSING = re.compile(r"오늘은 여기까지|오늘 영상은 여기|감사합니다|구독|다음 시간|다음 영상")
DEMO_W = re.compile(r"보여 ?드릴|시범|한 ?번 (?:해 ?)?볼게요|다시 (?:한 ?번 )?해 ?볼게요|직접 해 ?볼게요|해 ?보겠습니다")
HOOK_Q = re.compile(r"\?|(?<!니)까요|을까|할까|일까|될까|있을까|없을까|왜 ")
ROUTINE_Q = re.compile(r"(해|배워|가|시작해|알아|살펴|만나|들어가|차|연습해|보러 가)\s?(볼까요|봅시다|볼게요|보죠|보겠습니다)|시작할게요|시작합니다")
HOOK_W = re.compile(r"하나면|만 ?알면|달라(집니다|져요)|바뀝니다|바뀌어요|무조건|절대|이것만|핵심|비결|비밀|왜 ")
# 속마음·효과 자막은 감독님을 깎아내리지 않는 정해 둔 문구만 (외모·몸 농담 없음 · 실수를 놀리지 않음)
INNER_TEXTS = {"punchline": ["(머쓱)", "(민망)", "(아무 일 없었다)", "(괜히 말했나)", "(잠시 정적)"],
               "proud": ["(뿌듯)", "(자신감 뿜뿜)", "(어깨 으쓱)", "(이 맛이지)"],
               "confess": ["(솔직 고백)", "(고백 타임)", "(다 그런 거죠)", "(처음엔 다 그래요)"],
               "confident": ["(자신감 뿜뿜)", "(각오 완료)", "(오늘은 다르다)"],
               "fail": ["(아까비…)", "(다음엔 꼭!)", "(한 끗 차이)", "(괜찮아, 다시)"], "question": ["(생각 중…)", "(곰곰)", "(정답은?)"],
               "demo": ["(집중)", "(초집중 모드)", "(호흡 정리)", "(눈은 공에)", "(발끝에 집중)"],
               "success": ["(뿌듯)", "(이 맛이지)", "(만족)", "(완벽 그 자체)"]}
FX_TEXTS = {"kick": ["뻥!", "슛!", "쾅!"], "goal": ["골인!", "들어갔다!", "꽂혔다!", "깔끔하게 골!"], "success": ["나이스!!", "깔끔!", "완벽!", "굿!", "좋아!"],
            "surprise": ["두둥!", "오오!", "와우!"], "fail": ["아깝다…", "앗!", "아이고!"], "touch": ["툭!", "탁!", "착!", "톡!"]}
CONFESS = re.compile(r"못했|못 했|실수|넘어지|틀렸|헷갈")      # 스스로 낮추는 농담 → 공감 쪽 속마음
CONFIDENT = re.compile(r"안 넘어질|좋네요|자신|문제없|걱정 ?마")
SHOOTING = re.compile(r"슛|슈팅|골대|골인|골키퍼|차서|킥|득점|넣으면|넣는지")  # 이 말이 앞뒤에 있어야 공 소리에 '뻥!'·'골인!


LEAD = re.compile(r"^(?:자|어|음|그|아|네|예|그래서|그리고)[,.\s]+")  # 화면 글자에서 뺄 군말
# 딴소리·여담 (말 자막만 있으면 밋밋한 곳 → 속마음 글자) — (낱말, 글자) 앞의 것부터
ASIDES = ((r"여담|참고로|TMI|그냥 하는 말", "(TMI)"), (r"물 ?좀|목 ?말라|물 마시", "(물 타임)"), (r"공 ?좀 ?가져|공 ?주우|공 ?줍", "(공 줍줍)"),
          (r"덥네|덥다|더워|춥네|추워|바람", "(날씨 토크)"), (r"쉬었다|잠깐 ?쉬|숨 ?좀", "(숨 고르기)"))
RECAP = re.compile(r"정리해|정리하면|정리할게|요약하면|요약해")


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
ALIGN_VER = 5   # 단어 시각을 소리에 맞추는 규칙 판 (asr.json 'aligned' · 규칙을 고치면 올림 · 2: 뭉개진 낱말·잡음 덩어리까지 늘여 적은 낱말 ·
#                 3: 조용한 틈에서 시작해 제 시각 안에서 소리가 시작하는 낱말(영상 첫 낱말 '안녕하세요'를 0초부터 적음)은 그 소리부터 ·
#                 4: 추임새('아,') 뒤 뭉개진 낱말('아깝다.' 0.05초)을 추임새가 든 긴 소리 덩어리 뒷부분으로 — '아 아깝다' 소리를 추임새로 잘라 반응이 빠짐
#                 5: 조용한 틈에서 시작한 낱말은 바로 뒤(0.03초 안)에서 시작하는 소리 덩어리로 — 그 덩어리를 건너뛰어 '발을 살짝'이 잘림 ·
#                    추임새 덩어리 안에서 시작·끝나고 그 덩어리를 더 많이 차지한 내용 낱말('아깝다.')은 옮기지 않음 — 처음 받아쓴 영상에서 반응이 잘림)
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
            syl0 = len(re.findall(r"[가-힣]", str(w["w"]))) or 1
            fb = filler_blob
            if fb is not None and k > 0 and e0 - s0 < 0.06 * syl0 and fb[1] - fb[0] >= 0.6 and s0 >= fb[1] + 0.3 \
                    and all(float(x["e"]) <= fb[0] + 0.1 for x in ws[:k - 1]):   # (추임새가 그 덩어리의 첫 말일 때만 · '패스하고 그 자리에…'는 아님)
                # 추임새 뒤 뭉개진 낱말: 받아쓰기가 '아 아깝다' 소리를 '아,' 하나로 적고 '아깝다'를 다음 소리 앞 0.05초에 몰아 적음
                # (판정 round5: '아깝다'가 추임새와 함께 잘려 실패 반응이 빠짐) → 추임새 소리 덩어리의 뒷부분으로
                cut = fb[0] + min(0.35, 0.4 * (fb[1] - fb[0]))
                ws[k - 1]["e"] = round(max(float(ws[k - 1]["s"]) + 0.05, cut), 2)
                w["s"], w["e"] = round(cut + 0.02, 2), round(fb[1], 2)
                moved += 1
                prev_s, prev_e = float(w["s"]), float(w["e"])
                filler_blob = None
                continue
            new_s = s0
            bl = _blob_at(blobs, s0)
            nxt = next((b for b in blobs if b[0] > s0 + 0.03), None)
            if bl is None:  # 조용한 틈의 다음 소리는 바로 뒤에서 시작하는 덩어리도 (예전 +0.03초 거름: 0.03초 뒤 덩어리('발을 살짝')를 건너뛰고
                #               그다음 덩어리로 옮겨 그 소리를 낱말 없는 추임새로 잘랐음 · 판정 round5 S1 '살짝' 빠짐)
                nxt = next((b for b in blobs if b[0] > s0), None)
            if bl is None and nxt is not None and (nxt[0] - s0 <= 0.8 or nxt[0] < e0 - 0.1):  # 조용한 틈에서 시작 → 다음 소리에서
                # (제 시각 안에서 소리가 시작하면 멀어도: 받아쓰기가 영상 첫 낱말을 앞 무음 0초부터 적음 → 컷이 그 낱말 소리 바로 앞에서
                #  시작해도 낱말 가운데가 클립 앞이라 자막에서 빠짐 · 판정: 첫 자막이 '여러분.'만 · 인사가 빠져 밋밋한 시작)
                new_s, bl = nxt[0], nxt
                nxt = next((b for b in blobs if b[0] > nxt[0] + 0.03), None)
            syl = len(re.findall(r"[가-힣]", str(w["w"]))) or 1
            short_part = bl is not None and (bl[1] - bl[0] <= 0.9 or (bl[1] - new_s <= 0.9 and new_s - prev_e >= 0.3))
            own = bl is not None and bl is filler_blob and e0 <= bl[1] + 0.05 and e0 - s0 >= 0.1 * syl0 \
                and min(e0, bl[1]) - max(s0, bl[0]) > min(prev_e, bl[1]) - max(prev_s, bl[0])
            # (추임새 덩어리 안에서 시작해 그 안에서 끝나고 덩어리의 더 많은 부분을 차지한 내용 낱말은 그 덩어리가 제 소리 —
            #  받아쓰기 '아,' 15.8~16.96 · '아깝다.' 17.16~17.74 · 소리 16.8~17.84: 다음 덩어리로 옮기면 '아 아깝다'가 추임새로 잘림 ·
            #  예전에는 한 번 옮긴 뒤 다음 판(ALIGN_VER 4)이 되돌려서만 맞았음 → 처음 받아쓴 영상에서는 반응이 빠짐)
            if own and k > 0 and INTERJ.match(str(ws[k - 1]["w"]).strip()) and bl[0] < float(ws[k - 1]["e"]) < s0 - 0.02:
                ws[k - 1]["e"] = round(s0 - 0.02, 2)  # 같은 덩어리의 '아' 소리는 반응에 붙은 말 — 홀로 떨어진 추임새로 잘라 '아깝다' 첫소리까지 잘리지 않게
            if bl is not None and nxt is not None and not own and nxt[0] - bl[1] >= 0.12 and nxt[0] - new_s <= 1.4 and (
                    bl is filler_blob  # 추임새 낱말이 든 덩어리
                    or (e0 >= nxt[0] and short_part and e0 - new_s > 0.15 + 0.2 * syl + 0.5 * (nxt[0] - bl[1]))):  # 앞 덩어리까지 늘여 적은 낱말
                new_s = nxt[0]
            new_s = max(new_s, prev_s + 0.05)
            if new_s > s0 + 0.02:
                w["s"] = round(new_s, 2)
                e1 = max(e0, new_s + min(0.3, max(0.12, e0 - s0)))
                nb = _blob_at(blobs, new_s + 0.01)
                if nb is not None and k + 1 == len(ws):  # 구간 마지막 낱말을 늦춰 옮기면 그 소리 덩어리 끝까지 ('감사합니다.' 끝 0.35초가 잘리던 것)
                    e1 = max(e1, nb[1])
                w["e"] = round(e1, 2)
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
    # 구간 사이도 겹치지 않게 (마지막 낱말을 소리 덩어리 끝까지 늘이면 다음 구간 첫 낱말과 겹쳐 말 자막 두 줄이 한꺼번에 뜸 · 판정 U6)
    for i in range(len(out) - 1):
        wa, wb = out[i].get("words") or [], out[i + 1].get("words") or []
        if wa and wb and float(wa[-1]["e"]) > float(wb[0]["s"]) - 0.02:
            wa[-1]["e"] = round(max(float(wa[-1]["s"]) + 0.05, float(wb[0]["s"]) - 0.02), 2)
            out[i]["end"] = round(min(float(out[i]["end"]), max(float(wa[-1]["e"]), float(out[i]["start"]) + 0.05)), 2)
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


def respread(segs, blobs):
    """받아쓰기 구간 첫머리에 몰아 적은 낱말 묶음('자,' | '하나, 둘, 셋에'를 0.2초에)을 앞 구간 마지막 낱말 뒤의 받아 적지 않은 소리로 펼침 —
    _spread_runs 는 한 구간 안에서만 보므로 구간이 바뀌는 곳의 묶음은 그대로 남아 컷이 '하나, 둘'을 자르고 자막이 늦게 뜸 (판정).
    받아쓰기 파일은 그대로 두고 MSG 안에서만 씀."""
    if not blobs:
        return segs
    out, prev = [], None
    for sg in segs:
        ws = [dict(w) for w in sg.get("words") or []]
        if ws and prev is not None:
            tmp = [dict(prev)] + ws
            if _spread_runs(tmp, blobs):
                ws = tmp[1:]
                sg = dict(sg, words=ws, start=round(min(float(sg["start"]), float(ws[0]["s"])), 2))
        out.append(sg)
        if ws:
            prev = ws[-1]
    return out


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
    return re.sub(r"^\d{8}_[A-Za-z0-9-]+_", "", unicodedata.normalize("NFC", Path(name).stem)).strip()  # (맥·아이폰에서 온 풀어 쓴 한글도)


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


# (끝이 서술어·이음말·조사면 끊긴 말이 아님 · '건'(것은)·'건데' 는 문장 머리 — '사실 이건' … '발 기술 문제라기보다 …' 는 쉬었다 잇는 한 문장 · 판정 round5)
PRED_END = re.compile(r"(?:요|다|죠|까|네|지|고|서|면|데|며|게|니|라|자|야|어|아|봐|해|돼|줘|군|걸|건|래|냐|나|구나|거든|는데|지만|은|는|을|를|도)$")


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


# MSG 쇼츠도 '쇼츠 = 포인트 하나'(BR-092 · 인사·끝인사 빼기 · 장 나눔 점수 · 포인트 수만큼) — 판정 round7(D-261)에서 레슨 영상의
# 쇼츠 맥락 점수가 올라 켬 (챌린지는 빼고 · _shorts_points). False 면 v2.16.0 규칙 그대로 (D-168(6)·D-204)
SHORTS_POINTS = True


def _shorts_points(moms):
    """MSG 쇼츠에 BR-092(쇼츠 = 포인트 하나)를 걸지 — 스위치(SHORTS_POINTS)가 켜졌고 챌린지(점수판이 서는 영상)가 아닐 때만.
    챌린지의 'N번째'는 포인트가 아니라 시도라, 시도마다 끊으면 마지막 시도·결과가 쇼츠에서 빠짐 (판정 round7 S3 맥락 점수가 내려감 · D-261)."""
    return bool(SHORTS_POINTS) and not _scoreboard(moms)


def _recommend(name, segs, keep_pause=None, sig=None, points=None):
    """editor.recommend 를 문장 단위·깨진 말 뺀 받아쓰기로 → 정리 컷(tidy)·군더더기(junk_list).
    sig 에 소리 덩어리(blobs)가 있으면 받아쓰기가 다음 낱말에 붙여 버린 추임새('음…' 뒤 '이 세 가지만')도 뺌.
    points: 쇼츠 후보에 BR-092 를 걸지 (없으면 SHORTS_POINTS · 롱폼 정리 컷은 그대로)."""
    kept, extra = clean_lines(segs, (sig or {}).get("blobs"))
    rec = editor.recommend(name, keep_pause=keep_pause, segs=kept if segs else None, points=SHORTS_POINTS if points is None else points)
    if sig and (sig.get("onsets") or sig.get("demo")) and kept:
        # '다시 해 볼게요' 뒤에 말보다 공 소리(시범)·말 없는 시범이 먼저 오면 다시 찍기 신호가 아니라 '한 번 더 보여 줄게요' 안내 → 남김
        starts = sorted(float(x["start"]) for x in kept)
        keep_back = []
        for j in rec.get("junk_list") or []:
            if j["why"] != "슬레이트 말":
                continue
            if _demo_call_ok(j["b"], starts, sig.get("onsets") or (), sig.get("demo") or ()):
                keep_back.append(j)
        if keep_back:
            rec["junk_list"] = [j for j in rec["junk_list"] if j not in keep_back]
            rec["tidy"] = keep_cuts(rec["tidy"] + [{"in": max(0.0, j["a"] - 0.15), "out": j["b"] + 0.25} for j in keep_back], [])
    back = _demo_calls(extra, _words(segs), [float(x["start"]) for x in kept], (sig or {}).get("onsets") or (), (sig or {}).get("demo") or ())
    if back:  # 쉼으로 쪼개진 '어, … 다시 해볼게요.' 도 뒤에 공 소리(시범)가 오면 시범 예고 → '다시 해볼게요' 는 남김 ('어'는 그대로 뺌)
        extra = [x for x in extra if x not in back]
        rec["tidy"] = keep_cuts(rec["tidy"] + [{"in": max(0.0, a - 0.15), "out": b + 0.25} for a, b, _ in back], [])
    if sig and sig.get("blobs"):
        old = [(j["a"], j["b"]) for j in rec.get("junk_list") or []] + [(a, b) for a, b, _ in extra]
        fill = [f for f in blob_fillers(sig["blobs"], _words(segs), list(sig.get("onsets") or ()) + list(sig.get("hits") or ()))
                if not any(a <= (f[0] + f[1]) / 2 <= b for a, b in old)]
        if fill:
            rec["tidy"] = editor._minus(rec["tidy"], fill)
            extra = extra + fill
    rec["junk_list"] = sorted((rec.get("junk_list") or []) + [{"a": a, "b": b, "why": w} for a, b, w in extra], key=lambda j: (j["a"], -j["b"]))
    return rec


def _demo_call_ok(b, starts, onsets, demos=()):
    """'다시 해볼게요' 같은 말(끝 b) 뒤에 다음 말보다 공 소리(6초 안)나 말 없는 시범 시작(2초 안)이 먼저 오면 시범 예고."""
    nxt = min([t for t in starts if t > b + 0.3] or [1e9])
    return any(b <= o <= min(b + 6.0, nxt) for o in onsets) or any(b - 0.3 <= float(d[0]) <= min(b + 2.0, nxt) for d in demos or ())


def _demo_calls(extra, words, starts, onsets, demos=()):
    """clean_lines 가 '슬레이트 말'로 뺀 것 중 '다시 …'(다시 해볼게요) 다음 말보다 공 소리가 먼저(6초 안) 오는 것 — 다시 찍기가 아니라
    시범 예고 (판정 round5: '어, 다시 해볼게요.' 를 빼서 '해볼게요'가 사라짐 · editor.recommend 의 슬레이트 말과 같은 규칙)."""
    out = []
    for a, b, why in extra or ():
        if why != "슬레이트 말":
            continue
        txt = " ".join(w[2] for w in words if a - 0.1 <= (w[0] + w[1]) / 2 <= b + 0.1)
        if "다시" not in txt:
            continue
        if _demo_call_ok(b, starts, onsets, demos):
            out.append((a, b, why))
    return out


HIT_DB = 4.0   # 말하는 중에도 공 소리로 보는 탁 하고 끝나는 소리: 말소리 최대 크기보다 이만큼(dB) 넘게 클 때 (짧은 말 '자,'는 아님 · D-268)


def _onsets(wave, words, sr=16000, min_db=None):
    """순간 큰 소리 (공 차는 소리·부딪힘): 10ms 소리 세기가 0.1초 전보다 ONSET_DB 넘게 커졌다가 0.15초 안에 다시 줄어듦(탁 하고 끝나는 소리)
    · 말하는 중이 아닐 때 (말 첫소리는 이어지므로 빠짐) · min_db: 그 소리 최대 크기가 이보다 클 때만."""
    np = _np()
    hop = sr // 100
    n = len(wave) // hop
    if n < 20:
        return []
    x = wave[:n * hop].astype(np.float32).reshape(n, hop) / 32768.0
    db = 20 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-6)
    pad = np.concatenate([np.full(10, db[0]), db])   # 0.1초 앞까지의 가장 작은 크기 (앞 10칸 · 첫 칸은 자기 자신)
    base = np.lib.stride_tricks.sliding_window_view(pad[:-1], 10).min(axis=1)
    base[0] = db[0]
    hit = np.where((db - base >= ONSET_DB) & (db > -38))[0]
    talk = [(s - 0.25, e + 0.2) for s, e, _, _ in words]
    out, last = [], -9.0
    for i in hit:
        t = i / 100.0
        if t - last < 0.25:
            continue
        pk = float(db[i:i + 4].max())
        if min_db is not None and pk < min_db:
            continue
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


VOICE = {"hp": True, "comp": True}   # MSG 편집본의 목소리 보정 (웅웅거림 컷 + 크기 고르게 · _compile_once 의 voice 와 같음)


def _voice_peak(path, words, voice=None, sr=48000):
    """목소리 보정(editor.voice_filters)을 거친 말소리 최대 크기(dBFS · _speech_peak 와 같은 셈: 낱말이 들리는 0.1초 조각 최대값의 99% 지점).
    효과음은 목소리 보정 없이 섞으므로(효과음 트랙 voiceFx: false) 보정한 말소리 크기에 맞춰야 말보다 조금 작게 들림 —
    압축기가 말을 누르고 +6dB 키워 원본 말 크기에 맞추면 효과음이 말보다 3~5dB 작거나(보정 안 함) 커짐(보정함 · 판정: 띠로리 -0.8dB).
    _levels_pass 한 번으로 잼 (못 재면 None)."""
    r = _levels_pass(path, [], words, voice, sr)
    return r["voicePk"] if r else None


def _levels_pass(path, segs, words, voice=None, sr=48000):
    """원본 소리를 한 번만 흘려(48kHz · 원래 두 채널 + 목소리 보정한 두 채널, 10초씩 읽음 · 메모리는 0.01초 칸 값만) 말 크기들을 잼 →
    {dialogDb: 말할 때 평균 크기 중앙값, peakDb: 전체 최대(99.9%), speechPk: 말소리 최대, voicePk: 보정한 말소리 최대} (채널 하나 기준 dBFS) ·
    못 재면 None. 예전에는 원본 전체를 메모리에 올려(60분 원본 1.9GB) 8GB 노트북에서 멈출 수 있었고, 같은 소리를 세 번 풀었음 ·
    멈추기(✕)를 누르면 바로 MsgError."""
    np = _np()
    vf = ",".join(editor.voice_filters(VOICE if voice is None else voice)) or "anull"
    fc = f"[0:a]aresample=async=1:first_pts=0,aformat=sample_rates={sr}:channel_layouts=stereo,asplit[a][b];[b]{vf}[v];[a][v]amerge=inputs=2[o]"  # 늦게 시작하는 소리도 제자리 (D-324)
    try:
        p = subprocess.Popen([core.ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-filter_complex", fc, "-map", "[o]", "-ac", "4", "-ar", str(sr),
                              "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **core.NO_WINDOW)
    except OSError:
        return None
    hop = sr // 100            # 0.01초
    blk = hop * 4 * 4          # 0.01초 · 네 채널 · float32
    ss, pk10, vk10, hist = [], [], [], np.zeros(4096, np.int64)
    buf = b""
    try:
        while True:
            raw = p.stdout.read(blk * 1000)  # 10초
            if not raw:
                break
            buf += raw
            k = len(buf) // blk
            if not k:
                continue
            x = np.frombuffer(buf[:k * blk], np.float32).reshape(k, hop, 4)
            buf = buf[k * blk:]
            a = np.abs(x[:, :, :2]).max(axis=2)                       # 채널 중 큰 쪽 (원래 소리)
            ss.append((x[:, :, :2] ** 2).mean(axis=2).sum(axis=1))      # 0.01초 칸의 제곱 합 (채널 평균)
            pk10.append(a.max(axis=1))
            vk10.append(np.abs(x[:, :, 2:]).max(axis=(1, 2)))
            hist += np.bincount(np.minimum(4095, (a * 4096).astype(np.int64)).ravel(), minlength=4096)
            if _cancelled():
                p.kill()
                raise MsgError("MSG 후보 만들기를 멈췄어요")
    except MemoryError:
        p.kill()
        return None
    finally:
        p.stdout.close()
        p.wait()
    if p.returncode or not ss:
        return None
    ss, pk10, vk10 = np.concatenate(ss), np.concatenate(pk10), np.concatenate(vk10)
    n = len(ss)
    vals = []
    for sg in segs or ():
        a, b = int(float(sg["start"]) * 100), int(float(sg["end"]) * 100)
        if b - a > 25:
            vals.append(float(10 * np.log10(ss[a:b].sum() / max(1, (min(b, n) - a) * hop) + 1e-12)))
    rms = float(np.median(vals)) if vals else float(10 * np.log10(ss.sum() / (n * hop) + 1e-12))
    c = np.cumsum(hist)
    p999 = (int(np.searchsorted(c, 0.999 * c[-1])) + 0.5) / 4096
    on = np.zeros((n + 9) // 10, bool)
    for s0, e0, _, _ in words or ():
        on[max(0, int(s0 * 10)):min(len(on), int(math.ceil(e0 * 10)))] = True

    def pk_db(v10):
        m = np.zeros(len(on))
        for i in range(10):  # 0.1초 칸 최대값
            part = v10[i::10]
            m[:len(part)] = np.maximum(m[:len(part)], part)
        return round(float(20 * np.log10(np.percentile(m[on], 99) + 1e-6)), 1) if on.any() else None
    return {"dialogDb": round(rms, 1), "peakDb": round(float(20 * np.log10(p999 + 1e-6)), 1), "speechPk": pk_db(pk10), "voicePk": pk_db(vk10)}


def _voice_est(pk):
    """목소리 보정(압축기 -22dBFS 넘는 소리 1/3 · +6dB)을 거친 말소리 최대 크기 어림 — 잴 수 없을 때만 (원본 말소리 최대 크기 pk 에서)."""
    return round(float(pk) + 6.0 - 0.4 * max(0.0, float(pk) + 21.9), 1)


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
        # 다음 말 첫소리 앞 SNAP_PRE 초는 남김 ('세 번째'의 'ㅅ'처럼 약한 첫소리는 소리 덩어리보다 먼저 시작 · 0.06초만 남기면
        #  첫소리가 잘려 '세'가 빠져 들림 — 판정 round5 최종 S1 136.8초)
        out.append((round(max(0.0, a - 0.03), 2), round(max(b + 0.03, na - SNAP_PRE), 2), "추임새"))
    return out


def _collapsed(w):
    """뭉개진 낱말 (한 글자에 0.06초도 안 됨) — 받아쓰기가 시각을 모르고 몰아 적은 낱말 (s, e, 낱말, …)."""
    return w[1] - w[0] < 0.06 * (len(re.findall(r"[가-힣]", str(w[2]))) or 1)


SNAP_PRE = 0.12    # 말로 시작하는 컷: 첫 소리 앞 여유(초) · SNAP_POST: 정리할 곳 앞에서 끝나는 컷의 마지막 소리 뒤 여유
SNAP_POST = 0.15   # (둘을 합쳐 문장 사이 숨 0.3초 남짓 — 0.2초면 NG 를 뺀 자리에서 두 문장이 숨 없이 붙어 한 자막처럼 보임 · 판정)


JUNK_TAIL = 0.6    # 뺀 군말 소리가 컷 시작 뒤로 이만큼까지 이어질 때만 그 소리를 마저 뺌 (더 길면 군말이 아닌 소리(공 소리·시범)가 붙은 덩어리 · D-268)


def snap_edges(cuts, blobs, words, junk, hits=(), demos=()):
    """컷 가장자리를 실제 말소리에 맞춤: 말로 시작하는 컷은 첫 낱말 소리 덩어리 바로 앞에서 (앞에 붙은 추임새·숨소리·NG 끝은 뺌) ·
    정리할 곳(NG·군말) 바로 앞에서 끝나는 컷은 마지막 낱말 소리 덩어리 바로 뒤에서.
    hits: 탁 하고 끝나는 소리 시각(공 소리 · sig onsets+hits) · demos: 시범 구간 — 군말 소리를 마저 뺄 때 이것은 자르지 않음 (D-268)."""
    if not blobs or not words:
        return cuts
    out = []
    for c in cuts:
        a, b = float(c["in"]), float(c["out"])
        first = next((w for w in words if a - 0.05 <= w[0] <= a + 0.5), None)
        jj = next((j for j in junk if abs(float(j[1]) - a) < 0.1), None) if first is None else None
        if jj is not None:
            # 뺀 군말·NG 바로 뒤에서 시작하는 컷인데 그 소리 덩어리가 컷 시작 뒤까지 이어짐 — 받아쓰기가 추임새 시각을 앞당겨 적어
            # 남은 '어'가 들림 (판정 round7 S2 '어,' 받아쓰기 69.05~69.62 · 실제 소리 69.5~70.1 · U7) → 다음 낱말 소리 바로 앞에서 시작
            # (그 사이에 다른 소리 덩어리가 없고 1.5초 안일 때만 · D-265) · 덩어리가 컷 시작 뒤로 JUNK_TAIL 넘게 이어지거나 그 안·옮길 자리까지에
            # 공 소리·시범이 있으면 그대로 (군말에 바로 붙은 공 소리를 함께 잘라 냄 · 검토 · D-268)
            bl_a = _blob_at(blobs, a)
            nxt = next((w for w in words if a + 0.5 < w[0] <= a + 1.5), None)
            nb = (_blob_at(blobs, nxt[0] + 0.02) or _blob_at(blobs, nxt[1] - 0.02)) if nxt is not None else None
            if (bl_a is not None and nb is not None and bl_a[0] < a - 0.05 and bl_a[1] < nb[0] and nb[0] - a < 1.5
                    and bl_a[1] - a <= JUNK_TAIL and not any(bl_a[1] < x[0] < nb[0] for x in blobs)):
                a2 = max(bl_a[1] + 0.03, nb[0] - SNAP_PRE)
                if not any(a - 0.05 <= float(h) <= a2 for h in hits) and not any(float(d[0]) < a2 and a < float(d[1]) for d in demos):
                    a = a2
                    j0, j1 = float(jj[0]), float(jj[1])
                    # 앞 조각 끝도 받아쓰기의 군말 시작(앞당겨 적힘)이 아니라 진짜 군말 소리 바로 앞까지 — 군말 앞의 웃음·말꼬리를 소리 한가운데에서
                    # 끊지 않게 (판정 검토 S2: 웃음이 69.05초에서 잘림 · 진짜 '어'는 69.5초 · D-268). 진짜 군말 소리 시작 = 받아쓰기 군말을 소리 덩어리
                    # 끝까지 민 자리 (웃음과 군말이 한 덩어리여도) · 따로 떨어진 덩어리면 그 덩어리 시작 · 사이에 다른 낱말·공 소리가 없고 0.8초 안일 때만
                    gb = max(bl_a[0], j0 + (bl_a[1] - j1)) - 0.03
                    if out and abs(float(out[-1]["out"]) - j0) < 0.1 and gb > float(out[-1]["out"]) + 0.05:
                        pb = float(out[-1]["out"])
                        if (gb - pb <= 0.8 and not any(pb + 0.02 < w[0] < gb and not (j0 - 0.05 <= w[0] <= j1) for w in words)
                                and not any(pb <= float(h) <= gb for h in hits)):
                            out[-1] = dict(out[-1], out=round(gb, 3))
        bl = _blob_at(blobs, a)
        if first is not None and _collapsed(first) and bl is None:
            # 컷이 조용한 틈에서 시작하는데 첫 낱말이 뭉개짐: 받아쓰기 구간이 바뀌는 곳에서 몰아 적은 말('하나, 둘, 셋에')의 진짜 소리는
            # 바로 앞 소리 덩어리 (0.4초 안에서 끝나고 앞 낱말 뒤로 0.3초 넘게 받아 적지 않은 소리가 있을 때) — 그대로 두면 '하나, 둘'이 잘림
            p_end = max([w[1] for w in words if w[1] <= first[0] - 0.02] or [-9.0])
            bl = next((x for x in reversed(blobs) if x[1] <= first[0] + 0.01 and first[0] - x[1] <= 0.4 and x[1] - p_end >= 0.3), None)
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
                p_end = max([x[1] for x in blobs if x[1] <= bl[0] - 0.01] or [-9.0])  # 앞 소리(추임새·숨소리)는 넣지 않음
                a = max(a, bl[0] - SNAP_PRE, min(bl[0] - 0.06, p_end + 0.03))
        if any(abs(float(j[0]) - b) < 0.6 or float(j[0]) <= b <= float(j[1]) for j in junk):
            last = next((w for w in reversed(words) if b - 0.6 <= w[1] <= b + 0.05), None)
            if last is not None:
                bl = _blob_at(blobs, last[1] - 0.02)
                if bl is not None and bl[1] + SNAP_POST < b:
                    n_st = min([x[0] for x in blobs if x[0] >= bl[1] + 0.01] or [1e9])  # 다음 소리(NG 첫소리)는 넣지 않음
                    b = max(bl[1] + 0.08, min(bl[1] + SNAP_POST, n_st - 0.03))
        if b - a >= 0.2:
            out.append(dict(c, **{"in": round(a, 3), "out": round(b, 3)}))
    return out


def _chan_levels(path, segs, words, sr=48000):
    """말 크기 (채널 하나 기준 · dBFS): (말할 때 평균 크기 중앙값, 전체 최대, 말소리 최대) — 효과음·배경음악 레벨의 기준.
    흑백으로 섞은 소리(plan._audio16 · 두 채널을 더함)는 같은 소리가 양쪽에 있는 보통 촬영본에서 3dB 크게 나와
    효과음(스테레오 -3 dBFS)·배경음악을 그만큼 크게 맞추게 됨 → 두 채널을 그대로 받아 채널 기준으로 잼 (_levels_pass). 못 받으면 None."""
    r = _levels_pass(path, segs, words, sr=sr)
    return (r["dialogDb"], r["peakDb"], r["speechPk"]) if r else None


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
    _check()
    core.set_progress(label=LABEL, item=name, pct=33, detail="말소리 덩어리 찾는 중")
    rms_db, peak_db = _levels(wave, segs)
    speech_pk = _speech_peak(wave, words)
    pk16 = speech_pk
    blobs = _blobs(wave, speech_pk)  # 소리 덩어리는 같은 흑백 소리 기준으로
    segs = respread(segs, blobs)     # 구간 첫머리에 몰아 적은 낱말 (build_variants 와 같은 받아쓰기로)
    words = _words(segs)
    core.set_progress(label=LABEL, item=name, pct=36, detail="소리 크기 재는 중")
    lv = _levels_pass(path, segs, words)
    voice_pk = None
    if lv:  # 효과음·배경음악 레벨은 채널 하나 기준 크기로
        rms_db, peak_db = lv["dialogDb"], lv["peakDb"]
        speech_pk = lv["speechPk"] if lv["speechPk"] is not None else speech_pk
        voice_pk = lv["voicePk"]
    _check()
    core.set_progress(label=LABEL, item=name, pct=42, detail="웃음소리·환호 듣는 중")
    tags = _tags(path, wave, name)
    noisy = _spans((tags or {}).get("laugh"), 0.48, LAUGH_THR) + _spans((tags or {}).get("cheer"), 0.48, CHEER_THR)
    noisy += _spans((tags or {}).get("speech"), 0.48, 0.6, 0.3)  # 받아쓰기가 놓친 말(짧은 말·작은 소리)의 첫소리도 공 소리가 아님
    core.set_progress(label=LABEL, item=name, pct=62, detail="공 차는 소리 찾는 중")
    onsets = [t for t in _onsets(wave, words) if not any(a - 0.2 <= t <= b + 0.2 for a, b in noisy)]  # 웃음·환호 소리는 공 소리가 아님
    # 말소리보다 큰(HIT_DB) 탁 하고 끝나는 소리 (말하는 중·웃음 곁이어도 · 받아쓰기 시각이 틀려 말 사이 공 소리가 onsets 에서 빠진 것도) — 시범 찾기에는
    # 안 쓰고 소리 덩어리 추임새·컷 가장자리가 공 소리를 군말로 잘라 내지 않게만 씀 (판정 검토: S1 172.3초 공 소리(-5.5dB · 말 최대 -14dB)를
    # '추임새'로 빼 쇼츠에 0.6초 조각 · 짧은 말 '자,'(-28dB)는 아님 · D-268)
    hits = _onsets(wave, [], min_db=(pk16 + HIT_DB) if pk16 is not None else -10.0)
    _check()
    # 말 없는 시범 구간(앞 정리 전 · 정리할 곳 거르기 없이)도 함께 — '다시 한번 해볼게요.' 바로 뒤 말 없는 시범을 시범 예고로 알아보게
    # (예전에는 시범 구간을 정리 뒤에야 셈 → 그 말이 다시 찍기 신호로 빠짐 · 판정 round6 S1)
    pre = {"duration": float(info["duration"]), "motion": ev.get("motion") or [], "motionStep": 1.0 / float(ev.get("fps") or 2),
           "cuts": ev.get("cuts") or [], "onsets": onsets, "tags": tags, "tagStep": 0.48, "junk": []}
    rec = _recommend(name, segs, sig={"blobs": blobs, "onsets": onsets, "hits": hits, "tags": tags, "tagStep": 0.48, "demo": demo_windows(pre, words)})
    sig = {"v": SIG_VER, "sig": fsig, "tsig": tsig, "name": name, "duration": float(info["duration"]),
           "w": info["width"], "h": info["height"], "fps": info.get("fps", 30.0),
           "motion": ev.get("motion") or [], "motionStep": 1.0 / float(ev.get("fps") or 2), "cuts": ev.get("cuts") or [],
           "onsets": onsets, "hits": hits, "dialogDb": rms_db, "peakDb": peak_db, "speechPk": speech_pk, "voicePk": voice_pk, "blobs": blobs, "tags": tags, "tagStep": 0.48,
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
            ons = [t for t in sig.get("onsets") or () if a <= t <= b]
            act = [t for t, z in mz if a <= t <= b and z >= 1.0] + ons
            if act and not any(_in_spans((a + b) / 2, [j]) for j in junk):
                # 공 소리 앞은 차기 전 준비 동작까지 (DEMO_PRE 초 · 판정: 공 차는 순간만 남기면 시범이 잘리고 말과 이어지지 않음)
                lo = max(a, min(act) - 0.8)
                ons_v = [o for o in ons if any(abs(t - o) <= 1.0 and z >= 1.0 for t, z in mz)]  # 화면도 움직이는 공 소리만 (말 첫소리·잡음 아님)
                if ons_v:
                    lo = min(lo, max(a, min(ons_v) - DEMO_PRE))
                hi = min(b, max(act) + 1.2)
                for sa, sb in voice:  # 가장자리의 받아쓰지 못한 목소리(추임새 '어…'·작은 말)는 시범이 아님 → 그 앞뒤에서 끊음
                    if sa < hi and lo < sb:  # (소리 모델 칸이 0.48초라 목소리 시작은 0.3초 넉넉히 앞으로)
                        if sb >= hi - 0.1:
                            hi = min(hi, sa - 0.3)
                        elif sa <= lo + 0.1:
                            lo = max(lo, sb + 0.1)
                if hi - lo >= 1.2:
                    out.append([round(float(lo), 2), round(float(hi), 2)])
            cw = _cutaway(sig, a, b)
            if cw:   # 다른 장면으로 끼운 시범은 그 장면 전체 (움직임으로 찾은 구간과 겹치면 합침)
                ov = [o for o in out if min(cw[1], o[1]) - max(cw[0], o[0]) > -0.3]
                for o in ov:
                    out.remove(o)
                out.append([round(min([cw[0]] + [o[0] for o in ov]), 2), round(max([cw[1]] + [o[1] for o in ov]), 2)])
        prev = max(prev, e)
    return sorted(out)


def _cutaway(sig, a, b):
    """말 없는 틈(a~b) 안의 다른 장면(장면 바뀜 둘 사이 2초 넘게) → 그 장면 구간 (a~b 안으로) 또는 None.
    시범 영상을 끼워 찍은 원본에서 선수가 작게 보이는 시범은 화면 움직임이 작아(중앙값 아래) 움직임만으로는 못 찾고 통째로 잘림
    (판정 round6: S1 118~122초 시범이 빠짐 · 앞뒤 말 '하나, 둘, 셋' → '나이스!'만 남음)."""
    cuts = sorted(float(c) for c in sig.get("cuts") or ())
    for c1, c2 in zip(cuts, cuts[1:]):
        if a - 1.5 <= c1 < b - 1.0 and c2 - c1 >= 2.0 and c2 <= b + 1.5:
            lo, hi = max(a, c1 + 0.05), min(b, c2 - 0.05)
            if hi - lo >= 2.0:
                return lo, hi
    return None


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


EMPH_DUP = 8      # 말을 그대로 옮긴 강조 글자가 이 글자 수(띄어쓰기·문장 부호 빼고)보다 길면 안 띄움
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


def _sentence_head(lines, k, junk=(), most=2, gap=1.6):
    """말 k 가 든 문장의 첫 말 번호: 앞 말이 문장 끝(. ? ! …) 없이 gap 초 안에 이어지면 같은 문장 —
    받아쓰기가 낱말 시각을 뭉개 쉼처럼 적으면 문장이 '패스하고 그' | '자리에 서 있으면 왜 안 될까요?'로 잘림
    (판정: 첫 질문을 앞으로 옮기고 본편에서 뺐더니 '패스하고 그'만 남음 · 훅 글자도 앞부분이 빠져 뜻이 덜 전해짐).
    정리할 곳(NG·끊긴 말)이나 홀로 쓰인 추임새는 문장 첫머리로 잇지 않음."""
    j = k
    while j > 0 and k - j < most:
        p = lines[j - 1]
        t = str(p.get("text") or "").strip()
        if re.search(r"[.?!…]$", t) or float(lines[j]["start"]) - float(p["end"]) > gap or INTERJ.match(t) \
                or _in_spans((float(p["start"]) + float(p["end"])) / 2, junk):
            break
        j -= 1
    return j


def _recap_items(lines, k):
    """'오늘 배운 거 정리해 볼게요. 고개 들기, 디딤발 거리, 그리고 다음 방향으로 받기.' → ('① 고개 들기\n② 디딤발 거리\n③ 다음 방향으로 받기',
    목록 말 끝, 목록 말 시작) — 정리 말 뒤(같은 말 또는 바로 다음 말)의 쉼표 목록이 2~4개이고 하나가 12글자 안일 때만 · 아니면 None."""
    txt = str(lines[k].get("text") or "")
    m = RECAP.search(txt)
    rest = txt[m.end():]
    rest = re.sub(r"^[^.?!]*[.?!]\s*", "", rest) if re.search(r"[.?!]", rest) else ""
    a = float(lines[k]["start"])
    b = float(lines[k]["end"])
    j = k
    if rest.count(",") < 1 and k + 1 < len(lines) and float(lines[k + 1]["start"]) - b < 1.5:
        rest, b = str(lines[k + 1].get("text") or ""), float(lines[k + 1]["end"])
        a = float(lines[k + 1]["start"])
        j = k + 1
    # 쉼에서 쪼개진 목록('고개 들기, 디딤발 거리, 그리고' / '다음 방향으로 받기.')은 다음 말까지 (판정 round5 최종: 정리 카드에 셋째가 빠짐)
    while re.search(r"(?:,|그리고|또|마지막으로)\s*$", rest.strip()) and j + 1 < len(lines) and float(lines[j + 1]["start"]) - b < 1.5:
        j += 1
        rest, b = rest.rstrip() + " " + str(lines[j].get("text") or ""), float(lines[j]["end"])
    rest = re.split(r"(?<=[.?!])\s", rest.strip(), maxsplit=1)[0]   # 목록 문장까지만
    parts = [re.sub(r"^(?:그리고|또|마지막으로)\s*", "", x.strip(" .!?~")) for x in re.split(r",|\s그리고\s", rest)]
    parts = [x for x in parts if x]
    if not (2 <= len(parts) <= 4) or any(len(x.replace(" ", "")) > 12 or len(x.replace(" ", "")) < 2 for x in parts):
        return None
    return "\n".join(f"{CIRCLED[i]} {x}" for i, x in enumerate(parts)), b, a


RECAP_LINES = 2   # 정리 카드 한 장의 줄 수 (판정 도구 글자 배치 규칙: 화면 글자 한 장 2줄까지 · D-263)


def _recap_said(items, words, a, b):
    """정리 목록 항목마다 말하기 시작한 원본 시각 (못 찾으면 None) — 항목 순서대로, 앞 항목을 찾은 낱말 뒤에서만 찾음 (항목 첫 낱말의 앞 두 글자 ·
    한 글자 낱말('발 안쪽으로')은 그 글자) — 뒤 항목이 앞 항목과 같은 낱말로 시작해도('디딤발 위치' · '디딤발 방향') 앞 것을 잡지 않게 (검토 · D-269)."""
    ws = sorted([(float(w[0]), re.sub(r"[^\w]", "", str(w[2]))) for w in words if a <= float(w[0]) <= b], key=lambda w: w[0])
    out, k = [], 0
    for it in items:
        toks = re.sub(r"^[①-⑦]\s*", "", it).split()
        head = re.sub(r"[^\w]", "", toks[0])[:2] if toks else ""
        j = next((j for j in range(k, len(ws)) if head and ws[j][1].startswith(head)), None)
        out.append(ws[j][0] if j is not None else None)
        if j is not None:
            k = j + 1
    return out


def _recap_cards(m, words):
    """정리 말 순간(목록 '① …\n② …\n③ …') → 카드 [(글, 원본 시각, 길이)] — 2줄씩 나누고, 다음 장은 그 장 첫 항목을 말하기 시작할 때
    (_recap_said · 못 찾으면 목록 말 시간을 줄 수로 나눔) · 마지막 장은 다 말한 뒤 1.5초까지 (예전 한 장과 같음)."""
    items = [x for x in str(m.get("text") or "").split("\n") if x.strip()]
    t0 = float(m["t"]) + 0.3
    end = t0 + min(7.0, max(2.5, float(m["b"]) - float(m["t"]) + 1.5))
    if len(items) <= RECAP_LINES:
        return [(m["text"], t0, end - t0)]
    groups = [items[i:i + RECAP_LINES] for i in range(0, len(items), RECAP_LINES)]
    said = _recap_said(items, words, float(m["t"]), float(m["b"]) + 0.5)
    starts = [t0]
    for gi in range(1, len(groups)):
        at = said[gi * RECAP_LINES]
        prev = starts[-1]
        starts.append(max(prev + MIN_TEXT + 0.6, at - 0.1) if at is not None else prev + (end - t0) * len(groups[len(starts) - 1]) / len(items))
    out = []
    for k, (g, a) in enumerate(zip(groups, starts)):
        b = starts[k + 1] - 0.05 if k + 1 < len(starts) else max(end, a + 1.5)
        out.append(("\n".join(g), round(a, 2), round(max(MIN_TEXT, b - a), 2)))
    return out


def _cue_ok(lines, k, acts=()):
    """줄 k 의 시작 신호 말(CUE)이 정말 시작 신호인지 (D-373 · D-379): 신호 낱말이 짧은 마지막 구절(쉼표·마침표 뒤 · 준비·숫자 말 빼고 한
    낱말 더까지)에 있고, 그 줄이나 바로 앞 줄(2초 안)에 준비·경고·숫자 세기 말('준비'·'하면 … 혼난다'·'하나 둘')이 있거나, 분명한 시작 말
    ('시작'·'출발')이면 CUE_PLAY 초 안에 공 소리·시범(acts · 원본 시각)이 이어질 때만. 예전: CUE 만 봐서 '오늘은 패스부터 시작.'·'천천히 가자.'·
    '다음 영상에서 또 만나요. 가자!'에도 꼭 넣을 호루라기."""
    txt = str(lines[k].get("text") or "").strip()
    m = CUE.search(txt)
    if not m:
        return False
    head = txt[:m.start() + 1] if m.group(0)[:1] in ",.!?…" else txt[:m.start()]
    last = re.split(r"[,.!?…]", head)[-1]
    if len([w for w in last.split() if not CUE_FREE.fullmatch(w)]) > 1:
        return False
    a = float(lines[k]["start"])
    ctx = head + m.group(0)   # ('레디 고!'는 그 자체가 준비 말)
    if k > 0 and a - float(lines[k - 1]["end"]) <= 2.0:
        ctx = str(lines[k - 1].get("text") or "") + " " + ctx
    if CUE_CTX.search(ctx):
        return True
    end = float(lines[k]["end"])
    return bool(CUE_STRONG.search(m.group(0))) and any(end - 0.2 <= float(x) <= end + CUE_PLAY for x in acts)


def moments(sig, segs=None):
    """재미 순간 목록 [{kind, a, b, t, score(0~1), text, why}] — 정리할 곳(NG·추임새) 안의 것은 뺌."""
    segs = lines_of(segs if segs is not None else editor._segments_of(sig["name"]))
    words = _words(segs)
    tidy = keep_cuts(sig.get("tidy") or [{"in": 0.0, "out": sig["duration"]}], sig.get("demo"))
    junk = sig.get("junk") or []
    dur = sig["duration"]
    out = []

    def add(kind, a, b, t, score, text="", why="", **kw):
        if t is None or _in_spans(t, junk) or not _kept(t, tidy):
            return None
        out.append(dict({"kind": kind, "a": round(a, 2), "b": round(b, 2), "t": round(t, 2), "score": float(score), "text": text, "why": why}, **kw))
        return out[-1]

    # 시범(플레이): 말 없는 틈(DEMO_GAP 넘게) 안의 움직임 봉우리·순간 큰 소리 (공 소리 후보는 웃음 터지는 소리를 빼고 시범 안 말과 겹친 공 소리를 더함 · D-376)
    mz = _motion_z(sig)
    onsets = _kicks(sig)
    laughs = _spans((sig.get("tags") or {}).get("laugh"), sig.get("tagStep", 0.48), LAUGH_THR)
    laughs_j = _spans((sig.get("tags") or {}).get("laugh"), sig.get("tagStep", 0.48), LAUGH_JOKE)
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
        cuts_ = [float(c) for c in sig.get("cuts") or ()]
        # (장면 바뀜 순간의 움직임 봉우리는 화면이 바뀐 것이지 움직임이 아님)
        mot = [(t, z) for t, z in mz if a2 <= t <= b2 and not any(abs(t - c) <= 0.3 for c in cuts_)]
        peak = max(mot, key=lambda x: x[1]) if mot else (None, 0.0)
        if not ons and sig.get("demo"):  # 공 소리가 없으면 남기는 시범 구간 안의 움직임으로 (그 밖 봉우리는 편집에서 빠지는 곳)
            mot_in = [(t, z) for t, z in mot if any(float(d[0]) <= t <= float(d[1]) for d in sig["demo"])]
            if mot_in and max(z for _, z in mot_in) >= 1.5:
                peak = max(mot_in, key=lambda x: x[1])
        cw = _cutaway(sig, a, b) if not ons else None
        if cw and peak[1] < 1.5:
            peak = ((cw[0] + cw[1]) / 2, 1.5)   # 다른 장면으로 끼운 시범 (움직임이 작아도)
        if not ons and peak[1] < 1.5:
            continue
        if not ons and not any(a - 0.3 <= c <= b + 0.3 for c in sig.get("cuts") or ()):
            # 공 소리 없이 움직이기만 하고 앞뒤 말과 같은 장면(장면 바뀜 없음)이면 말하는 사람이 움직인 것(물 마시기·몸짓) — 시범이 아님
            # (판정 round5: '다시 보기 ▶'가 감독님이 말하는 장면 위에 떠서 무엇을 다시 보는지 모름 · S1 물 마시는 장면 · S2 22초)
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
            # 말 한 구절을 그대로 옮긴 긴 강조 글자('발 쪽으로 정확하게 패스해주세요!')는 같은 때의 말 자막과 같은 글이 두 번 → 글자는 안 띄우고
            # 순간만 남김 (확대로 강조 · 판정: 강조 자막이 바로 위 대사 자막을 거의 그대로 되풀이)
            dup = len(_norm_txt(lab)) > EMPH_DUP and _norm_txt(lab).rstrip("!") in _norm_txt(txt)
            add("emphasis", a, b, t, 1.0 + sc, "" if dup else lab, "기술 이름·강조 낱말")
        if SURPRISE.search(sp):
            add("surprise", a, b, _word_at(words, SURPRISE.pattern, k) or a, 1.5, txt, "놀람 말")
        if SUCCESS.search(txt):
            # 반응 말 묶음의 시작 ('와, 이거죠.' + '완벽해요.' · '그렇죠.' + '이게 완벽한 …') — 효과음은 그 앞(보이는 결과)에 (D-372)
            r0 = a
            for x in reversed(segs[:k]):
                if r0 - float(x["end"]) > REACT_GAP or not REACT.search(str(x.get("text") or "").strip()) or len(str(x.get("text") or "")) > 14:
                    break
                r0 = float(x["start"])
            strong = bool(re.search(SUCCESS.pattern.replace("|완벽|좋아요", ""), txt))
            # '좋아요'·'좋습니다'·'완벽'은 설명 중에도 씀 → 반응으로 시작하는 말이고 바로 앞(KICK_REACT 초 안)에 실제 공 소리가 있을 때만 (D-376)
            led = bool(WEAK_WIN.match(LEAD.sub("", txt))) or r0 < a - 0.01
            if strong or (led and any(r0 - KICK_REACT <= o <= r0 + 0.3 for o in onsets)):
                add("success", a, b, _word_at(words, SUCCESS.pattern, k) or a, 2.0, txt, "성공 말", react0=round(r0, 2))
        if FAIL.search(txt):
            add("fail", a, b, _word_at(words, FAIL.pattern, k) or a, 2.0, txt, "아쉬움 말")
        # 숫자 세기: 쉼에서 쪼개진 줄('하나,' / '둘, 셋 리듬으로 …')은 이어서 봄 · 순간 시각은 '하나'를 말한 때 (틱·숫자 글자가 그 뒤 3초 안의
        # 숫자 말을 찾음 · 예전: 줄 시작 → 앞에 다른 말이 붙은 줄이면 숫자를 못 찾음 · D-373)
        c_txt, c_b = txt, b
        if not COUNT.search(txt) and re.search(r"하나[,\s]*(?:둘[,\s]*)?$", txt):
            for x in segs[k + 1:k + 3]:
                if float(x["start"]) - c_b > 1.6:
                    break
                c_txt, c_b = c_txt + " " + str(x.get("text") or "").strip(), float(x["end"])
                if COUNT.search(c_txt):
                    break
        if COUNT.search(c_txt):
            t_c = _word_at(words, r"^하나", k)
            add("count", a, c_b, a if t_c is None else t_c, 1.0, c_txt, "숫자 세기")
        if SECTION.search(txt):
            # 순간 시각은 군말('자,'·'그리고') 뒤 첫 낱말 — 줄 시작이면 '자,'가 정리할 군말 안이라 순간째 빠졌음 (S1 '자, 첫 번째 포인트' 0/36 · D-379)
            t_s = next((w0 for w0, _, w, j in words if j == k and not SECTION_LEAD.fullmatch(w.strip())), a)
            add("section", a, b, t_s, 1.0, txt, "다음 순서로 넘어감")
        tt = TOTAL.search(txt)
        if tt and a < dur * 0.3:  # 챌린지 전체 횟수 ('다섯 번 차서')
            add("total", a, b, a, 1.0, tt[1], "챌린지 횟수")
        if CLOSING.search(txt) and a > dur * 0.6:
            add("closing", a, b, a, 1.0, txt, "마무리 말")
        if DEMO_W.search(txt):
            add("demo_call", a, b, a, 1.0, txt, "시범 예고")
        if HOOK_Q.search(txt) and not ROUTINE_Q.search(txt) and len(txt.replace(" ", "")) >= 6:
            h = _sentence_head(segs, k, junk)
            qa, qtxt = float(segs[h]["start"]), " ".join(str(x.get("text") or "").strip() for x in segs[h:k + 1])
            add("question", qa, b, qa, 1.5 + (1.0 if HOOK_W.search(txt) else 0.0), qtxt, "질문")
            if first_q is None and a < dur * 0.3:
                first_q = (qa, b, qtxt)
        aside = next((lab for rx, lab in ASIDES if re.search(rx, txt)), None)
        if aside and not JOKE.search(txt):
            add("aside", a, b, min(b - 0.2, a + 1.0), 1.0, aside, "딴소리·여담")
        if RECAP.search(txt):
            items = _recap_items(segs, k)
            if items:
                add("recap", a, items[1], items[2], 2.0, items[0], "오늘 배운 것 정리")
        j_txt, j_b = txt, b
        # 쉼에서 쪼개진 문장('저도 처음엔' / '잘 못했어요.')은 문장 끝까지 이어서 봄 (판정 round6: S2 펀치라인을 못 찾음 · D-262) — 그 줄만으로
        # 농담이면 잇지 않고(이으면 펀치라인이 다음 포인트 말 위로 감 · 검토 · D-269), 이어서 농담이 되면 그 줄에서 멈춤
        for x in ([] if JOKE.search(txt) else segs[k + 1:k + 3]):
            if re.search(r"[.?!…]$", j_txt) or float(x["start"]) - j_b > 1.6:
                break
            j_txt, j_b = j_txt + " " + str(x.get("text") or "").strip(), float(x["end"])
            if JOKE.search(j_txt):
                break
        if JOKE.search(j_txt):
            la = next((x for x, _ in laughs_j if -0.3 <= x - j_b <= 1.5), None)
            add("punchline", a, j_b, j_b - 0.1, 2.0, j_txt, "농담 말", **({"laugh": la} if la is not None else {}))  # (말 끝 바로 앞 · 정리 컷이 말 끝에서 끝나도 남음)
        for la, lb in laughs_j:  # 말이 끝나고 1.5초 안에 웃음 → 그 말이 펀치라인 (조금 작은 웃음도 · LAUGH_JOKE)
            if 0 <= la - b <= 1.5:
                add("punchline", a, b, b - 0.1, 3.0, txt, "말 끝에 웃음소리", laugh=la)
                break
        if CUE.search(txt) and _cue_ok(segs, k, onsets + [p["a"] for p in out if p["kind"] == "play"]):  # 시작 신호 ('혼난다. 시작!') — 신호 말 끝에 호루라기 (D-373)
            w_end = max([e for s0, e, w, j in words if j == k] or [b])
            add("cue", a, b, w_end, 1.0, txt, "시작 신호 말")
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
        if m["kind"] == "success" and dedup and dedup[-1]["kind"] == "success" and dedup[-1].get("react0") == m.get("react0"):
            # 한 반응 묶음('들어갔어요.' + '나이스.')은 성공 하나 — 예전: 줄마다 성공 소리 둘(같은 골에 레벨업2·레벨업) · D-379
            dedup[-1]["b"] = max(dedup[-1]["b"], m["b"])
            continue
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


ZOOM_MAX = 1.3   # MSG 가 쓰는 가장 큰 확대 (듬뿍 쾅 확대)


def zoom_anchor(fc, fb, most=ZOOM_MAX, margin=0.02):
    """확대 기준점 — 얼굴 가운데지만, 가장 크게 확대해도 머리 꼭대기(얼굴 상자 위 25%)가 화면 위로 잘리지 않게 위로 올림
    (기준점 a 로 S 배 확대하면 y → a + S(y - a) · 판정: 130% 확대에서 머리 윗부분이 잘림). 작은 확대에도 같은 점을 써서 이어지는 조각끼리
    화면이 튀지 않음 (가장 큰 확대에 맞춘 점은 더 작은 확대에서도 안전)."""
    if not fb or most <= 1.001:
        return fc
    top = float(fb[1]) - 0.25 * (float(fb[3]) - float(fb[1]))
    bound = (most * top - margin) / (most - 1.0)
    return [fc[0], round(max(0.0, min(float(fc[1]), bound)), 3)]


# ---------- 3. 스타일 (다섯 부분) ----------
# 사건 1분당 기준 개수 (보통 · 무게 1) — 무게·양(INTENSITY)을 곱해 이 영상의 예산이 됨
BASE_PER_MIN = {"emphasis": 2.0, "situ": 1.0, "inner": 1.0, "fx": 1.5, "punch_zoom": 2.0, "slowmo_replay": 0.67, "freeze": 0.33,
                "shake": 0.5, "sfx": 3.0, "reaction": 1.0}

PRESETS = {
    "담백 레슨형": {
        "desc": "첫 질문으로 시작 → 작은 제목 · 상자 말 자막 + 초록 상자 강조 자막 조금 · 딩동·휙 정도 · 잔잔한 배경음악",
        "intro": {"type": "hook_line", "clips": 1, "teaserSec": 3.0, "titleCard": True, "flash": False, "letterbox": False, "titleLook": "box",
                  "titleColor": "#0F7A3D"},
        "rhythm": {"keepPause": 0.5, "splitShot": 0, "curve3": [0, 0, 0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.08},
        "captions": {"on": True, "pos": "bottom", "color": "#FFFFFF", "karaoke": False, "box": True, "emphColor": "#FFFFFF", "emphFont": "Pretendard",
                     "emphBox": "#0F7A3D", "emphEffect": "fade", "situLook": "box"},
        "fun": {"punch_zoom": 0.6, "slowmo_replay": 0.5, "freeze": 0.0, "shake": 0.0, "montage": 0.0, "sfx": 0.45, "reaction": 0.3, "replayLook": "plain",
                "perMin": {"emphasis": 1.6, "situ": 0.8, "inner": 0.6, "fx": 0.3}},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "잔잔", "lesson": "잔잔", "demo": "경쾌", "outro": "잔잔"}, "duck": -8.0, "bgmDb": -7.0,
                  "palette": "clean"},
    },
    "예능 MSG형": {
        "desc": "명장면 3개 티저 + 번쩍 → 제목 카드 · 여러 색 예능 자막 · 확대·효과음·슬로모 다시 보기·정지 화면 · 분위기 따라 바뀌는 배경음악",
        "intro": {"type": "teaser", "clips": 3, "teaserSec": 4.5, "titleCard": True, "flash": True, "letterbox": False, "titleLook": "stamp",
                  "titleColor": "#FFE14D"},
        "rhythm": {"keepPause": 0.35, "splitShot": 6.0, "curve3": [4.0, 6.0, 5.0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.12},
        "captions": {"on": True, "pos": "bottom", "color": "#FFFFFF", "karaoke": False, "emphColor": "#FFE14D", "emphFont": "Black Han Sans",
                     "emphEffect": "stamp", "situLook": "box", "capWeight": "Black", "capSize": 62, "capStroke": 9},
        "fun": {"punch_zoom": 1.0, "slowmo_replay": 1.0, "freeze": 1.0, "shake": 1.0, "montage": 1.0, "sfx": 1.0, "reaction": 1.0, "replayLook": "plain",
                "perMin": {"emphasis": 2.4, "situ": 1.2, "inner": 1.4, "fx": 1.4}},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "신남", "lesson": "잔잔", "demo": "경쾌", "outro": "신남"}, "duck": -7.0, "bgmDb": -5.0,
                  "palette": "variety"},
    },
    "쇼츠 하이텐션형": {
        "desc": "2.5초마다 컷 + 번갈아 확대 · 가운데 노래방 자막 · 효과 글자 흔들기 · 처음부터 신나는 배경음악",
        "intro": {"type": "teaser", "clips": 2, "teaserSec": 3.0, "titleCard": False, "flash": True, "letterbox": False, "titleLook": "stamp",
                  "titleColor": "#FFD400"},
        "rhythm": {"keepPause": 0.25, "splitShot": 2.5, "curve3": [2.0, 2.5, 2.5], "tempo": 0, "zoomEvery": 3.0, "zoomScale": 1.15},
        "captions": {"on": True, "pos": "middle", "color": "#FFFFFF", "karaoke": True, "emphColor": "#FFD400", "emphFont": "Black Han Sans",
                     "emphEffect": "pop", "situLook": "box"},
        "fun": {"punch_zoom": 1.3, "slowmo_replay": 0.8, "freeze": 0.6, "shake": 1.5, "montage": 1.0, "sfx": 1.4, "reaction": 1.0, "replayLook": "plain",
                "perMin": {"emphasis": 2.0, "situ": 0.8, "inner": 1.2, "fx": 2.2}},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "신남", "lesson": "신남", "demo": "신남", "outro": "신남"}, "duck": -7.0, "bgmDb": -6.0,
                  "palette": "variety"},
    },
    "다큐 감성형": {
        "desc": "제목 화면으로 시작 + 위아래 검은 띠 · 왼쪽 위 상황 자막 · 명장면 슬로모·흑백 정지 · 효과음은 묵직하게 조금 · 감성 배경음악",
        "intro": {"type": "title_first", "clips": 1, "teaserSec": 3.0, "titleCard": True, "flash": False, "letterbox": True, "titleLook": "film",
                  "titleColor": "#FFFFFF"},
        "rhythm": {"keepPause": 0.6, "splitShot": 0, "curve3": [0, 0, 0], "tempo": 0, "zoomEvery": 0, "zoomScale": 1.06},
        "captions": {"on": True, "pos": "bottom", "color": "#F5F1E8", "karaoke": False, "emphColor": "#FFFFFF", "emphFont": "Do Hyeon",
                     "emphEffect": "fade", "situLook": "plain", "capSize": 56, "capStroke": 7, "capWeight": "Bold"},
        "fun": {"punch_zoom": 0.4, "slowmo_replay": 1.4, "freeze": 1.2, "shake": 0.0, "montage": 0.6, "sfx": 0.35, "reaction": 0.3, "replayLook": "letterbox",
                "perMin": {"emphasis": 1.0, "situ": 1.4, "inner": 0.4, "fx": 0.0}},
        "sound": {"lufs": -14.0, "bgm": True, "moods": {"intro": "감성", "lesson": "감성", "demo": "감성", "outro": "감성"}, "duck": -8.0, "bgmDb": -6.0,
                  "palette": "cinematic"},
    },
}
# 배운 값을 묶어 둘 범위 (이상한 레퍼런스 하나가 터무니없는 양을 만들지 않게) — 기본 스타일 범위의 바깥쪽 조금까지
BANDS = {"keepPause": (0.2, 0.8), "splitShot": (0.0, 12.0), "zoomEvery": (0.0, 20.0), "zoomScale": (1.0, 1.3), "tempo": (0.0, 9.0),
         "perMin": (0.0, 3.0), "weight": (0.0, 1.6), "duck": (-14.0, -6.0), "lufs": (-15.0, -13.0), "bgmDb": (-10.0, -4.0)}
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


SFX_AT_MIN = 3   # 참고 영상 효과음이 이만큼 넘게 잡혀야 자리 비율을 믿음 (D-377)


def learned_sound(pl, fun_pm, cap_style):
    """배운 스타일의 효과음 성격 (D-377) → (팔레트, 자리 비율 {caption, cut, zoom, laugh} | None).
    참고 영상 효과음이 글자·확대·웃음에 붙으면 예능 소리(variety) · 컷에 붙으면 깔끔한 소리(clean) · 효과음을 못 찾았으면 자막 성격
    (예능·섞인 자막 → 예능 소리) — 예전: 늘 예능 소리 · 효과음 양만 배움. fun_pm: 참고 영상 재미 요소 1분 개수 (지금은 안 봄 · 부르는 쪽 그대로)."""
    at = (pl.get("fun") or {}).get("sfxAt") if isinstance(pl, dict) else None
    if isinstance(at, dict) and int(at.get("n") or 0) >= SFX_AT_MIN:
        n = float(at["n"])
        sh = {k: round(int(at.get(k) or 0) / n, 2) for k in ("caption", "cut", "zoom", "laugh")}
        return ("variety" if sh["caption"] + sh["zoom"] + sh["laugh"] >= 0.4 else "clean"), sh
    # 참고 영상에서 효과음을 못 찾았거나 자리를 셀 만큼 없으면 자막 성격을 따름 (예능 자막·쾅 확대가 많은 스타일은 예능 소리 · D-379 —
    # 예전: 효과음 양이 0 이면 자막을 보기 전에 깔끔한 소리 → 도블락 스타일(예능 자막 1분 56개) 30편이 모두 바둠츠·레벨업·뻥을 잃음)
    return ("variety" if cap_style in ("variety", "mixed") else "clean"), None


def learned_aspects(st):
    """배운 스타일(list_styles 한 줄) → 다섯 부분 값 (기본 스타일 범위로 묶음)."""
    p = st.get("params") or {}
    pl = st.get("plan") or {}
    base = _copy(PRESETS["예능 MSG형"])
    intro = pl.get("intro") or {}
    it = intro.get("type") if intro.get("type") in ("teaser", "hook_line", "title_first", "greeting", "cold_open") else "cold_open"
    base["intro"] = {"type": it, "clips": 3 if it == "teaser" else 1, "teaserSec": _clamp(intro.get("teaserSec") or 4, (3.0, 6.0)),
                     "titleCard": bool(intro.get("titleCard")) or it == "title_first", "flash": it == "teaser", "letterbox": False, "titleLook": "stamp",
                     "titleColor": "#FFE14D"}
    every = _clamp(p.get("zoomEvery") or 0, BANDS["zoomEvery"])
    base["rhythm"] = {"keepPause": _clamp(p.get("keepPause") or 0.4, BANDS["keepPause"]), "splitShot": _clamp(p.get("splitShot") or 0, BANDS["splitShot"]),
                      "curve3": [_clamp(x, BANDS["splitShot"]) for x in (p.get("curve3") or [0, 0, 0])][:3],
                      "tempo": _clamp(p.get("tempo") or 0, BANDS["tempo"]), "zoomEvery": every if every >= 2 else 0,
                      "zoomScale": _clamp(p.get("zoomScale") or 1.1, BANDS["zoomScale"])}
    cj = pl.get("captions") or {}
    pm = cj.get("perMin") or {}
    colors = [c for c in (cj.get("colors") or []) if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(c)) and str(c).upper() not in ("#FFFFFF", "#111111", "#000000")]
    sty = cj.get("style") or "speech"
    if colors:
        base["intro"]["titleColor"] = colors[0].upper()
    base["captions"] = {"on": bool(p.get("captions", True)), "pos": p.get("captionPos") if p.get("captionPos") in CAP_Y else "bottom",
                        "color": str(p.get("captionColor") or "#FFFFFF").upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(p.get("captionColor") or "")) else "#FFFFFF",
                        "karaoke": False, "emphColor": colors[0].upper() if colors else "#FFE14D", "emphFont": "Black Han Sans",
                        "emphEffect": "stamp" if sty in ("variety", "mixed") else "pop", "situLook": "box"}
    fun = {x["key"]: float(x.get("perMin") or 0) for x in ((pl.get("fun") or {}).get("items") or []) if isinstance(x, dict) and x.get("key")}
    w = lambda k, base_pm: _clamp(fun.get(k, 0) / base_pm, BANDS["weight"])  # noqa: E731
    # 화면 글자 양(강조·상황·속마음·효과 글자)은 '재미' 부분 (자막 부분은 말 자막 모양만 · 섞기에서 재미를 가져오면 개그 글자도 따라옴)
    base["fun"] = {"punch_zoom": max(w("punch_zoom", 1.0), 0.3), "slowmo_replay": max(w("slowmo_replay", 0.3), w("replay", 0.3)), "freeze": w("freeze", 0.2),
                   "shake": w("shake", 0.3), "montage": 1.0 if fun.get("montage") else 0.0, "sfx": max(w("sfx", 1.5), 0.3),
                   "reaction": w("reaction_cut", 0.5), "replayLook": "plain",
                   "perMin": {"emphasis": _clamp(cj.get("keywordPerMin") or pm.get("emph") or (1.0 if sty in ("variety", "mixed") else 0.6), BANDS["perMin"]),
                              "situ": _clamp(pm.get("situ") or 0.5, BANDS["perMin"]), "inner": _clamp(pm.get("inner") or 0, BANDS["perMin"]),
                              "fx": _clamp(pm.get("sfx") or 0, BANDS["perMin"])}}
    snd = _copy(PRESETS["예능 MSG형"]["sound"])
    snd["palette"], at = learned_sound(pl, fun, sty)   # 효과음 성격·자리도 참고 영상에서 (D-377)
    if at:
        snd["sfxAt"] = at
    snd["lufs"] = _clamp(p.get("lufs") if p.get("lufs") is not None else -14, BANDS["lufs"])
    if not intro.get("bgmAtStart"):
        snd["moods"]["intro"] = "잔잔"
    base["sound"] = snd
    base["desc"] = st.get("desc") or ""
    # 고정 틀 쇼츠의 화면 배치 (BR-256): 쇼츠 값(섞어 배운 스타일이면 byFormat.shorts) · 알려 주기만 하고 MSG 화면 배치(seq_layout)에는 쓰지 않음
    lay = ((p.get("byFormat") or {}).get("shorts") or {}).get("layout") or p.get("layout")
    if isinstance(lay, dict):
        base["layout"] = _copy(lay)
    return base


def preset_list():
    return [{"name": k, "desc": describe_style(v, "보통"), "kind": "preset"} for k, v in PRESETS.items()]


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
    def descs(st):  # 양마다 실제로 하는 것 (편집실 칩에 지금 고른 양의 설명을 보여 줌)
        return {i: describe_style(st, i) for i in INTENSITY}
    pre = [{"kind": "preset", "name": k, "desc": describe_style(v, "보통"), "descs": descs(v), "aspects": {a: describe_aspect(a, v[a]) for a in ASPECTS}}
           for k, v in PRESETS.items()]
    learned = []
    for st in style.list_styles():
        asp = learned_aspects(st)
        learned.append({"kind": "style", "name": st["name"], "desc": st.get("plan_desc") or st.get("desc") or "", "descs": descs(asp),
                        "aspects": {a: describe_aspect(a, asp[a]) for a in ASPECTS}})
    mixes = []
    for m in list_mixes():
        notes = []
        st = {a: _aspect_from(m["aspects"][a], a, notes)[0] for a in ASPECTS}
        mixes.append({"kind": "mix", "name": m["name"], "desc": " · ".join(f"{ASPECT_KO[a]} {m['aspects'][a]['name']}" for a in ASPECTS),
                      "descs": descs(st), "intensity": m["intensity"]})
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


def _per_min_txt(v):
    return f"1분에 {v:.1f}번".replace(".0번", "번")


def describe_aspect(aspect, val, intensity=None):
    """부분 값 → 한 줄 설명 (스타일 섞기 화면 · 편집실 스타일 칩). intensity 를 주면 그 양에서 엔진이 실제로 하는 것만
    (판정: 설명은 '명장면 3개 티저'인데 보통은 1개 · '2.5초마다 컷'인데 5초 · 담백에서는 스타일끼리 같은 컷)."""
    mild = intensity == "담백"
    if aspect == "intro":
        ty = val.get("type")
        n = min(int(val.get("clips") or 3), TEASER_N.get(intensity, 3)) if intensity else int(val.get("clips") or 3)
        if ty == "teaser" and not mild:
            t = f"명장면 {n}개 + 첫 질문 미리 보기" + (" · 번쩍 전환" if val.get("flash") else "")
        elif ty in ("teaser", "hook_line", "title_first") or (mild and ty != "greeting"):
            t = "첫 질문 장면으로 시작 (질문이 있으면)"
        else:
            t = {"greeting": "인사로 시작", "cold_open": "바로 본론"}.get(ty, "바로 본론")
        if ty == "title_first":
            t += " · 그 위에 큰 영화 제목"
        elif ty == "teaser" and not mild and val.get("titleCard"):
            t += " → 본편 첫 장면 위 큰 제목"
        elif val.get("titleCard") or mild:
            t += " · 작은 제목"
        return t + (" · 위아래 검은 띠" if val.get("letterbox") else "")
    if aspect == "rhythm":
        rf = RHYTHM.get(intensity, 1.0) if intensity else 1.0
        s = f"말 사이 {val.get('keepPause', 0.4)}초 넘게 쉬면 자름"
        if mild:
            return s + " · 긴 말은 나누지 않음 (담백은 스타일마다 컷이 같아요)"
        sp = float(val.get("splitShot") or 0)
        if sp and rf:
            s += f" · 긴 말은 {sp / rf:.0f}초마다 나눠 번갈아 확대"
        elif float(val.get("zoomEvery") or 0) and rf:
            s += f" · {float(val['zoomEvery']) / rf:.0f}초마다 확대 컷"
        return s
    if aspect == "captions":
        pos = {"bottom": "아래", "middle": "가운데", "top": "위"}.get(val.get("pos"), "아래")
        font = {"Black Han Sans": "굵은 예능 글씨", "Do Hyeon": "차분한 도현 글씨"}.get(val.get("emphFont"), "깔끔한 글씨")
        return ("노래방 말 자막 (가운데)" if val.get("karaoke") else f"{'상자 ' if val.get('box') else ''}말 자막 {pos}") + f" · 강조 글자는 {font}"
    if aspect == "fun":
        pm = _text_rates(val, intensity)
        parts = [f"{k} {_per_min_txt(v)}" for k, v in pm.items() if v >= 0.05]
        names = {"punch_zoom": "확대", "slowmo_replay": "슬로모 다시 보기", "freeze": "정지 화면", "shake": "흔들기", "montage": "몽타주", "sfx": "효과음"}
        off = set()
        if mild:
            off |= {"freeze", "shake", "montage", "slowmo_replay"}
        elif intensity == "보통":
            off |= {"montage"}
        on = [f"{v}" + ("↑" if float(val.get(k) or 0) >= 1.2 else "") for k, v in names.items() if float(val.get(k) or 0) >= 0.3 and k not in off]
        return " · ".join(parts + on) or "재미 요소 거의 없음"
    m = val.get("moods") or {}
    return (f"배경음악 {m.get('intro', '')}→{m.get('lesson', '')}·{m.get('demo', '')}" if val.get("bgm") else "배경음악 없음") + \
        f" · 말할 때 {abs(int(val.get('duck', -14)))}dB 줄임 · 효과음 {({'clean': '깔끔', 'variety': '예능', 'cinematic': '묵직'}).get(val.get('palette'), '예능')}"


TEXT_KO = {"emphasis": "강조 글자", "situ": "상황 글자", "inner": "속마음", "fx": "효과 글자"}


def _text_rates(fun, intensity=None):
    """재미 부분의 화면 글자 1분 개수 {이름: 개수} (양을 주면 그 양에서 · 담백은 속마음·효과 글자 없음)."""
    pm = (fun or {}).get("perMin") or {}
    mult = INTENSITY.get(intensity, 1.0) if intensity else 1.0
    return {TEXT_KO[k]: (0.0 if intensity == "담백" and k in ("inner", "fx") else float(pm.get(k) or 0) * mult) for k in TEXT_KO}


def describe_style(st, intensity="보통"):
    """스타일 전체(다섯 부분) → 그 양에서 실제로 하는 것 한 줄 (PRESETS 의 desc 대신 엔진 값으로 만듦)."""
    return " · ".join(describe_aspect(a, st[a], intensity) for a in ("intro", "rhythm", "fun"))


# ---------- 4. 사건 고르기 ----------
SFX_PALETTE = {  # 사건(뜻) → 효과음 (없으면 소리 없음 · 뜻 표 D-373)
    # 속마음 '(집중)'·'(생각 중…)'·'(TMI)'·'(뿌듯)'에는 소리 없음 (예전: 모두 띠용) · 실수에 띠로리(놀림) 없음 — 듬뿍만 작은 '퍽' ·
    # 농담은 웃음이 있으면 스스로 낮추는 농담에도 (같이 웃음) · 다음 포인트 = 휙 · 숫자는 하나하나 틱 · 시작 신호 = 호루라기 ·
    # 확대 휙은 웃음·반응 순간의 확대에만 (zoom) · soft = 말 위에 작게 놓는 짧은 소리
    "clean": {"success": "딩동", "section": "휙", "replay": "휙", "title": "짠", "count": "틱", "teaser": "휙", "emphasis": "팡",
              "freeze": "찰칵", "montage": "휙", "end": "맑은 짧은 음악", "cue": "호루라기", "soft": "팡"},
    "variety": {"success": "레벨업", "fail": "퍽", "surprise": "두둥", "punchline": "바둠츠", "kick": "뻥", "emphasis": "뽁",
                "section": "휙", "count": "틱", "freeze": "찰칵", "replay": "휙", "title": "짠", "teaser": "휙", "montage": "휙",
                "end": "경쾌 짧은 음악", "cue": "호루라기", "zoom": "휙", "soft": "뽁"},
    "cinematic": {"kick": "퍽", "replay": "라이저", "freeze": "찰칵", "title": "쿵", "success": "맑은 짧은 음악", "teaser": "휙", "section": "휙",
                  "count": "틱", "end": "맑은 짧은 음악", "soft": "딸깍"},
}
SFX_ALT = {"레벨업": ("레벨업2", "딩동"), "맑은 짧은 음악": ("딩동",), "쿵": ("짠",), "라이저": ("휙",), "경쾌 짧은 음악2": ("경쾌 짧은 음악",)}   # 말 틈이 짧으면 더 짧은 같은 뜻 소리
CLEAN_ONLY = {"휙", "딩동", "짠", "틱", "딸깍", "팡", "맑은 짧은 음악", "찰칵", "물음표", "호루라기"}   # 담백: 이 소리들만
TEXT_KINDS = ("emphasis", "situ", "inner", "fx", "count", "score")
INNER_MIN = 2.0   # 속마음 글자가 떠 있는 최소 시간(초)
SHORTS_HOOK_SEC = 2.5   # 쇼츠 첫 훅 글자 시간(초)
ORD = {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7}
TOTAL = re.compile(r"(다섯|열|세|네|여섯|일곱|여덟|아홉|[3-9]|10) ?(?:번|개|회) ?(?:차|도전|던지|해 ?보|시도|슛|슈팅)")


def _seed(*parts):
    return int(hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:8], 16)


CIRCLED = "①②③④⑤⑥⑦"


def _cap_dup(text, t0, t1, caps):
    """화면 글자가 같은 때(원본 [t0, t1]) 보이는 말 자막을 되풀이하는지 — 글자(띄어쓰기·문장 부호·괄호 빼고)가 말 자막 안에 그대로 들어 있거나
    말 자막이 글자 안에 다 들어 있으면 같은 글 (판정: '대박!' ← '어? 이것도 들어갔어요. 대박!' · '완벽!' ← '완벽해요.' ·
    '두 번째 포인트' ← '두 번째 포인트는 디딤발이에요.' · 예전에는 거의 통째로 옮긴 것만 봄)."""
    n = re.sub(r"[()]", "", _norm_txt(text))
    if len(n) < 2:
        return False
    for c in caps or ():
        if float(c["end"]) <= t0 or float(c["start"]) >= t1:
            continue
        cn = _norm_txt(c.get("text"))
        if cn and (n in cn or cn in n):
            return True
    return False


TITLE_SHIFT = 4.0   # 작은 제목을 같은 말 자막 뒤로 미루는 한도 (초)


def _title_after_said(B, s0, ln, topic, caps, main_end):
    """작은 제목('슈팅 챌린지')이 보이는 동안 본편 말 자막이 같은 말('오늘은 슈팅 챌린지예요')을 하면 → (시작, 길이):
    그 말이 1.2초 넘게 뒤에 나오면 제목을 그 전에 끝내고(첫 질문 장면 위 제목), 아니면 그 자막이 끝난 뒤로 미룸(TITLE_SHIFT 초 안·본편 안) ·
    둘 다 안 되면 그대로 (판정 round5: 제목과 자막이 같은 말을 한꺼번에)."""
    if not topic:
        return s0, ln
    for it in sorted(B.main, key=lambda x: float(x["start"])):
        a, b = max(s0, float(it["start"])), min(s0 + ln, editor.i_end(it))
        if b <= a:
            continue
        sp = editor.i_sp(it)
        src_a, src_b = float(it["in"]) + (a - float(it["start"])) * sp, float(it["in"]) + (b - float(it["start"])) * sp
        for c in sorted(caps or (), key=lambda c: float(c["start"])):
            if float(c["end"]) <= src_a or float(c["start"]) >= src_b or not _cap_dup(topic, float(c["start"]), float(c["end"]), [c]):
                continue
            said = max(a, B.src_to_tl(float(c["start"])) or a)
            if said - s0 >= 1.2:
                return s0, round(said - s0 - 0.05, 3)
            e = B.src_to_tl(float(c["end"]) - 0.02)
            if e is not None and s0 < e + 0.1 <= s0 + TITLE_SHIFT and e + 0.1 + ln <= main_end:
                return round(e + 0.1, 3), ln
            return s0, ln
    return s0, ln


def _dedupe_text(kind, text, t, dur, caps, alts=(), term=""):
    """같은 때 말 자막을 되풀이하는 화면 글자 → 새 정보로 바꾼 (글, 원본 시각) 또는 None (안 띄움 · 그 순간은 확대가 맡음):
    상황 자막은 꼬리표('동작 ①') · 효과 글자는 같은 뜻의 다른 문구 · 강조는 그 말 자막에 없는 근처 기술 이름('디딤발 체크!') ·
    속마음은 안 띄움 (판정: 말 자막 안의 낱말을 다시 띄우면 같은 말이 두 번)."""
    if not caps or not _cap_dup(text, t - 0.1, t + dur, caps):
        return text, t
    if kind == "situ":
        b = _situ_badge(text)
        return (b, t) if b and not _cap_dup(b, t - 0.1, t + dur, caps) else None
    if kind == "fx":
        o = next((o for o in alts if o != text and not _cap_dup(o, t - 0.1, t + dur, caps)), None)
        return (o, t) if o else None
    # 강조는 안 띄움 (그 순간은 확대가 맡음) — 예전에는 근처(12초 안) 기술 이름('패스 체크!'·'수비 체크!')으로 바꿨으나 지금 말과 안 맞음
    # ('완벽해요' 순간에 '패스 체크!' · '이게 진짜 핵심이에요'에 '수비 체크!' · 판정 round5 최종)
    return None


def _situ_badge(text):
    """말 자막과 같은 상황 자막('첫 번째 동작') → 같은 뜻의 짧은 꼬리표('동작 ①') · 순서 말이 없으면 None (그 글자는 안 띄움)."""
    mm = re.search(r"(첫|두|세|네|다섯|여섯|일곱) 번째 ([가-힣A-Za-z]+)", text or "")
    if not mm or ORD.get(mm[1], 0) > len(CIRCLED):
        return None
    return ("마지막! " if str(text).startswith("마지막") else "") + f"{mm[2]} {CIRCLED[ORD[mm[1]] - 1]}"


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


SERIES = re.compile(r"^(마지막! )?(첫|두|세|네|다섯|여섯|일곱) 번째 ([가-힣A-Za-z]+)$")
BADGE = re.compile(r"^(마지막! )?([가-힣A-Za-z]+) ([①②③④⑤⑥⑦])$")


def _series_badges(cands):
    """번호가 붙은 상황 자막 묶음('첫 번째 포인트'·'두 번째 포인트'… · 시도 '두 번째 슛'·받아쓰기가 놓친 '세 번째 도전')을 한 가지 꼴로 —
    말한 순서 말은 늘 같은 때 말 자막과 같은 글이 되므로 짧은 꼬리표('포인트 ②')로 · 이름은 그 묶음에서 가장 많이 말한 것
    (판정: '첫 번째 도전' → '슛 ②' → '세 번째 도전'처럼 한 영상 안에서 꼴이 바뀌면 실수로 보임)."""
    ser = []
    for c in cands:
        if c["kind"] != "situ" or c.get("look") == "recap":
            continue
        m = SERIES.match(c.get("text") or "")
        if m:
            ser.append((c, m[1] or "", m[3], ORD.get(m[2], 0)))
            continue
        m = BADGE.match(c.get("text") or "")   # 이미 꼬리표가 된 것 ('슛 ②' · 말 자막과 같아 바꾼 것)도 같은 묶음으로
        if m:
            ser.append((c, m[1] or "", m[2], CIRCLED.index(m[3]) + 1))
    if not ser:
        return
    groups = {}
    for c, pre, noun, n in ser:  # 이름이 다르면(동작·포인트) 다른 묶음 · 도전은 그 영상의 시도 묶음(슛)에 합침
        groups.setdefault("try" if noun in ("도전", "슛", "슈팅", "시도") else noun, []).append((c, pre, noun, n))
    for g in groups.values():
        names = [x[2] for x in g if not x[0].get("must")] or [x[2] for x in g]
        noun = max(dict.fromkeys(names), key=names.count)
        for c, pre, _, n in g:
            if 1 <= n <= len(CIRCLED):
                c["text"] = pre + f"{noun} {CIRCLED[n - 1]}"


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


def text_looks(st, fmt, cap_y, card=False, skin=None):
    """사건별 글자 모양 (위치는 말 자막 자리에서 0.15 넘게 떨어뜨림).
    card: 은평 스타일 쇼츠 — 위 띠의 흰 제목 상자·사실 줄(화면 위 0.095~0.2) 아래, 영상 창 위 끝(0.28) 바로 위로 상황 자막을 내림 (BR-242).
    skin: 풋살사관학교 스킨 영상 창 {top, bottom} — 상황 자막·점수판은 영상 창 맨 위 줄 (위 칠판 띠 바로 아래 · BR-323)."""
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
        situ = dict(T, weight="Bold", size=58 if sh else 56, fill="#111111", stroke="#FFFFFF", strokeW=10, bgOn=True, bg="#FFFFFF", bgOpacity=0.85,
                    x=0.07, y=(0.85 if low else 0.15) if not sh else 0.18, align="left", effect="fade", font="Do Hyeon")  # (50 은 '구석에 아주 작게' · 판정)
    else:
        situ = dict(T, weight="Bold", size=58 if sh else 54, fill="#FFFFFF", stroke="#000000", strokeW=10, bgOn=True, bg="#0F7A3D", bgOpacity=0.9,
                    x=0.06, y=(0.86 if low else 0.14) if not sh else 0.18, align="left", effect="slide")
    # 속마음: 1.4초 동안 흐려지며 나오면 반쯤 흐린 채로 지나가 '너무 작고 흐려 거의 안 읽힘' (판정 round5 최종) → 조금 크게·굵은 테두리·바로 나타남
    inner = dict(T, font="Do Hyeon", weight="Bold", size=70 if sh else 72, fill="#9AD7FF", stroke="#000000", strokeW=8, x=0.74 if not sh else 0.7,
                 y=0.45 if not low else 0.6, align="center", effect="pop", rot=-4)
    fx = dict(T, font="Black Han Sans", weight="Black", size=150 if sh else 132, fill="#FF9F1C", stroke="#111111", strokeW=10, x=0.5,
              y=(0.5 if not low else 0.6) if not sh else 0.48, align="center", effect="shake", rot=0)
    # 점수판은 휴대폰에서도 읽히게 크게 (판정: 점수판 글씨가 작아 휴대폰에서 읽기 어려움)
    score = dict(situ, bgOn=True, bg="#111111", bgOpacity=0.8, fill="#FFE14D", x=0.94, y=0.15 if not low else 0.86, align="right", effect="pop",
                 font="Black Han Sans", weight="Black", size=68 if not sh else 72)
    if sh:
        score.update(y=0.27)
    if sh and card:
        situ["y"] = EP_ROW
    if sh and skin:
        situ["y"] = _skin_row(skin["top"], situ)
        score["y"] = _skin_row(skin["top"], score)
    return {"emphasis": emph, "situ": situ, "inner": inner, "fx": fx, "score": score,
            "count": dict(fx, fill="#FFFFFF", size=140 if sh else 120, effect="stamp")}


# ---------- 양(담백·보통·듬뿍)에 맞춘 빠르기 ----------
# 화면이 바뀌는 사건(점프 컷·확대가 바뀜·끼워 넣은 장면·글자·도형이 나타남)의 1분 개수 범위와, 아무것도 안 바뀌는 가장 긴 시간(초).
# 담백 = 깔끔한 레슨(긴 쉼만 자르고 글자는 꼭 필요한 것만) · 보통 = 적당히 · 듬뿍 = 예능처럼 자주.
DENSITY = {"담백": (3.3, 5.8, 14.6), "보통": (6.5, 11.6, 9.85), "듬뿍": (10.8, 19.3, 5.85)}
# 효과음 + 배경음악 곡 수의 1분 개수 범위 (스타일의 효과음 무게로 범위 안 자리를 정함)
AUDIO_DENSITY = {"담백": (1.2, 2.8), "보통": (3.3, 6.6), "듬뿍": (6.3, 11.5)}
PACE = {"담백": {"cuts": 1.0}, "보통": {"cuts": 1.5}, "듬뿍": {"cuts": 2.5}}
# 컷 리듬(긴 말을 나눠 번갈아 확대)을 얼마나 쓰는지: 스타일의 목표 컷 길이를 이 값으로 나눔 (보통은 조금 길게 · 담백은 안 나눔 —
# 판정: 예전에는 모든 양에서 0이라 쇼츠 하이텐션형도 예능과 같은 컷·같은 확대)
RHYTHM = {"담백": 0.0, "보통": 0.55, "듬뿍": 0.85}
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
SLIVER_KEEP = ("play", "punchline", "emphasis", "success", "fail", "surprise", "section", "count", "total", "question", "hook_line", "closing",
               "aside")   # 딴소리('자, 공 좀 가져올게요')도 말한 문장 — 빼면 판정이 '빠진 말'로 봄 (round5)


def drop_slivers(cuts, moms, keep=()):
    """앞뒤가 다 잘린 짧은 조각(SLIVER 초 미만) 빼기 — 재미 순간·장 나눔·마무리 말이 든 조각은 남김 ('물 좀 마시고 할게요' 같은 혼잣말 조각).
    keep: 남길 소리 구간 [(a, b)] (웃음·환호 — 조각의 절반 넘게 겹치면 남김) · 펀치라인 말이 끝나고 1초 안에 시작하는 조각(웃음·반응)도 남김
    (빼면 농담이 웃음 없이 끝남 · 판정 검토 S2 68.1~69.5초 웃음 · D-268)."""
    out = []
    for k, c in enumerate(cuts):
        a, b = float(c["in"]), float(c["out"])
        cut_before = k > 0 and a - float(cuts[k - 1]["out"]) > 0.05
        cut_after = k + 1 < len(cuts) and float(cuts[k + 1]["in"]) - b > 0.05
        if b - a < SLIVER and cut_before and cut_after and not any(m["kind"] in SLIVER_KEEP and a - 0.1 <= m["t"] <= b + 0.1 for m in moms) \
                and not any(min(b, float(y)) - max(a, float(x)) > 0.5 * (b - a) for x, y in keep) \
                and not any(m["kind"] == "punchline" and 0.0 <= a - float(m["b"]) <= 1.0 for m in moms):
            continue
        out.append(c)
    return out


LAUGH_BEAT = (0.9, 1.6)   # 웃음소리가 붙은 농담 말 끝에서 남길 박자(초): 적어도 앞 값 · 웃음이 늦게 터지면 웃음 시작 0.6초 뒤까지(뒤 값까지)


def keep_laugh_beat(cuts, moms, words, junk=(), last=True):
    """웃음소리가 붙은 농담 말(punchline · laugh) 끝에서 조각이 바로 끝나면(정리 컷이 웃음을 쉼으로 봄) 웃음 박자(LAUGH_BEAT)까지 늘림 —
    농담 뒤 효과음이 들어갈 틈 · 웃음도 들림 (예전: 말 끝 0.2초 뒤 컷 → 다음 말까지 0.3초라 바둠츠가 다음 말을 가려 빠짐 · D-375).
    다음 낱말 0.12초 앞·다음 조각·정리할 곳(NG·군말 · junk [(a, b, why)]) 앞까지만 · last=False 면 마지막 조각은 그대로 (쇼츠 길이)."""
    out = [dict(c) for c in cuts]
    for m in moms:
        if m.get("kind") != "punchline" or m.get("laugh") is None:
            continue
        b = float(m["b"])
        for k, c in enumerate(out):
            if not float(c["in"]) < b <= float(c["out"]) + 0.05:
                continue
            if k + 1 < len(out) or last:
                want = min(max(b + LAUGH_BEAT[0], float(m["laugh"]) + 0.6), b + LAUGH_BEAT[1])
                lim = min([want] + [float(w[0]) - 0.12 for w in words if float(w[0]) >= b + 0.05]
                          + ([float(out[k + 1]["in"])] if k + 1 < len(out) else [])
                          + [float(j[0]) for j in junk if float(j[0]) >= float(c["out"]) - 0.05])
                if lim > float(c["out"]) + 0.1:
                    c["out"] = round(lim, 2)
            break
    return out


def _cheers(sig):
    """웃음·환호 소리 구간 [(a, b)] (소리 모델 점수 · moments 와 같은 기준)."""
    tg, step = sig.get("tags") or {}, sig.get("tagStep", 0.48)
    return _spans(tg.get("laugh"), step, LAUGH_THR) + _spans(tg.get("cheer"), step, CHEER_THR)


def _kicks(sig):
    """공 소리 후보 (원본 시각) — 순간 큰 소리(onsets) 중 웃음·환호 소리 안의 것은 빼고(웃음 터지는 소리를 공 소리로 봄), 말하는 중에도 탁 하고
    끝나는 소리(hits) 중 시범 구간(앞뒤 1초) 안의 것을 더함 (말소리와 겹친 공 소리를 놓치던 것 · D-376). 0.25초 안에 붙은 것은 하나."""
    tg, step = sig.get("tags") or {}, sig.get("tagStep", 0.48)
    noisy = _spans(tg.get("laugh"), step, LAUGH_NOISE) + _spans(tg.get("cheer"), step, LAUGH_NOISE)
    demo = [(float(d[0]) - 1.0, float(d[1]) + 1.0) for d in sig.get("demo") or ()]
    xs = [float(o) for o in sig.get("onsets") or ()] + [float(h) for h in sig.get("hits") or () if any(a <= float(h) <= b for a, b in demo)]
    out = []
    for x in sorted(x for x in xs if not any(a - 0.1 <= x <= b + 0.1 for a, b in noisy)):
        if not out or x - out[-1] > 0.25:
            out.append(round(x, 3))
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


def _keep_sound_only(B, e, why):
    """글자 사건을 빼면서 그 사건의 꼭 넣을 뜻 소리(다음 포인트·성공·웃음 뒤 농담·숫자 세기 · meta must)는 남김 → 남겼으면 True (사건은 소리만 ·
    D-375). 소리는 그 자리(글자 첫 장면)에서, 말을 가리면 앞뒤 0.3~0.6초 안 말 틈으로 (_audio_items)."""
    r = e.get("refs") or {}
    snd = [k for k in r.get("sfx") or [] if k < len(B.sfx) and B.sfx[k] and _meta(B.sfx[k]).get("must")]
    if not snd:
        return False
    for k in r.get("sfx") or []:
        if k not in snd and k < len(B.sfx):
            B.sfx[k] = None
    for k in snd:
        x = B.sfx[k]
        tl = float(x[0])
        src = B.tl_to_src(tl)
        win = _tl_win(B, src, tl, src - 0.3, src + 0.6, back=_meta(x).get("mk") == "section") if src is not None else (round(tl - 0.3, 3), round(tl + 0.6, 3))
        if _meta(x).get("lead") is not None and win[0] > _meta(x)["lead"] - 0.4:   # 다음 포인트: 남은 군말('자,') 앞 쉼까지 (D-379)
            win = (round(float(_meta(x)["lead"]) - 0.4, 3), win[1])
        B.sfx[k] = (tl,) + tuple(x[1:4]) + (dict(_meta(x), title=None, win=None if _meta(x).get("group") else win,
                                                 why=f"{_meta(x).get('why') or ''} · 글자 없이 소리만"),)
    e.update(kind="sfx", text=B.sfx[snd[0]][1], why=f"{e.get('why') or ''} · 소리만 ({why})", refs={"sfx": snd}, accent=True,
             moment=e.get("moment") or {"kind": e["kind"]})
    return True


def _drop(B, title_ids=(), event_ids=(), keep_sound=True):
    """글자·사건 빼기 (그 사건의 효과음·확대도 함께 · keep_sound 면 꼭 넣을 뜻 소리는 소리만 남김 · D-375)."""
    title_ids, event_ids = set(title_ids), set(event_ids)
    by = {it["id"]: it for it in B.items}
    keep = []
    for e in B.events:
        r = e.get("refs") or {}
        if r.get("titles"):
            r["titles"] = [x for x in r["titles"] if x not in title_ids]
        if e["id"] in event_ids or (e["kind"] in TEXT_KINDS and "titles" in r and not r["titles"]):
            if e["kind"] in TEXT_KINDS:
                old_titles = set(r.get("titles") or [])
                if keep_sound and _keep_sound_only(B, e, "글자를 뺌"):   # 꼭 넣을 뜻 소리는 글자를 빼도 소리만 남김 (D-375)
                    title_ids |= old_titles
                    keep.append(e)
                    continue
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


DROP_ORDER = ("inner", "fx", "shake", "punch", "emphasis", "situ", "score")   # 뺄 수 있는 사건 종류 (빼는 차례는 스타일 무게 _keep_value)
ACCENT_KEY = {"emphasis": "emphasis", "success": "success", "fail": "fail", "punchline": "punchline", "play": "kick", "surprise": "surprise"}   # 소리만 남길 때 쓸 소리 (D-262)
# 재미 순간 지키기(순간마다 사건 하나 · 양 맞추기에서 지키기 · 못 지키면 소리만 · D-262)를 하는 양 — 듬뿍만 (D-269: 보통에서는 화면 사건 양 범위가
# 좁아 지키려고 숫자 세기·상황 자막을 먼저 빼고 순간을 소리만 남겨, 화면에 보이는 순간이 예전보다 줄고 말 위에 효과음만 늘어남 · 판정 검토)
VITAL_LEVELS = ("듬뿍",)
ACCENT_GAP = 0.35   # 소리만 남길 효과음은 말이 이만큼(초) 쉬는 틈에서만 (말 첫소리·한가운데 위 '팡'은 엉뚱 · 판정 round7 · D-269)


def _accent_slot(B, words, m, src):
    """소리만 남길 효과음 자리 (타임라인 시각 · 없으면 None): 그 순간(앞 0.3초 ~ 뒤 0.8초) 안에서 말이 ACCENT_GAP 초 넘게 쉬는 곳 중
    원본 시각 src 에 가장 가까운 곳 (src 자리가 비었으면 그대로 · 낱말이 끝난 0.05초 뒤 · 편집본에 남은 곳만) (D-269)."""
    lo, hi = float(m["a"]) - 0.3, float(m["b"]) + 0.8
    ws = [(float(w[0]), float(w[1])) for w in words or () if float(w[1]) > lo - 1.0 and float(w[0]) < hi + 1.0]
    cands = [float(src)] + [e + 0.05 for _, e in ws if lo <= e + 0.05 <= hi]
    for g in sorted(cands, key=lambda g: abs(g - float(src))):
        if any(a - 0.05 < g + ACCENT_GAP and g < b for a, b in ws):
            continue
        tl = B.src_to_tl(g)
        if tl is not None:
            return round(tl, 3)
    return None


def _accent_win(B, m, slot):
    """소리만 남긴 순간의 소리를 옮겨도 되는 타임라인 창 (그 순간 앞 0.3초 ~ 뒤 0.8초 · slot 이 든 본편 조각 안)."""
    src = B.tl_to_src(slot)
    if src is None:
        return (round(slot - 0.3, 3), round(slot + 0.5, 3))
    return _tl_win(B, src, slot, float(m["a"]) - 0.3, float(m["b"]) + 0.8)


STILL_WHY = "오래 멈춰 보이지 않게 살짝 확대"


def _keep_value(e, st):
    """양 범위를 넘을 때 덜 빼는 정도 (클수록 나중에 뺌) — 스타일이 무게를 둔 것을 남김: 예능·쇼츠는 개그 글자·효과 글자, 다큐는 상황 자막,
    담백은 강조 글자. 같은 무게면 화면 글자(재미 글자)가 확대보다 먼저 남음 (판정: 듬뿍이 거의 확대·휙 소리뿐 · 개그 글자가 먼저 빠짐)."""
    fun = (st or {}).get("fun") or {}
    pm = fun.get("perMin") or ((st or {}).get("captions") or {}).get("perMin") or {}
    k = e["kind"]
    if k in ("emphasis", "situ", "inner", "fx"):
        return 0.2 + 1.3 * float(pm.get(k) or 0) / 1.5
    if k == "punch":
        return float(fun.get("punch_zoom") or 0) * (0.5 if e.get("why") == STILL_WHY else 1.0)
    if k == "shake":
        return 0.7 * float(fun.get("shake") or 0)
    if k == "score":  # 챌린지 점수판: 재미 글자보다 나중에 뺌 (최종 결과는 빼지 않음 · govern)
        return 1.7
    return 1.0


def _rhythm_value(st):
    """컷 리듬(번갈아 확대)을 남기는 정도 — 짧게 자르는 스타일(쇼츠)일수록 큼 (그래도 그 스타일의 효과 글자·강조보다는 먼저 뺌)."""
    sp = float(((st or {}).get("rhythm") or {}).get("splitShot") or 0)
    return 0.3 + min(0.9, 2.0 / sp) if sp > 0 else 0.3


SOFT_LOOKS = ((108.0, 0.0), (111.0, 0.05), (106.0, -0.05))   # 나눠서 살짝 확대할 때 차례로 쓰는 (크기 %, 기준점 좌우 옮김)


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
    if v > 103.0:
        new["fx"] = {}
    else:  # 살짝 확대는 크기·좌우 자리를 돌려 가며 (늘 같은 108% 가운데 확대가 되풀이되면 기계적 · 판정: 듬뿍 확대 16~25번)
        n = getattr(B, "soft_n", 0)
        B.soft_n = n + 1
        sc, dx = SOFT_LOOKS[n % len(SOFT_LOOKS)]
        new["fx"] = {"scale": {"v": sc, "k": []}, "anchor": {"v": [round(min(0.85, max(0.15, fc[0] + dx)), 3), fc[1]], "k": []}}
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


SPLIT_CLEAR = 1.5    # 오래 멈춘 곳을 나눌 때 다른 화면 사건과 이만큼(초)은 떨어뜨림 (0.7초 사이에 두 번 바뀌면 덜컥거림)
SRC_CUT_CLEAR = 1.2  # 원본 자체의 장면 바뀜과도 이만큼은 떨어뜨림
SRC_CUT_SNAP = 0.45  # …다만 이만큼 안이면 바로 그 장면 바뀜 자리에서 (화면이 한 번에 바뀜)
ZOOM_GAP = 1.6       # 확대가 바뀐 뒤 이만큼 안에 화면이 또 바뀌면(확대·컷) 덜컹거림 → 마지막에 덜어 냄 (govern.space)


def govern(B, intensity, fc, total, moms=(), words=(), src_cuts=(), st=None, protect=None):
    """양에 맞춰 화면 사건 수 맞추기 (DENSITY):
    1) 너무 많으면 덜 중요한 것부터 뺌 — 속마음·효과 글자·흔들기 → 컷 리듬으로 번갈아 바꾼 확대 → 확대 → 강조·상황 자막
    2) 오래(한도 초 넘게) 아무것도 안 바뀌는 곳은 본편 클립을 나눠 살짝 확대/원래 크기로 (그 안에 재미 순간이 있으면 그 자리에서)
    3) 보통·듬뿍: 아직 아무 사건도 안 붙은 재미 순간(시범·펀치라인·강조·성공·실패)에 살짝 확대 (범위 위쪽을 넘지 않게)
    4) 그래도 범위 아래쪽보다 적으면 가장 긴 곳부터 더 나눔. 나눈 곳은 '확대' 사건으로 남김 (MSG 목록에서 뺄 수 있게).
    5) 나눈 뒤 범위 위쪽을 넘으면 1)을 한 번 더 (멈춘 화면이 새로 생기지 않는 것만).
    빼는 차례: 같은 종류면 사건이 몰린 곳부터 (예전처럼 뒤쪽부터 빼면 영상 끝부분의 재미 순간만 빠짐 · 판정: 순간 재현율).
    protect: 재미 순간(시범·펀치라인·강조·성공·실패·놀람)을 혼자 보여 주는 사건은 빼지 않음 — 그래도 양 범위를 넘으면 넘은 채로 두고
    compile_seq 가 구성(쉼 컷·숫자 세기·다시 보기 수)을 줄여 다시 만듦 · 그래도 안 되면 protect=False: 그런 사건은 화면 부분만 빼고
    소리(그 순간의 효과음)는 남김 — 화면 사건 양은 지키고 순간은 귀로 살림 (VITAL_LEVELS(듬뿍)만 · 없으면 그 양이면 켬 · 소리는 말이 쉬는 틈에서만 ·
    양 범위는 _audio_items 가 맞춤 · D-262 · D-269). 확대 간격 규칙(space)은 protect 여도 지킴 (그런 사건은 소리만 · D-269).
    나누는 자리: 말과 말 사이를 먼저 · 다른 사건·원본의 장면 바뀜(src_cuts · 원본 시각)과 떨어진 곳 (판정: 0.7초 간격 확대 두 번 ·
    일정한 간격의 확대는 기계적)."""
    lo, hi, still = DENSITY[intensity]
    vital = intensity in VITAL_LEVELS
    protect = vital if protect is None else (bool(protect) and vital)
    mins = max(0.25, total / 60.0)
    kind_rank = {k: i for i, k in enumerate(DROP_ORDER)}
    rv = _rhythm_value(st)
    scut_tl = sorted(t for t in (B.src_to_tl(float(c)) for c in src_cuts or ()) if t is not None)
    gaps_tl = []   # 말과 말 사이 (타임라인)
    ws = sorted(words or ())
    for w0, w1 in zip(ws, ws[1:]):
        if w1[0] - w0[1] >= 0.12:
            t = B.src_to_tl((w0[1] + w1[0]) / 2)
            if t is not None:
                gaps_tl.append(t)

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
    def redundant(e):  # 같은 순간(원본 1.5초 안)을 다른 사건도 보여 줌
        if e.get("src") is None:
            return False
        return any(o is not e and o.get("src") is not None and o["kind"] not in ("bgm", "chapters") and abs(float(o["src"]) - float(e["src"])) <= 1.5
                   for o in B.events)

    key_moms = [m for m in moms or () if m["kind"] in MOMENT_KINDS]

    def covers(e, m):
        return e.get("src") is not None and float(m["a"]) - 1.0 <= float(e["src"]) <= float(m["b"]) + 1.0

    def lone(e):  # 이 사건만 보여 주는 재미 순간 (없으면 None) — 화면에 보이는 사건만 셈 (소리만 남긴 사건은 그 순간을 보여 주지 않음 · D-269)
        return next((m for m in key_moms if covers(e, m) and not any(o is not e and o["kind"] not in ("bgm", "chapters") and not o.get("accent")
                                                                      and covers(o, m) for o in B.events)), None)

    def sole(e):  # 이 사건만 보여 주는 재미 순간이 있음 → 맨 나중에 뺌 (빼면 그 순간이 영상에서 사라짐)
        return lone(e) is not None

    pal = SFX_PALETTE.get(((st or {}).get("sound") or {}).get("palette"), SFX_PALETTE["variety"])

    def accent(e, keep_items=False, why="화면 사건 양"):
        """재미 순간을 혼자 보여 주는 사건을 소리만 남김 (글자·확대를 빼고 · 소리가 없던 사건은 그 순간 효과음을 더함) → 바꿨으면 True.
        VITAL_LEVELS(듬뿍)만 · 소리는 그 순간 안에서 말이 쉬는 틈에만 (_accent_slot · 틈이 없으면 소리도 안 남김) · 그 순간에 맞는 소리가
        팔레트에 없으면(위로하는 말 등) 안 남김 — 예전에는 장 넘김 '딸깍'을 대신 붙여 말 위에 뜻 없는 소리가 났음 (판정 검토 · D-269).
        keep_items: 조각 크기는 그대로 둠 (이미 화면이 안 바뀌는 확대 · D-266)."""
        m = lone(e)
        r = e.get("refs") or {}
        snd = [k for k in r.get("sfx") or [] if k < len(B.sfx) and B.sfx[k]]
        # 듬뿍은 그 순간의 소리를 · 보통은 꼭 넣을 뜻 소리(성공·웃음 뒤 농담·다음 포인트)가 붙은 사건만 소리로 남김 (D-375)
        must = any(_meta(B.sfx[k]).get("must") for k in snd)
        if m is None or not (vital or must) or e["kind"] not in ("emphasis", "situ", "inner", "fx", "punch", "shake") or e.get("src") is None:
            return False
        slot = _accent_slot(B, words, m, float(e["src"]))
        consol = m["kind"] == "punchline" and any(f["kind"] == "fail" and 0 <= float(m["t"]) - float(f["t"]) <= 10.0 for f in key_moms)
        name = B.sfx[snd[0]][1] if snd else (None if consol else pal.get(ACCENT_KEY.get(m["kind"])))
        if slot is None or not name:
            return False
        tids = set(r.get("titles") or [])
        if e["kind"] in ("punch", "shake") and not keep_items:
            for it in B.items:
                if it["id"] in set(r.get("items") or []):
                    it["fx"] = {k: v for k, v in (it.get("fx") or {}).items() if k not in ("scale", "pos", "anchor")}
        B.titles = [t for t in B.titles if t["id"] not in tids]
        win = _accent_win(B, m, slot)
        if snd:  # 사건의 소리 하나만 · 말이 쉬는 틈으로 옮김
            x = B.sfx[snd[0]]
            B.sfx[snd[0]] = (slot,) + tuple(x[1:4]) + (dict(_meta(x), title=None, win=win, why=f"{_meta(x).get('why') or ''} · 글자 없이 소리만"),)
            for k in snd[1:]:
                B.sfx[k] = None
            snd = snd[:1]
        else:
            snd = [_sfx_at(B, slot, name, -1.0, "accent", why=f"{SFX_WHY.get(m['kind'], '재미 순간')} · 글자 없이 소리만", win=win, soft=m["kind"] == "fail")]
        e.update(kind="sfx", t=round(slot, 2), text=B.sfx[snd[0]][1], why=f"{e.get('why') or ''} · 소리만 ({why})", refs={"sfx": snd}, accent=True,
                 moment=e.get("moment") or {"kind": m["kind"]})
        return True

    def trim():
        while count() + need_fill() > hi * mins:
            now = [t for t, _ in evs()]
            crowd = lambda e: sum(1 for t in now if abs(t - float(e["t"])) <= 6.0)  # noqa: E731
            # 같은 순간을 다른 사건(점수판·효과 글자·확대)이 이미 보여 주는 것부터 — 그 순간의 하나뿐인 사건(펀치라인의 속마음 글자)을 먼저 빼면
            # 그 재미 순간이 통째로 사라짐 (판정: 순간 재현율) · 한 순간에 글자 두 개가 겹쳐 뜨는 것도 줄어듦
            cand = sorted([e for e in B.events if e["kind"] in kind_rank and not (e["kind"] == "score" and str(e.get("text") or "").startswith("최종"))
                           and not (protect and sole(e))],
                          key=lambda e: (sole(e), 0 if redundant(e) else 1, round(_keep_value(e, st), 2), kind_rank[e["kind"]], -crowd(e), -e["t"]))
            e = next((e for e in cand if without(e)), None)
            rp = [(x, y) for x, y in rhythm_pairs() if flat(x, y)]
            if e is not None and (_keep_value(e, st) < rv or not rp):
                if accent(e):  # 그 순간의 하나뿐인 사건 → 소리만 남김 (D-262)
                    continue
                B.lost = getattr(B, "lost", 0) + (1 if sole(e) else 0)
                _drop(B, event_ids=[e["id"]])
                continue
            if rp:  # 번갈아 확대한 조각을 앞 조각 크기로 (컷 리듬보다 양 범위가 먼저)
                x, y = rp[len(rp) // 2]
                y["fx"] = {k: v for k, v in (x.get("fx") or {}).items()}
                continue
            break

    def splittable(t):
        return next((x for x in B.items if x["track"] == "V1" and x["media"] == "main" and x["start"] + 0.6 <= t <= editor.i_end(x) - 0.6
                     and not _zoom_of(x)[1] and not _zoom_of(x)[2]), None)
    mom_tl = []
    for m in moms or ():
        if m["kind"] in MOMENT_KINDS:
            t = B.src_to_tl(m["t"])
            if t is not None:
                mom_tl.append((t, m))

    def split_at(t, why, m=None, lo=-1e9, hi=1e9):
        near = [x for x in scut_tl if abs(x - t) <= SRC_CUT_SNAP and lo <= x <= hi]
        if near:  # 원본 장면 바뀜 바로 옆이면 그 자리에서 (두 번 바뀌지 않고 한 번에)
            x = min(near, key=lambda x: abs(x - t))
            t = x if splittable(x) is not None else t
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

    def clear(t, a, b):  # 다른 사건과 넉넉히 떨어진 자리 · 원본 장면 바뀜과는 떨어지거나 같은 자리 (split_at 이 그 자리로 맞춤)
        return a + SPLIT_CLEAR <= t <= b - SPLIT_CLEAR and not any(SRC_CUT_SNAP < abs(t - x) < SRC_CUT_CLEAR for x in scut_tl)

    def fill(gap_lim, need=None):
        """gap_lim 초 넘게 아무것도 안 바뀌는 곳을 나눔 (긴 곳부터 · need 개까지) — 그 안의 재미 순간 자리를 먼저, 다음은 말과 말 사이
        (둘 다 다른 사건·원본 장면 바뀜과 떨어진 곳 먼저 · 없으면 예전처럼 가운데 근처)."""
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
                ok = lambda t: a + 1.0 <= t <= b - 1.0 and t - a <= gap_lim and b - t <= gap_lim * n  # noqa: E731
                moms_ = [(t, m) for t, m in sorted(mom_tl, key=lambda x: abs(x[0] - t0)) if ok(t)]
                gaps_ = [(t, None) for t in sorted(gaps_tl, key=lambda x: abs(x - t0)) if ok(t) and abs(t - t0) <= 0.35 * g]
                near = [(t0 + d, None) for d in (0.0, 0.7, -0.7, 1.4, -1.4, 2.1, -2.1, 2.8, -2.8)]
                tries = [x for x in moms_ + gaps_ if clear(x[0], a, b)] + moms_ + [x for x in near if clear(x[0], a, b)] + near
                for t, m in tries:
                    if not (a + 1.0 <= t <= b - 1.0) or t - a > gap_lim:
                        continue
                    if split_at(t, ("재미 순간 살짝 확대 · " + m.get("why", "")) if m else STILL_WHY, m, max(a + 1.0, b - gap_lim * n), min(b - 1.0, a + gap_lim)):
                        done += 1
                        got = True
                        break
                if got:
                    break
            if not got:
                break
        return done
    # 오래 멈춘 곳(글자·컷·확대가 하나도 없는 곳)만 먼저 나누고, 그다음 양 범위를 넘는 만큼 스타일 무게가 낮은 것부터 뺌 —
    # 예전처럼 먼저 빼면(나눌 자리까지 셈) 멈춘 곳을 깨던 재미 글자가 빠지고 그 자리를 '오래 멈춰 보이지 않게' 확대가 채움 (판정: 듬뿍이 확대뿐)
    fill(still)
    trim()
    if intensity != "담백":  # 사건이 안 붙은 재미 순간에 살짝 확대
        for t, m in sorted(mom_tl, key=lambda x: -x[1].get("score", 0)):
            if count() >= hi * mins - 1:
                break
            if any(abs(t2 - t) < SPLIT_CLEAR for t2, _ in evs()) or any(SRC_CUT_SNAP < abs(t - x) < SRC_CUT_CLEAR for x in scut_tl):
                continue
            split_at(t, "재미 순간 살짝 확대 · " + m.get("why", ""), m)
    short = int(lo * mins + 0.999) - count()
    if short > 0:
        fill(2.5, short)
    fill(still)  # 순간 확대로 크기가 바뀌며 사라진 경계가 있으면 한 번 더 (멈춘 화면 한도는 꼭 지킴)
    trim()  # 나누기가 재미 순간 자리를 고르느라 꼭 필요한 것보다 많이 나눴으면, 멈춘 화면이 다시 생기지 않는 것만 덜어 냄
    if count() > hi * mins:
        # 그래도 양 범위를 넘으면: 컷 리듬으로 번갈아 바꾼 크기와 '오래 멈춰…' 확대를 모두 되돌리고, 멈춘 곳만 고르게 다시 나눔 (가장 적은 수) —
        # 컷 리듬 조각은 말 경계에 붙어 고르지 않아, 하나씩 빼면 멈춘 화면이 생겨 못 빼고 양 범위를 넘은 채 남음 (판정 F3)
        for _ in range(200):
            rp = rhythm_pairs()
            if not rp:
                break
            x, y = rp[0]
            y["fx"] = {k: v for k, v in (x.get("fx") or {}).items()}
        still_ev = [e["id"] for e in B.events if e["kind"] == "punch" and e.get("why") == STILL_WHY]
        if still_ev:
            _drop(B, event_ids=still_ev)
        fill(still)
        trim()

    def cont(x, y):  # 원본이 이어지는 두 본편 조각
        return abs(editor.i_end(x) - y["start"]) < 0.02 and abs(float(x["out"]) - float(y["in"])) < 0.06 and abs(editor.i_sp(x) - editor.i_sp(y)) < 1e-3

    def space():
        """확대가 바로 앞뒤 확대·컷과 ZOOM_GAP 초 안에 또 바뀌면(덜컹 · 판정: '하나, 둘' 구령에 1.0초 간격 확대 두 번 · 0.6초 간격 확대) —
        쾅 확대·흔들기는 그 사건을 빼고, 나눠서 바꾼 크기는 그 조각(과 이어지는 같은 크기 조각)을 앞 조각 크기로 (멈춘 화면 한도가 새로 넘지 않는 것만)."""
        _smooth_zoom_joins(B)  # (흔들기·쾅 확대가 앞 조각 크기에서 시작하게 맞춘 뒤의 모습으로 셈 — 108% 위 흔들기 뒤 100% 로 돌아오는 것까지)
        skip = set()
        for _ in range(80):
            vis = [(t, k) for t, k in evs() if k in ("zoom", "cut")]
            bad = None
            for (t0, k0), (t1, k1) in zip(vis, vis[1:]):
                if not (0.05 < t1 - t0 < ZOOM_GAP):
                    continue
                bad = next((t for t, k in ((t1, k1), (t0, k0)) if k == "zoom" and round(t, 2) not in skip), None)
                if bad is not None:
                    break
            if bad is None:
                return
            skip.add(round(bad, 2))
            v1 = sorted([x for x in B.items if x["track"] == "V1" and x["media"] == "main"], key=lambda x: x["start"])
            k = next((i for i, x in enumerate(v1) if abs(x["start"] - bad) < 0.03), None)
            if k is None:
                continue
            y = v1[k]
            if _zoom_of(y)[1] or _zoom_of(y)[2]:  # 쾅 확대·흔들기
                e = next((e for e in B.events if e["kind"] in ("punch", "shake") and y["id"] in ((e.get("refs") or {}).get("items") or [])), None)
                if e is not None and without(e):
                    # (protect 여도 확대 간격이 먼저 — 그 순간의 하나뿐인 사건이면 소리만 · 판정 round4 확대 간격 고친 것이 되살아남 · D-269)
                    if not accent(e):
                        _drop(B, event_ids=[e["id"]])
                    continue
                x = v1[k - 1] if k else None
                ks = _zoom_of(y)[1]
                cut_t = max([t for t, kk in vis if kk == "cut" and t < y["start"] - 0.05] or [-1.0])
                if x is not None and ks and not _zoom_of(y)[2] and cont(x, y) and abs(x["start"] - cut_t) < 0.05 and y["start"] - cut_t < ZOOM_GAP \
                        and not _zoom_of(x)[1] and not _zoom_of(x)[2] and abs(_zoom_of(x)[0] - float(ks[0]["v"])) < 0.5:
                    # 빼면 멈춘 화면이 생기는 쾅 확대가 컷 바로 뒤면 그 컷 자리에서 시작 (화면이 한 번에 바뀜 · 판정: 컷 0.9초 뒤 확대)
                    d = float(x["in"]) - float(ks[0]["t"])
                    end_v = float(ks[-1]["v"])
                    fx_x = dict(x.get("fx") or {}, scale={"v": end_v, "k": [dict(kk, t=round(float(kk["t"]) + d, 4)) for kk in ks]},
                                anchor=json.loads(json.dumps((y.get("fx") or {}).get("anchor") or {"v": fc, "k": []})))
                    fx_y = dict(y["fx"], scale={"v": end_v, "k": []})
                    trial = [dict(it, fx=fx_x) if it is x else dict(it, fx=fx_y) if it is y else it for it in B.items]
                    if over(visual_events(trial, B.titles, B.shapes, total)) > over(evs()):  # 멈춘 화면이 새로 생기면 그대로
                        continue
                    x["fx"], y["fx"] = fx_x, fx_y
                    if e is not None:
                        e["refs"]["items"] = [x["id"]] + [i for i in e["refs"].get("items") or [] if i != x["id"]]
                        e["t"] = round(x["start"], 2)
                continue
            if k == 0 or not cont(v1[k - 1], y) or _zoom_of(v1[k - 1])[1] or _zoom_of(v1[k - 1])[2]:
                continue
            run = [y]
            for z in v1[k + 1:]:
                if not cont(run[-1], z) or _zoom_of(z)[1] or _zoom_of(z)[2] or abs(_zoom_of(z)[0] - _zoom_of(y)[0]) > 0.5:
                    break
                run.append(z)
            keep = {kk: vv for kk, vv in (v1[k - 1].get("fx") or {}).items() if kk in ("scale", "anchor")}
            ids = {it["id"] for it in run}

            def fx_of(it):
                return dict({kk: vv for kk, vv in (it.get("fx") or {}).items() if kk not in ("scale", "anchor", "pos")}, **json.loads(json.dumps(keep)))
            if removable([dict(it, fx=fx_of(it)) if it["id"] in ids else it for it in B.items]):
                for it in run:
                    it["fx"] = fx_of(it)
    space()

    # 화면이 안 바뀌는 확대 사건 정리 (D-266): 나눠서 바꾼 크기를 space 가 앞 조각 크기로 되돌리면 조각은 남고 사건만 남아, MSG 목록·판정에
    # 확대가 하나 더 있음 (판정 round7 S1: 'punch 효과가 0.7초 간격으로 두 번' · 보이는 확대는 하나) → 그 사건은 빼고(조각 크기는 그대로),
    # 그 순간을 혼자 보여 주던 것이면 소리만 남김 (보통·듬뿍 · D-262)
    zt = [t for t, k in evs() if k in ("zoom", "cut")]
    by = {it["id"]: it for it in B.items}
    for e in list(B.events):
        if e["kind"] != "punch" or e.get("accent"):
            continue
        its = [by[i] for i in (e.get("refs") or {}).get("items") or [] if i in by]
        if not its or any(_zoom_of(x)[1] or _zoom_of(x)[2] for x in its) or any(abs(x["start"] - t) < 0.05 for x in its for t in zt):
            continue
        if not accent(e, keep_items=True, why="화면이 안 바뀌는 확대"):
            for k in (e.get("refs") or {}).get("sfx") or []:
                if k < len(B.sfx):
                    B.sfx[k] = None
            B.events = [o for o in B.events if o is not e]


def _smooth_zoom_joins(B):
    """이어지는 본편 조각에서 살짝 확대한 조각(108%) 바로 뒤의 쾅 확대·흔들기가 100%에서 다시 시작하면 화면이 한 번 줄었다 커짐
    (판정: 같은 때 확대가 두 번) → 앞 조각 크기에서 시작하게 맞춤."""
    v1 = sorted([x for x in B.items if x["track"] == "V1" and x["media"] == "main"], key=lambda x: x["start"])
    for a, b in zip(v1, v1[1:]):
        va, ka, pa = _zoom_of(a)
        vb, kb, _ = _zoom_of(b)
        if ka or pa or len(kb) < 2 or abs(va - 100.0) < 0.5 or abs(editor.i_end(a) - b["start"]) > 0.02 or abs(float(a["out"]) - float(b["in"])) > 0.06:
            continue
        sc = b["fx"]["scale"]
        k0 = float(kb[0]["v"])
        if abs(k0 - va) < 0.5:
            continue
        if all(abs(float(k["v"]) - k0) < 0.5 for k in kb):  # 흔들기: 크기 그대로 · 앞 조각보다 작아지지 않게
            for k in sc["k"]:
                k["v"] = round(max(k0, va), 1)
        else:  # 쾅 확대: 앞 조각 크기에서 시작
            sc["k"][0]["v"] = round(va, 1)
        sc["v"] = round(va, 1)
        if a.get("fx", {}).get("anchor") and not b["fx"].get("anchor"):
            b["fx"]["anchor"] = json.loads(json.dumps(a["fx"]["anchor"]))


def _audio_target(intensity, w, fmt="long"):
    """효과음 무게 w(스타일) → 이 양에서 꼭 넣을 소리 말고 더 넣을 소리(강조 뽁·확대 휙 등)의 1분 개수 (SFX_BAND 범위 안 · 배경음악 곡은 안 셈 · D-374)."""
    lo, hi = SFX_BAND.get(fmt, SFX_BAND["long"])[intensity]
    return max(0.3, lo + (hi - lo) * min(1.0, max(0.0, float(w or 0)) / 1.4) - 1.0)


DEMO_TXT = ("실전 시범", "한 번 더!")   # 시범 상황 자막 (시간 순서대로 · 다 쓰면 더 붙이지 않음 — 같은 글자 되풀이·예고 말을 시범 중간에 붙이지 않게)
AGAIN_TALK = 6.0   # 앞 시범 뒤 이보다 오래(초 · 낱말 길이 합) 설명했으면 다음 시범은 새 설명의 시범이라 '한 번 더!'가 아님 (판정 round7 · D-269)


def _demo_labels(plays, moms, words=()):
    """시범마다 상황 자막 글자 {id(시범): 글자}: 바로 앞(앞 시범이 끝난 뒤 · 20초 안)에 실패가 있었을 때만 '다시 도전' —
    실패 없이 이어지는 시범에 '다시 도전'을 붙이면 엉뚱함(판정: 리듬 시범에 '다시 도전'). 나머지는 DEMO_TXT 를 차례로.
    words: 앞 시범과 이 시범 사이에 AGAIN_TALK 초 넘게 설명했으면 '한 번 더!'를 붙이지 않음 (판정 round7 S2: '하나, 둘, 셋 리듬으로
    움직여 보세요' 설명 뒤 시범에 '한 번 더!' · D-269)."""
    out, n_plain, prev_b = {}, 0, -1e9
    for m in sorted(plays, key=lambda m: m["t"]):
        talk = sum(max(0.0, min(float(w[1]), float(m["a"])) - max(float(w[0]), prev_b)) for w in words or ())
        if any(x["kind"] == "fail" and prev_b - 1.0 <= x["t"] <= m["a"] + 0.5 and m["a"] - x["t"] <= 20.0 for x in moms):
            out[id(m)] = "다시 도전"
        elif n_plain and any(x["kind"] == "section" and prev_b <= x["t"] <= m["a"] for x in moms):
            out[id(m)] = None  # 앞 시범 뒤에 새 순서('두 번째 동작은…')를 알렸으면 같은 동작을 '한 번 더' 하는 게 아님 (판정: 엉뚱)
        elif n_plain and talk > AGAIN_TALK:
            out[id(m)] = None  # 앞 시범 뒤에 새 설명을 길게 했으면 같은 동작을 '한 번 더' 하는 게 아님
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


TEXT_OVERPICK = 1.6   # 보통·듬뿍: 스타일의 글자 양보다 이만큼 더 골라 두고 양 범위에 맞춰 덜어 냄


def _sfx_key(c, intensity):
    """고른 사건 → (뜻 소리 열쇠 | None, 꼭 넣을 소리인지) — 뜻 표 (D-373)."""
    k = c["kind"]
    if k == "fx":
        lk = c.get("look")
        return {"success": ("success", True), "kick": ("kick", False), "surprise": ("surprise", False),
                "fail": ("fail", False) if intensity == "듬뿍" else (None, False)}.get(lk, (None, False))
    if k == "inner":   # 속마음은 농담(펀치라인)만 · 웃음소리가 있으면 꼭
        return ("punchline", c.get("laugh") is not None) if c["src"] == "punchline" and not c.get("nosfx") else (None, False)
    if k == "situ":
        if c["src"] == "section" and not c.get("cont"):
            return "section", True
        if c["src"] in ("demo_call", "play", "recap") and not c.get("cont"):
            return "section", False   # 시범 예고·'실전 시범'·정리 카드 첫 장: 말 틈이 있을 때만 (_audio_items)
        return None, False
    if k == "emphasis":
        return "emphasis", False
    if k == "count":
        return "count", intensity != "담백"   # 담백: 양 안에서 (첫 숫자에만 · _compile_once)
    if k == "accent":   # 소리만 (plan_events 가 더한 것 · 순간 종류 그대로)
        return {"success": "success", "punchline": "punchline", "section": "section", "cue": "cue", "count": "count"}.get(c["src"]), True
    if k == "punch" and c["src"] in ("reaction", "punchline") and c.get("laugh") is not None and not c.get("nosfx"):
        return "zoom", False   # 웃음소리가 잡힌 웃음·반응 순간의 쾅 확대만 (말하는 중 강조 확대·오래 멈춘 곳 확대·실수 뒤 얼굴에는 소리 없음 · D-375 · D-379)
    return None, False


ZOOM_FAIL_CLEAR = 3.0   # 실수 순간 뒤 이만큼(초) 안의 반응 확대에는 휙을 안 붙임 (D-379)
OPT_AT = {"emphasis": ("caption",), "surprise": ("caption",), "kick": ("caption", "cut"), "zoom": ("zoom", "laugh")}   # 더 넣는 소리 → 참고 영상의 자리
OPT_AT_MIN = 0.1   # 배운 스타일에서 그 자리 효과음 비율이 이보다 적으면 더 넣는 소리를 안 붙임 (D-377)


def _result_time(m, words, kept_at):
    """성공 소리·효과 글자 자리 (원본 시각): 반응 말 묶음(react0) 바로 앞 0.45초가 말 없이 남아 있으면 거기(보이는 결과) · 아니면 그 순간 그대로 (D-372)."""
    r0 = float(m.get("react0", m["a"]))
    t = r0 - 0.45
    if kept_at(t) and not any(float(w[1]) > r0 - 0.8 and float(w[0]) < r0 - 0.02 for w in words):
        return round(t, 2)
    return m["t"]


def _moment_slot(m, words, kept_at):
    """소리만 남길 순간의 (맞출 원본 시각, 창 앞, 창 뒤) — 성공: 반응 말 앞(보이는 결과) · 농담: 말 끝 0.1초 뒤(웃음 전) · 다음 포인트: 말 시작 0.25초 앞 ·
    시작 신호: 신호 말 끝. 편집본에 남지 않은 곳이면 (None, …)."""
    a, b = float(m["a"]), float(m["b"])
    if m["kind"] == "success":
        t, lo, hi = _result_time(m, words, kept_at), float(m.get("react0", a)) - 0.8, b + 0.6
    elif m["kind"] == "punchline":
        la = float(m["laugh"]) if m.get("laugh") is not None else b + 0.3
        t, lo, hi = b + 0.1, b + 0.05, max(b + 0.3, la + 0.1)
    elif m["kind"] == "section":   # (m["t"] = 군말 뒤 첫 낱말 · 군말('자,')이 편집본에 남았으면 그 앞 · D-379)
        ts = float(m["t"])
        t, lo, hi = (a - 0.25 if a < ts - 0.01 and kept_at(a - 0.25) else ts - 0.25), min(a, ts) - 0.6, ts + 0.1
    elif m["kind"] == "count":
        t, lo, hi = float(m["t"]), float(m["t"]) - 0.1, float(m["t"]) + 3.0
    else:   # cue
        t, lo, hi = float(m["t"]) + 0.03, float(m["t"]), float(m["t"]) + 0.4
    # (농담 소리는 말 끝 뒤에서만 — 말 끝이 잘렸으면 말 시작으로 옮기지 않음 · 펀치라인보다 먼저 나면 엉뚱)
    for x in (t, lo, hi) + (() if m["kind"] == "punchline" else (a, float(m["t"]))):
        if kept_at(x):
            return round(x, 2), lo, hi
    return None, lo, hi


def plan_events(sig, moms, st, intensity, fmt, seed, kept, words, knobs=None, avoid=(), ai=None, caps=None):
    """재미 순간 → 사건 (원본 시각 기준) · 예산·간격·동시에 보이는 글자 수 지키기 · 시드 고정.
    knobs: 양 범위에 맞추려고 줄인 구성 (다시 보기·정지 화면 최대 수 · 숫자 세기 여부) · avoid: 티저에 쓴 원본 구간 (다시 보기로 또 쓰지 않게).
    ai: 클로드가 쓴 재미 글자 {moment_key: {kind, text}} (사용자가 켰을 때만 · 없거나 규칙에 안 맞으면 정해 둔 문구).
    caps: 말 자막 (같은 때 말 자막을 되풀이하는 글자는 고르기 전에 새 정보로 바꾸거나 뺌 — 고른 뒤에 빼면 그만큼 재미 글자가 빔)."""
    ai = ai or {}
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

    pm = fun.get("perMin") or cap.get("perMin") or {}   # (예전에 저장한 스타일 값은 자막 부분에 있었음)
    # 보통·듬뿍은 재미 글자를 넉넉히 고르고 양 범위는 govern 이 스타일 무게로 맞춤 (글자가 멈춘 화면을 깨면 '오래 멈춰…' 확대가 빠짐 ·
    # 판정: 듬뿍이 거의 확대·휙뿐이고 개그 글자는 1분에 1~3개)
    over = 1.0 if mild else TEXT_OVERPICK
    B = {"emphasis": budget(pm.get("emphasis", 0) * over), "situ": budget(pm.get("situ", 0) * over),
         "inner": 0 if mild else budget(pm.get("inner", 0) * over), "fx": 0 if mild else budget(pm.get("fx", 0) * over),
         "punch": budget(BASE_PER_MIN["punch_zoom"] * fun.get("punch_zoom", 0), int(minutes + 0.99) if mild else None),
         "replay": budget(BASE_PER_MIN["slowmo_replay"] * fun.get("slowmo_replay", 0) / 1.0,
                          1 if mild else max(1, int(minutes / (2.0 if intensity == "보통" else 1.5))) if fun.get("slowmo_replay", 0) > 0 else 0),
         "freeze": 0 if mild else budget(BASE_PER_MIN["freeze"] * fun.get("freeze", 0), (1 if intensity == "보통" else max(1, int(minutes / 3)))
                                        if fun.get("freeze", 0) > 0 else 0),
         "shake": 0 if mild else budget(BASE_PER_MIN["shake"] * fun.get("shake", 0), 1 if intensity == "보통" else None),
         "sfx": int(_audio_target(intensity, fun.get("sfx", 0), fmt) * minutes + 0.5), "count": 99, "score": 99}
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
    n_cards = {}   # 정리 말 순간마다 카드 장 수 (한 덩어리 · D-269)

    def cand(kind, m, pri, **kw):
        t = kw.pop("t", m["t"])
        if kind == "inner":
            kw["dur"] = max(float(kw.get("dur") or 0), INNER_MIN)  # (괄호 속마음 6~11글자 · 1.4초로는 다 못 읽음)
        if kw.get("text") and kind in ("emphasis", "situ", "fx", "inner") and caps:
            alts = next((g for g in FX_TEXTS.values() if kw["text"] in g), ()) if kind == "fx" else ()
            r = _dedupe_text(kind, kw["text"], t, float(kw.get("dur") or 1.0), caps, [o for o in alts if not used_txt.get(o)],
                             _term_near(words, t, 12.0) if kind == "emphasis" else "")
            if r is None:
                return
            if r[0] != kw["text"] and kind == "fx":
                used_txt[r[0]] = used_txt.get(r[0], 0) + 1
            kw["text"], t = r
        if not kept_at(t) or ("text" in kw and kw["text"] is None):
            return
        cands.append(dict({"kind": kind, "t": round(t, 2), "pri": pri, "src": m["kind"], "why": m.get("why", ""), "mt": m.get("text", ""), "mid": id(m)}, **kw))

    plays = sorted([m for m in moms if m["kind"] == "play"], key=lambda m: -m["score"])
    onsets = _kicks(sig)
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

    def ai_txt(m, kind):
        """클로드가 이 순간에 쓴 글 (그 종류일 때만 · 한 번만 씀)."""
        x = ai.get(moment_key(m))
        if not x or x.get("kind") != kind or x.get("used"):
            return None
        if _norm_txt(x["text"]) and used_txt.get(x["text"]):
            return None
        x["used"] = True
        used_txt[x["text"]] = used_txt.get(x["text"], 0) + 1
        return x["text"]

    fails = [m["t"] for m in moms if m["kind"] == "fail"]
    wins = [m["t"] for m in moms if m["kind"] == "success"]
    laughs = _spans((sig.get("tags") or {}).get("laugh"), sig.get("tagStep", 0.48), LAUGH_JOKE)
    for m in moms:
        k, sc = m["kind"], m["score"]
        txt = m.get("text") or ""
        n_before = len(cands)
        if k == "emphasis":
            cand("emphasis", m, 2.0 + sc, text=m["text"] or None, dur=1.6)
        elif k == "section" or (k == "demo_call" and any(0.0 <= p["a"] - m["b"] <= 8.0 for p in plays)):  # 시범 예고는 곧 시범이 이어질 때만
            cand("situ", m, 1.6 + sc, text=_situ_text(m, near_words(m["t"], 30.0)), dur=2.2, lead=m["a"] if m["a"] < m["t"] - 0.01 else None)
        elif k == "count" and re.search(r"하나[,\s]+둘", txt):
            cand("count", m, 1.5, text="하나 둘 셋", dur=0.7)
        elif k == "punchline":
            after_fail = any(0 <= m["t"] - f <= 10.0 for f in fails)  # 실수 뒤 위로하는 말: 놀리는 효과음 없이 공감 쪽
            after_win = any(0 <= m["t"] - w <= 12.0 for w in wins)  # 성공 뒤 웃음: 머쓱이 아니라 뿌듯
            key = "confess" if CONFESS.search(txt) or after_fail else "proud" if after_win else "confident" if CONFIDENT.search(txt) else "punchline"
            # 글자·소리는 말이 끝난 바로 뒤 (말 끝 0.1초 · 펀치라인이 들리고 나서 · 예전: 말 끝 0.1초 앞 → 바둠츠가 말보다 먼저 · D-372)
            t_j = float(m["b"]) + 0.1 if kept_at(float(m["b"]) + 0.1) else m["t"]
            # 위로하는 말·스스로 낮추는 농담은 효과음 없이 — 웃음소리가 있으면 같이 웃는 쪽이라 소리 (D-373)
            cand("inner", m, 1.8 + sc, text=ai_txt(m, "inner") or pick(INNER_TEXTS[key]), dur=1.6, t=t_j,
                 nosfx=((after_fail or key == "confess") and m.get("laugh") is None) or not kept_at(float(m["b"]) + 0.1), laugh=m.get("laugh"))
            cand("punch", m, 1.7 + sc, dur=2.2, t=max(m["a"], m["b"] - 1.2), laugh=m.get("laugh"))
        elif k == "fail" and acted(m["t"]):
            cand("fx", m, 1.6 + sc, text=ai_txt(m, "fx") or pick(FX_TEXTS["fail"], txt), dur=1.0, look="fail")
            cand("inner", m, 1.4 + sc, text=ai_txt(m, "inner") or pick(INNER_TEXTS["fail"]), dur=1.5, t=m["t"] + 0.6)
        elif k == "success" and acted(m["t"]):
            goal = re.search(r"들어갔|골인|넣었", txt) and SHOOTING.search(near_words(m["t"], 20.0))
            # 효과 글자·성공 소리는 반응 말 바로 앞(공이 들어간 · 보이는 결과) 말 없는 곳에 — 반응 말('나이스!') 위에 얹으면 말을 가림 (D-372)
            cand("fx", m, 1.9 + sc, text=ai_txt(m, "fx") or pick(FX_TEXTS["goal" if goal else "success"], txt), dur=1.1, look="success",
                 t=_result_time(m, words, kept_at))
            if not mild:  # 성공 뒤 속마음 ('(뿌듯)') — 효과 글자와 떨어져서 (시범 반응 말이 끝날 즈음)
                cand("inner", m, 1.1 + sc, text=ai_txt(m, "inner") or pick(INNER_TEXTS["success"]), dur=1.4, t=min(m["b"], m["t"] + 1.8))
        elif k == "surprise":
            cand("fx", m, 1.3 + sc, text=ai_txt(m, "fx") or pick(FX_TEXTS["surprise"], txt), dur=1.0, look="surprise")
            cand("shake", m, 1.0 + sc, dur=0.35)
        elif k == "reaction":
            # 확대 휙은 웃음소리가 함께 잡힌 반응에만 · 실수 바로 뒤(3초 안)의 얼굴은 웃음이 아니라 아쉬움 (얼굴만 보고 '웃는 얼굴 쾅 확대' 휙 →
            # '아, 아깝다.' 뒤 휙이 다음 말을 가림 · 판정에서 예전이 나았던 단 두 편 · D-379)
            la = next((x for x, y in laughs if x - 0.5 <= float(m["b"]) and y + 0.5 >= float(m["a"])), None)
            cand("punch", m, 1.5 + sc, dur=2.0, t=m["a"], laugh=la, nosfx=any(0.0 <= float(m["t"]) - f <= ZOOM_FAIL_CLEAR for f in fails))
        elif k == "question":
            cand("inner", m, 1.0 + sc, text=ai_txt(m, "inner") or pick(INNER_TEXTS["question"]), dur=1.5, t=m["b"] + 0.1)
        elif k == "aside":  # 딴소리·여담 ('물 좀 마시고 할게요' → '(물 타임)') · 클로드가 쓴 글이 있으면 그것
            cand("inner", m, 1.5 + sc, text=ai_txt(m, "inner") or pick([m["text"]]), dur=1.6, t=m["t"])
        elif k == "recap":  # 정리 말 → 번호 목록 카드 (말하는 동안 · 다 말한 뒤 1.5초)
            # 한 장은 2줄까지 — 목록이 셋·넷이면 두 장으로 나눠 셋째 것을 말할 때 둘째 장 (판정 round6: S1 정리 카드 3줄 · D-263)
            cards = _recap_cards(m, words)
            n_cards[id(m)] = len(cards)
            for k2, (txt2, t2, d2) in enumerate(cards):
                cand("situ", m, 3.0, text=txt2, dur=d2, t=t2, look="recap", cont=k2 > 0)
        elif k == "talk" and ai.get(moment_key(m)):  # 클로드가 쓴, 말 자막만 이어지는 긴 설명 위 속마음·상황 글자
            x = ai[moment_key(m)]
            # (예산과 따로: 재미 순간이 없는 긴 설명 위라 멈춘 화면을 확대 대신 글자로 깸 · 양 범위는 govern 이 맞춤)
            cand(x["kind"], m, 1.2, text=ai_txt(m, x["kind"]), dur=1.6, free=True)
        if k == "emphasis" and not mild:
            # 강조 글자가 말 자막과 같아 빠졌으면 그 순간은 확대가 맡음 — 강조 글자 자리의 우선순위로 (D-154 뒤 강조 순간이 통째로 빠지던 것 ·
            # 판정 round6 순간 재현율: S1 강조 6개 중 3개)
            cand("punch", m, (2.0 if len(cands) == n_before else 1.2) + sc, dur=2.4, t=m["t"])
    demo_label = _demo_labels(plays, moms, words)
    for i, m in enumerate(plays):
        ons = [o for o in onsets if m["a"] <= o <= m["b"]]
        after = [x for x in moms if x["kind"] in ("fail", "success") and m["b"] - 0.5 <= x["t"] <= m["b"] + 5.0]
        failed = bool(after) and after[0]["kind"] == "fail"
        if ons and SHOOTING.search(near_words(ons[0])):  # 공 차는 소리 글자는 슛 이야기를 할 때만 (트래핑·패스에 '뻥!' 은 엉뚱함)
            cand("fx", m, 1.2 + m["score"], text=ai_txt(m, "fx") or pick(FX_TEXTS["kick"]), dur=0.9, look="kick", t=ons[0])
        elif ons and not mild:  # 트래핑·패스 시범은 가벼운 터치 소리 글자 ('툭!' · 소리 없음)
            cand("fx", m, 1.0 + m["score"], text=ai_txt(m, "fx") or pick(FX_TEXTS["touch"]), dur=0.8, look="touch", t=ons[0])
        if not failed and not mild:  # 시범이 시작할 때 속마음 ('(집중)') — 말 없는 시범 장면이 글자 없이 길게 이어지지 않게
            cand("inner", m, 0.9 + m["score"] * 0.5, text=ai_txt(m, "inner") or pick(INNER_TEXTS["demo"]), dur=1.4, t=m["a"] + 0.7)
        if ons and i < 3 and not failed:  # 실패한 시도를 흔들거나 다시 보여 주면 실수를 놀리는 것처럼 보임 (판정)
            cand("shake", m, 0.9 + m["score"], dur=0.35, t=ons[0])
        # 다시 보기: 공 소리가 난 시범을 먼저 · 티저에 쓴 장면은 뒤로 (판정: 티저와 같은 컷을 다시 보여 줌) · 실패한 시도는 안 함
        in_teaser = any(min(m["b"], y) - max(m["a"], x) > 0.3 for x, y in avoid)
        if not failed:
            cand("replay", m, 2.0 + m["score"] + (0.8 if ons else 0.0) - (1.5 if in_teaser else 0.0), a=m["a"], b=m["b"], at=m["b"])
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
        if last is not None:  # 마지막 점수판 글자를 최종 결과로 ('5/5 · 3골' 뒤 2.5초에 같은 뜻의 '최종 결과'를 또 띄우지 않게 · 글자 하나 덜 띄움)
            last.update(text=f"최종 결과 · {fin[2]}번 중 {fin[3]}골!", dur=3.4)
        else:
            cand("situ", board[-1], 8.5, text=f"최종 결과 · {fin[2]}번 중 {fin[3]}골!", dur=2.6, t=board[-1]["t"] + 2.5, must=True)
    _series_badges(cands)
    cands.sort(key=lambda c: (-c["pri"], c["t"]))
    used = {k: 0 for k in B}
    picked = []
    texts = []  # (a, b, 자리)

    def free_text(a, b, slot):
        on = [x for x in texts if x[0] < b and a < x[1]]
        return len(on) < 2 and not any(x[2] == slot for x in on)

    def fits(c):
        """자리·간격 규칙 (예산은 따로) → 들어가면 True (강조 글자와 겹친 확대 수도 셈)."""
        k, t = c["kind"], c["t"]
        if k in TEXT_KINDS:
            if not free_text(t, t + c["dur"], k):
                return False
            near = [p for p in picked if p["kind"] in TEXT_KINDS and p["kind"] != "score" and abs(p["t"] - t) < gap
                    and not (c.get("cont") and p.get("look") == "recap" and p.get("mid") == c.get("mid"))]   # (정리 카드 둘째 장은 첫 장 바로 뒤)
            if near and k != "score":  # 점수판은 제 자리(오른쪽 위)에만 · 다른 글자 간격에 안 셈
                return False
        elif k in ("punch", "shake"):
            if any(p["kind"] in ("punch", "shake") and abs(p["t"] - t) < max(gap, 3.0) for p in picked):
                return False
            # 강조 글자와 확대가 매번 같이 나오면 과함 → 보통은 글자만, 듬뿍도 2번까지만 겹침
            if c["src"] == "emphasis" and any(p["kind"] == "emphasis" and abs(p["t"] - t) < 1.0 for p in picked):
                if intensity != "듬뿍" or used.get("_pair", 0) >= 2:
                    return False
                used["_pair"] = used.get("_pair", 0) + 1
        elif k in ("replay", "freeze"):
            if any(p["kind"] in ("replay", "freeze") and abs(p["t"] - t) < 20.0 for p in picked):
                return False
        return True

    def take(c):
        picked.append(c)
        bk = "fx" if c["kind"] == "fx" else c["kind"]
        used[bk] = used.get(bk, 0) + 1
        if c["kind"] in TEXT_KINDS:
            texts.append((c["t"], c["t"] + c["dur"], c["kind"], c.get("mid")))

    for c in cands:
        k = c["kind"]
        bk = "fx" if k == "fx" else k
        if c.get("cont") and not any(p.get("look") == "recap" and p.get("mid") == c.get("mid") for p in picked):
            continue  # 정리 카드 둘째 장은 첫 장을 골랐을 때만
        if used.get(bk, 0) >= B.get(bk, 0) and not c.get("must") and not c.get("cont") and not (c.get("free") and B.get(bk, 0) > 0):
            continue
        if fits(c):
            take(c)
    # 정리 카드는 한 덩어리 (D-269): 장 하나라도 못 고르면(말 자막과 같아 뺌 · 자리 없음) 모두 뺌 — '①②'만 뜨고 셋째 항목을 말할 때 사라지면
    # 오늘 배운 것이 둘뿐인 것처럼 보임 (검토)
    for mid in {c["mid"] for c in picked if c.get("look") == "recap"}:
        if sum(1 for c in picked if c.get("look") == "recap" and c["mid"] == mid) < n_cards.get(mid, 1):
            used["situ"] = used.get("situ", 0) - sum(1 for c in picked if c.get("look") == "recap" and c["mid"] == mid)
            picked[:] = [c for c in picked if not (c.get("look") == "recap" and c["mid"] == mid)]
            texts[:] = [x for x in texts if x[3] != mid]
    if not mild and knobs.get("vital", intensity in VITAL_LEVELS):
        # 중요한 재미 순간(시범·펀치라인·강조·성공·실패·놀람)마다 사건 하나는 — 종류별 예산(글자·확대 수)에 걸려 아무 사건도 못 받은 순간에
        # 그 순간의 후보 중 자리가 맞는 가장 높은 것 (끼워 넣는 장면·흔들기는 빼고) · 양 범위는 govern 이 이 사건들을 지키며 맞춤
        # (판정 round6 순간 재현율 F5 16/45 — 강조·성공 순간이 예산·양 맞추기에서 빠짐 · D-262)
        for m in sorted([m for m in moms if m["kind"] in MOMENT_KINDS and kept_at(m["t"])], key=lambda m: -m["score"]):
            if any(float(m["a"]) - 1.0 <= p["t"] <= float(m["b"]) + 1.0 for p in picked):
                continue
            # (시범 상황 자막 '실전 시범'·'한 번 더!'는 순서대로 붙이는 글이라 예산 밖으로 더하지 않음 — 엉뚱한 '한 번 더!'가 퍼짐 · 검토 · D-269)
            mine = [c for c in cands if c.get("mid") == id(m) and c["kind"] not in ("replay", "freeze", "shake") and c not in picked
                    and not (c["kind"] == "situ" and c.get("text") in DEMO_TXT)]
            # 스타일의 글자 양은 지킴: 예산이 남은 종류 먼저 · 개그 글자(속마음·효과 글자)는 예산을 넘겨 더하지 않고 확대·강조로 (스타일 섞기 판정)
            room = lambda c: used.get("fx" if c["kind"] == "fx" else c["kind"], 0) < B.get(c["kind"], 0)  # noqa: E731
            for c in [c for c in mine if room(c)] + [c for c in mine if not room(c) and c["kind"] not in ("inner", "fx")]:
                if fits(c):
                    c["vital"] = True
                    take(c)
                    break
    # 효과음: 사건의 뜻에 맞는 소리 (D-373 · 담백은 깔끔한 소리만) — 꼭 넣을 소리(다음 포인트·성공·웃음 뒤 농담·숫자 세기)는 예산과 상관없이,
    # 나머지(강조 뽁·확대 휙·놀람·공 소리 글자)는 스타일의 효과음 예산 안에서 (예전: 1분 개수를 채우려고 아무 확대·상황 자막에 휙 · D-374) ·
    # 배운 스타일은 참고 영상 효과음이 거의 안 붙던 자리(글자·확대)에는 더 넣는 소리를 안 붙임 (D-377)
    sfx_budget = B["sfx"]
    at = (st.get("sound") or {}).get("sfxAt")
    order = sorted(picked, key=lambda c: (not _sfx_key(c, intensity)[1], -c["pri"]))
    sfx_t = []
    for c in order:
        key, must = _sfx_key(c, intensity)
        name = pal.get(key) if key else None
        if not name or (mild and name not in CLEAN_ONLY):
            continue
        if not must and at and key in OPT_AT and sum(float(at.get(x) or 0) for x in OPT_AT[key]) < OPT_AT_MIN:
            continue   # (OPT_AT 에 없는 뜻 소리(실수·농담·다음 포인트·숫자)는 자리 비율과 상관없이 · 예전: 없으면 0 이라 늘 빠짐 · D-379)
        if not must:
            if sfx_budget <= 0:
                continue
            sfx_budget -= 1
        if any(abs(c["t"] - x) < 1.0 for x in sfx_t):
            continue
        c["sfx"], c["sfxMust"] = name, must
        sfx_t.append(c["t"])
    # 글자가 못 들어간 중요한 순간은 소리만 (D-375): 성공·웃음 뒤 농담·다음 포인트·시작 신호에 고른 사건이 없거나 소리가 없으면 그 순간의 뜻 소리를
    # 말이 쉬는 틈에 (자리는 _audio_items 가 그 창 안에서 · 말을 가리면 뺌) — 예전: 글자가 말 자막과 같아·자리가 없어·예산 때문에 빠지면 소리도 함께 빠짐
    for m in moms:
        key = {"success": "success", "punchline": "punchline", "section": "section", "cue": "cue", "count": "count"}.get(m["kind"])
        if (key == "punchline" and (m.get("laugh") is None or mild)) or (key == "count" and mild):
            key = None   # 웃음소리 없는 농담 말은 속마음 글자가 맡음 (글자가 빠지면 소리도) · 담백은 숫자 세기 글자가 빠지면 소리도
        ticks = []
        if key == "count":   # 숫자 세기 글자를 양 때문에 뺐어도 숫자마다 틱 (말 위 작은 소리)
            ticks = [round(w0, 2) for w0, _, w, _ in words if m["t"] - 0.1 <= w0 <= m["t"] + 3.0 and re.fullmatch(r"(하나|둘|셋|넷)[,.!]*", w) and kept_at(w0)][:4]
            key = key if re.search(r"하나[,\s]+둘", m.get("text") or "") and ticks else None
        if key == "success" and not acted(m["t"]):
            key = None
        name = pal.get(key) if key else None
        if not name or (mild and name not in CLEAN_ONLY):
            continue
        if any(p.get("sfx") and p.get("mid") == id(m) and _sfx_key(p, intensity)[0] == key for p in picked):
            continue   # (그 순간의 같은 뜻 소리가 이미 있음 — 확대 휙 같은 다른 뜻 소리는 안 셈)
        t0, lo, hi = _moment_slot(m, words, kept_at)
        if t0 is None or any(abs(t0 - x) < 1.0 for x in sfx_t):
            continue
        if fmt == "shorts" and key == "section" and kept and t0 - float(kept[0]["in"]) < SHORTS_HOOK_SEC + 0.5:
            continue   # 쇼츠가 그 포인트로 시작: 첫 장면 훅 소리(짠)가 맡음 (훅 글자 아래 말 한가운데에 또 소리를 넣지 않음)
        picked.append({"kind": "accent", "t": round(t0, 2), "pri": 0.5, "src": m["kind"], "why": m.get("why", ""), "mt": m.get("text", ""), "mid": id(m),
                       "sfx": name, "sfxMust": True, "win": (round(lo, 2), round(hi, 2)), "laugh": m.get("laugh"), "ticks": ticks})
        sfx_t.append(t0)
    # 몽타주: 시범이 4개 넘을 때 (담백 제외)
    mont = None
    # 명장면은 실제로 공을 찬 시범(공 소리)·칭찬·반응이 붙은 시범 중 실패하지 않은 것만 (판정: 설명하는 장면에 '오늘의 명장면')
    strong = [m for m in plays if (any(m["a"] <= o <= m["b"] for o in onsets) or re.search(r"칭찬|반응", m.get("why") or ""))
              and not any(x["kind"] == "fail" and m["b"] - 0.5 <= x["t"] <= m["b"] + 5.0 for x in moms)]
    if knobs.get("montage", MONTAGE_N.get(intensity, 0)) and fmt == "long" and fun.get("montage", 0) >= 0.5 and len(strong) >= 4:
        mont = sorted(strong[:8], key=lambda m: m["t"])
    scuts = [float(x) for x in sig.get("cuts") or ()]
    keep = []
    for c in picked:  # 확대는 원본 장면 바뀜 바로 옆이면 그 자리에서, 조금 떨어져 있으면 안 함 (0.5~1초 사이에 화면이 두 번 바뀌면 덜컥거림 · 판정)
        if c["kind"] == "punch":
            near = [x for x in scuts if abs(x - c["t"]) <= SRC_CUT_SNAP and kept_at(x)]
            if near:
                c["t"] = round(min(near, key=lambda x: abs(x - c["t"])), 2)
            elif any(abs(x - c["t"]) < SRC_CUT_CLEAR for x in scuts):
                continue
        keep.append(c)
    return sorted(keep, key=lambda c: c["t"]), mont


# ---------- 5. 편집본 만들기 ----------
TEASER_FLASH = 0.24     # 티저 사이 흰 번쩍 길이(초)
TITLE_CARD = 2.0        # 제목 카드 길이
END_SEC = 8.0           # 엔드 화면 (유튜브 최종 화면 5~20초)
REPLAY_SPEED = 0.5
REPLAY_SRC = 2.5        # 다시 보기에 쓰는 원본 길이(초) — 느리게 5초 (판정: 말 없이 7초 넘게 이어지면 늘어짐)
FREEZE_SEC = 1.7
MONTAGE_CLIP = 0.65
MONTAGE_N = {"듬뿍": 4}   # 몽타주 장면 수 (듬뿍만 · 담백·보통은 몽타주 없음)
TEASER_N = {"담백": 1, "보통": 2, "듬뿍": 3}  # 티저 명장면 수 (스타일의 clips 와 작은 쪽 · 첫 질문은 따로 더함 · 양 범위를 넘으면 _reduce 가 줄임)
LETTERBOX = 0.09        # 다큐 위아래 검은 띠 높이 (화면 높이 비율)
HOOK_NEAR = 30.0        # 첫 질문 장면으로 시작하면, 본편 첫 이만큼(초) 안에서 같은 질문을 뺌
HOOK_NEAR_TXT = 12.0    # 담백: 질문을 본편 첫 이만큼(초) 안에 말하면 첫 장면 위 질문 글자는 안 띄움 (말 자막이 그 질문을 보여 줌)
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


# 실어 둔 제목 글꼴에 없는 글자 (fc-query 로 확인 · 없으면 libass 가 다른 글꼴로 바꿔 그려 한 글자만 모양이 달라짐 · Windows 는 고르는 글꼴도 다름)
_NO_GLYPH = {"Black Han Sans": {"…": "...", "·": ", ", "▶": "", "►": "", "★": "", **{c: str(i + 1) for i, c in enumerate("①②③④⑤⑥⑦")}},
             "Do Hyeon": {"…": "...", "·": ", ", "►": ""}}


def glyph_safe(text, font):
    """화면 글자를 그 글꼴에 있는 글자로 ('아깝다…' → '아깝다...' · 검은고딕의 '①' → '1') — 미리보기·내보내기가 같은 글을 씀."""
    if not text:
        return text
    # 동그라미 숫자는 어느 글꼴에서도 한글보다 작고 가늘게 그려져 작은 화면에서 '⊙'처럼 뭉개짐 (판정 round5: '포인트 ③') →
    # 목록 머리는 '1.', 나머지는 '3'
    text = re.sub(r"(^|\n)([①-⑦]) ", lambda m: f"{m[1]}{CIRCLED.index(m[2]) + 1}. ", str(text))
    text = "".join(str(CIRCLED.index(ch) + 1) if ch in CIRCLED else ch for ch in text)
    rep = _NO_GLYPH.get(font)
    if not rep:
        return text
    if "·" in rep:  # 점수판 '3/5 · 2골': 쉼표로 바꾸면 검은고딕에서 마침표처럼 보여 '3/5. 2골' (판정 round6) → '3/5 - 2골'
        text = re.sub(r"([가-힣])\s*·\s*(\d)", r"\1: \2", re.sub(r"(\d)\s*·\s*", r"\1 - ", text))   # ('최종 결과: 5번 중 3골!')
    out = "".join(rep.get(ch, ch) for ch in str(text))
    return re.sub(r"[ ]+,", ",", re.sub(r"[ ]{2,}", " ", out)).strip()


def _mid(file):
    return "msg_" + hashlib.sha1(file.encode("utf-8")).hexdigest()[:8]


class _Build:
    """타임라인을 앞에서부터 차례로 쌓는 도우미 (V1/A1 쌍·정지 화면·전환·효과음·사건 기록)."""

    def __init__(self, name, info, fmt):
        self.name, self.info, self.fmt = name, info, fmt
        self.items, self.trans, self.titles, self.shapes, self.markers = [], [], [], [], []
        self.tactics = []  # 전술 그림 (시범 장면의 발밑 원·화살표 · place_tactics)
        self.pos = 0.0
        self.media = {}
        self.sfx = []      # (타임라인 시각, 이름, 더 줄일 dB(None=0), 사건)
        self.events = []
        self.main = []     # 본편 V1 클립 (원본 → 타임라인 바꾸기용)
        self.lost = 0      # 양 범위 때문에 뺀 사건 중 그것만 보여 주던 재미 순간이 있던 것 (govern)
        self.card = False  # 은평 스타일 쇼츠 (BR-240 · 위 띠 제목 상자·사실 줄은 글자 자리 정리에서 그대로 두고 피함)
        self.lay = None    # 은평 스타일 화면 구성 (띠 그림·로고 포함 · editor.ep_layout · 한 편집본 안에서 같은 값)
        self.cap_lim = None  # 은평 스타일 말 자막 아래 끝 한계 (전체 강좌 줄·로고 위 · editor.ep_cap_lim)
        self.fixed = set()  # 자리를 옮기지 않는 글자 id (은평 스타일 큰 제목·사실 줄 · 스킨 큰 제목·주제 줄)
        self.skin = None   # 풋살사관학교 스킨 영상 창 {top, bottom} (BR-323 · 화면 구성은 B.lay · 위·아래 띠는 글자 자리 정리에서 피함)
        self.skin_faces = None   # 스킨: 얼굴 따라가기가 본 얼굴 {원본 시각: 상자들} (MSG 글자가 얼굴을 피함 · editor.skin_screen_faces · D-538)
        self.skin_rep = {}   # 스킨: 얼굴·공 모델을 못 썼는지 (noFace·noBall → 편집본 skinNoFace·skinNoBall · '확인할 것') · 나눈 클립 (split · D-539)
        self.cap_lift = []   # 스킨: 말 자막을 공 위로 올릴 원본 구간 (editor.skin_cap_lifts · 편집본 capLift · D-539)

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
        st = dict(look, **extra)
        t = {"id": editor._nid(), "text": glyph_safe(text, st.get("font")), "start": round(start, 3), "dur": round(dur, 3), "style": st, "msg": True}
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

    def tl_to_src(self, t):
        for it in self.main:
            if float(it["start"]) <= t < editor.i_end(it):
                return float(it["in"]) + (t - float(it["start"])) * editor.i_sp(it)
        return None


def _sfx_at(B, t, name, lvl=None, kind="", why="", title=None, win=None, must=False, soft=False, group=None, **extra):
    """효과음 하나 예약 → B.sfx 번호. t = 소리의 맞출 점(hit)이 올 타임라인 시각 (시작 시각은 _audio_items 가 소리 모양으로 정함 · D-371).
    title: 이 글자의 첫 장면에 맞춤 (글자 자리가 바뀌면 따라감) · win: 소리만인 소리가 말을 가리면 옮겨도 되는 창 (타임라인 (앞, 뒤)) ·
    must: 꼭 넣을 뜻 소리 (양 범위와 상관없이) · soft: 늘 작게 · group: 한 묶음(숫자 세기 틱)은 간격 규칙에서 하나로 · why: 편집실에 보일 이유."""
    meta = dict({"why": why, "title": title, "win": win, "must": bool(must), "soft": bool(soft), "group": group}, **extra)
    B.sfx.append((round(float(t), 4), name, lvl, kind, meta))
    return len(B.sfx) - 1


def _meta(x):
    """B.sfx 한 줄의 덧붙인 정보 (예전 4칸 줄은 빈 dict)."""
    return x[4] if x and len(x) > 4 and isinstance(x[4], dict) else {}


def _tl_win(B, src_t, tl, lo, hi, back=False):
    """원본 시각 창 (lo, hi) → 타임라인 창 — tl 이 든 본편 조각 안으로 (컷을 넘어 다른 장면으로 가지 않게).
    back: 다음 포인트 소리 — 조각이 컷으로 시작하면 바로 앞 조각의 끝(말이 쉬는 꼬리)까지 앞으로 (컷에서 넘어가는 휙 · 예전: 컷에서 잘려
    S2 '두번째 동작은' 창 37.98~38.08 · 18편 모두 빠짐 · D-379)."""
    it = next((x for x in B.main if float(x["start"]) - 1e-6 <= tl < editor.i_end(x)), None)
    a0, a1 = (float(it["start"]), editor.i_end(it)) if it is not None else (tl - 0.5, tl + 0.5)
    sp = editor.i_sp(it) if it is not None else 1.0
    if back and it is not None:
        prev = next((x for x in B.main if abs(editor.i_end(x) - a0) < 0.02 and x is not it and abs(editor.i_sp(x) - 1.0) < 1e-3), None)
        if prev is not None:
            a0 = float(prev["start"])
    return (round(max(a0, tl + (float(lo) - float(src_t)) / sp), 3), round(min(a1, tl + (float(hi) - float(src_t)) / sp), 3))


SFX_WHY = {"success": "성공 반응", "fail": "아쉬운 순간 (작게)", "surprise": "놀람", "kick": "공 차는 소리", "punchline": "농담 뒤", "section": "다음 순서로 넘어감",
           "cue": "시작 신호", "emphasis": "강조 글자", "count": "숫자 세기", "reaction": "웃는 얼굴", "play": "시범", "demo_call": "시범 예고", "recap": "정리"}


def _sfx_reason(c, text=""):
    """고른 사건 → 효과음을 넣은 이유 한 줄 (편집실 효과음 클립에 보임 · D-378)."""
    src = c.get("src") or c["kind"]
    k = "success" if c.get("look") == "success" else "fail" if c.get("look") == "fail" else "surprise" if c.get("look") == "surprise" else \
        "kick" if c.get("look") == "kick" else src
    lab = SFX_WHY.get(k, SFX_WHY.get(c["kind"], "재미 순간"))
    if src == "punchline" and c.get("laugh") is not None:
        lab = "농담 뒤 웃음"
    said = _short_text(str(c.get("mt") or ""), 16)
    shown = f"글자 '{_short_text(text, 12)}'" if text else ""
    return " · ".join(x for x in (lab, shown or (f"말 '{said}'" if said else "")) if x)


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
    """제목 카드·훅의 주제: 사용자가 붙인 짧은 한국어 영상 제목(파일 이름 · '[풋살 꿀팁]' 같은 말머리는 빼고)이 있으면 그것,
    없으면 대사에서 가장 많이 나온 주제어 (판정: 주제어 한 낱말 '패스'·'슛'만 있는 제목 카드는 너무 짧음)."""
    import hooks
    title = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", _title_of(name))).strip() if name else ""
    if title and re.search(r"[가-힣]", title) and len(title.replace(" ", "")) <= 12 and not re.search(r"\d{3,}|IMG|DSC|MOV", title, re.I):
        return title
    tk = hooks.topic_keywords([s.get("text", "") for s in segs], 3)
    return tk[0] if tk else "오늘의 레슨"


HOOK_MAX = {"long": 24, "shorts": 14}   # 훅 글자 최대 글자 수 (띄어쓰기 빼고) — 롱폼은 두 줄로 (_wrap2)


def _hook_text(moms, segs, fmt, name=None):
    """훅 자막: 앞부분 질문(문장 전체 · '패스하고 그 자리에 서 있으면 왜 안 될까요?'), 길면 물음표로 끝나는 구절, 없으면 '주제어 + 이것만 알면!'."""
    h = next((m for m in moms if m["kind"] == "hook_line"), None)
    lim = HOOK_MAX.get(fmt, 18)
    if h:
        t = re.sub(r"^(자|어|음|그|네|아)[,\s]+", "", h["text"].strip())
        t = re.sub(r"\s+", " ", t)
        if len(t.replace(" ", "")) <= lim:
            return t
        m = re.search(r"([^,.]*\?)", t)
        if m and len(m[1].replace(" ", "")) <= lim:
            return m[1].strip()
    return f"{_topic(segs, name)}, 이것만 알면 달라져요!"


def question_like(m):
    """훅 순간이 실제 질문인지 (앞부분 강한 말만 있는 훅 문장으로는 콜드 오픈을 하지 않음)."""
    return bool(m) and m.get("why") == "앞부분 질문"


def _wrap2(text, one=13):
    """화면 글자를 두 줄로 (띄어쓰기 빼고 one 자 넘을 때) — 큰 훅 글자가 한 줄로 길면 작아짐. 나누는 곳: 두 줄 길이가 비슷하고
    이음말(…면·…고·…데) 뒤를 먼저 · 꾸밈말('그 자리'·'받는 사람') 사이와 한 글자 낱말 뒤('서 / 있으면')는 피함."""
    import captions
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    ws = t.split(" ")
    if len(t.replace(" ", "")) <= one or len(ws) < 2:
        return t
    n = [len(re.sub(r"[^0-9A-Za-z가-힣]", "", w)) for w in ws]

    def score(k):
        a, b, w = sum(n[:k]), sum(n[k:]), ws[k - 1]
        punct = bool(re.search(r"[,.?!…~]$", w))
        return (abs(a - b) + (0 if punct or captions._CLAUSE.search(w) else 4 if captions._NICE.search(w) else 6)
                + (8 if captions._modifier(w, ws[k]) else 0) + (4 if n[k - 1] <= 1 and not punct else 0))
    best = min(range(1, len(ws)), key=score)
    return " ".join(ws[:best]) + "\n" + " ".join(ws[best:])


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


SFX_GAP = 3.3        # 효과음 최대 크기 = 보정한 말소리 최대 크기 - 이만큼 (말보다 조금 작게 · 1~6dB 범위 가운데) — voicePk 는 SIG_VER 14 부터
#                      내보내기와 같게 두 채널로 보정해 잼 (예전 한 채널 값은 편집본보다 0.5~1.1dB 작아 그만큼을 GAP 2.5 에 넣어 두었음)
SFX_SOFT = 4.3       # 글자에 붙는 작은 소리·짧은 음악은 조금 더 작게
SFX_FLOOR = -60.0    # 효과음·배경음악 레벨 아래 끝 (예전 -30 은 말이 아주 작은 원본(멀리 둔 카메라)에서 효과음이 말보다 커짐 · 소리 크기 맞추기가 전체를 다시 키움)
QUIET_VOICE = -36.0  # 보정한 말소리 최대가 이보다 작으면 알림 (마이크 없이 멀리서 찍은 영상)
SFX_CREST = 10.0     # 효과음 귀 크기(0.4초 K 무게)는 목표 최대 크기보다 이만큼 작게까지 (부딪히는 소리 딩동·짠·휙·레벨업은 9.8~13dB · 호루라기 2dB · D-379)
SFX_DUCK = 9.0       # 효과음 꼬리가 말과 겹치면 이만큼 줄임 (말이 가려지지 않게)
SFX_DUCK_MIN = 0.8   # 이보다 긴 효과음만 꼬리를 줄임 (앞 0.5초는 그대로)
STINGS = ("짠", "짠2", "짠3", "경쾌 짧은 음악", "경쾌 짧은 음악2", "경쾌 짧은 음악3", "맑은 짧은 음악", "맑은 짧은 음악2", "맑은 짧은 음악3",
          "라이저", "두구두구")


def _sfx_level(sig, name, kind, file_pk=None, gap=None, file_loud=None):
    """효과음 레벨(dB) = 목표 최대 크기(말 최대 크기 - 3~4dB) - 이 파일의 실제 최대 크기.
    파일 크기를 재서 맞춤 (예전 판처럼 '모든 효과음 파일은 -3 dBFS' 라고 가정하면 작은 파일은 안 들리고 큰 파일은 말보다 튐).
    gap: 말보다 이만큼(dB) 작게 (말 위에 작게 놓는 소리 · SFX_OVER_TALK).
    file_loud: 파일의 가장 큰 0.4초 귀 크기(sfxlib.loud_db) — 길게 울리는 높은 소리(호루라기)는 최대 크기가 같아도 귀 크기가 10dB 가까이 커서
    귀 크기가 '목표 최대 크기 - SFX_CREST' 를 넘지 않게 줄임 (부딪히는 소리 딩동·짠·휙은 그대로 · D-379 — 예전: 최대 크기로만 맞춰
    호루라기 -6 dBFS 로 만든 3dB 덜기(SOFTER)도 사라지고 가장 큰 말보다 7dB 크게 들림)."""
    sp = sig.get("voicePk")  # 목소리 보정을 거친 말소리 (효과음은 보정 없이 섞음)
    if sp is None:
        raw = sig.get("speechPk")
        if raw is None:  # 예전 신호 (말 최대 크기를 안 잰 것): 전체 최대 크기에서 조금 뺌
            raw = float(sig.get("peakDb") or -6.0) - 2.0
        sp = _voice_est(raw)
    if gap is None:
        gap = SFX_SOFT if kind in ("emphasis", "count", "section", "inner") or name in STINGS else SFX_GAP
    pk = sfxlib.PEAK_DB if file_pk is None else float(file_pk)
    lv = float(sp) - gap - pk
    if file_loud is not None:
        lv = min(lv, float(sp) - gap - SFX_CREST - float(file_loud))
    return round(min(18.0, max(SFX_FLOOR, lv)), 1)


# 양 범위(DENSITY)를 넘으면 차례로 줄이는 구성: 쉼 자르기 → 몽타주 → 정지 화면 → 숫자 세기 → 다시 보기 1개 → 티저 장면 → 다시 보기 0개
def _knobs(intensity):
    v = intensity in VITAL_LEVELS   # 재미 순간 지키기 (순간마다 사건 하나 · 양 맞추기에서 지킴 · D-262 · 듬뿍만 · D-269)
    return {"cuts": PACE[intensity]["cuts"], "montage": MONTAGE_N.get(intensity, 0), "teaser": TEASER_N[intensity], "freeze": None, "replay": None,
            "count": True, "vital": v, "protect": v}


def _reduce(k, have=None, keep=(), light=False):
    """한 단계 줄임 → 줄였으면 True. have: 방금 만든 편집본의 사건 수 {종류: 개수} (줄여도 바뀌지 않는 단계는 건너뜀 — 다시 만드는 데
    한 번에 몇 초씩 걸림) · keep: 이 스타일·양의 얼굴이라 남길 것 ('replay'·'freeze'·'montage' — 보통·듬뿍에서 스타일이 무게를 둔 것은
    하나는 남김 · 판정: 긴 원본에서 다시 보기·정지·몽타주가 모두 빠져 스타일끼리 같아짐).
    light: 재미 순간을 지키려고 줄일 때 — 스타일 모양이 덜 바뀌는 것만 (쉼 컷 · 숫자 세기 · 다시 보기 2개 넘으면 1개 · D-262)."""
    def n(kind, dflt):  # 방금 편집본의 그 종류 사건 수 (모르면 dflt · 사건 기록에 없는 종류는 0)
        return have.get(kind, 0) if have else dflt
    if k["cuts"] > 0.75:
        k["cuts"] = round(max(0.7, k["cuts"] * 0.7), 2)
        return True
    if light:
        if k["count"] and n("count", 1):
            k["count"] = False
            return True
        if (k["replay"] is None or k["replay"] > 1) and n("replay", 2) > 1:
            k["replay"] = 1
            return True
        return False
    if k["montage"] and n("montage", 1) and "montage" not in keep:
        k["montage"] = 0
        return True
    if (k["freeze"] is None or k["freeze"] > 0) and n("freeze", 1):
        floor = 1 if "freeze" in keep else 0
        if (k["freeze"] if k["freeze"] is not None else 99) > floor and n("freeze", 1) > floor:
            k["freeze"] = floor
            return True
    if k["count"] and n("count", 1):
        k["count"] = False
        return True
    if (k["replay"] is None or k["replay"] > 1) and n("replay", 2) > 1:
        k["replay"] = 1
        return True
    if k["teaser"] > 0 and n("teaser", 1):   # (0 이면 명장면 없이 첫 질문 장면으로만 시작)
        k["teaser"] -= 1
        return True
    if (k["replay"] is None or k["replay"] > 0) and n("replay", 1) and "replay" not in keep:
        k["replay"] = 0  # 마지막으로 다시 보기도 뺌 (끼워 넣은 장면 하나가 화면 사건 셋: 들어가는 컷·표시·나오는 컷)
        return True
    return False


def _signature(st, intensity):
    """이 스타일·양에서 꼭 남길 끼워 넣는 장면 (_reduce 의 keep)."""
    if intensity == "담백":
        return ()
    fun = st["fun"]
    keep = []
    if float(fun.get("slowmo_replay") or 0) >= 0.5:
        keep.append("replay")
    if float(fun.get("freeze") or 0) >= 1.0:
        keep.append("freeze")
    if intensity == "듬뿍" and float(fun.get("montage") or 0) >= 1.0:
        keep.append("montage")
    return tuple(keep)


def moment_key(m):
    return f"{m['kind']}:{float(m['t']):.2f}"


def _ai_copy(ai):
    return {k: dict(v) for k, v in (ai or {}).items()}


def compile_seq(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log=print, caps=None, ai=None, win=None):
    """스타일·양 → 새 편집본 하나 (+ 필요한 효과음·배경음악·정지 화면 미디어). caps: 말 자막 (글자 자리를 피하려고 · 없으면 받아쓰기로 만듦).
    ai: 클로드가 쓴 재미 글자 (moment_key → {kind, text} · 없으면 정해 둔 문구).
    win: 쇼츠 구간을 정해서 줄 때 (끝까지 자동으로 · editor.recommend 의 쇼츠 하나 {start, end, cuts, hook} + 있으면 point — 첫머리 주제 줄도
    그 구간의 것 · _win_short) — 없으면 MSG 가 고름 (v2.16.0 그대로).
    롱폼은 화면 사건 수가 양 범위(DENSITY) 위쪽을 넘을 때만 구성을 한 단계씩 줄여 다시 만듦 (재미 글자보다 구조를 먼저 줄임 ·
    범위 안이면 그대로 — 예전처럼 '빠진 재미 순간'까지 줄이면 긴 원본에서 다시 보기·정지·몽타주가 모두 빠지고 스타일끼리 같아짐)."""
    knobs = _knobs(intensity)
    keep = _signature(st, intensity)
    best = None
    prev = None
    for _ in range(10):
        _check()
        with editor.ep_font_scope(fmt == "shorts" and editor.shorts_card_on()):   # 은평 스타일 쇼츠: 글자 자리는 KFA 글꼴 폭으로 (BR-311)
            seq, media, n_ev, lost = _compile_once(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log, caps, knobs, ai, win)
        tot = editor.seq_total(seq)
        over = round(max(0.0, n_ev - DENSITY[intensity][1] * max(0.25, tot / 60.0)), 2)
        if best is None or (over, lost) < best[0]:
            best = ((over, lost), seq, media)
        kinds = dict((seq.get("msg") or {}).get("summary", {}).get("kinds") or {})
        kinds["teaser"] = sum(1 for e in seq["msg"]["events"] if e["kind"] == "teaser" and "명장면" in (e.get("why") or ""))
        if fmt != "long" or not over:
            if fmt == "shorts" and tot < min_shorts() and knobs.get("pointStop", True) and _shorts_points(moms):
                # 다음 포인트 앞에서 끊었더니 정리 컷·쉼 컷 뒤 min_shorts() 초가 안 됨 → 끊지 않고 다시 (쇼츠가 아예 빠지지 않게 · D-267)
                knobs["pointStop"] = False
                best = None
                continue
            break
        if prev is not None and prev[0] != knobs["cuts"] and prev[1:] == (round(tot, 2), n_ev):
            knobs["cuts"] = min(knobs["cuts"], 0.7)  # 쉼 컷을 줄여도 바뀌지 않음 → 그 단계는 건너뜀 (다시 만드는 데 한 번에 몇 초씩)
        prev = (knobs["cuts"], round(tot, 2), n_ev)
        if not _reduce(knobs, kinds, keep, light=knobs.get("protect", False)):
            if knobs.get("protect", False):
                # 재미 순간을 지킨 채로는 더 줄일 구성이 없음 → 양 범위에 맞춰 순간 사건은 소리만 남김 (양 범위가 먼저 · D-262)
                knobs["protect"] = False
                continue
            if knobs.get("vital", False):
                # 그래도 넘으면 순간마다 사건 하나 더하기(plan_events)도 끄고 예전처럼 (스타일 얼굴보다는 먼저 · D-262)
                knobs["vital"] = False
                continue
            if not keep:
                break
            keep = ()   # 스타일 얼굴을 남기고도 양 범위를 넘으면 (아주 짧은 원본) 마지막으로 그것까지 줄임 — 양 범위가 먼저
            if not _reduce(knobs, kinds, keep):
                break
    _check()
    return best[1], best[2]


def _compile_once(name, info, sig, segs, moms, st, intensity, fmt, seed, label, log, caps, knobs, ai=None, win=None):
    dur = float(info["duration"])
    words = _words(segs)
    rh = st["rhythm"]
    pts = _shorts_points(moms)   # (쇼츠 후보에만 · 롱폼 정리 컷은 그대로)
    rec = _recommend(name, segs, rh.get("keepPause") or None, sig, points=pts)
    junk = [(j["a"], j["b"], j["why"]) for j in rec.get("junk_list") or []]
    pace = dict(PACE[intensity], cuts=knobs["cuts"])
    short_r = None   # 쇼츠로 고른 추천 구간 (첫머리 주제 줄 · BR-120)
    # 농담 뒤 효과음을 넣는 스타일이면 웃음 박자를 남김 (D-375)
    joke_snd = intensity != "담백" and bool(SFX_PALETTE.get(st["sound"].get("palette"), SFX_PALETTE["variety"]).get("punchline"))
    if fmt == "shorts":
        base = _shorts_window(rec, moms, dur, sig.get("demo") or [], win, points=pts, stop=knobs.get("pointStop", True))
        # 시범 장면으로 살린 곳에 걸친 군말·NG 도 뺌 — 롱폼과 같게 (판정 round7: S2 쇼츠에 '어,' · S1 쇼츠 첫머리 '어' 가 남음 · U7 · D-265)
        hits = list(sig.get("onsets") or ()) + list(sig.get("hits") or ())
        base = snap_edges(editor._minus(base, junk) or base, sig.get("blobs") or [], words, junk, hits, sig.get("demo") or ())
        # (앞뒤가 다 잘린 짧은 조각을 롱폼처럼 빼지는 않음 — 쇼츠는 쉼 컷이 촘촘해 '공이 조금 뒤로 갔네요'·'하나,' 같은 말 조각이 빠짐 ·
        #  판정 검토 고침 렌더 r7r · 0.6초 '그렇죠.' 조각은 공 소리를 군말로 자른 탓이라 hits 로 고침 · D-268)
        # 첫머리 주제 줄은 이 쇼츠가 실제로 보여 주는 구간의 포인트로 — 정해 준 구간(끝까지 자동으로)이면 그 구간
        # (MSG 가 고르는 구간(_shorts_best)은 다른 포인트일 수 있음 · 그러면 다른 쇼츠의 주제 줄이 붙음)
        short_r = _win_short(win, segs, sig) if win else _shorts_best(rec, moms)
        if joke_snd:
            base = keep_laugh_beat(base, moms, words, junk, last=False)
    else:
        base = keep_cuts(rec["tidy"], sig.get("demo")) or [{"in": 0.0, "out": dur}]
        base = editor._minus(base, junk) or base  # 시범 장면으로 살린 곳에 걸친 군말·NG 도 뺌
        base = snap_edges(pace_pauses(base, junk, pace["cuts"], sig.get("blobs") or ()), sig.get("blobs") or [], words, junk,
                          list(sig.get("onsets") or ()) + list(sig.get("hits") or ()), sig.get("demo") or ())
        base = drop_slivers(base, moms, _cheers(sig))
        if joke_snd:
            base = keep_laugh_beat(base, moms, words, junk)
    intro = st["intro"]
    mild = intensity == "담백"
    hk_m = next((m for m in moms if m["kind"] == "hook_line"), None)
    teaser_on = fmt == "long" and intro.get("type") == "teaser" and not mild
    hk_near = hk_m is not None and bool(base) and hk_m["a"] - float(base[0]["in"]) <= HOOK_NEAR and hk_m["b"] - hk_m["a"] <= 6.0
    # 티저 스타일: 본편 앞부분의 첫 질문(훅 문장)을 티저 끝에 감독님 목소리로 넣고 본편에서는 뺌 — 티저 글자로 띄운 질문을 5초 뒤 본편에서
    # 또 들으면 되풀이 (판정: '반복' · 티저가 1.7초뿐이라 인사와 이어지지 않음)
    n_tz = min(int(intro.get("clips") or 3), knobs["teaser"])
    # (스타일 설명대로 명장면 n_tz 개 + 끝에 첫 질문 · 판정: '명장면 3개 티저'라더니 보통은 1개)
    wins = _teaser_windows(moms, n_tz, dur, junk) if teaser_on and n_tz > 0 else []
    teaser_q = bool(wins) and hk_near
    # 첫 질문 장면으로 시작 (콜드 오픈 · 감독님 목소리): 첫 질문 스타일 · 제목으로 시작하는 스타일(다큐 · 그 위에 큰 제목) · 담백은 티저 스타일도
    # (아주 짧은 원본에서 양 범위 때문에 명장면을 다 뺀 티저 스타일도) — 질문이 앞 30초 안에 있을 때만
    # (판정: 담백·다큐는 모두 '안녕하세요. 오늘은 …' 인사로 시작해 첫 3초 훅이 약함)
    hook_clip = fmt == "long" and hk_near and intro.get("type") in (("hook_line", "title_first") + (("teaser",) if mild or not wins else ())) \
        and (question_like(hk_m) or intro.get("type") == "hook_line")
    film_open = hook_clip and intro.get("type") == "title_first"
    if (hook_clip or teaser_q) and hk_near:
        # 첫 질문 장면으로 시작하면 본편 앞부분에서 그 질문을 또 하지 않게 뺌 (판정: 같은 질문을 0초와 5초에 두 번 봄) · 문장 전체를
        # (moments 가 쉼으로 잘린 문장 앞머리까지 잡음 · 판정: '패스하고 그'만 남음)
        # 앞뒤 쉼도 함께 (말 없는 2초짜리 조각이 남아 컷이 하나 더 생기고 화면이 빈 채로 멈춤) — 앞 말 끝·다음 말 첫소리에서 0.15초씩 남김
        pw = max([w[1] for w in words if w[1] <= hk_m["a"] + 0.05] or [hk_m["a"] - 0.25])
        nw = min([w[0] for w in words if w[0] >= hk_m["b"] - 0.05] or [hk_m["b"] + 0.35])
        cut_a = max(min(pw + 0.15, hk_m["a"] - 0.1), hk_m["a"] - 1.0)
        cut_b = min(max(nw - 0.15, hk_m["b"] + 0.2), hk_m["b"] + PAUSE_MUST)
        base = editor._minus(base, [(round(cut_a, 2), round(cut_b, 2), "첫 질문")]) or base
    # 컷 리듬: 스타일의 목표 컷 길이 ÷ RHYTHM (양이 적을수록 긴 말을 덜 나누고 확대도 덜 바꿈 · 담백은 안 나눔 · 쇼츠 형식은 늘 빠르게)
    rf = max(RHYTHM[intensity], RHYTHM["듬뿍"]) if fmt == "shorts" else RHYTHM[intensity]
    slow = (lambda v: float(v or 0) / rf if rf > 0 and float(v or 0) > 0 else 0.0)  # noqa: E731
    every = slow(rh.get("zoomEvery"))
    zoom = min(1.6, max(1.0, float(rh.get("zoomScale") or 1.0)))
    if every <= 0:
        zoom = min(zoom, editor.SOFT_ZOOM)
    c3 = [slow(x) for x in (rh.get("curve3") or [0, 0, 0])][:3]
    cuts = editor._rhythm([dict(c) for c in base], segs, {"splitShot": slow(rh.get("splitShot")), "curve3": c3, "tempo": rh.get("tempo") or 0}, every)
    # 담백: 원본 순서를 바꾸는 티저·첫 질문 장면 없이 본편 첫 장면 위에 작은 제목만 (질문이 늦게 나오면 질문 글자도) — 화면 사건을 양에 맞게 적게
    if caps is None:
        caps = editor._captions_of(segs, info)
    picked, mont = plan_events(sig, moms, st, intensity, fmt, seed, base, words, knobs, avoid=[(a, b) for a, b, _ in wins], ai=_ai_copy(ai), caps=caps)
    _check()
    fc = zoom_anchor(face_center(sig) or [0.5, 0.38], face_box(sig))
    B = _Build(name, info, fmt)
    B.card = fmt == "shorts" and editor.shorts_card_on()   # 은평 스타일 쇼츠 (BR-240 · 스타일 화면 '쇼츠 화면 배치')
    sk_lay = editor.skin_layout(info=info) if fmt == "shorts" and not B.card and editor.shorts_skin_on() else None   # 풋살사관학교 스킨 (BR-320 · 원본 비율로 창을 덮음 · 못 읽으면 지금처럼)
    cap_y = caption_y(st, fmt)
    if B.card:
        cap_y = max(cap_y, editor.EP_CAP_Y)   # 말 자막은 아래 띠 (BR-242)
        B.lay = seq_layout(fmt, True)
        logo = bool(B.lay.get("logo"))
        B.cap_lim = editor.ep_cap_lim(editor.ep_longline(0, logo) if editor.ep_line_on() else None, logo)
        if B.cap_lim is not None:
            cap_y = min(cap_y, B.cap_lim)   # 말 자막은 전체 강좌 줄·로고 위로 (D-472 · 편집실·가편집 ep_cap_clear 와 같은 한계)
    elif sk_lay:
        B.lay, B.skin = sk_lay, editor.skin_window(sk_lay["skin"])
        cap_y = editor.skin_cap_y(B.skin["bottom"])   # 말 자막은 영상 창 아래 끝 바로 위 (BR-322 · 배운 자막 자리와 상관없이)
    looks = text_looks(st, fmt, cap_y, card=B.card, skin=B.skin)
    snd = dict(st["sound"], moods=_moods_for(st["sound"].get("moods") or {}, moms))
    pal = SFX_PALETTE.get(snd.get("palette"), SFX_PALETTE["variety"])
    sections = []   # 배경음악 구간 (타임라인 시작, 끝, 분위기)
    topic = _topic(segs, name)
    hook = _hook_text(moms, segs, fmt, name)
    if fmt == "shorts" and win and win.get("hook"):  # 정해 준 쇼츠 구간: 쇼츠마다 다른 위 큰 제목 (editor.recommend 가 고른 것 · BR-093)
        hook = str(win["hook"])
    hook_look = dict(looks["emphasis"], y=0.2 if cap_y > 0.5 else 0.7, size=84, fill="#FFFFFF", effect="pop")

    # --- 인트로 ---
    if teaser_on and wins:  # (명장면이 없으면 아래 첫 질문 장면으로 — 본편에서 뺀 질문이 사라지지 않게)
        prev = None
        made = []
        tz_sfx = []
        for a, b, m in wins:
            v, au = B.clip(a, b, nocaps=True, vol_db=-3.0)
            if prev is not None and intro.get("flash"):
                tr = B.flash(prev, v)
                # 휙 봉우리를 명장면이 바뀌는 컷(흰 번쩍 가운데)에 (D-371 · 예전: 컷 0.12초 앞에서 시작 → 봉우리가 컷 뒤)
                tz_sfx.append(_sfx_at(B, v["start"], pal.get("teaser", "휙"), None, "teaser", why="티저 명장면이 바뀌는 컷"))
                made.append(tr["id"]) if tr else None
            prev = v
            made += [v["id"], au["id"]]
        q_at = B.pos
        if wins and teaser_q:  # 티저 끝에 첫 질문 (감독님 목소리 · 말 자막 그대로)
            # (훅 글자는 명장면 위에서만 — 질문 장면까지 남기면 질문 앞 말('오늘은 슈팅 챌린지예요') 동안 다른 글이 떠 말과 자막이 다르고
            #  스튜디오 화면 위에 따로 놂 · 판정 round5 최종)
            v, au = B.clip(max(0.0, hk_m["a"] - 0.1), hk_m["b"] + 0.2)
            tr = B.flash(prev, v) if prev is not None and intro.get("flash") else None
            made += [v["id"], au["id"]] + ([tr["id"]] if tr else [])
        if wins:
            hk = B.title(_wrap2(hook), 0.0, max(1.5, min(q_at, 8.0)) if teaser_q else min(B.pos, 3.2), hook_look)
            B.event("teaser", 0.0, hook, f"명장면 {len(wins)}개 미리 보기" + (" + 첫 질문" if teaser_q else ""),
                    refs={"items": made, "titles": [hk["id"]], "sfx": tz_sfx}, ins={"start": 0.0, "len": round(B.pos, 3)})
    elif hook_clip:
        h = hk_m
        # 다큐: 질문은 말 자막 그대로 + 그 위에 큰 제목 (질문 글자를 따로 띄우면 화면 글자가 셋) · 나머지: 말 자막 대신 큰 질문 글자
        v, au = B.clip(max(0.0, h["a"] - 0.1), h["b"] + 0.2, nocaps=not film_open)
        refs = {"items": [v["id"], au["id"]], "titles": []}
        if not film_open:
            refs["titles"].append(B.title(_wrap2(hook), 0.0, B.pos, dict(hook_look, size=80))["id"])
        B.event("teaser", 0.0, hook, "첫 질문으로 시작", refs=refs, ins={"start": 0.0, "len": round(B.pos, 3)})
    shorts_hook = fmt == "shorts"  # 쇼츠: 티저 없이 맨 위에 훅 자막을 처음 2.5초 (본편을 다 만든 뒤 넣음)
    # 제목은 늘 움직이는 본편 위에 (정지 사진 제목 카드는 들어가고 나오는 컷 둘 + 2초 멈춘 화면 — 짧은 영상에서는 그만큼 재미 글자가 빠짐 ·
    # 판정: 다큐 듬뿍이 2초 정지 제목 카드로 시작) · 티저 스타일은 티저가 끝나고 본편이 시작할 때 크게 (예능 제목 카드 느낌)
    overlay_title = fmt == "long" and bool(intro.get("titleCard") or mild)
    big_title = (teaser_on and bool(wins)) or film_open
    intro_end = B.pos

    # --- 본편 조각 (확대·흔들기 표시 · 끼워 넣을 곳) ---
    pieces = [{"in": float(c["in"]), "out": float(c["out"]), "zoom": bool(c.get("zoom")), "speed": float(c.get("speed") or 1.0)} for c in cuts]
    inserts = {}   # 조각 번호 → 그 앞에 넣을 것 [(종류, 사건)]
    for c in picked:
        if c["kind"] == "punch":
            t0 = c["t"]
            p0 = next((p for p in pieces if p["in"] <= t0 < p["out"]), None)
            if p0 is not None and t0 - p0["in"] < ZOOM_GAP / 2:  # 컷 바로 뒤 → 컷 자리에서 (화면이 한 번에 바뀜)
                t0 = p0["in"]
            i = _split(pieces, t0)
            if i is None:
                continue
            end = min(pieces[i]["out"], t0 + c["dur"])
            if pieces[i]["out"] - end < ZOOM_GAP:  # 남는 조각이 짧으면 컷까지 확대한 채로 (되돌아온 뒤 바로 컷 · 판정: 0.4초 사이 두 번 바뀜)
                end = pieces[i]["out"]
            _split(pieces, end)
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
            c["at"] = _snap_insert(_after_reaction(lines, c["b"]), pieces, words)
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
    closing = _final_closings(moms, dur)
    mont_at = None
    if mont and fmt == "long" and closing and any(p["in"] - 0.2 <= closing[0] < p["out"] for p in pieces):
        mont_at = closing[0]
        _split(pieces, mont_at, 0.2)

    def _montage():
        s0 = B.pos
        made, prev = [], [x for x in B.items if x["track"] == "V1"][-1] if B.items else None
        m_sfx = []
        for k, m in enumerate(mont[:knobs["montage"]]):
            a = max(0.0, m["t"] - 0.2)
            v, au = B.clip(a, min(dur, a + MONTAGE_CLIP), nocaps=True, vol_db=-6.0)
            tr = B.flash(prev, v, "white", 0.14) if prev is not None else None
            made += [v["id"], au["id"]] + ([tr["id"]] if tr else [])
            if k % 2 == 0:  # 휙 소리는 한 컷 걸러 (번쩍 전환마다 다 넣으면 시끄러움) · 봉우리를 컷에
                m_sfx.append(_sfx_at(B, v["start"], pal.get("montage", "휙"), -1.5, "montage", why="명장면 몽타주 컷"))
            prev = v
        tt = B.title("오늘의 명장면", s0, B.pos - s0, dict(looks["emphasis"], y=0.2 if cap_y > 0.5 else 0.75, size=80, fill="#FFFFFF", effect="pop"))
        B.event("montage", s0, "오늘의 명장면", f"시범 {len(mont[:knobs['montage']])}개를 빠르게", refs={"items": made, "titles": [tt["id"]], "sfx": m_sfx},
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
            refs = {"items": [v["id"]], "fx": "punch"}
            if c.get("sfx"):  # 웃음·반응 순간의 쾅 확대: 휙 봉우리를 확대 시작에 (D-375 · 자리는 _audio_items 가 확대 사건으로 다시 찾음)
                refs["sfx"] = [_sfx_at(B, v["start"], c["sfx"], None, "punch", why="웃음·반응 순간 쾅 확대" if c["src"] != "reaction" else "웃는 얼굴 쾅 확대")]
            B.event("punch", v["start"], "", c.get("why") or "확대", src=p["in"], refs=refs)
        if p.get("shake"):
            B.event("shake", v["start"], "", "화면 흔들기", src=p["in"], refs={"items": [v["id"]], "fx": "shake"})
    for _, c in ins_at:
        _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y)
    main_end = B.pos
    mild_open = fmt == "long" and mild and main_start < 0.05 and main_end - main_start > 6.0   # 담백: 작은 제목을 본편 첫 장면 위에
    # 담백의 첫 질문 글자는 질문을 곧(HOOK_NEAR_TXT 초 안) 말하지 않을 때만 — 곧 말하면 말 자막과 같은 질문이 두 번·세 줄로 쌓임 (판정)
    h_tl = B.src_to_tl(hk_m["a"]) if hk_m else None
    hook_over = mild_open and bool(hook) and not (h_tl is not None and h_tl - main_start <= HOOK_NEAR_TXT)
    if overlay_title and main_end - main_start > TITLE_CARD + 1.0:
        # 제목으로 시작하는 스타일(다큐)·담백은 첫 장면부터 (첫 1초 안에 화면이 바뀌어야 함) · 나머지는 첫 컷과 떨어뜨려 (같은 때 바뀌면 화면만 바쁨)
        # · 첫 질문 장면으로 시작하면 그 장면 위에 (질문 글자와 함께 · 화면 글자 2개까지)
        s0 = 0.0 if hook_clip else main_start if intro.get("type") == "title_first" or mild_open or big_title else \
            main_start + min(3.0, (main_end - main_start) / 4)
        ln = TITLE_CARD + (1.0 if hook_over else 0.6)
        s0, ln = _title_after_said(B, s0, ln, topic, caps, main_end)
        refs = {"shapes": [], "titles": []}
        # (다큐의 첫 질문 장면 위 큰 제목도 한 줄 — 질문 말 자막·'오늘의 주제'·제목이 한 화면에 셋이면 복잡 · 판정 round5 최종)
        for kind, tx, look in _small_title(st, _short_text(topic, 10), hook_over or hook_clip, big=big_title):
            if kind == "shape":
                refs["shapes"].append(B.shape(*look, s0, ln, 0.92, 24, "작은 제목")["id"])
            else:
                refs["titles"].append(B.title(tx, s0, ln, look)["id"])
        tx_ids = [i for i in refs["titles"]]
        refs["sfx"] = [_sfx_at(B, s0, pal.get("title", "짠"), None, "title", why=f"작은 제목 '{_short_text(topic, 10)}'", title=tx_ids[-1] if tx_ids else None,
                               must=True)]
        B.event("title", s0, topic, "작은 제목 (본편 위)", refs=refs)
    if hook_over:
        txt = _wrap2(hook)
        hk = B.title(txt, main_start, min(3.2, main_end - main_start), dict(hook_look, size=76, y=0.72 if cap_y > 0.5 else 0.3))
        B.event("teaser", main_start, hook, "첫 장면 위 질문 글자", refs={"titles": [hk["id"]]})

    # --- 글자·효과음 사건 (본편 시각으로 옮김) ---
    if caps is None:
        caps = editor._captions_of(segs, info)
    for c in picked:
        if c["kind"] not in TEXT_KINDS and c["kind"] != "accent":
            continue
        tl = B.src_to_tl(c["t"])
        if tl is None:
            continue
        refs = {"titles": []}
        text = c.get("text") or ""
        only_snd = c["kind"] == "accent"   # 소리만 (글자 없이 · D-375)
        if c["kind"] in ("emphasis", "situ", "fx", "inner") and text and _cap_dup(text, c["t"] - 0.1, c["t"] + float(c.get("dur") or 1.0), caps):
            # 같은 때 말 자막과 같은 글: 강조는 핵심 낱말만('달라져요!') · 상황 자막은 꼬리표('동작 ①') · 그래도 같으면 글자 없이
            # (강조 순간은 확대가 맡음 · 꼭 넣을 뜻 소리(다음 포인트·성공·웃음 뒤 농담)는 소리만 남김 — 예전: 글자와 함께 빠짐 · D-375)
            cap_txt = " ".join(x["text"] for x in caps if float(x["end"]) > c["t"] - 0.1 and float(x["start"]) < c["t"] + float(c.get("dur") or 1.0))
            alt = _emph_short(text, cap_txt) if c["kind"] == "emphasis" else _situ_badge(text) if c["kind"] == "situ" else None
            if not alt or _cap_dup(alt, c["t"] - 0.1, c["t"] + float(c.get("dur") or 1.0), caps):
                if not (c.get("sfx") and c.get("sfxMust") and not mild):
                    continue
                only_snd = True
            else:
                text = alt
        if c["kind"] in TEXT_KINDS and not only_snd:
            look = dict(looks[c["kind"]])
            if c["kind"] == "fx":
                look["rot"] = random.Random(seed + int(c["t"] * 10)).choice((-8, -6, 6, 8))
                look["fill"] = {"fail": "#4DA3FF", "success": "#FFE14D", "surprise": "#FF4D4D"}.get(c.get("look"), "#FF9F1C")
            if c["kind"] == "emphasis":
                tl -= 0.12  # 낱말이 들리기 바로 앞에 (효과음이 말 첫소리를 가리지 않게)
            d = c["dur"]
            if shorts_hook and c["kind"] in ("situ", "score") and tl < SHORTS_HOOK_SEC + 0.1:
                # 쇼츠 첫 훅 글자(맨 위) 동안 위쪽 상황·점수 글자는 그 뒤에 (판정 round5 최종: '포인트 2'가 훅 글자와 겹쳐 가려짐)
                d, tl = max(1.0, d - (SHORTS_HOOK_SEC + 0.1 - tl)), SHORTS_HOOK_SEC + 0.1
                if c["src"] == "section":
                    c["sfx"] = None   # 쇼츠가 그 포인트로 시작: 첫 장면 훅 소리(짠)가 맡음 — 훅 길이로 정해진 2.6초에 휙을 또 넣지 않음 (D-379)
            if c["kind"] == "count":
                # 숫자가 하나씩 옆으로 쌓임 ('1' → '1 2' → '1 2 3') · 마지막 숫자 뒤 0.6초까지 · 겹치지 않게 자리를 나눔
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
                    # 숫자 하나하나에 틱 (글자 첫 장면 · 예전: 첫 숫자에만 · D-373) — 한 묶음이라 효과음 간격 규칙은 함께 셈
                    grp = f"count:{tl:.2f}"
                    # (담백은 첫 숫자에만 · 양 안에서)
                    refs["sfx"] = [_sfx_at(B, t2, c.get("sfx") or "틱", None, "count", why=f"숫자 세기 '{n}'", title=tid, must=bool(c.get("sfxMust")), group=grp)
                                   for (t2, n), tid in list(zip(tls, refs["titles"]))[:1 if mild else None]] if c.get("sfx") else []
                    B.event("count", tl, "하나 둘 셋", c["why"], src=c["t"], refs=refs)
                continue
            # 끼워 넣은 장면(다시 보기·정지·몽타주) 앞에서 끝냄 — 앞 장면의 글자가 다시 보기 위에 남으면 다른 화면 위에 겹침 (판정 round5: '나이스!!')
            # (글자 시작과 거의 같은 때 시작하는 끼운 장면도 — 말 끝 뒤에 띄우는 농담 글자가 바로 뒤 다시 보기와 0.05초 차이로 겹쳤음)
            nxt = min([float(e["ins"]["start"]) for e in B.events if e.get("ins") and e["kind"] in ("replay", "freeze", "montage")
                       and float(e["ins"]["start"]) > tl - 0.03] or [1e9])
            if tl + d > nxt - 0.02:
                d = nxt - 0.02 - tl
                if d < MIN_TEXT:  # 너무 짧으면 조금 앞당겨 MIN_TEXT 초 — 뒤에서 MIN_TEXT 로 늘려도 끼워 넣은 장면에 안 걸리게 (예전 0.5초는 0.8초로 늘어 걸쳤음)
                    tl, d = max(0.0, nxt - 0.02 - MIN_TEXT), MIN_TEXT
                    if c["src"] == "punchline" and c.get("sfx"):
                        c["sfx"] = None   # 농담 소리는 말 끝 앞으로 당기지 않음 (바로 뒤 끼운 장면 컷의 휙이 그 순간을 맡음)
            tt = B.title(text, max(0.0, tl), d, look)
            refs["titles"].append(tt["id"])
        if c.get("sfx"):
            # 소리 맞출 점 = 글자 첫 장면 (글자가 늦게 뜨면 _audio_items 가 그 자리로 · 예전: 말 첫소리를 피하려고 글자보다 0.1~0.2초 먼저) ·
            # 소리만이면 그 순간의 말 틈 창 안 (D-371·D-372)
            tid = refs["titles"][0] if refs["titles"] else None
            t_anchor = next((t_["start"] for t_ in B.titles if t_["id"] == tid), tl) if tid else tl
            win = None
            if only_snd:
                lo, hi = c.get("win") or (min(c["t"], c.get("lead") or c["t"]) - 0.3, c["t"] + 0.8)
                win = _tl_win(B, c["t"], tl, lo, hi, back=c["src"] == "section")
            if c.get("ticks"):   # 숫자 세기 (글자 없이): 숫자 말마다 틱 한 묶음
                grp = f"count:{tl:.2f}"
                refs["sfx"] = [_sfx_at(B, B.src_to_tl(w0), c["sfx"], None, "count", why=f"숫자 세기 (글자 없이) {k_ + 1}", must=True, group=grp)
                               for k_, w0 in enumerate(c["ticks"]) if B.src_to_tl(w0) is not None]
            else:
                refs["sfx"] = [_sfx_at(B, t_anchor, c["sfx"], -4.0 if c.get("look") == "fail" else None, "accent" if only_snd else c["kind"],
                                       why=_sfx_reason(c, "" if only_snd else text), title=tid, win=win, must=bool(c.get("sfxMust")),
                                       soft=c.get("look") == "fail", group=None, mk=_sfx_key(c, intensity)[0] or c["src"],
                                       lead=B.src_to_tl(c["lead"]) if c.get("lead") is not None else None)]   # (다음 포인트 줄 시작 · 군말 앞)
        ev = B.event(c["kind"] if c["kind"] in TEXT_KINDS and not only_snd else "sfx", tl, (text if not only_snd else "") or c.get("sfx") or "", c["why"],
                     src=c["t"], refs=refs)
        ev["moment"] = {"kind": c["src"]}
        if only_snd:
            ev["accent"] = True
        if c.get("look") == "recap":
            ev["_recap"] = c["mid"]
    _join_recap(B, picked)

    topic_line = opening = ""
    if shorts_hook and B.pos > 3.0:
        hk, kt, opening = _shorts_intro(B, hook, short_r, rec.get("videoTopic") or "", editor.shorts_topic_on(), looks, st, cap_y, fmt, caps)
        box = None
        if B.skin:  # 풋살사관학교 스킨: 큰 제목 밑 흰 둥근 상자 (폭은 큰 제목에 맞춤) · 두 줄 큰 제목이면 상자를 두 줄 높이로 · 주제 줄 자리가 모자라면
            box = editor.skin_box(hk, B.pos)   # 주제 줄을 줄이고 그래도 안 되면 이 쇼츠에서만 빼고 studio.log 에 까닭 (D-540)
            gone = editor.skin_stack(hk, box, [kt] if kt else [], B.skin["top"])
            if gone:
                editor.skin_gone_note(hk, gone)
                B.titles.remove(kt)
                kt = None
        topic_line = kt["text"] if kt else ""
        k_h = _sfx_at(B, 0.0, pal.get("title", "짠"), None, "hook", why="쇼츠 첫 장면 큰 글자", title=hk["id"], must=True)
        refs = {"titles": [hk["id"]] + ([kt["id"]] if kt else []), "sfx": [k_h]}
        if B.skin:  # 풋살사관학교 스킨: 큰 제목 밑 흰 둥근 상자 (도형 · 끝까지 · 폭은 큰 제목에 맞춤) — 큰 제목·주제 줄은 글자 자리 정리에서 옮기지 않음
            B.shapes.append(box)
            refs["shapes"] = [box["id"]]
            B.fixed |= set(refs["titles"])
        if B.card:  # 은평 스타일: 큰 제목 밑 흰 상자 (도형 · 끝까지) — 큰 제목·사실 줄은 글자 자리 정리에서 옮기지 않음
            box = editor.ep_box(B.pos)
            B.shapes.append(box)
            refs["shapes"] = [box["id"]]
            B.fixed |= set(refs["titles"])
            xt, xg = editor.ep_extras(B.pos, logo=bool(B.lay.get("logo")))   # 시리즈 이름표(큰 제목과 한 묶음 · 빼면 같이 빠짐)·전체 강좌 줄(따로) · 고른 것만 (BR-245 · BR-246)
            B.titles += xt
            B.shapes += xg
            B.fixed |= {t["id"] for t in xt}
            refs["titles"] += [t["id"] for t in xt if t["plan"] == "series"]
            refs["shapes"] += [g["id"] for g in xg]
        B.event("hook", 0.0, f"{kt['text']} · {hk['text']}" if kt else hk["text"],
                ("쇼츠 내내 흰 상자 제목 + 사실 줄" if kt else "쇼츠 내내 흰 상자 제목") if B.card else
                ("쇼츠 내내 흰 상자 제목 + 주제 줄" if kt else "쇼츠 내내 흰 상자 제목") if B.skin else
                "쇼츠 첫 2.5초 주제 줄 + 포인트" if kt else "쇼츠 첫 2.5초 훅 자막", refs=refs)
        # 첫머리가 이미 말한 순서('동작 2' · '두 번째 포인트')를 바로 뒤 상황 자막이 또 띄우면 뺌 (검토: 2.6초에 '동작 2'가 다시 뜸)
        _drop_said_order(B, hk, kt)

    # --- 몽타주 (마무리 말이 없으면 끝나기 전) ---
    if mont and fmt == "long" and not mont_done:
        _montage()
        sections.append((mont_done[0], mont_done[1], snd.get("moods", {}).get("outro", "신남")))

    # --- 엔드 화면 ---
    if fmt == "long" and B.pos >= 25.0 and not _end_overlay(B, moms, intensity, pal, dur, face_box(sig), words, caps, junk):
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
    if fmt == "long" and intro.get("letterbox"):
        # 다큐: 처음부터 끝까지 위아래 검은 띠 (영화 화면 느낌 · 화면 사건이 아님) — 제목 화면에만 띠가 있다가 없어지면 화면이 튐 (판정)
        for y in (0.0, 1.0 - LETTERBOX):
            B.shape(0.0, y, 1.0, LETTERBOX, "#000000", 0.0, total, 1.0, 0, "검은 띠 (영화 화면)")["full"] = True
    if B.skin:   # 스킨: 화면 좌우는 얼굴·공을 따라 · 세로 원본은 위아래도 · 확대 기준점은 창 좌표로 (BR-326 · BR-328 · 전술 그림·글자 자리를 셈하기 전에)
        B.skin_faces = editor.skin_reframe(name, B.items, B.lay, info=info, speech=[(w[0], w[1]) for w in words], report=B.skin_rep)
        _skin_split_refs(B)
    caps_tl = editor.timeline_captions({"items": B.items, "captions": caps, "format": fmt}) if st["captions"].get("on", True) else []
    lay_cap = editor.skin_caption(_caption_style(st, fmt, cap_y), B.skin["bottom"]) if B.skin else _caption_style(st, fmt, cap_y)   # 스킨: 실제 말 자막(96 · D-529)을 피함
    if B.skin:   # 차기 직전 공이 말 자막 줄에 있으면 그동안 말 자막을 공 위로 (편집본 capLift · 글자 자리 정리도 올린 자막을 피함 · D-539)
        B.cap_lift = editor.skin_cap_lifts(name, B.items, {"format": "shorts", "layout": B.lay, "info": info}, lay_cap)
    layout_titles(B, caps_tl, lay_cap, total, face_box(sig), moms if intensity != "담백" else (), words, intensity in VITAL_LEVELS)
    if fmt == "long":
        govern(B, intensity, fc, total, moms, words, sig.get("cuts") or (), st, knobs.get("protect", False))
        _smooth_zoom_joins(B)
    _check()
    _settle_titles(B)
    place_tactics(B, name, sig, moms, intensity, fmt)
    seq_items_audio = _audio_items(B, sig, sections, seed, snd, intensity, words)
    # 소리만 남긴 순간인데 소리 양 범위·간격 때문에 그 소리도 빠졌으면 사건도 뺌 (아무것도 안 남은 사건 · D-262)
    B.events = [e for e in B.events if not (e.get("accent") and not (e.get("refs") or {}).get("items"))]

    # --- 챕터 (3분 넘을 때) · 썸네일 장면 ---
    if fmt == "long" and total >= 180:
        _chapters(B, moms, topic)
    thumbs = _thumb_picks(moms, hook, B)

    tracks = editor.default_tracks()
    for tr in tracks:
        if tr["id"] == "A2":
            tr.update(role="dialog", name="효과음", voiceFx=False)  # 효과음은 목소리 보정(압축기) 없이 · 크기는 보정한 말소리에 맞춤
        if tr["id"] == "A3":
            tr["name"] = "배경음악"
    if any(it["track"] == "A4" for it in B.items):
        tracks.append({"id": "A4", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0.0, "role": "music", "name": "배경음악"})
    if any(it["track"] == "A5" for it in B.items):
        tracks.append({"id": "A5", "k": "a", "lock": False, "mute": False, "solo": False, "target": False, "h": 1, "vol": 0.0, "role": "dialog", "name": "효과음",
                       "voiceFx": False})
    capst = editor.ep_caption(_caption_style(st, fmt, cap_y)) if B.card else _caption_style(st, fmt, cap_y)   # (은평 스타일: 어두운 글씨는 흰 글씨)
    if B.card and B.cap_lim is not None:   # ep_caption 이 아래 띠(0.8)로 내린 것을 다시 강좌 줄·로고 위로 (D-472)
        capst["y"] = min(float(capst["y"]), B.cap_lim)
    if B.skin:   # 스킨: 흰 글씨 + 검은 테두리 · 영상 창 아래 끝 바로 위 (BR-322)
        capst = editor.skin_caption(capst, B.skin["bottom"])
    layout = dict(B.lay) if B.card or B.skin else seq_layout(fmt)
    seq = editor._new_seq(label, fmt, B.items, trans=B.trans, titles=B.titles, shapes=B.shapes, tactics=B.tactics, markers=B.markers, captionStyle=capst,
                          captionsOn=bool(st["captions"].get("on", True)), layout=layout,
                          master={"volume": 1.0, "normalize": True, "lufs": float(snd.get("lufs") or -14.0)},
                          duck={"on": True, "amount": float(snd.get("duck") or -14.0)},
                          # 목소리 보정: 웅웅거림 컷 + 크기 고르게 (외치는 말·감탄이 튀지 않아 소리 크기 -14 LUFS·순간 최대를 함께 지킴)
                          voice=dict(VOICE))
    seq["tracks"] = tracks
    seq["auto"] = "msg"
    if B.skin and B.skin_rep.get("noFace"):   # 얼굴 모델을 못 써 창을 얼굴에 맞추지 못함 → 내보낸 뒤 검수 '확인할 것' (D-538)
        seq["skinNoFace"] = True
    if B.skin and B.skin_rep.get("noBall"):   # 공 찾기 모델을 못 써 시범 장면에서 공을 못 따라감 → '확인할 것' (D-539)
        seq["skinNoBall"] = True
    if B.skin and getattr(B, "cap_lift", None):   # 차기 직전 공이 말 자막 줄에 있으면 그동안 말 자막을 공 위로 (편집본 capLift · D-539)
        seq["capLift"] = B.cap_lift
    if B.card or B.skin:  # 은평 스타일·스킨 큰 제목·사실(주제) 줄·흰 상자는 편집본 끝까지 (끝에 남는 효과음·배경음악 꼬리까지 · BR-241 · BR-321)
        end = round(editor.seq_total(seq), 3)
        for x in [t for t in B.titles if t["id"] in B.fixed] + [g for g in B.shapes if g.get("plan") in ("card", "series")]:
            x["dur"] = end
    summ = summary_of(B.events, sections, total, seq_items_audio)
    seq["msg"] = {"v": 1, "style": {"label": st.get("label"), "kind": st.get("kind"), "sources": st.get("sources"), "sfx": snd.get("palette") or "variety"},
                  "intensity": intensity, "seed": seed,
                  "events": B.events, "thumb": thumbs, "summary": summ, "notes": st.get("notes") or [], "hook": hook, "topic": topic, "topicLine": topic_line, "opening": opening,
                  "bgm": [{"a": round(a, 2), "b": round(b, 2), "mood": md} for a, b, md in sections]}
    return seq, list(B.media.values()), len(visual_events(B.items, B.titles, B.shapes, total)), B.lost


# ---------- 전술 그림 (시범 장면에 발밑 원·화살표 · D-182) ----------
TACTIC_ON = {"보통": (0.5, 3), "듬뿍": (1.0, 6)}   # 양 → (본편 1분에 몇 개, 최대 개수) · 담백은 넣지 않음
TACTIC_GAP = 6.0      # 전술 그림끼리 최소 간격(타임라인 초)
TACTIC_PRE, TACTIC_POST = 0.8, 2.2   # 시범 순간(공 소리·가장 큰 움직임) 앞뒤로 이만큼 (원본 초)
TACTIC_MIN = 1.2      # 이보다 짧게 남으면 넣지 않음
TACTIC_SEEN = 0.6     # 따라간 장면(마지막으로 본 곳까지) 중 이 비율 넘게 선수를 찾아야 넣음 (자주 놓치면 원이 엉뚱한 곳에 남음)
TACTIC_MIN_H = 0.15   # 선수가 화면 높이의 이만큼은 보여야 (멀리 작은 사람·관중은 빼고)
TACTIC_MOVE = 0.08    # 듬뿍: 선수가 화면 너비의 이만큼 넘게 움직이면 움직인 길을 곡선 화살표로
TACTIC_NEAR = 0.25    # 공 차는 순간 선수 발과 공 사이가 화면의 이만큼 안이어야 그 선수 (I-182)
TACTIC_CLEAR, TACTIC_CLEAR_D = 1.5, 0.05   # 다음으로 가까운 사람은 이 배수·이만큼 넘게 멀어야 (두 아이가 공 곁에 있으면 건너뜀)
TACTIC_KICK = 0.06    # 다음 1초 안에 공이 그 선수 발에서 이만큼 넘게 멀어져야 (찬 사람 확인)
TACTIC_OWN = 4        # 몰고 가기: 다음 1초(8장면) 중 이만큼 넘게 같은 공이 그 선수 발 곁에 있으면 공을 가진 사람
TACTIC_BALL_JUMP = 0.15   # 공이 한 장면(1/8초)에 화면의 이만큼 넘게 옮겨 가면 다른 공으로 봄 (공이 둘 보이는 장면 · 못 본 장면이 있어도 두 장면 몫까지만)
TACTIC_CROWD = 4      # 공이 보이는 장면에 사람이 이보다 많으면(다섯 명 이상 · 몰려 있는 연습·경기) 건너뜀
TACTIC_CLOSE = 0.9    # 사람 상자가 화면 높이의 이만큼 넘으면 너무 가까이 찍힌 장면 (신발·다리만 · 원이 뜻이 없음)
_TRACKS = {}          # (영상, 원본 a, b, 순간) → 선수 상자들 (스타일·양마다 다시 찾지 않게)


def _framed(p):
    """원이 뜻이 있는 크기로 보이는 선수 — 너무 가까이 찍혀(화면 높이 TACTIC_CLOSE 넘게·머리가 위 가장자리에 닿음) 신발·다리만 보이는 장면은 뺌."""
    return p[3] <= TACTIC_CLOSE and p[1] > 0.01


def _feet_d(p, ball):
    return math.hypot(p[0] + p[2] / 2 - (ball[0] + ball[2] / 2), p[1] + p[3] - (ball[1] + ball[3] / 2))


def _nballs(found):
    """그 장면에서 찾은 공 수 (detect.people 'balls' · 예전 결과는 'ball' 하나)."""
    f = found or {}
    return len(f["balls"]) if "balls" in f else (1 if f.get("ball") else 0)


def _subject(found):
    """공 차는 순간 장면에서 시범하는 선수 — 확실할 때만 (아니면 None · 엉뚱한 아이에게 원을 그리느니 안 넣음, I-182).
    공이 보이면: 발이 공에 가장 가까운 사람이 TACTIC_NEAR 안이고, 다음으로 가까운 사람(작게 보이는 사람 포함)보다 뚜렷이 가까워야
    (TACTIC_CLEAR 배 · 그리고 TACTIC_CLEAR_D 넘게). 공이 안 보이면: 화면에 사람이 한 명뿐일 때만 그 사람.
    너무 작거나(멀리) 너무 가까이 찍힌(신발만) 사람은 None."""
    allp = (found or {}).get("persons") or []
    ps = [p for p in allp if p[3] >= TACTIC_MIN_H]
    if not ps:
        return None
    ball = (found or {}).get("ball")
    if not ball:
        return ps[0] if len(allp) == 1 and _framed(ps[0]) else None
    if _nballs(found) > 1:
        return None   # 공이 둘 넘게 보임 (모두 공을 가진 연습·골대 옆 공) — 누가 시범하는지 모름
    if sum(1 for p in allp if p[3] >= TACTIC_MIN_H * 0.5) > TACTIC_CROWD:
        return None   # 아이들이 여럿 몰려 있는 장면 — 상자가 겹쳐 엉뚱한 아이로 넘어가기 쉬움 (I-182)
    ds = sorted(((_feet_d(p, ball), i) for i, p in enumerate(allp) if p[3] >= TACTIC_MIN_H * 0.5), key=lambda x: x[0])
    near = min(ps, key=lambda p: _feet_d(p, ball))
    d0 = _feet_d(near, ball)
    if d0 >= TACTIC_NEAR or not _framed(near):
        return None
    rest = [d for d, i in ds if allp[i] is not near]
    if rest and rest[0] < max(d0 * TACTIC_CLEAR, d0 + TACTIC_CLEAR_D):
        return None   # 공 곁에 두 사람 — 누가 찼는지 모름
    return near


def _same_ball(seq, raw, k0, win):
    """k0 다음 win 장면 중 같은 공(한 장면에 TACTIC_BALL_JUMP 넘게 튀지 않음)과 따라간 상자가 둘 다 보이는 장면 [(k, 공, 상자)].
    공이 둘 보이는 장면에서 찾기 모델이 옆 아이의 공으로 넘어간 장면은 뺌 (못 본 장면이 있어도 두 장면 몫까지만 허용)."""
    last, lk, out = (seq[k0][1] or {}).get("ball"), k0, []
    for k in range(k0 + 1, min(len(seq), k0 + 1 + win)):
        ball, bx = (seq[k][1] or {}).get("ball"), raw[k][1]
        if not ball or bx is None:
            continue
        if math.hypot(ball[0] + ball[2] / 2 - last[0] - last[2] / 2, ball[1] + ball[3] / 2 - last[1] - last[3] / 2) > TACTIC_BALL_JUMP * min(2, k - lk):
            continue
        last, lk = ball, k
        out.append((k, ball, bx))
    return out


def _kicked(seq, raw, k0, win=None):
    """고른 선수가 공을 가진 사람인지 확인 — (차기) k0 다음 1초 안에 같은 공이 그 선수 발에서 TACTIC_KICK 넘게 멀어지거나
    (몰고 가기) 같은 공이 TACTIC_OWN 장면 넘게 그 선수 발 곁(TACTIC_NEAR 안 · 다른 사람보다 뚜렷이 가까움)에 있어야.
    seq = [(시각, 찾은 것)] · raw = [(시각, 따라간 상자 또는 None)] (같은 길이). 공이 안 보이면 False."""
    win = win or int(tactic.TRACK_FPS)
    ball0, bx0 = (seq[k0][1] or {}).get("ball"), raw[k0][1]
    if not ball0 or bx0 is None:
        return False
    if any(_nballs(seq[k][1]) > 1 for k in range(k0, min(len(seq), k0 + 1 + win))):
        return False   # 그 1초 안에 공이 둘 넘게 보이는 장면이 있음 → 누구 공인지 모름
    d0, own = _feet_d(bx0, ball0), 0
    for k, ball, bx in _same_ball(seq, raw, k0, win):
        d = _feet_d(bx, ball)
        if d - d0 >= TACTIC_KICK:
            return True
        others = sorted(_feet_d(p, ball) for p in (seq[k][1] or {}).get("persons") or [] if _feet_d(p, ball) > d + 0.01)
        if d < TACTIC_NEAR and (not others or others[0] >= max(d * TACTIC_CLEAR, d + TACTIC_CLEAR_D)):
            own += 1
            if own >= TACTIC_OWN:
                return True
    return False


def _seen_run(raw, k0, gap=2):
    """k0 장면에서 앞뒤로, 이어서 gap 장면 넘게 놓치기 전까지 선수를 본 처음·마지막 장면 번호."""
    lo = hi = k0
    for step in (-1, 1):
        miss, i = 0, k0 + step
        while 0 <= i < len(raw):
            if raw[i][1] is None:
                miss += 1
                if miss >= gap:
                    break
            else:
                miss = 0
                if step < 0:
                    lo = i
                else:
                    hi = i
            i += step
    return lo, hi


def _track_play(name, a, b, t, detect_fn=None):
    """원본 a~b 초 시범의 선수 상자들 [(원본 시각, [x, y, w, h])] (못 찾으면 []) — 공 차는 순간(t)에 공과 가장 가까운 선수를 골라
    앞뒤로 따라감 · 끝에서 놓친 장면(장면 바뀜)은 뺌. 한 번 찾으면 기억."""
    key = (name, round(a, 2), round(b, 2), round(t, 2))
    if key in _TRACKS:
        return _TRACKS[key]
    out = []
    try:
        if detect_fn is None:
            import detect
            if not detect.ensure(label=LABEL):
                return []   # 모델을 못 쓰면 전술 그림 없이 (기억하지 않음 — 다음에 다시 시도)
            detect_fn = detect.people
        frames = tactic._frames(core.VIDEOS / name, a, b, tactic.TRACK_FPS)
        seq = [(ft, detect_fn(im)) for ft, im in frames]
        k0 = min(range(len(seq)), key=lambda k: abs(seq[k][0] - t)) if seq else 0
        first = _subject(seq[k0][1]) if seq else None
        if first is not None:
            raw = tactic.follow_boxes([(ft, (r or {}).get("persons") or []) for ft, r in seq], first[0] + first[2] / 2, first[1] + first[3] / 2, start=k0)
            if (seq[k0][1] or {}).get("ball") and not _kicked(seq, raw, k0):
                raw = []   # 공을 차지도 몰고 가지도 않음 → 시범하는 사람인지 모름 (건너뜀)
            lo, hi = _seen_run(raw, k0) if raw else (0, -1)   # 공 차는 순간에서 이어 본 곳만 (두 장면 넘게 놓친 뒤에 다시 잡은 상자는 다른 사람일 수 있음)
            if raw and sum(1 for _, x in raw[lo: hi + 1] if x is not None) >= TACTIC_SEEN * (hi - lo + 1):
                out = tactic.smooth(raw[lo: hi + 1])
    except Exception as e:  # noqa: BLE001 — 전술 그림은 곁가지: 못 넣어도 MSG 는 그대로
        import studiolog
        studiolog.write(f"MSG 전술 그림을 넣지 못했어요 · {type(e).__name__}: {' '.join(str(e).split())[:200]}")
        out = []
    _TRACKS[key] = out
    return out


def seq_layout(fmt, card=False):
    """MSG 편집본 화면 배치 (쇼츠는 흐린 배경 위 1.35배 · card: 은평 스타일 파란 띠 + 켠 띠 그림·로고 · editor.ep_layout)."""
    if fmt == "shorts" and card:
        return editor.ep_layout()
    if fmt == "shorts":
        return {"mode": "blur", "bar": "#000000", "zoom": 1.35, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}
    return {"mode": "fill", "bar": "#000000", "zoom": 1.0, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}


def place_tactics(B, name, sig, moms, intensity, fmt, detect_fn=None):
    """보통·듬뿍: 시범 장면(play)에 선수를 따라가는 발밑 원 (듬뿍은 움직인 길 곡선 화살표도) — 점수 높은 시범부터 · 간격 TACTIC_GAP.
    선수를 못 찾거나 원이 화면 밖이면 그 장면은 건너뜀. 넣은 것은 사건 'tactic' 으로 (MSG 빼기 한 번에 지워짐)."""
    if intensity not in TACTIC_ON:
        return []
    per_min, most = TACTIC_ON[intensity]
    W, H = editor.frame_size(fmt)
    u = min(W, H) / tactic.BASE
    main = [it for it in B.items if it["track"] == "V1" and it.get("media", "main") == "main"]
    mins = sum(editor.i_len(it) for it in main) / 60.0
    n_max = min(most, max(1, int(mins * per_min + 0.5)))
    seq = {"format": fmt, "layout": getattr(B, "lay", None) or seq_layout(fmt, getattr(B, "card", False))}
    md = {"kind": "video", "w": B.info.get("width"), "h": B.info.get("height")}
    used, made = [], []
    cuts = sorted(float(c) for c in sig.get("cuts") or ())
    for m in sorted([m for m in moms if m["kind"] == "play"], key=lambda m: (-m["score"], m["t"])):
        if len(made) >= n_max:
            break
        tl = B.src_to_tl(m["t"])
        it = next((x for x in main if float(x["start"]) - 1e-6 <= (tl if tl is not None else -1) < editor.i_end(x)), None)
        if it is None or it.get("rev"):
            continue
        a, b = max(float(it["in"]), m["t"] - TACTIC_PRE), min(float(it["out"]), m["t"] + TACTIC_POST)
        a = max([a] + [c + 0.05 for c in cuts if c <= m["t"]])   # 원본 장면 바뀜을 넘지 않게 (다른 장면 위에 원이 남지 않게)
        b = min([b] + [c - 0.05 for c in cuts if c > m["t"]])
        if b - a < TACTIC_MIN:
            continue
        t0, t1 = editor.i_tl(it, a), editor.i_tl(it, b)
        if any(t0 < y + TACTIC_GAP and x < t1 + TACTIC_GAP for x, y in used):
            continue
        boxes = _track_play(name, a, b, m["t"], detect_fn)
        boxes = boxes[:next((i for i, (_, bx) in enumerate(boxes) if not _framed(bx)), len(boxes))]   # 선수가 카메라로 다가와 화면을 채우면(머리가 위에 닿음) 거기서 끝
        if not boxes:
            continue
        t0 = max(t0, editor.i_tl(it, boxes[0][0]))   # 선수를 처음·마지막으로 본 곳 사이만
        t1 = min(t1, editor.i_tl(it, boxes[-1][0]) + 1.0 / tactic.TRACK_FPS)   # (끝 0.25초 동안 흐려지며 사라짐)
        if t1 - t0 < TACTIC_MIN:
            continue
        pts = []
        for ts, bx in boxes:
            tl2 = editor.i_tl(it, ts)
            fx, fy = tactic.src_to_frame(it, md, seq, W, H, *tactic.box_anchor("ring", bx), tl2)
            pts.append((tl2, fx / W, fy / H))
        x0, y0 = pts[0][1], pts[0][2]
        if not all(0.04 <= x <= 0.96 and 0.1 <= y <= 0.97 for _, x, y in pts):
            continue   # 발이 화면 밖으로 나가면 원이 잘림
        l0 = tactic.src_to_frame(it, md, seq, W, H, boxes[0][1][0], 0, t0)[0]
        l1 = tactic.src_to_frame(it, md, seq, W, H, boxes[0][1][0] + boxes[0][1][2], 0, t0)[0]
        size = round(min(180.0, max(35.0, abs(l1 - l0) * 0.85 / u)), 1)
        dur = round(t1 - t0, 3)
        ring = {"id": editor._nid(), "kind": "ring", "name": tactic.NAMES["ring"], "pts": [[round(x0, 4), round(y0, 4)]], "start": round(t0, 3), "dur": dur,
                "color": tactic.DEFAULTS["ring"]["color"], "width": tactic.DEFAULTS["ring"]["width"], "size": size, "draw": 0.5,
                "follow": tactic.follow_offsets(pts, t0), "msg": True}
        ids = [ring["id"]]
        B.tactics.append(ring)
        text, why = "발밑 원", "시범 장면 · 선수를 따라가는 원"
        x1, y1 = pts[-1][1], pts[-1][2]
        if intensity == "듬뿍" and abs(x1 - x0) >= TACTIC_MOVE:   # 옆으로 움직였을 때만 (카메라 쪽으로 다가오면 길이 아래로 꺾여 뜻이 없음)
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            ln = math.hypot((x1 - x0) * W, (y1 - y0) * H) or 1.0
            nx, ny = -(y1 - y0) * H / ln, (x1 - x0) * W / ln   # 진행 방향의 옆 (화면 px 단위 벡터)
            if ny > 0:
                nx, ny = -nx, -ny   # 위로 휘게 (썸네일 arrowLayer 처럼)
            bend = 0.22 * ln
            ctrl = [round(mx + nx * bend / W, 4), round(my + ny * bend / H, 4)]
            arrow = {"id": editor._nid(), "kind": "curve", "name": tactic.NAMES["curve"], "pts": [[round(x0, 4), round(y0, 4)], ctrl, [round(x1, 4), round(y1, 4)]],
                     "start": round(t0 + 0.2, 3), "dur": round(max(0.8, dur - 0.2), 3), "color": tactic.DEFAULTS["curve"]["color"],
                     "width": tactic.DEFAULTS["curve"]["width"], "draw": round(min(1.2, max(0.4, (dur - 0.2) * 0.6)), 2), "msg": True}
            B.tactics.append(arrow)
            ids.append(arrow["id"])
            text, why = "발밑 원 + 움직인 길", "시범 장면 · 선수를 따라가는 원과 움직인 길 화살표"
        B.event("tactic", t0, text, why, src=m["t"], refs={"tactics": ids})
        used.append((t0, t1))
        made.append(ids)
    return made


# ---------- 글자 자리 정리 (안전 영역 · 말 자막 줄 · 서로 겹침) ----------
SAFE = 0.05          # 화면 가장자리 5% 안쪽(유튜브 플레이어·휴대폰에서 잘리지 않는 자리)에만 글자
MIN_TEXT = 0.8       # 화면 글자는 적어도 이만큼(초) 보여 줌 (읽을 틈)
_FONTS = {}
# 먼저 자리를 잡는 순서 (앞일수록 그대로 두고, 뒤의 것을 옮기거나 줄이거나 뺌)
LAYOUT_PRI = {"title": 0, "teaser": 0, "end": 0, "hook": 0, "montage": 1, "score": 2, "replay": 3, "freeze": 3, "situ": 4, "count": 5,
              "emphasis": 6, "fx": 7, "inner": 8}


def _font(style):
    """(PIL 글꼴 또는 None, 줄 높이 비율) — editor.build_ass 와 같은 글꼴·비율 (은평 스타일 쇼츠는 그 KFA 글꼴 · editor.ep_font_scope · BR-311)."""
    fx = editor.ep_font_now()
    f = style.get("font")
    if fx:
        key = (fx["path"], fx["index"], int(round(float(style.get("size") or 50))))
        if key not in _FONTS:
            try:
                from PIL import ImageFont
                _FONTS[key] = ImageFont.truetype(fx["path"], key[2], index=fx["index"])
            except Exception:  # noqa: BLE001 — 글꼴을 못 읽으면 글자 수로 어림
                _FONTS[key] = None
        return _FONTS[key], fx["k"]
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


def layout_titles(B, caps_tl, cap_style, total, face=None, moms=(), words=(), vital=True):
    """MSG 화면 글자 자리 정리: 안전 영역(5%) 안으로 · 말 자막과 같은 줄·같은 때는 피함 · 같은 때 겹치는 글자는 위아래로 옮기고,
    자리가 없으면 겹치지 않게 늦게 띄우거나 뺌 (점수판·상황 자막처럼 앞 순서인 것은 그대로) · 적어도 MIN_TEXT 초.
    face: 주인공 얼굴 자리 [x0, y0, x1, y1] (0~1 · 롱폼만) — 되도록 얼굴을 안 가리는 자리로 (판정: 티저 글자 상자가 이마·눈을 가림 ·
    얼굴을 피할 자리가 없으면 가리더라도 띄움).
    moms: 재미 순간 — 자리가 없어 글자가 다 빠진 사건이 그 순간을 혼자 보여 주던 것이면 효과음은 남김 (소리만 · D-262 · 듬뿍만 넘겨 줌 ·
    한 순간의 사건이 함께 다 빠졌으면 그중 하나(효과음 차례가 높은 것)만 · 말이 쉬는 틈에서만(words · _accent_slot) · D-269).
    vital=False(보통): 꼭 넣을 뜻 소리(성공·웃음 뒤 농담·다음 포인트)가 붙은 사건만 소리로 남김 (D-375).
    → 뺀 글자 id 집합."""
    W, H = (1080, 1920) if B.fmt == "shorts" else (1920, 1080)
    fpx = None
    if face and B.fmt == "long":
        fx0, fy0, fx1, fy1 = face
        fpx = [fx0 * W, fy0 * H, fx1 * W, fy1 * H]
    sx0, sy0, sx1, sy1 = SAFE * W, SAFE * H, (1 - SAFE) * W, (1 - SAFE) * H
    cboxes = [(c["start"], c["end"], text_box(c["text"], cap_style, W, H)) for c in caps_tl or ()]
    band = (min(b[1] for _, _, b in cboxes), max(b[3] for _, _, b in cboxes)) if cboxes else None
    lifted = []   # 스킨: 공 위로 올린 말 자막 (그 시간 그 자리도 피함 · editor.skin_cap_lift · D-539)
    if getattr(B, "skin", None) and getattr(B, "cap_lift", None):
        lq = {"format": "shorts", "layout": getattr(B, "lay", None) or {}, "items": B.items, "capLift": B.cap_lift}
        for c in caps_tl or ():
            ly = editor.skin_cap_lift(lq, c["start"], c["end"])
            if ly is not None:
                lifted.append((c["start"], c["end"], text_box(c["text"], dict(cap_style, y=ly), W, H)))
    kind_of = {}
    for e in B.events:
        for tid in (e.get("refs") or {}).get("titles") or []:
            kind_of.setdefault(tid, e["kind"])
    placed, gone = [], set()
    # 은평 스타일 쇼츠(BR-242): 위 띠의 흰 제목 상자·큰 제목·사실 줄은 그 자리 그대로 · 다른 글자는 그 둘레를 피함 (화면 글자 2개 셈에는 안 넣음)
    fixed = getattr(B, "fixed", None) or set()
    fboxes = [text_box(t["text"], t["style"], W, H) for t in B.titles if t["id"] in fixed]
    fboxes += [[s["x"] * W, s["y"] * H, (s["x"] + s["w"]) * W, (s["y"] + s["h"]) * H] for s in B.shapes if s.get("plan") in ("card", "series")]
    lr = editor.ep_logo_rect(getattr(B, "lay", None), W, H) if getattr(B, "card", False) else None
    if lr:   # 아래 띠 로고 자리에는 어떤 글자도 (BR-283 · 밀어낸 강조·상황·속마음 글자도 0.86~0.95 로 내려가 로고를 덮지 않게)
        fboxes.append(lr)
    sk_faces = getattr(B, "skin_faces", None) if getattr(B, "skin", None) else None
    sk_seq = {"format": "shorts", "layout": getattr(B, "lay", None) or {}, "info": getattr(B, "info", None) or {}}
    if getattr(B, "skin", None):   # 풋살사관학교 스킨(BR-323): 위 띠(0 ~ 영상 창 위 끝)·아래 띠(창 아래 끝 ~ 화면 끝)에는 고정 글자(큰 제목·주제 줄) 말고 어떤 글자도
        fboxes += editor.skin_bands(B.lay, W, H)
    for t in sorted(B.titles, key=lambda t: (LAYOUT_PRI.get(kind_of.get(t["id"]), 9), t["start"])):
        if t["id"] in fixed:
            continue
        st = t["style"]
        if total - t["start"] < MIN_TEXT:  # 영상 끝에 걸려 읽을 틈 없이 스치는 글자는 뺌 (판정: 0.3초 글자)
            gone.add(t["id"])
            continue
        t["dur"] = round(max(t["dur"], MIN_TEXT), 3)
        box = text_box(t["text"], st, W, H)
        if box[2] - box[0] > sx1 - sx0:  # 안전 영역보다 넓은 글자(쇼츠의 긴 질문)는 글자 크기를 줄여 맞춤
            st["size"] = max(24, int(float(st.get("size") or 50) * (sx1 - sx0) / (box[2] - box[0]) * 0.97))
            box = text_box(t["text"], st, W, H)
        dx = sx0 - box[0] if box[0] < sx0 else sx1 - box[2] if box[2] > sx1 else 0.0
        if dx:
            st["x"] = round(float(st.get("x") if st.get("x") is not None else 0.5) + dx / W, 4)
        y0 = float(st["y"])
        on_caps = band is not None and any(min(b, t["start"] + t["dur"]) - max(a, t["start"]) > 0.02 for a, b, _ in cboxes)

        a, b = t["start"], t["start"] + t["dur"]
        # 스킨 쇼츠(D-538): 창 안 얼굴이 기본 배치보다 1.84배 커 가운데 높이(0.48)의 반응·숫자 글자가 눈·코를 덮었음 (검토 edit r1) →
        # 얼굴 따라가기가 본 그 장면 얼굴(화면 좌표 · 글자 처음·가운데·끝)을 피함 (기본·은평 배치는 그대로)
        fsk = []
        if sk_faces and kind_of.get(t["id"]) not in FACE_FREE:
            for tt in (a + 0.2, (a + b) / 2, b - 0.2):
                fsk += [[x * W, y * H, (x + w) * W, (y + h) * H] for x, y, w, h in editor.skin_screen_faces(B.items, tt, sk_seq, sk_faces, W, H) or ()]
            if kind_of.get(t["id"]) == "score":   # 점수판(최종 결과도)은 소유자 '틀린 개수' 자리(창 맨 위 줄 오른쪽)에 — 얼굴 상자 위 1/4(이마·머리카락)은 덮어도 됨
                fsk = [[f[0], f[1] + SKIN_SCORE_BROW * (f[3] - f[1]), f[2], f[3]] for f in fsk]   # (예전: 긴 '최종 결과'가 이마에 걸려 말 자막 바로 위로 내려감 · 검토 look r2 · D-539)
        avoid_face = (fpx is not None or bool(fsk)) and kind_of.get(t["id"]) not in FACE_FREE

        def free(y, a, b, face_ok=False, x=None):
            bx = text_box(t["text"], dict(st, y=y) if x is None else dict(st, y=y, x=x), W, H)
            if bx[1] < sy0 - 0.5 or bx[3] > sy1 + 0.5:
                return False
            if avoid_face and not face_ok and ((fpx is not None and _overlap(bx, fpx) > 0.12) or any(_overlap(bx, f) > 0.12 for f in fsk)):
                return False
            if any(_overlap(bx, fb) > 0 for fb in fboxes):
                return False
            if on_caps and min(bx[3], band[1]) - max(bx[1], band[0]) > 0:
                return False
            if any(min(b, l_b) - max(a, l_a) > 0.02 and _overlap(bx, lb) > 0 for l_a, l_b, lb in lifted):
                return False
            same = [(p_a, p_b, pb) for p_a, p_b, pb in placed if min(b, p_b) - max(a, p_a) > 1e-3]
            if len(same) >= 2 and any(sum(1 for q_a, q_b, _ in same if q_a <= m < q_b) >= 2 for m in [a] + [x for q in same for x in (q[0], q[1]) if a <= x < b]):
                return False  # 화면 글자는 한꺼번에 2개까지
            return not any(_overlap(bx, pb) > 0.05 for p_a, p_b, pb in same)
        ys = [y0] + [round(y0 + d * sg, 3) for d in (0.07, 0.14, 0.21, 0.28, 0.35, 0.42, 0.5) for sg in ((-1, 1) if y0 >= 0.5 else (1, -1))]
        y = next((yy for yy in ys if 0.0 < yy <= 1.0 and free(yy, a, b)), None)
        if y is None and fsk:   # 스킨: 위아래로 피할 곳이 없으면(창 높이를 거의 채운 얼굴) 얼굴 옆으로 · 창 맨 위 줄·말 자막 바로 위 줄 · 반응 글자는 같은 뜻 짧은 글 ·
            y = _skin_dodge(B, t, st, fsk, ys, free, a, b, kind_of.get(t["id"]), band, W, H, (sx0, sx1))   # 그래도 안 되면 조금 줄여서 (D-538 · D-539)
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
    for e in B.events:   # 한 덩어리 사건(정리 카드 두 장)은 글자 하나라도 빠지면 모두 (D-269)
        ids = (e.get("refs") or {}).get("titles") or []
        if e.get("unit") and any(x in gone for x in ids):
            gone |= set(ids)
    if gone:
        B.titles = [t for t in B.titles if t["id"] not in gone]
        keep, lost = [], []
        for e in B.events:
            r = e.get("refs") or {}
            if r.get("titles"):
                r["titles"] = [x for x in r["titles"] if x not in gone]
                if not r["titles"] and e["kind"] in TEXT_KINDS:  # 글자가 다 빠진 사건은 그 효과음도 뺌 (아래: 순간을 혼자 보여 주던 것은 소리만)
                    lost.append(e)
                    continue
            keep.append(e)
        # 남은 사건으로 셈 — 한 순간의 사건 둘이 함께 빠지면 서로를 '아직 있음'으로 보고 둘 다 소리까지 빠졌음 (검토 · D-269)
        saved = set()
        for m in [m for m in moms or () if m["kind"] in MOMENT_KINDS]:
            mine = [e for e in lost if id(e) not in saved and _covers(e, m) and any(k < len(B.sfx) and B.sfx[k] and (vital or _meta(B.sfx[k]).get("must"))
                                                                                  for k in (e.get("refs") or {}).get("sfx") or [])]
            if not mine or any(_covers(o, m) and o["kind"] not in ("bgm", "chapters") and not o.get("accent") for o in keep):
                continue
            e = max(mine, key=lambda e: max(SFX_PRI.get(B.sfx[k][3], 4) for k in e["refs"]["sfx"] if k < len(B.sfx) and B.sfx[k]))
            snd = [k for k in e["refs"]["sfx"] if k < len(B.sfx) and B.sfx[k]][:1]
            slot = _accent_slot(B, words, m, float(e["src"]))
            if slot is None:
                continue
            for k in e["refs"]["sfx"]:
                if k not in snd and k < len(B.sfx):
                    B.sfx[k] = None
            x = B.sfx[snd[0]]
            B.sfx[snd[0]] = (slot,) + tuple(x[1:4]) + (dict(_meta(x), title=None, win=_accent_win(B, m, slot),
                                                            why=f"{_meta(x).get('why') or ''} · 글자 자리가 없어 소리만"),)
            e.update(kind="sfx", t=round(slot, 2), text=B.sfx[snd[0]][1], why=f"{e.get('why') or ''} · 소리만 (글자 자리 없음)", refs={"sfx": snd},
                     accent=True, moment=e.get("moment") or {"kind": m["kind"]})
            saved.add(id(e))
        for e in lost:
            if id(e) not in saved:
                if _keep_sound_only(B, e, "글자 자리 없음"):   # 꼭 넣을 뜻 소리는 소리만 (D-375)
                    saved.add(id(e))
                    continue
                for k in (e.get("refs") or {}).get("sfx") or []:
                    if k < len(B.sfx):
                        B.sfx[k] = None
        ok = {id(e) for e in keep} | saved
        B.events = [e for e in B.events if id(e) in ok]
    return gone


SKIN_SCORE_BROW = 0.25  # 스킨 점수판이 덮어도 되는 얼굴 상자 위쪽 몫 (이마·머리카락 · 눈은 아래 3/4 · D-539)
SKIN_TEXT_MIN = 60      # 스킨 MSG 글자를 얼굴을 피하려고 줄일 때 한도 (속마음·상황 글자 70 → 49 로 줄어 휴대폰에서 안 읽혔음 · 검토 edit r2 · D-539)
SKIN_SHRINK = (0.85, 0.7, 0.6)   # 얼굴 옆에도 안 들어갈 때 줄이는 배율 (차례대로 · SKIN_TEXT_MIN 밑은 안 씀)


def _skin_dodge(B, t, st, fsk, ys, free, a, b, kind, band, W, H, safe):
    """스킨 MSG 글자가 얼굴(fsk · 화면 화소)을 피할 자리 (layout_titles · 위아래로 피할 곳이 없을 때 · D-538 · D-539) — 차례대로:
    ① 같은 크기로 얼굴 옆(왼·오른) ② 영상 창 맨 위 줄(상황 자막 줄)·말 자막 바로 위 줄 (원래 가로 자리 · 얼굴 옆) ③ 반응 글자(fx)는 같은 뜻 글 가운데 더 짧은 것
    (FX_TEXTS 같은 묶음 · '나이스!!' → '굿!' · '깔끔하게 골!' → '골인!' · 사건 글도 바꿈) ④ SKIN_SHRINK 배율로 줄여서(SKIN_TEXT_MIN 까지 · 반응 글자는 0.6 배까지)
    → 글 아래 끝 y (st 의 x·size·글은 그 자리로) | None (모두 안 되면 처음 글·크기 그대로 · 부른 쪽이 얼굴 위라도 띄움 · I-535)."""
    size0, text0, x0 = float(st.get("size") or 50), t["text"], st.get("x")
    win = getattr(B, "skin", None) or {}
    sx0, sx1 = safe

    def rows():   # 창 맨 위 줄 · 말 자막 바로 위 줄 (글 아래 끝 y)
        bh = text_box(t["text"], dict(st, y=0.5), W, H)
        out = []
        if win.get("top") is not None:
            out.append(math.ceil((float(win["top"]) + editor.TXT_GAP + (0.5 * H - bh[1]) / H) * 1000 - 1e-6) / 1000)
        lo = band[0] if band else (float(win["bottom"]) * H if win.get("bottom") is not None else None)
        if lo is not None:
            out.append(math.floor(((lo - editor.TXT_GAP * H) - (bh[3] - 0.5 * H)) / H * 1000 + 1e-6) / 1000)
        return out

    def sides():
        bk = text_box(t["text"], st, W, H)
        bw0, c0 = bk[2] - bk[0], (bk[0] + bk[2]) / 2
        return sorted({round(c / W, 4) for f in fsk for c in (f[0] - editor.TXT_GAP * W - bw0 / 2, f[2] + editor.TXT_GAP * W + bw0 / 2)
                       if sx0 <= c - bw0 / 2 and c + bw0 / 2 <= sx1}, key=lambda c: abs(c * W - c0))

    def at(first):   # 이 글·크기로: (first 면 원래 가로 자리 위아래는 이미 봄) 원래 가로 자리 → 창 맨 위·말 자막 위 줄 → 얼굴 옆
        st["x"] = x0
        cand = ([] if first else list(ys)) + rows()
        y = next((yy for yy in cand if 0.0 < yy <= 1.0 and free(yy, a, b)), None)
        if y is not None:
            return y
        for xx in sides():
            y = next((yy for yy in list(ys) + rows() if 0.0 < yy <= 1.0 and free(yy, a, b, x=xx)), None)
            if y is not None:
                st["x"] = xx
                return y
        st["x"] = x0
        return None
    y = at(True)
    if y is not None:
        return y
    words = [text0]
    if kind == "fx":   # 반응 글자: 같은 뜻 글 가운데 더 짧은 것 (짧은 것부터)
        font = st.get("font")
        same = lambda u, v: str(u).replace("…", "...") == str(v).replace("…", "...")  # noqa: E731 — ('아깝다…' = 글꼴에 맞춘 '아깝다...')
        grp = next((g for g in FX_TEXTS.values() if any(same(glyph_safe(w, font), text0) for w in g)), ())
        w0 = text_box(text0, st, W, H)
        alts = sorted({glyph_safe(w, font) for w in grp if not same(glyph_safe(w, font), text0)}, key=lambda w: text_box(w, st, W, H)[2] - text_box(w, st, W, H)[0])
        for w in [w for w in alts if text_box(w, st, W, H)[2] - text_box(w, st, W, H)[0] < w0[2] - w0[0]]:
            t["text"] = w
            y = at(False)
            if y is not None:
                _retext(B, t["id"], text0, w)
                return y
            words.append(w)
        t["text"] = text0
    floor = size0 * min(SKIN_SHRINK) if kind == "fx" else SKIN_TEXT_MIN
    for k in SKIN_SHRINK:
        z = int(size0 * k)
        if z < floor - 1e-6:
            break
        st["size"] = z
        for w in words:
            t["text"] = w
            y = at(False)
            if y is not None:
                if w != text0:
                    _retext(B, t["id"], text0, w)
                return y
    st["size"], st["x"], t["text"] = size0, x0, text0
    return None


def _retext(B, tid, old, new):
    """글자(tid)를 다른 글로 바꾼 것을 그 사건 글에도 (MSG 목록이 화면 글자와 같게)."""
    for e in B.events:
        if tid in ((e.get("refs") or {}).get("titles") or []) and (e.get("text") == old or str(e.get("text") or "").replace("…", "...") == old.replace("…", "...")):
            e["text"] = new


def _join_recap(B, picked):
    """정리 카드 두 장을 MSG 사건 하나로 (글자 모두 refs · 목록 [빼기] 한 번에 정리 전체가 빠짐) · 한 장이라도 못 만들었으면(말 자막과 같음 ·
    편집본에서 잘린 곳) 모두 뺌 — 한 덩어리 (D-269). event['unit'] = True: 글자 자리 정리(layout_titles)도 하나라도 빠지면 모두 뺌."""
    want = {}
    for c in picked:
        if c.get("look") == "recap":
            want[c["mid"]] = want.get(c["mid"], 0) + 1
    for mid, n in want.items():
        evs = sorted([e for e in B.events if e.get("_recap") == mid], key=lambda e: e["t"])
        if len(evs) < n:
            _drop(B, event_ids=[e["id"] for e in evs])
            continue
        if len(evs) > 1:
            head = evs[0]
            for e in evs[1:]:
                head["refs"]["titles"] = list(head["refs"].get("titles") or []) + list(e["refs"].get("titles") or [])
                if e["refs"].get("sfx"):
                    for k in e["refs"]["sfx"]:
                        if k < len(B.sfx):
                            B.sfx[k] = None
            head["text"] = "\n".join(e["text"] for e in evs)
            head["unit"] = True
            gone = {e["id"] for e in evs[1:]}
            B.events = [e for e in B.events if e["id"] not in gone]
    for e in B.events:
        e.pop("_recap", None)


def _covers(e, m):
    """사건 e(원본 시각 src)가 재미 순간 m 을 보여 주는지 (앞뒤 1초 · govern 의 covers 와 같음)."""
    return e.get("src") is not None and float(m["a"]) - 1.0 <= float(e["src"]) <= float(m["b"]) + 1.0


def _small_title(st, topic, compact=False, big=False):
    """본편 첫 장면 위 작은 제목 (왼쪽 위) — 인트로 부분의 모양(titleLook · 섞은 스타일은 인트로를 가져온 스타일) (판정: 담백에서 스타일끼리
    말 자막 글꼴만 달라 고르기 어려움): box(담백 레슨형) → 색 상자 + 흰 글씨 · film(다큐) → 흰 도현 글씨 · stamp(예능·쇼츠·배운 스타일) →
    강조 색 검은고딕 글씨 + 굵은 테두리. compact: 질문 글자와 함께 뜰 때 '오늘의 주제' 줄을 빼서 화면 글자를 2개까지.
    → [('shape', None, (x, y, w, h, 색)) | ('title', 글, 모양)]"""
    it = st["intro"]
    look, color = it.get("titleLook") or "stamp", it.get("titleColor") or "#FFE14D"
    T = editor.TITLE_STYLE
    out = []
    head = dict(T, weight="Bold", size=44, fill="#FFFFFF", strokeW=0, x=0.065, y=0.125, align="left", effect="fade")  # (36 은 휴대폰에서 안 읽힘 · 판정)
    main = dict(T, weight="Black", size=72 if not compact else 64, fill="#FFFFFF", strokeW=0, x=0.065, y=0.235 if not compact else 0.19, align="left",
                effect="fade")
    if big and look == "film":  # 다큐 첫 질문 장면 위 영화 같은 큰 제목 (가운데 · 말 자막 위)
        head.update(font="Do Hyeon", stroke="#111111", strokeW=5, x=0.5, y=0.42, align="center", size=46)
        main.update(font="Do Hyeon", size=124, fill=color, stroke="#111111", strokeW=8, x=0.5, y=0.62, align="center")
    elif big:  # 티저 뒤 본편 첫 장면 위 큰 예능 제목 (가운데 · 찍히듯)
        head.update(stroke="#111111", strokeW=7, x=0.5, y=0.36, align="center", size=50, effect="pop")
        main.update(font="Black Han Sans", size=132, fill=color, stroke="#111111", strokeW=11, x=0.5, y=0.56, align="center", effect="stamp")
    elif look == "box":
        out.append(("shape", None, (0.04, 0.06, 0.34, 0.2, color)))
    elif look == "film":
        head.update(font="Do Hyeon", stroke="#111111", strokeW=4)
        main.update(font="Do Hyeon", size=main["size"] + 8, fill=color, stroke="#111111", strokeW=6)
    else:
        head.update(stroke="#111111", strokeW=6)
        main.update(font="Black Han Sans", size=main["size"] + 10, fill=color, stroke="#111111", strokeW=9, effect="stamp")
    if not compact:
        out.append(("title", "오늘의 주제", head))
    out.append(("title", topic, main))
    return out


def _settle_titles(B):
    """글자 등장 다듬기 (판정: 0.0초 첫 프레임에 글자 없는 빈 초록 제목 상자 · 첫 0.25초에 작게 떴다 커지는 티저 글자):
    · 영상 맨 처음(0.05초 안)에 뜨는 글자는 등장 효과 없이 첫 프레임부터 다 보이게
    · 상자(도형) 위에 얹은 글자는 상자와 같은 때 나타나게 — 흐려지며·밀려 나타나는 효과(fade·slide)는 상자만 먼저 보이게 하므로 없앰
      (크게 찍히는 stamp·pop 은 첫 프레임부터 글자가 보여 그대로)."""
    by_shape = {s["id"]: s for s in B.shapes}
    by_title = {t["id"]: t for t in B.titles}
    for e in B.events:
        r = e.get("refs") or {}
        shs = [by_shape[i] for i in r.get("shapes") or () if i in by_shape]
        if not shs:
            continue
        s0 = min(s["start"] for s in shs)
        for tid in r.get("titles") or ():
            t = by_title.get(tid)
            if t is None or not (s0 - 0.01 <= t["start"] <= s0 + 0.5):
                continue
            end = t["start"] + t["dur"]
            t["start"], t["dur"] = round(s0, 3), round(end - s0, 3)
            if t["style"].get("effect") in ("fade", "slide"):
                t["style"]["effect"] = "none"
    for t in B.titles:
        if t["start"] < 0.05:
            t["style"]["effect"] = "none"


def _is_term(t):
    """용어 사전(풋살 용어·기본 레슨 기술)에 있는 말인지 (띄어쓰기 무시)."""
    import hooks
    k = hooks._compact(t)
    return bool(k) and any(hooks._compact(x) == k for x in editor._point_terms())


def _shorts_openings(vt, r):
    """MSG 쇼츠 첫 2.5초 큰 줄 후보 [(글 | None=예전 훅, 주제 줄이 꼭 있어야 하는지)] — 앞에서부터 씀 (BR-120 · 그 위 주제 줄은
    editor.topic_line). 다 안 되면 예전 훅(첫 질문)만. 가편집 쇼츠의 위 큰 제목(추천 구간 hook · BR-093) 중 용어 사전 말로 만든 것
    ('트래핑 꿀팁' · '디딤발!') · 그 제목이 영상 주제와 같은 말뿐이면('슈팅 챌린지') 이 쇼츠의 요점 말('디딤발 방향') → 예전 훅 —
    둘 다 무엇에 대한 쇼츠인지는 주제 줄이 말하므로 주제 줄을 실제로 놓았을 때만 · 못 놓으면 주제를 말하는 그 제목 그대로 (검토:
    위쪽 말 자막·확대한 첫 장면에서 주제 낱말이 빠진 큰 줄만 남음). '패스 / 두 번째 동작'처럼 영상 주제 + 순서면 요점 말 → 영상 주제
    ('패스 앤 무브 드릴' · 순서는 주제 줄 '동작 2') — 순서 말뿐인 줄('두 번째 동작')은 같은 때 말 자막('두번째 동작은 …')을 되풀이하고
    무엇의 동작인지 말하지 않아 안 씀 (검토). 문장·질문 제목은 받아쓰기 헛말일 수 있어 큰 줄로 안 씀 (요점 말이 있으면 그것)."""
    if not r:
        return []
    h = str(r.get("hook") or "").strip()
    p = (r.get("point") or {}).get("phrase")
    pt = [(p, True)] if p and not (vt and editor._shares_word(p, vt)) else []
    if "\n" in h:
        first, second = (x.strip() for x in h.split("\n", 1))
        if editor.ORD_ANY.search(second) and _is_term(first):
            if not (vt and editor._shares_word(first, vt)):
                return [(h, False)]
            return pt + ([(vt, False)] if len(re.sub(r"\s", "", vt)) <= editor.TOPIC_MAX and "\n" not in vt else [])
        return pt
    m = re.fullmatch(r"(.+?)(?: 꿀팁| 챌린지|!)", h)
    if m and _is_term(m.group(1)):
        if vt and editor._shares_word(m.group(1), vt):  # 영상 주제와 같은 말뿐 ('슈팅 챌린지' · '슈팅 꿀팁')
            return pt + [(None, True), (h, False)]
        return [(h, False)]
    return pt


def _shorts_main(vt, r):
    """MSG 쇼츠 첫 2.5초 큰 줄의 첫 후보 (_shorts_openings) → 글 또는 None(예전 훅)."""
    c = _shorts_openings(vt, r)
    return c[0][0] if c else None


def _ord_num(text):
    """글 속 순서 번호 ('두 번째 동작' · '동작 ②' · '동작 2' · '포인트 3' → 2·2·2·3) — 없으면 None."""
    t = str(text or "")
    m = re.search(r"(첫|두|세|네|다섯|여섯|일곱)\s?번째", t)
    if m:
        return ORD[m[1]]
    m = re.search(r"[①-⑦]", t)
    if m:
        return CIRCLED.index(m[0]) + 1
    m = re.search(r"(?:포인트|동작|기술|방법|팁|단계|도전|슛)\s?(\d)(?!\d)", t)
    return int(m[1]) if m else None


def _shown_cap_dup(B, text, caps, t1=SHORTS_HOOK_SEC):
    """text 가 타임라인 [0, t1] 동안 보이는 말 자막을 되풀이하는지 (_cap_dup · 본편 클립으로 원본 시각을 찾아 봄)."""
    for it in B.main:
        a, b = max(0.0, float(it["start"])), min(t1, editor.i_end(it))
        if b <= a:
            continue
        sp = editor.i_sp(it)
        if _cap_dup(text, float(it["in"]) + (a - float(it["start"])) * sp, float(it["in"]) + (b - float(it["start"])) * sp, caps):
            return True
    return False


def _shorts_intro(B, hook, short_r, vt, topic_on, looks, st, cap_y, fmt, caps):
    """MSG 쇼츠 첫 2.5초 (BR-120) → (큰 줄 title, 주제 줄 title|None, 큰 줄을 이 쇼츠 포인트로 바꿨으면 그 글 · 아니면 '').
    큰 줄 후보(_shorts_openings)를 차례로: 같은 때 말 자막을 되풀이하는 줄은 건너뛰고, 주제 낱말을 뺀 줄('디딤발 방향')은 그 위 주제 줄을
    실제로 놓았을 때만 (못 놓으면 다음 후보 — 검토: 위쪽 말 자막·확대한 첫 장면에서 주제 없는 '두 번째 동작'만 남음) · 끝은 예전 훅(첫 질문)."""
    hlook = dict(looks["emphasis"], y=0.22, size=96, fill="#FFFFFF", effect="pop")
    point = (short_r or {}).get("point")
    caps_on = st["captions"].get("on", True)
    for main, need in (_shorts_openings(vt, short_r) if topic_on else []) + [(None, False)]:
        if main and caps_on and _shown_cap_dup(B, main, caps):
            continue
        if main:
            htxt = main
        else:
            lim = 14   # 쇼츠 한 줄 14자 — 문장 부호·글꼴에 없어 바꾼 '...'까지 셈 (판정 U6: '퍼스트 터치 레슨, 이것만 알면...' 16자)
            htxt = _short_text(hook, lim)
            head = re.split(r"[,.?!]", str(hook))[0].strip()
            if htxt != hook and 4 <= len(head.replace(" ", "")) <= lim and head != str(hook).strip():
                htxt = head   # 잘라야 하면 앞 구절에서 ('퍼스트 터치 레슨, 이것만…' 대신 '퍼스트 터치 레슨')
            while lim > 6 and len(re.sub(r"\s", "", glyph_safe(htxt, hlook.get("font")))) > 14:
                lim -= 1
                htxt = _short_text(hook, lim)
        if B.card:  # 은평 스타일: 흰 상자 안 한 줄 큰 제목 · 처음부터 끝까지 (BR-241)
            hk = B.title(" ".join(htxt.split()), 0.0, B.pos, editor.ep_title_style(" ".join(htxt.split())))
        elif B.skin:  # 풋살사관학교 스킨: 흰 둥근 상자 안 주황 큰 제목 · 처음부터 끝까지 · 한 줄로 80 밑으로 줄어야 하면 앞 구절·포인트 말·영상 주제로 (BR-321 · D-531) · 고른 글이 그래도 72 밑이면 두 줄 (D-540)
            raw = htxt if main else str(hook or htxt)
            htxt = editor.skin_headline(htxt, [point.get("phrase") if isinstance(point, dict) else None, vt])
            hk = B.title(htxt, 0.0, B.pos, editor.skin_title_style(htxt))
        else:
            hk = B.title(htxt, 0.0, SHORTS_HOOK_SEC, hlook)
        kick = editor.topic_line(vt, point, htxt) if topic_on else ""
        if B.skin and topic_on:   # 스킨 주제 줄: 흰 상자보다 넓지 않은 글 · 묻는 말 훅을 다른 글로 바꿨으면 그 뒤 물음 구절 먼저 (editor.skin_topic_fit · D-539)
            alts = [editor.skin_hook_tail(raw, htxt)] + editor.topic_lines(vt, point, htxt)
            alts = [a for a in alts if a and len(re.sub(r"\s", "", a)) <= editor.TOPIC_MAX + 2]
            kick = alts[0] if alts else ""
            kt = _card_fact(B, kick, hk, alts) if kick else None
        else:
            kt = (_card_fact(B, kick, hk) if B.card or B.skin else _topic_kicker(B, kick, hk, st, cap_y, fmt)) if kick else None
        if kt or not need:
            return hk, kt, (hk["text"] if main else "")
        B.titles.remove(hk)
    return hk, kt, ""   # (끝 후보는 늘 위에서 돌아감)


def _drop_said_order(B, hk, kt):
    """첫머리(큰 줄·주제 줄)가 말한 순서 번호와 같은 번호의 상황 자막이 첫머리 바로 뒤(SHORTS_HOOK_SEC + 1.5초 안)에 뜨면 뺌 → 뺀 사건 수."""
    on = _ord_num(hk["text"]) or (_ord_num(kt["text"]) if kt else None)
    dup = [e["id"] for e in B.events if on and e["kind"] == "situ" and float(e["t"]) <= SHORTS_HOOK_SEC + 1.5 and _ord_num(e.get("text")) == on]
    if dup:
        # 소리도 함께 뺌 — 첫머리(훅 짠)가 이미 그 포인트를 알림 · 남기면 훅 길이로 정해진 2.6초에 같은 포인트 휙이 또 남 (쇼츠 25/126 · D-379)
        _drop(B, event_ids=dup, keep_sound=False)
    return len(dup)


def _skin_split_refs(B):
    """스킨 세로 원본 위아래 맞춤이 나눈 클립(editor._skin_split · report split {예전 id: [새 id]} · 나눈 조각을 또 나누면 이어서)을 사건 refs·본편 목록·
    나가는 전환이 이어받음 — MSG 목록 [빼기]가 나뉜 뒤 조각까지 함께 빼고, 나가는 전환은 마지막 조각에서 (_split_main 과 같음 · D-539)."""
    sp = B.skin_rep.pop("split", None) if isinstance(getattr(B, "skin_rep", None), dict) else None
    if not sp:
        return

    def all_of(x):
        out = [x]
        for y in sp.get(x, []):
            out += all_of(y)
        return out
    for e in B.events:
        r = e.get("refs") or {}
        if r.get("items"):
            r["items"] = [y for x in r["items"] for y in all_of(x)]
    for tr in B.trans:
        if tr.get("a") in sp:
            tr["a"] = all_of(tr["a"])[-1]
    byid = {x["id"]: x for x in B.items}
    main = []
    for x in B.main:
        main += [byid.get(y, x) for y in all_of(x["id"]) if y == x["id"] or (byid.get(y) or {}).get("track") == "V1"]
    B.main[:] = main


def _skin_row(top, look):
    """스킨 영상 창 맨 위 줄 — 글 상자(text_box · 상자 여백 포함) 위 끝이 영상 창 위 끝(top) + TXT_GAP 이 되는 글 아래 끝 y (셋째 자리 올림 · BR-323)."""
    W, H = 1080, 1920
    b = text_box("가나다", dict(look, y=0.5), W, H)
    return math.ceil((float(top) + editor.TXT_GAP + 0.5 - b[1] / H) * 1000 - 1e-6) / 1000


TOPIC_KICK = (64, 56)   # MSG 쇼츠 주제 줄 글자 크기 (큰 것부터 · 큰 줄 96 · 휴대폰에서 읽히는 크기)
EP_ROW = 0.27   # 은평 스타일 쇼츠: 상황 자막·'다시 보기 ▶'·점수판 줄 (글 아래 끝 · 사실 줄 0.195 아래 · 영상 창 위 끝 0.28 위 · BR-242)


def _card_fact(B, text, hk, alts=None):
    """은평 스타일 사실 줄 (BR-241): 흰 상자 큰 제목(hk) 아래 작은 흰 글씨 한 줄 · 큰 제목과 같은 때(끝까지) · 주제 줄(plan topic) —
    길면(editor.TOPIC_MAX + 2 넘음) None (큰 제목만). 스킨(B.skin)이면 큰 흰 주제 줄 (editor.skin_topic_style · BR-321) ·
    alts(스킨 · 차례대로 후보): 흰 상자보다 넓지 않은 것 (editor.skin_topic_fit · D-539)."""
    if len(re.sub(r"\s", "", text)) > editor.TOPIC_MAX + 2:
        return None
    if getattr(B, "skin", None):
        text = " ".join(text.split())
    if getattr(B, "skin", None) and alts:
        text, look = editor.skin_topic_fit(alts, hk, B.skin["top"])
        if not text:
            return None
    else:
        look = editor.skin_topic_style(text, B.skin["top"], hk["style"].get("size")) if getattr(B, "skin", None) else dict(editor.EP_FACT)   # (스킨: 큰 제목보다 크지 않게 · D-531)
    kt = B.title(text, 0.0, float(hk["dur"]), look)
    kt["plan"], kt["under"] = "topic", hk["id"]
    return kt


def _topic_kicker(B, text, hk, st, cap_y, fmt="shorts"):
    """MSG 쇼츠 주제 줄 title (BR-120) — 큰 줄(hk) 바로 위 (말 자막이 위쪽이면 큰 줄 바로 아래부터) · 스타일 강조 색·글꼴
    (그 글꼴에 없는 글자('①' 등)가 있으면 기본 글꼴) · 한 줄 · 위쪽 안전 영역(editor.TOPIC_TOP) 안 · 말 자막 띠·영상(흐린 배경 위 본 화면)과
    안 겹침. 위에 자리가 모자라면 큰 줄을 조금 내림(영상 위까지) · 그래도 없으면 None (큰 줄만)."""
    W, H = (1080, 1920) if fmt == "shorts" else (1920, 1080)
    hl = hk["style"]
    c = st["captions"]
    base = dict(hl, fill="#FFFFFF" if hl.get("bgOn") else (c.get("emphColor") or "#FFE14D"))
    if not hl.get("bgOn"):
        base["strokeW"] = min(int(hl.get("strokeW") or 8), 8)
    if "·" in text and "·" in (_NO_GLYPH.get(base.get("font")) or {}):  # 사이 점이 없는 글꼴(검은고딕·도현)은 '|' — 사이 점 하나로 큰 줄과 글꼴이 달라지지 않게 (검토)
        text = re.sub(r"\s*·\s*", " | ", text)
    if glyph_safe(text, base.get("font")) != text:
        base.pop("font", None)
        base["weight"] = "Black"
    if len(re.sub(r"\s", "", text)) > editor.TOPIC_MAX + 2:
        return None
    pad = (max(6, base.get("strokeW", 0)) if base.get("bgOn") else base.get("strokeW", 0))
    gap, top_ok = editor.TXT_GAP * H, editor.TOPIC_TOP * H
    lay = seq_layout(fmt)
    zoomed = any(it["track"] == "V1" and float(it["start"]) < editor.TOPIC_SEC and (_zoom_of(it)[0] > 100.5 or _zoom_of(it)[1]) for it in B.items)
    vtop = H * (1 - min(1.0, W * float(lay.get("zoom") or 1) * 9 / 16 / H)) / 2   # 흐린 배경 위 본 화면 위 끝 (확대 안 했을 때)
    cb = text_box("가나다라", dict(_caption_style(st, fmt, cap_y)), W, H) if st["captions"].get("on", True) else None
    mb = text_box(hk["text"], hl, W, H)

    def clear(b):
        return b[1] >= top_ok - 1e-6 and not (cb and min(b[3], cb[3]) - max(b[1], cb[1]) > -gap)
    for size in TOPIC_KICK:
        look = dict(base, size=size)
        kb = text_box(text, dict(look, y=0.5), W, H)
        if kb[2] - kb[0] > editor.TOPIC_ONE * (W - 80):
            continue
        kh = kb[3] - kb[1]                      # 상자 높이 (테두리·여백 포함)
        tries = [("below", 0.0)] if cap_y < 0.5 and not zoomed else []   # 말 자막이 위쪽: 큰 줄 아래 (영상 위 끝까지만 · 확대한 장면이면 안 씀)
        tries.append(("above", 0.0))
        deficit = top_ok - (mb[1] - gap - kh)
        if deficit > 0 and not zoomed:   # 위에 자리가 모자람: 큰 줄을 그만큼 내림 (영상 위 끝까지만)
            tries.append(("above", deficit))
        for where, d in tries:
            m = [mb[0], mb[1] + d, mb[2], mb[3] + d]
            if d and (m[3] > vtop - gap or (cb and min(m[3], cb[3]) - max(m[1], cb[1]) > -gap)):
                continue
            y = m[1] - gap - pad if where == "above" else m[3] + gap + kh - pad   # 글 아래 끝 (상자 아래 = y + pad)
            b = text_box(text, dict(look, y=y / H), W, H)
            if not clear(b) or (where == "below" and b[3] > vtop):
                continue
            if d:
                hl["y"] = round(float(hl["y"]) + d / H, 4)
            yk = math.floor(y / H * 10000) / 10000 if where == "above" else math.ceil(y / H * 10000) / 10000   # 틈이 줄지 않게
            kt = B.title(text, 0.0, float(hk["dur"]), dict(look, y=yk))
            kt["plan"], kt["under"] = "topic", hk["id"]
            return kt
    return None


def _shorts_best(rec, moms):
    """쇼츠로 쓸 추천 구간: 재미 순간이 가장 많은 곳 (없으면 None)."""
    best, bs = None, -1.0
    for r in rec.get("shorts") or []:
        sc = sum(m["score"] for m in moms if r["start"] <= m["t"] <= r["end"] and m["kind"] in ("play", "punchline", "success", "emphasis", "surprise"))
        if sc > bs:
            best, bs = r, sc
    return best


def _win_best(win):
    """정해 준 쇼츠 구간(끝까지 자동으로 · 포인트 하나 = 쇼츠 하나) → 추천 구간 {start, end, cuts} — 컷이 없으면 None (앞쪽 정리 구간 50초)."""
    if not (win or {}).get("cuts"):
        return None
    return {"start": float(win["start"]), "end": float(win["end"]), "cuts": [dict(c) for c in win["cuts"]]}


def _win_short(win, segs, sig=None):
    """정해 준 쇼츠 구간(win)을 첫머리 주제 줄(BR-120)의 추천 구간으로 — 이 쇼츠가 실제로 보여 주는 그 구간 그대로 (_shorts_best 아님).
    위 큰 제목(hook)은 win 의 것 (끝까지 자동으로가 고른 이 쇼츠 제목 · 예전 훅과 같음) · 포인트는 win 에 있으면 그것(editor.recommend 가
    그 구간으로 셈 · autorun 포인트) 아니면 그 구간의 문장 단위 받아쓰기로 (editor.short_point · editor.recommend 와 같은 clean_lines).
    컷이 없으면 None (_shorts_window 가 앞쪽 정리 구간을 씀 · 추천 구간이 아님)."""
    r = _win_best(win)
    if r is None:
        return None
    r["hook"] = str(win.get("hook") or "")
    p = win.get("point")
    r["point"] = dict(p) if isinstance(p, dict) and p else editor.short_point(r, clean_lines(segs, (sig or {}).get("blobs"))[0] if segs else [])
    return r


def _point_mark(m):
    """포인트를 나누는 분명한 장 나눔 말인 순간 (editor.SECTION · '그럼 두 번째는'처럼 이음말 뒤의 순서도 — editor.POINT_OPEN · D-267 ·
    '그 다음'·'이번엔'은 한 동작 안 순서 말이라 아님 · D-168)."""
    t = str(m.get("text") or "").strip()
    return m["kind"] == "section" and bool(editor.SECTION.match(t) or editor.POINT_OPEN.match(t))


def _shorts_window(rec, moms, dur, sig_demo=(), win=None, points=None, stop=True):
    """쇼츠: 추천 구간 중 재미 순간이 가장 많은 곳(_shorts_best · 없으면 앞쪽 정리 구간 50초)의 컷 목록.
    win: 정해 준 구간 (끝까지 자동으로 · 포인트 하나 = 쇼츠 하나) — 고르지 않고 그 구간을 씀 (시범 살리기·끝 다듬기는 같음).
    points: 쇼츠 = 포인트 하나(BR-092 · 없으면 SHORTS_POINTS) — 포인트를 여는 말로 시작하는 구간에 앞 포인트의 시범을 붙이지 않고,
    정리 말('오늘 배운 거')·끝인사·다음 포인트 앞에서 끝냄 (D-260) — 그 앞까지 남는 컷이 min_shorts() 초 넘을 때만 (원본 시간이 아니라
    남는 컷 길이로 · D-267) · stop=False 면 다음 포인트 앞에서 끊지 않음 (끊어서 쇼츠가 너무 짧아졌을 때 compile_seq 가 다시 만듦 ·
    포인트를 넘는 쇼츠가 쇼츠 없음보다 나음 · D-267)."""
    pts = SHORTS_POINTS if points is None else points
    best = _win_best(win) if win else _shorts_best(rec, moms)
    if best:  # 그 구간 안의 시범 장면도 살림 (말 정리만 하면 시범이 빠짐)
        demo = [d for d in sig_demo if best["start"] <= d[0] and d[1] <= best["end"]]
        # 구간이 시범 바로 뒤 반응('아이고, 공이 조금 뒤로 갔네요')으로 시작하면 그 시범부터 (판정 round5 최종: 쇼츠가 무엇에 대한
        # 반응인지 모르는 말로 시작) — 시작 8초 안에 끝나는 시범 장면을 앞에 붙임
        opens = pts and any(_point_mark(m) and best["start"] - 0.5 <= float(m["a"]) <= best["start"] + 1.5 for m in moms)
        pre = [] if opens else [d for d in sig_demo if best["start"] - 8.0 <= d[1] <= best["start"] + 0.5 and d[0] < best["start"]]
        if pre:
            d0 = min(pre, key=lambda d: d[0])
            demo.append([float(d0[0]), float(best["start"])])
        cuts = keep_cuts(best["cuts"], demo)
        # 끝 15초 안에서 새 순서('세 번째 포인트')를 꺼내고 설명 중에 끝나면 그 말 앞에서 끝냄 (판정 round5 최종: 쇼츠가 설명 중간에 잘린 채 끝남)
        end = float(cuts[-1]["out"]) if cuts else 0.0
        # (끝 8초 안의 딴소리('물 좀 마시고 할게요')로 끝나도 그 앞에서 — 마무리 없이 흐지부지 · 판정 round6)
        sec = [float(m["a"]) for m in moms if (m["kind"] == "section" and end - 15.0 < float(m["a"]) < end - 0.5)
               or (m["kind"] == "aside" and end - 8.0 < float(m["a"]) < end - 0.5)]
        if pts and stop:  # 포인트 하나: 시작 뒤의 다음 포인트·정리 말·끝인사 앞에서 (얼마나 남든 · 앞에 남는 컷이 min_shorts() 초 넘을 때)
            a0 = float(cuts[0]["in"]) if cuts else float(best["start"])
            sec += [float(m["a"]) for m in moms if (_point_mark(m) or m["kind"] in ("recap", "closing")) and a0 + 3.0 < float(m["a"]) < end - 0.5]
        if sec:
            at = min(sec) - 0.2
            kept = sum(max(0.0, min(float(c["out"]), at) - float(c["in"])) for c in cuts)
            if (kept >= min_shorts()) if pts and stop else (at - float(cuts[0]["in"]) >= _stop_min()):
                cuts = [dict(c, out=min(float(c["out"]), at)) for c in cuts if float(c["in"]) < at - 0.3]
        return cuts
    out, tot = [], 0.0
    for c in rec.get("tidy") or [{"in": 0.0, "out": min(dur, 50.0)}]:
        if tot >= 50:
            break
        b = min(float(c["out"]), float(c["in"]) + 50 - tot)
        out.append({"in": float(c["in"]), "out": b})
        tot += b - float(c["in"])
    return out


def _replay_span(a, b, t, cuts):
    """다시 보기 원본 구간을 원본 장면 바뀜 안쪽으로 (판정 round5: 시범 뒤 스튜디오 장면까지 느리게 나와 '다시 보기' 표시가 말하는 장면 위에 남음) ·
    공 차는 순간(t) 앞뒤 0.3초는 늘 남기고, 남는 길이가 1초보다 짧으면 그대로."""
    for x in sorted(float(v) for v in cuts):
        if t + 0.3 < x < b and x - 0.05 - a >= 1.0:
            b = x - 0.05
        if a < x < t - 0.3 and b - (x + 0.05) >= 1.0:
            a = x + 0.05
    return round(a, 3), round(b, 3)


def _insert(B, c, name, sig, st, intensity, looks, pal, seed, cap_y):
    """슬로모 다시 보기 · 정지 화면을 지금 자리에 끼워 넣기."""
    prev = [x for x in B.items if x["track"] == "V1"]
    prev = prev[-1] if prev else None
    s0 = B.pos
    refs = {"items": [], "titles": [], "shapes": [], "trans": []}
    if c["kind"] == "replay":
        a, b = max(0.0, c["a"] - 0.3), min(float(sig["duration"]), c["b"] + 0.3)
        if b - a > REPLAY_SRC:  # 다시 보기는 원본 REPLAY_SRC 초까지 (공 차는 순간 조금 앞부터 · 느리게 두 배)
            a = max(a, c["t"] - 1.2)
            b = min(b, a + REPLAY_SRC)
        a, b = _replay_span(a, b, float(c["t"]), sig.get("cuts") or ())
        fx = None
        if intensity == "듬뿍":  # 다른 각도 느낌: 살짝 당겨서
            fx = {"scale": {"v": 118.0, "k": []}, "anchor": {"v": [0.5, 0.55], "k": []}}
        v, au = B.clip(a, b, REPLAY_SPEED, fx, nocaps=True, mute=True)
        refs["items"] += [v["id"], au["id"]]
        tr = B.flash(prev, v, "white", 0.2) if prev is not None and st["sound"].get("palette") != "cinematic" else B.flash(prev, v, "dissolve", 0.3)
        if tr:
            refs["trans"].append(tr["id"])
        ln = B.pos - s0
        # 다시 보기 표시는 원본 화면 글씨처럼 보이지 않게 크고 진하게 (판정: 오른쪽 위에 작게 붙어 다시 보기 장면인지 알아보기 어려움)
        lt = dict(editor.TITLE_STYLE, weight="Black", size=54, fill="#FFFFFF", stroke="#000000", strokeW=12, bgOn=True, bg="#000000", bgOpacity=0.78,
                  x=0.93, y=0.14 if cap_y > 0.5 else 0.8, align="right", effect="fade")
        if B.fmt == "shorts":
            lt.update(size=58, y=EP_ROW if B.card else _skin_row(B.skin["top"], dict(lt, size=58)) if B.skin else 0.2)   # 은평 스타일: 사실 줄 아래 · 영상 창 바로 위 (BR-242) · 스킨: 영상 창 맨 위 줄 (BR-323)
        refs["titles"].append(B.title("다시 보기 ▶", s0 + 0.15, ln - 0.15, lt)["id"])
        if st["fun"].get("replayLook") == "letterbox" and not (B.fmt == "long" and st["intro"].get("letterbox")) and not B.card and not B.skin:  # (늘 띠가 있으면 또 안 넣음 · 은평 스타일·스킨은 띠 틀)
            refs["shapes"] += [B.shape(0, 0, 1, 0.1, "#000000", s0, ln, 1.0, 0, "검은 띠")["id"], B.shape(0, 0.9, 1, 0.1, "#000000", s0, ln, 1.0, 0, "검은 띠")["id"]]
        # 휙(다큐는 라이저) 봉우리를 다시 보기로 바뀌는 컷에 (예전: 컷 0.1초 앞에서 시작 → 봉우리가 컷 뒤 · D-371)
        refs["sfx"] = [_sfx_at(B, s0, pal.get("replay", "휙"), None, "replay", why=f"{mmss(c['t'])} 시범 다시 보기로 바뀌는 컷")]
        k = pal.get("kick")
        if k:  # 느린 화면 속 실제 공 소리마다 (예전: 첫 공 소리 하나만 · 웃음 소리를 공 소리로 보던 것도 뺌 · D-376)
            for o in [o for o in _kicks(sig) if a <= o <= b]:
                refs["sfx"].append(_sfx_at(B, s0 + (o - a) / REPLAY_SPEED, k, None, "kick", why=f"다시 보기 속 공 차는 소리 ({mmss(o)})"))
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
        refs["sfx"] = [_sfx_at(B, s0, pal.get("freeze", "찰칵"), None, "freeze", why="결정적 순간 직전 화면 멈춤")]
        if intensity == "듬뿍" and pal.get("zoom"):
            refs["sfx"].append(_sfx_at(B, s0 + 0.35, "긁기", -1.0, "freeze", why="화면 멈춤 (레코드 긁는 소리)"))
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


END_TAIL = 2.4         # 마지막 말 뒤에 남길 끝 시간(초 · 원본에 있을 때만) — 마지막 자막이 다 읽히고 엔드 글자를 말 자막 없이 띄울 자리
END_TEXT_MIN = 1.2     # 말 자막이 사라진 뒤 이만큼 남아야 엔드 큰 글자 · 모자라면 구석의 작은 구독 표시만
# 엔드 큰 글자 후보 (앞의 것부터) — 마무리 말에 같은 뜻이 있으면 다음 것 (판정: '다음 영상에서 만나요' 자막 위에 '다음 영상도 같이 봐요!')
END_LINES = (("다음 영상도 같이 봐요!", r"다음\s?(?:영상|시간|에)"), ("오늘 영상 어떠셨나요?", r"댓글|어떠셨|어땠"),
             ("시청해 주셔서 고마워요!", r"감사|고마"), ("구독하고 다음 레슨도 받아 가세요!", r"구독"))


def end_line(said):
    """마무리 말(said)과 뜻이 겹치지 않는 엔드 큰 글자 · 다 겹치면 None."""
    return next((line for line, rx in END_LINES if not re.search(rx, said or "")), None)


def _end_overlay(B, moms, intensity, pal, dur, face=None, words=(), caps=None, junk=()):
    """엔드 화면을 마무리 인사('오늘은 여기까지…') 위에 (유튜브 최종 화면 5~20초 · 판정: 따로 붙인 8초 정지 화면은 멈춘 화면·빈 상자) —
    마무리 말부터 본편을 조금 어둡게 하고, 말이 끝나고 말 자막이 사라진 뒤에 엔드 큰 글자 + 구독 표시를 한꺼번에 (말 자막·얼굴 위에 글자를
    쌓지 않게 · 판정: 엔드 글자가 마지막 대사 자막 바로 위에 쌓여 얼굴을 가리고 같은 말이 두 번). 마지막 말 뒤에 원본이 있으면 END_TAIL 초까지
    이어 붙이고(마지막 자막도 다 읽힘), 끝 시간이 모자라면 큰 글자 없이 얼굴 반대쪽 위 구석에 작은 구독 표시만.
    마무리 말부터 끝까지 5초가 안 되면 원본 뒤를 조금 더 이어 붙임. 마무리 말이 없거나 끝에서 너무 멀면 False (따로 붙이는 엔드 화면으로)."""
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
    last = B.main[-1]
    sp = editor.i_sp(last)

    def extend_to(src_out):  # 마지막 본편 클립(과 짝 소리)을 원본에서 더 이어 붙임 (같은 장면이 이어짐 · 컷 없음)
        nout = round(src_out, 4)
        if nout <= float(last["out"]) + 1e-3:
            return
        for a in [x for x in B.items if x["track"] == "A1" and x.get("link") and x.get("link") == last.get("link")]:
            a["out"] = nout
        last["out"] = nout
        B.pos = editor.i_end(last)
    need = END_MIN - (B.pos - s0)
    if need > 0:
        more = need + 0.3
        if float(last["out"]) + more * sp > dur - 0.05:
            return False
        extend_to(float(last["out"]) + more * sp)
    said = [w for w in words if float(last["in"]) <= w[0] < float(last["out"])]
    if said:  # 마지막 말 뒤 끝 시간 (다음 말·정리할 곳 앞에서 멈춤 · 최종 화면 20초를 넘지 않게)
        w_end = max(w[1] for w in said)
        limit = min([dur - 0.05, float(last["out"]) + (END_MAX - (B.pos - s0)) * sp]
                    + [w[0] - 0.25 for w in words if w[0] >= float(last["out"]) - 0.01] + [float(j[0]) for j in junk if float(j[0]) >= w_end - 0.01])
        extend_to(min(w_end + END_TAIL * sp, limit))
    first = _cut_main(B, s0)
    if first is None:
        return False
    for x in B.main[B.main.index(first):]:
        x["color"] = dict(x.get("color") or {}, **END_DIM)
    ln = B.pos - s0
    caps_tl = editor.timeline_captions({"items": B.items, "captions": caps, "format": B.fmt}) if caps else []
    t_free = max([s0] + [c["end"] + 0.12 for c in caps_tl if c["end"] > s0 - 0.05])
    said_txt = " ".join(w[2] for w in words if (B.src_to_tl(w[0]) or -1.0) >= s0 - 0.05)
    line = end_line(said_txt)
    ids, end_sfx = [], []
    fy = (face[1] + face[3]) / 2 if face else 0.35
    if line and B.pos - t_free >= END_TEXT_MIN:
        # 말 자막이 사라진 뒤 아래쪽(말 자막 자리 · 얼굴은 보통 위쪽)에 큰 글자 + 구독 표시 (얼굴이 아래쪽이면 위쪽에)
        y1, y2 = (0.78, 0.9) if fy < 0.55 else (0.2, 0.31)
        ids.append(B.title(line, t_free, B.pos - t_free, dict(editor.TITLE_STYLE, font="Black Han Sans", size=84, fill="#FFFFFF", stroke="#111111",
                                                              strokeW=8, y=y1, effect="pop"))["id"])
        ids.append(B.title("구독 · 좋아요 부탁해요!", t_free, B.pos - t_free, dict(editor.TITLE_STYLE, weight="Black", size=44, fill="#FFFFFF",
                                                                              stroke="#E5484D", strokeW=0, bgOn=True, bg="#E5484D", bgOpacity=1.0,
                                                                              y=y2, effect="pop"))["id"])
        end_sfx = [_sfx_at(B, t_free, pal.get("end", "경쾌 짧은 음악"), None, "end", why="엔드 글자 (말이 끝난 뒤)", title=ids[0], must=True)]
    else:
        # 끝 시간이 모자라거나 할 말이 다 겹침: 말하는 동안 얼굴 반대쪽 위 구석에 작은 구독 표시만 (말 자막·얼굴을 안 가림 · 효과음 없음)
        fx = (face[0] + face[2]) / 2 if face else 0.5
        left = fx > 0.6
        t1 = min(s0 + 0.4, B.pos - MIN_TEXT)
        ids.append(B.title("구독 · 좋아요", t1, B.pos - t1, dict(editor.TITLE_STYLE, weight="Black", size=40, fill="#FFFFFF", stroke="#E5484D", strokeW=0,
                                                              bgOn=True, bg="#E5484D", bgOpacity=1.0, x=0.06 if left else 0.94,
                                                              align="left" if left else "right", y=0.13, effect="pop"))["id"])
    # 본편 위에 얹은 것이라 빼도 빈자리를 당기지 않음(ins.overlay) · 어둡게 한 본편은 빼면 원래 밝기로(refs.dim · 편집실 msgRemove)
    B.event("end", s0, line or "구독 · 좋아요", "엔드 화면 (마무리 인사 위 · 유튜브 최종 화면 자리)",
            refs={"titles": ids, "dim": dict(END_DIM), "sfx": end_sfx}, ins={"start": round(s0, 3), "len": round(ln, 3), "overlay": True})
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
    k_end = _sfx_at(B, s0 + 0.3, pal.get("end", "경쾌 짧은 음악"), None, "end", why="엔드 화면 글자", title=t1["id"], must=True)
    B.event("end", s0, "다음 영상도 같이 봐요!", "엔드 화면 (유튜브 최종 화면 자리)", refs={"items": made, "trans": [tr["id"]] if tr else [], "shapes": [x["id"] for x in sh],
                                                                     "titles": [t1["id"], t2["id"]] + ([t3["id"]] if t3 else []), "sfx": [k_end]},
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


REACT = re.compile("|".join((PRAISE.pattern, SUCCESS.pattern, FAIL.pattern, r"(?:^|\s)(?:와|우와|대박|헐|오오+|아|아이고)[,.!?~…]*$")))
TIP = re.compile(r"(?:할|하실|받을|찰|때릴|넣을|볼)\s?때는|핵심|포인트|중요|기억하|(?:하|해|주|보|넣|차|받|빼|들)세요|(?:해|하)\s?주세요")  # 반응이 아닌 새 설명·당부
REACT_GAP = 1.5      # 반응 말('와,'·'아 아깝다.') 뒤 이만큼 안에 이어지는 말은 같은 반응 ('와, … 이거죠. 완벽해요.' · '아 아깝다. 터치가 조금 길었네요.')
REACT_MAX = 8.0      # 반응을 이어 붙이는 최대 길이 (시범 끝에서)


def _after_reaction(lines, b):
    """시범이 끝난 b 뒤 다시 보기를 넣을 원본 시각 (늘 말과 말 사이).
    바로 뒤 말이 반응(칭찬·감탄·아쉬움)이면 그 반응이 끝난 뒤 — 숨 안 쉬고 이어지는 말과, 감탄·쉼표로 끝난 반응 말 뒤 REACT_GAP 초 안에
    이어지는 말까지 한 반응으로 봄 (판정: '와,'와 '이거죠.' 사이·'아 아깝다.'와 '터치가 조금 길었네요.' 사이에 다시 보기를 끼워 말이 7~9초 끊김).
    반응이 아닌 말(다음 설명·장 나눔·마무리 인사)은 반응으로 잇지 않고 그 말 앞에 넣음 — 판정: 시범 뒤 '그리고 마지막으로 …' 설명을
    반응으로 보고 그 뒤(마무리 인사 바로 앞)로 밀려나 무엇을 다시 보여 주는지 모르는 엉뚱한 다시 보기."""
    def stop(t):  # 반응으로 잇지 않는 말 (다음 순서·마무리·시범 예고·새 설명 '슈팅할 때는 디딤발 방향이 핵심이에요.')
        return SECTION.search(t) or CLOSING.search(t) or DEMO_W.search(t) or TIP.search(t)

    def react(t):
        return REACT.search(t) or SURPRISE.search(" " + t + " ")
    for k, ln in enumerate(lines):
        mid = ln["start"] <= b < ln["end"]
        if not (b - 0.3 <= ln["start"] <= b + 2.0 or mid):  # 반응 말 (또는 시범 끝이 말 중간)
            continue
        if ln["end"] <= b + 0.05:  # 시범이 끝나기 전에 끝난 짧은 말('툭.' · 숫자 세기)은 시범의 일부 (그 앞에 넣으면 시범 끝이 잘림)
            continue
        txt = ln["text"].strip()
        if not react(txt) and (not mid or stop(txt)):
            if ln["start"] >= b:  # 다음 말이 반응이 아님 → 시범이 끝난 뒤, 그 말 앞 (시범 끝과 말 사이)
                return round(max(b + 0.05, min(b + 0.3, ln["start"] - 0.05)), 3)
            if ln["start"] >= b - 1.0:  # 시범 끝에 걸쳐 시작하는 새 설명·장 나눔: 그 말 앞 (말은 자르지 않음 · 시범 끝 0.3초까지만 뒤로 남음)
                return round(max(b - 0.3, ln["start"] - 0.05), 3)
        end, prev = ln["end"], ln
        for nx in lines[k + 1:]:
            gap = nx["start"] - end
            open_ = re.search(r",$", prev["text"].strip()) or react(prev["text"].strip())
            if (gap > 0.35 and not (gap <= REACT_GAP and open_)) or nx["end"] - b > REACT_MAX or stop(nx["text"].strip()):
                break
            end, prev = nx["end"], nx
        return round(end + 0.15, 3)
    return round(b + 0.3, 3)


def _snap_insert(at, pieces, words, ahead=2.5):
    """끼워 넣을 자리(원본 at)를 바로 뒤 컷 자리로 (그 사이에 말이 없을 때) — 이어진 장면 가운데에 끼우면 화면이 두 번(들어가는 컷·
    나오는 컷) 바뀌지만 이미 컷인 자리에 끼우면 한 번만 더 바뀜 (양 범위를 넘어 재미 글자가 빠지지 않게)."""
    for k in range(1, len(pieces)):
        pa, pb = pieces[k - 1], pieces[k]
        if abs(float(pa["out"]) - float(pb["in"])) <= 0.06:  # 이어진 조각 (확대로만 나눔)
            continue
        if at - 0.25 <= float(pa["out"]) <= at + ahead and not any(at <= w[0] < float(pa["out"]) for w in words):
            return float(pb["in"])
    return at


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


SFX_MIN_GAP = 1.0    # 효과음 맞출 점 사이 최소 간격(초) — 한 묶음(숫자 세기 틱)은 하나로 봄
SFX_PER_10S = 3      # 10초 안 효과음 순간 수 (숫자 세기 틱 묶음은 하나 · D-374)
SFX_PRI = {"title": 9, "end": 9, "replay": 8, "freeze": 8, "score": 8, "fx": 7, "teaser": 6, "kick": 6, "hook": 6, "inner": 5, "count": 5, "accent": 6,
           "situ": 4, "emphasis": 3, "montage": 2, "punch": 3, "cue": 7}
# 들리는 효과음 1분 개수 (아래, 위) — 배경음악 곡 수는 안 셈 · 쇼츠는 짧아 따로 (예전: 롱폼과 같은 범위에서 곡 수를 빼 짧은 쇼츠는 0~1개 · D-374)
SFX_BAND = {"long": {"담백": (1.0, 3.0), "보통": (2.5, 6.6), "듬뿍": (5.0, 11.5)},
            "shorts": {"담백": (2.0, 4.0), "보통": (4.0, 8.0), "듬뿍": (6.0, 12.0)}}
SFX_OVER_TALK = 6.0  # 말 위에 작게 놓는 짧은 소리: 보정한 말소리 최대보다 이만큼(dB) 작게 (D-372)
EARLY = {"section": 0.35, "success": 0.8}   # 꼭 넣을 다음 포인트·성공 소리는 글자 첫 장면보다 이만큼(초)까지 앞(그 말 앞 컷·쉼)에서도 (D-379)
MISSED_CLEAR = 2.0   # 꼭 넣을 소리가 못 들어간 순간 앞뒤 이만큼(초)에는 더 넣는 소리도 안 넣음 (D-379)
SFX_SHIFT = {"situ": 0.6, "emphasis": 0.6, "fx": 0.6, "inner": 0.6, "score": 0.6, "title": 1.0, "end": 1.0}   # 말을 가리면 글자와 함께 늦출 수 있는 만큼(초)
SFX_ANCHOR_KO = {"title": "글자 첫 장면", "hook": "글자 첫 장면", "end": "글자 첫 장면", "situ": "글자 첫 장면", "emphasis": "글자 첫 장면", "fx": "글자 첫 장면",
                 "inner": "글자 첫 장면", "count": "숫자 글자 첫 장면", "score": "글자 첫 장면", "replay": "장면이 바뀌는 컷", "freeze": "장면이 바뀌는 컷",
                 "teaser": "장면이 바뀌는 컷", "montage": "장면이 바뀌는 컷", "kick": "공 차는 소리", "punch": "확대 시작", "accent": "말이 쉬는 틈"}


def _talk_tl(B, words):
    """들리는 말 (타임라인 [(시작, 끝)]) — 소리를 켠 원본 조각(본편·티저·첫 질문 · 소리 끈 다시 보기·엔드 화면은 뺌)."""
    out = []
    for it in B.items:
        if it["track"] != "A1" or it.get("media") != "main" or it.get("mute") or it.get("rev"):
            continue
        a, i0, i1, sp = float(it["start"]), float(it["in"]), float(it["out"]), editor.i_sp(it)
        for w in words or ():
            s0, e0 = float(w[0]), float(w[1])
            if s0 < i1 - 0.02 and e0 > i0 + 0.02:
                out.append((a + (max(s0, i0) - i0) / sp, a + (min(e0, i1) - i0) / sp))
    return sorted(out)


EPS = 1e-6   # 시각 비교 여유 (받아쓰기 시각의 소수점 찌꺼기 105.57999999999997 같은 것)


def _ms_down(t):
    """초 → 1ms 단위로 내림 (다음 낱말 바로 앞 자리 · 반올림하면 낱말 쪽으로 0.0005초 넘어갈 수 있음)."""
    return math.floor(t * 1000.0 + EPS) / 1000.0


def _ms_up(t):
    """초 → 1ms 단위로 올림 (앞 낱말 끝 바로 뒤 자리)."""
    return math.ceil(t * 1000.0 - EPS) / 1000.0


def _loud_part(start, shp, i0=0.0):
    """효과음의 크게 들리는 부분 (타임라인 [앞, 뒤]) — 첫소리부터 최대보다 12dB 작아질 때까지(sfxlib.shape 의 loud · D-379) (점점 커지는 소리는
    봉우리 뒤 0.05초 넘게)."""
    lo = max(shp["attack"], i0)
    hi = max(shp["loud"], shp["hit"] + (0.05 if shp["hit"] > shp["attack"] + 0.01 else 0.02), shp["attack"] + 0.02)
    return start + lo - i0, start + max(lo + 0.02, hi) - i0


def _sfx_clean(talk, start, shp, i0=0.0):
    """크게 들리는 부분이 낱말과 안 겹치고 말이 쭉 이어지는 한가운데(앞 0.35초 안에 말이 끝나고 뒤 0.35초 안에 말이 시작)도 아님 (D-372)."""
    lo, hi = _loud_part(start, shp, i0)
    j = bisect.bisect_left(talk, (lo - 30.0,))
    near = talk[max(0, j - 2):bisect.bisect_right(talk, (hi + 1.0,))]
    m = min(0.02, (hi - lo) / 4)   # (아주 짧은 틱(20ms)도 낱말 첫소리와 겹침을 봄 · 예전 0.02 여유면 낱말 첫소리 위 틱이 '안 겹침')
    if any(s0 < hi - m and e0 > lo + m for s0, e0 in near):
        return False
    before = any(start - 0.35 <= e0 <= start + 0.05 for _, e0 in near)
    after = any(hi - 0.05 <= s0 <= hi + 0.35 for s0, _ in near)
    return not (before and after)


def _sfx_duck(talk, t, d, lvl):
    """긴 효과음(레벨업·짧은 음악) 꼬리가 다음 말 첫소리(talk · 타임라인)와 겹치면 그 앞에서 SFX_DUCK dB 줄이는 크기 키프레임 —
    앞부분 크기는 그대로 (판정: 듬뿍의 효과음·음악이 말을 가려 받아쓰기가 틀림 · 말 버스만이면 CER 4.9% → 섞은 편집본 9.3%).
    키프레임 시각은 효과음 파일 안의 시각 (처음부터 1배속으로 씀). (_audio_items 가 꼬리를 먼저 다음 말 앞에서 자르고 남은 것만)"""
    if d < SFX_DUCK_MIN:  # 짧은 소리(휙·뽁)는 그대로 — 가운데 최대 크기까지 줄어 안 들림 (판정 기준 말보다 6dB 넘게 작음)
        return []
    ws = [a for a in talk if t + 0.5 < a < t + d - 0.05]
    if not ws:
        return []
    w0 = ws[0] - t
    return [{"t": round(max(0.0, w0 - 0.15), 3), "v": round(lvl, 2), "e": "lin"}, {"t": round(w0, 3), "v": round(lvl - SFX_DUCK, 2), "e": "lin"}]


def place_sfx(B, sig, intensity, words=(), pal=None):
    """예약한 효과음(B.sfx) → 놓을 소리 목록 [{k, start, in, name, kind, lvl, soft, why, ...}] (D-371~D-374).
    1) 맞출 점: 글자에 붙은 소리는 그 글자의 (자리 정리 뒤) 첫 장면 · 확대 소리는 확대 시작 · 나머지는 예약한 시각 —
       시작 = 맞출 점 - 소리의 hit (휙·라이저는 봉우리가 그 점에 · sfxlib.shape)
    2) 말 지키기: 크게 들리는 부분이 낱말·말 한가운데에 걸리면 같은 뜻의 더 짧은 소리 → (소리만인 소리) 창 안 말 틈 → (글자 소리) 글자와 함께
       말 틈까지 늦춤 → 짧은 소리는 말 위에 작게(말보다 SFX_OVER_TALK dB) → 아니면 뺌
    3) 양: 꼭 넣을 소리(제목·엔드·다음 포인트·성공·웃음 뒤 농담·숫자 세기·시작 신호) 먼저, 나머지는 차례대로 1분 SFX_BAND 위쪽까지 ·
       맞출 점 사이 SFX_MIN_GAP 초 · 크게 들리는 부분끼리 안 겹침 · 10초에 SFX_PER_10S 순간까지."""
    assets = editor.ASSETS
    pal = pal or {}
    talk = _talk_tl(B, words)
    tit = {t["id"]: t for t in B.titles}
    ev_of = {}
    for e in B.events:
        for k in (e.get("refs") or {}).get("sfx") or []:
            ev_of.setdefault(k, e)
    by_item = {it["id"]: it for it in B.items}
    v1 = sorted((it for it in B.items if it["track"] == "V1"), key=lambda x: x["start"])
    cuts = [float(y["start"]) for x, y in zip(v1, v1[1:]) if not (x["media"] == y["media"] and abs(float(x["out"]) - float(y["in"])) < 0.06)]
    shp_c = {}

    def shp(nm):
        if nm not in shp_c:
            fn = sfxlib.ensure_sfx(nm, assets)
            shp_c[nm] = dict(sfxlib.shape(nm, assets / fn), file=fn)
        return shp_c[nm]

    def zoom_at(e):
        """확대 사건 → 확대가 시작하는 타임라인 시각 (확대가 없어졌으면 None)."""
        for iid in (e.get("refs") or {}).get("items") or []:
            it = by_item.get(iid)
            if it is None or it["track"] != "V1":
                continue
            v, ks, pk = _zoom_of(it)
            if len(ks) >= 2 and max(k["v"] for k in ks) - min(k["v"] for k in ks) > 0.5:
                return max(float(it["start"]), min(editor.i_tl(it, float(ks[0]["t"])), editor.i_end(it)))
            if abs(v - 100.0) > 0.5 or pk:
                return float(it["start"])
        return None

    cands = []
    for k, x in enumerate(B.sfx):
        if not x:
            continue
        t, nm, lvl, kind = x[:4]
        meta = _meta(x)
        e = ev_of.get(k)
        tt = tit.get(meta.get("title")) if meta.get("title") else None
        anchor = float(tt["start"]) if tt is not None else float(t)
        if kind == "punch":
            z = zoom_at(e) if e is not None and e["kind"] == "punch" else None
            if z is None and not (e is not None and e.get("accent")):
                continue   # 확대가 빠졌으면 그 소리도
            anchor = z if z is not None else anchor
        must = bool(meta.get("must")) or kind in ("title", "hook", "end")
        cands.append({"k": k, "t": anchor, "name": nm, "lvl": lvl, "kind": kind, "meta": meta, "title": tt, "ev": e, "must": must})
    total = max(B.pos, 1.0)
    band = SFX_BAND.get(B.fmt, SFX_BAND["long"])[intensity]
    cap_n = max(1, int(band[1] * max(0.25, total / 60.0) + 0.5))
    kept = []

    def moments_in(lo, hi, extra=None):
        gs = {}
        for c in kept + ([extra] if extra else []):
            if lo <= c["hit"] < hi:
                gs[c["meta"].get("group") or id(c)] = 1
        return len(gs)

    def fits(c, start, nm, soft):
        s_ = shp(nm)
        hit = start + s_["hit"]
        if hit < -0.05 or hit > total - 0.05:
            return False
        lo, hi = _loud_part(start, s_)
        grp = c["meta"].get("group")
        for o in kept:
            if grp and o["meta"].get("group") == grp:
                continue
            if abs(o["hit"] - hit) < SFX_MIN_GAP:
                return False
            if o["lo"] < hi - 0.03 and lo < o["hi"] - 0.03:
                return False
        trial = {"hit": hit, "meta": c["meta"]}
        wins = [hit - 10.0 + 1e-6] + [o["hit"] for o in kept if hit - 10.0 < o["hit"] <= hit]
        return all(moments_in(w, w + 10.0, trial) <= SFX_PER_10S and sum(1 for o in kept if w <= o["hit"] < w + 10.0) + 1 <= SFX_PER_10S + 1
                   for w in wins)

    def take(c, start, nm, soft, how, shift=0.0):
        s_ = shp(nm)
        lo, hi = _loud_part(start, s_)
        kept.append(dict(c, start=start, name=nm, soft=soft, how=how, hit=start + s_["hit"], lo=lo, hi=hi, shift=shift))

    def title_can_move(c, shift):
        """글자에 붙은 소리를 옮길 때 글자도 함께 옮겨도 되나 — 컷(다른 장면·끼운 장면)을 넘지 않고, 끝은 그대로 두고 남는 길이가
        MIN_TEXT·원래의 60% 넘게."""
        a0, d0 = float(c["title"]["start"]), float(c["title"]["dur"])
        if any(min(a0, a0 + shift) < x <= max(a0, a0 + shift) + 1e-6 for x in cuts):
            return False
        return a0 + d0 - (a0 + shift) >= max(MIN_TEXT, 0.6 * d0) - 1e-6

    def soft_take(c, nm, at, ak, structural=False, back=0.25):
        """작은 소리(말 위에 작게): 낱말 위에 놓으면 실제 섞기에서 안 들림 → 그 자리 · 낱말 끝 바로 뒤 · 다음 낱말 바로 앞 중
        크게 들리는 부분이 낱말과 안 겹치는 가장 가까운 낱말 사이 틈 (글자에 붙은 소리는 글자도 함께 · 앞 back 초(꼭 넣을 다음 포인트·성공은
        EARLY)~뒤 SFX_SHIFT) · 없으면 뺌 (제목·훅·엔드는 그 자리에 작게)."""
        s_ = shp(nm)
        l0, l1 = _loud_part(0.0, s_)   # 크게 들리는 부분 (소리 시작 기준)
        if c["title"] is not None:
            lo_w, hi_w = at - back, at + SFX_SHIFT.get(c["kind"], 0.3)
        else:   # 소리만: 그 순간 창 안에서도 맞출 점 가까이만 (멀리 가면 다른 말에 붙은 소리처럼 들림 · 성공은 반응 말 앞 back 초까지)
            w0, w1 = c["meta"].get("win") or (at - back, at + 0.3)
            # (꼭 넣을 성공 소리는 창 끝 = 반응 말 끝 뒤까지 — 반응 말 앞이 컷으로 붙어 틈이 없을 때 · D-379)
            lo_w, hi_w = max(w0, at - back), (w1 if c["must"] and c["meta"].get("mk") == "success" else min(w1, at + 0.3))
        lo_w, hi_w = min(lo_w, at), max(hi_w, at)
        if c["meta"].get("group"):   # 숫자 세기 틱: 숫자 말 바로 앞 틈까지만 (다음 숫자로 넘어가지 않게)
            hi_w = at + 0.05
        near_w = [(s0, e0) for s0, e0 in talk if lo_w - 1.0 <= e0 and s0 <= hi_w + 1.0]
        # 낱말 끝 바로 뒤 자리는 올림 · 다음 낱말 바로 앞 자리는 내림 (1ms) — 반올림하면 딱 맞는 틈이 소수점 찌꺼기로 0.0005초 겹쳐 빠졌음
        # (S1 '포인트 ③' 낱말 105.57999999999997 · 숫자 세기 틱 186개 중 50개 · D-379)
        pts = ({round(at, 3)} | {_ms_up(e0 + 0.02 - l0 + s_["hit"]) for _, e0 in near_w}
               | {_ms_down(s0 - 0.02 - l1 + s_["hit"]) for s0, _ in near_w})
        pts = sorted((v for v in pts if lo_w - 1e-6 <= v <= hi_w + 1e-6), key=lambda v: (abs(v - at), v))
        for v in pts:
            st = v - s_["hit"]
            lo, hi = _loud_part(st, s_)
            # 낱말과 안 겹치는 낱말 사이 틈만 (낱말 한가운데에 작게 놓으면 실제 섞기에서 안 들림 · 첫소리 앞 0.02초도 비움 · 1µs 넘게 겹쳐야 겹침)
            if any(s0 - 0.02 < hi - EPS and lo < e0 - 0.01 - EPS for s0, e0 in near_w) or not fits(c, st, nm, True):
                continue
            move = c["title"] is not None and abs(v - at) > 1e-3
            if move and not title_can_move(c, v - at):
                if not (c["must"] and v < at and c["meta"].get("mk") == "section"):   # 꼭 넣을 다음 포인트 소리는 컷 앞 쉼에도 (글자는 그대로 · D-379)
                    continue
                move = False
            take(c, st, nm, True, (f"{ak}에 맞춤" if abs(v - at) < 1e-3 else f"{ak} 옆 낱말 사이 틈 ({v - at:+.2f}초)") + " · 말 위라 작게"
                 + (f" ('{nm}')" if nm != c["name"] else ""), shift=(v - at) if move else 0.0)
            return True
        if structural and fits(c, at - s_["hit"], nm, True):
            take(c, at - s_["hit"], nm, True, f"{ak}에 맞춤 · 말 위라 작게")
            return True
        return False

    def early_take(c, names, back):
        """다음 포인트·성공 소리 (D-379): 글자 첫 장면이 그 말 첫 낱말이라 휙의 봉우리 뒤 꼬리(0.1초)·성공 소리가 늘 낱말에 걸림 → 그 앞 back 초
        안의 컷 · 첫 낱말 바로 앞 쉼 · 앞 낱말 끝 바로 뒤에 맞춤 (글자는 컷을 넘지 않으면 함께 앞당김 · 끝은 그대로). 예전: 제자리·늦추기·
        작은 소리만 봐서 긴 쉼(3초) 뒤 컷에서 시작하는 '세 번째 포인트'도 소리가 빠짐 (S1 '포인트 ③' 14/20 · S2 '동작 ①' 17/22)."""
        at = c["t"]
        for nm in names:
            s_ = shp(nm)
            l0, l1 = _loud_part(0.0, s_)
            pts = [x for x in cuts if not any(s0 + 0.02 < x < e0 - 0.02 for s0, e0 in talk)]
            # 낱말 바로 앞(그 말 첫 낱말 · 반응 말 첫 낱말) · 앞 낱말 끝 바로 뒤
            pts += [_ms_down(s0 - 0.03 - (l1 - s_["hit"])) for s0, _ in talk if at - back <= s0 < at + 0.3]
            pts += [_ms_up(e0 + 0.03 + s_["hit"] - l0) for _, e0 in talk if at - back - 0.5 <= e0 < at]
            # 성공 소리는 그 장면 안에서만 (컷 앞은 다른 장면 · 다시 보기 — 그 순간 소리가 아님) · 다음 포인트는 컷에서 넘어가는 휙이라 컷 앞도
            floor = max([x for x in cuts if x <= at + EPS] or [-1e9]) - EPS if c["meta"].get("mk") == "success" else -1e9
            sec = c["meta"].get("mk") == "section"   # (다음 포인트는 컷이 먼저 — 장면이 바뀌며 휙)
            for h in sorted({round(v, 3) for v in pts if max(at - back, floor) - EPS <= v < at - 0.01},
                            key=lambda v: (not (sec and any(abs(v - x) < 1e-3 for x in cuts)), at - v)):
                st = h - s_["hit"]
                if not (_sfx_clean(talk, st, s_) and fits(c, st, nm, False)):
                    continue
                move = c["title"] is not None and title_can_move(c, h - at)
                at_cut = any(abs(h - x) < 1e-3 for x in cuts)
                take(c, st, nm, False, f"{'소리 봉우리' if nm in sfxlib.SWELL else '첫소리'}를 {'장면이 바뀌는 컷' if at_cut else '그 말 앞 쉬는 틈'}에 맞춤 "
                     f"({h - at:+.2f}초{' · 글자도 함께' if move else ''})" + (f" · 더 짧은 '{nm}'" if nm != c["name"] else ""),
                     shift=(h - at) if move else 0.0)
                return True
        return False

    order = sorted(cands, key=lambda c: (not c["must"], -SFX_PRI.get(c["kind"], 4), c["t"]))
    missed = []   # 못 넣은 꼭 넣을 소리의 맞출 점
    for c in order:
        if not c["must"] and len({o["meta"].get("group") or id(o) for o in kept}) >= cap_n:
            continue
        if not c["must"] and any(abs(c["t"] - x) < MISSED_CLEAR for x in missed):
            continue   # 꼭 넣을 소리가 못 들어간 순간 옆에 다른 뜻 소리를 넣지 않음 (그 순간의 소리로 들림 · '꽂혔다!' 성공 대신 1.7초 뒤 뽁 · D-379)
        names = [c["name"]] + [n for n in SFX_ALT.get(c["name"], ()) if n in sfxlib.CATALOG]
        ak = SFX_ANCHOR_KO.get(c["kind"], "예약한 자리")
        if c["title"] is None and ak.endswith("글자 첫 장면"):
            ak = "그 순간"   # (글자를 빼고 소리만 남긴 것)
        done = False
        if c["meta"].get("soft"):   # 늘 작게 (아쉬운 순간 '퍽')
            soft_take(c, c["name"], c["t"], ak)
            continue
        for nm in names:   # 1) 제자리 (같은 뜻의 더 짧은 소리까지)
            st = c["t"] - shp(nm)["hit"]
            if _sfx_clean(talk, st, shp(nm)) and fits(c, st, nm, False):
                take(c, st, nm, False, f"{'소리 봉우리' if nm in sfxlib.SWELL else '첫소리'}를 {ak}에 맞춤" + (f" · 말 틈이 짧아 더 짧은 '{nm}'" if nm != c["name"] else ""))
                done = True
                break
        early = EARLY.get(c["meta"].get("mk")) if c["must"] and (c["title"] is not None or not c["meta"].get("win")) else None
        if early and c["meta"].get("lead") is not None:   # 다음 포인트 줄 앞 군말('자,')이 남았으면 그 앞 쉼까지 (D-379)
            early = min(1.2, max(early, c["t"] - float(c["meta"]["lead"]) + EARLY["section"]))
        if not done and early:
            done = early_take(c, names, early)   # 1b) 다음 포인트·성공: 그 말 앞 컷·쉼으로 조금 앞당김
        if not done and c["meta"].get("win"):   # 2) 소리만인 소리: 창 안의 말 틈으로
            lo, hi = c["meta"]["win"]
            # 낱말 끝 바로 뒤(0.05초)부터 — 창 끝(앞·뒤)은 그런 틈이 없을 때만 (창 끝은 말 없는 곳 한가운데라 맞출 점이 흐려짐)
            # (다음 포인트는 창 안의 컷도 — 컷에서 넘어가는 휙 · 창 끝(말 앞 0.6초)보다 그 말에 가까움 · D-379)
            cut_in = [x for x in cuts if lo <= x <= hi and not any(s0 + 0.02 < x < e0 - 0.02 for s0, e0 in talk)] if c["meta"].get("mk") == "section" else []
            slots = sorted(set(round(v, 3) for v in [c["t"]] + [e0 + 0.05 for _, e0 in talk if lo <= e0 + 0.05 <= hi] + cut_in), key=lambda v: abs(v - c["t"]))
            slots += [v for v in (round(lo, 3), round(hi, 3)) if v not in slots]
            for sl in slots:
                for nm in names:
                    st = sl - shp(nm)["hit"]
                    if _sfx_clean(talk, st, shp(nm)) and fits(c, st, nm, False):
                        where = "장면이 바뀌는 컷에 맞춤" if sl in cut_in else "말이 쉬는 틈에"
                        take(c, st, nm, False, f"{where} ({sl - c['t']:+.2f}초)" + (f" · 더 짧은 '{nm}'" if nm != c["name"] else ""))
                        done = True
                        break
                if done:
                    break
        if not done and c["title"] is not None and c["kind"] in SFX_SHIFT and not c["meta"].get("group"):   # 3) 글자와 함께 말 틈까지 늦춤
            a0 = float(c["title"]["start"])
            for sl in sorted(e0 + 0.05 for _, e0 in talk if a0 < e0 + 0.05 <= a0 + SFX_SHIFT[c["kind"]]):
                if not title_can_move(c, sl - a0):
                    break
                for nm in names:
                    st = sl - shp(nm)["hit"]
                    if _sfx_clean(talk, st, shp(nm)) and fits(c, st, nm, False):
                        take(c, st, nm, False, f"말을 가리지 않게 글자와 함께 {sl - a0:.2f}초 늦춤", shift=sl - a0)
                        done = True
                        break
                if done:
                    break
        if not done:   # 4) 짧은 소리는 말 위에 작게 · 제목·엔드처럼 꼭 넣을 짧은 소리도 작게 · 성공은 짧은 '딩동'을 작게 · 다음 포인트·강조 글자는 작은 '뽁'으로
            soft_opts = []
            mk = c["meta"].get("mk") or c["kind"]
            if c["name"] in sfxlib.SOFT:
                soft_opts.append(c["name"])
            elif c["kind"] in ("title", "hook", "end"):
                soft_opts += [n for n in names if shp(n)["audible"] <= 0.3]
            elif mk == "success" and "딩동" in sfxlib.CATALOG:
                soft_opts.append("딩동")   # (성공에 '뽁'은 뜻이 다름 · 판정 검사 WRONG_SOUND)
            elif mk in ("section", "emphasis", "situ", "score") and pal.get("soft") and c["title"] is not None:
                soft_opts.append(pal["soft"])   # (글자가 보일 때만 — 글자 없이 뜻 다른 '뽁'만 나면 엉뚱 · 농담·놀람·공 소리 글자에는 안 씀)
            at = c["t"]
            if mk == "section" and c["title"] is None:
                # 글자 없는 다음 포인트: 휙이 들어갈 말 틈이 없으면 작은 소리('팡'·'뽁' · 묵직 소리는 '딸깍')를 그 순간의 컷(없으면 그 자리)에 작게
                # (예전 '딸깍'만: 너무 작아 실제 섞기에서 안 들림)
                soft_opts.append(pal.get("soft") or "딸깍")
                lo, hi = c["meta"].get("win") or (at - 0.4, at + 0.2)
                near = [x for x in cuts if lo - 0.05 <= x <= hi + 0.05 and not any(s0 < x < e0 for s0, e0 in talk)]   # (낱말 한가운데의 컷은 아님)
                at = min(near, key=lambda x: abs(x - at)) if near else at
            for nm in soft_opts:
                if soft_take(c, nm, at, "장면이 바뀌는 컷" if at != c["t"] else ak, structural=c["kind"] in ("title", "hook", "end"),
                             back=max(0.25, early or 0.0, EARLY.get(mk, 0.0) if c["must"] else 0.0)):
                    done = True
                    break
        if not done and c["must"]:
            missed.append(c["t"])
    # 옮긴 글자 (그 사건의 같은 때 글자·도형 함께 · 작은 소리는 낱말 사이 틈으로 조금 앞당길 수도)
    for c in kept:
        if abs(c["shift"]) > 1e-6 and c["title"] is not None:
            a0 = float(c["title"]["start"])
            r = (c["ev"] or {}).get("refs") or {}
            ids = set(r.get("titles") or []) | set(r.get("shapes") or []) | {c["title"]["id"]}
            for g in B.titles + B.shapes:
                if g["id"] in ids and abs(float(g["start"]) - a0) < 0.06:
                    end0 = float(g["start"]) + float(g["dur"])   # (끝은 그대로 — 늘리면 끼운 장면 위로 넘어감)
                    g["start"] = round(float(g["start"]) + c["shift"], 3)
                    g["dur"] = round(max(0.3, end0 - float(g["start"])), 3)
            if c["ev"] is not None and abs(float(c["ev"]["t"]) - a0) < 0.06:
                c["ev"]["t"] = round(float(c["ev"]["t"]) + c["shift"], 2)
    return sorted(kept, key=lambda c: c["start"]), talk


def _audio_items(B, sig, sections, seed, snd, intensity, words=()):
    """효과음(A2·겹치면 A5) · 배경음악(A3/A4 번갈아 · 1초 겹쳐 바꿈) 클립. 효과음 자리·양은 place_sfx (D-371~D-374) ·
    효과음 클립마다 넣은 이유(why · 편집실 클립에 보임 · D-378)."""
    assets = editor.ASSETS

    def media_for(fn):
        mid = _mid(fn)
        if mid not in B.media:
            e = editor.media_entry(fn, "assets", mid)
            B.media[mid] = e
        return mid, B.media[mid]

    def aitem(track, mid, start, a, b, lvl, fi=0.0, fo=0.0, keys=()):
        it = {"id": editor._nid(), "track": track, "media": mid, "start": round(start, 4), "in": round(a, 4), "out": round(b, 4), "speed": 1.0, "rev": False,
              "link": None, "fx": {"level": {"v": round(lvl, 2), "k": list(keys)}} if abs(lvl) > 0.05 or keys else {}, "gain": 0.0, "fadeIn": round(fi, 3),
              "fadeOut": round(fo, 3), "mute": False}
        B.items.append(it)
        return it

    pal = SFX_PALETTE.get(snd.get("palette"), SFX_PALETTE["variety"])
    placed, talk = place_sfx(B, sig, intensity, words, pal)
    starts = sorted(a for a, _ in talk)
    n_sfx = 0
    ends = {"A2": -1.0, "A5": -1.0}
    idmap = {}
    for c in placed:
        name = c["name"]
        fn = sfxlib.ensure_sfx(name, assets)
        mid, md = media_for(fn)
        d = float(md.get("dur") or 0.5)
        t, i0 = c["start"], 0.0
        if t < 0:   # 맞출 점이 영상 처음 근처 → 소리 앞을 잘라 씀 (점은 그대로)
            t, i0 = 0.0, -t
        out_ = d
        nxt = next((a for a in starts if a > t + 0.05 + max(0.0, c["hi"] - c["start"] - i0)), None)
        if d - i0 > SFX_DUCK_MIN and nxt is not None and nxt < t + (d - i0):   # 긴 소리 꼬리는 다음 말 앞에서 끝냄 (D-372 · 줄이기는 그다음)
            out_ = max(i0 + min(d - i0, (c["hi"] - c["start"]) + 0.1), i0 + (nxt - t) - 0.03)
        tr = "A2" if t >= ends["A2"] else "A5" if t >= ends["A5"] else None
        if tr is None:
            continue
        pk, ld = sfxlib.peak_db(assets / fn), sfxlib.loud_db(assets / fn)
        lv = _sfx_level(sig, name, c["kind"], pk, file_loud=ld) + (c["lvl"] or 0.0)  # lvl: 이 소리만 더 작게(dB)
        if c["soft"]:
            lv = min(lv, _sfx_level(sig, name, c["kind"], pk, gap=SFX_OVER_TALK, file_loud=ld))
        it = aitem(tr, mid, t, i0, out_, lv, fo=min(0.15, (out_ - i0) / 3) if out_ < d - 1e-3 else 0.0,
                   keys=_sfx_duck([a for a in starts if a < t + out_ - i0], t, out_ - i0, lv))
        it["why"] = f"자동 효과음 '{name}' · {c['meta'].get('why') or SFX_ANCHOR_KO.get(c['kind'], '')} · {c['how']}"
        ends[tr] = t + (out_ - i0)
        idmap[c["k"]] = it["id"]
        n_sfx += 1
    for k, x in enumerate(B.sfx):
        if x and k not in idmap:
            B.sfx[k] = None   # 말을 가리거나 양·간격 때문에 못 놓은 소리
    # 사건 기록의 효과음 번호 → 클립 id · 소리만인 사건은 MSG 목록에 실제로 놓인 소리 이름·맞출 점 (place_sfx 가 더 짧은 소리·작은 소리로 바꾸고
    # 자리를 옮김 — 예전: 예약한 이름 '휙'·예약 시각이 남아 목록이 팡·뽁이 나는 곳을 '휙'으로 · 소리만 사건 341개 중 109개 · D-379)
    by_k = {c["k"]: c for c in placed if c["k"] in idmap}
    for e in B.events:
        if "sfx" in e["refs"]:
            ks = e["refs"].pop("sfx")
            pl = [by_k[k] for k in ks if k in by_k]
            if e["kind"] == "sfx" and pl:
                e["text"] = pl[0]["name"]
                e["t"] = round(max(0.0, pl[0]["hit"]), 2)
            e["refs"]["items"] = e["refs"].get("items", []) + [idmap[k] for k in ks if idmap.get(k)]
    # 배경음악
    # 말이 없을 때 배경음악 평균 크기 = 말 평균 크기(채널 기준) + bgmDb · 말할 때는 duck(-7~-8dB)만큼 더 줄어 보정한 말보다 약 21~23dB 작게
    # (판정 round5: 예전 bgmDb -7~-10 · duck -13~-14 는 말보다 30dB 남짓 작아 '배경음악이 거의 안 들림' · 판정 기준은 15dB 넘게 작게 ·
    #  말 크기 기준 dialogDb 는 보정 전 원본 소리라 보정한 말이 5~6dB 더 큼)
    music_db = float(sig.get("dialogDb") or -24.0) + float(snd.get("bgmDb") or -8.0)
    lvl = round(min(6.0, max(SFX_FLOOR, music_db - sfxlib.BGM_RMS_DB)), 1)
    tracks = ("A3", "A4")
    seeds, nvar = {}, {}
    for k, (a, b, mood) in enumerate(sections):
        if mood == "코믹":
            continue
        sd = seeds.setdefault(mood, _seed(sig.get("sig"), mood) % 1000)  # 같은 영상이면 후보들이 같은 음악을 씀 (파일 하나 · 비교하기 쉽게)
        mine_bgm = _user_bgm(mood, sd, assets)   # '내 배경음악' 폴더에 넣은 곡이 있으면 그것 (사용자가 고른 음악 · 앱에 싣지 않음)
        tr = tracks[k % 2]
        x0 = max(0.0, a - (0.5 if k else 0.0))
        x1 = b + (0.5 if k < len(sections) - 1 else 0.0)
        t, first = x0, True
        mine = []
        while t < x1 - 0.05:
            if mine_bgm:
                fn, L, lv_k = mine_bgm[0], mine_bgm[1], round(min(6.0, max(SFX_FLOOR, music_db - mine_bgm[2])), 1)
            else:  # 만든 음악: 같은 조·빠르기로 진행·악기를 바꾼 판을 차례로 (긴 영상에서 같은 30~50초가 10번 넘게 되풀이되지 않게)
                v = nvar.get(mood, 0)
                nvar[mood] = v + 1
                fn, L = sfxlib.ensure_bgm(mood, sd, assets, variant=v % sfxlib.BGM_VARIANTS)
                lv_k = lvl
            mid, md = media_for(fn)
            ln = min(L, x1 - t)
            it = aitem(tr, mid, t, 0.0, ln, lv_k, 1.0 if first and k else (0.4 if first else 0.0), 0.0)
            mine.append(it)
            first = False
            t += ln
        if mine:
            mine[-1]["fadeOut"] = 1.0 if k < len(sections) - 1 else 2.5
        B.event("bgm", a, mood, f"배경음악 {mood} {mmss(a)}~{mmss(b)}" + (" · 내 배경음악" if mine_bgm else ""), refs={"items": [x["id"] for x in mine]})
    if intensity == "듬뿍":  # 펀치라인 바로 앞에서 음악을 0.4초 멈춤 (웃음 포인트를 살리는 예능 편집)
        for e in [e for e in B.events if e["kind"] == "inner" and re.search(r"웃음|농담", e.get("why") or "")][:2]:
            _music_gap(B, e["t"] - 0.45, 0.45)
    return {"sfx": n_sfx, "bgm": len(sections), "musicDb": lvl}


USER_BGM = "내 배경음악"   # 작업 폴더 안 · 사용자가 넣은 배경음악 (분위기 이름 폴더 '신남'·'경쾌'·'잔잔'·'감성' 또는 파일 이름에 분위기 · 아니면 아무 분위기에나)
_RMS = {}


def user_bgm_dir():
    return core.WORK / USER_BGM


def _user_bgm_files(mood):
    d = user_bgm_dir()
    try:
        fs = sorted(p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in editor.AUDIO_EXTS)
    except OSError:
        return []
    tag = lambda p, m: m in p.parent.name or m in p.stem  # noqa: E731
    mine = [p for p in fs if tag(p, mood)]
    return mine or [p for p in fs if not any(tag(p, m) for m in sfxlib.MOODS)]


def _user_bgm(mood, seed, assets):
    """'내 배경음악' 폴더의 곡 하나 → (미디어 폴더의 파일 이름, 길이 초, 평균 크기 dBFS) · 없거나 못 읽으면 None.
    같은 영상·분위기면 늘 같은 곡 (seed) · 미디어 폴더에 한 번 복사 (원래 파일은 그대로)."""
    fs = _user_bgm_files(mood)
    if not fs:
        return None
    src = fs[seed % len(fs)]
    try:
        h = hashlib.sha1(f"{src.name}|{src.stat().st_size}|{src.stat().st_mtime_ns}".encode("utf-8")).hexdigest()[:10]
        dst = Path(assets) / f"내배경음악_{h}{src.suffix.lower()}"
        if not dst.exists():
            shutil.copy2(src, dst)
        if dst.name not in _RMS:
            r = core.run([core.ffmpeg(), "-v", "info", "-nostats", "-i", str(dst), "-af", "volumedetect", "-vn", "-f", "null", "-"])
            m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
            _RMS[dst.name] = float(m[1]) if m else sfxlib.BGM_RMS_DB
        dur = float(editor.probe(dst)["duration"] or 0)
    except (OSError, ValueError, KeyError, RuntimeError):
        return None
    return (dst.name, dur, _RMS[dst.name]) if dur >= 5.0 else None


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


CLOSE_TAIL = 90.0    # 마무리 말은 영상 끝 이만큼(초) 또는 15% 안의 것만 (레슨 중간의 '네, 감사합니다'는 마무리가 아님)


def _final_closings(moms, dur):
    """영상 끝 부분의 마무리 말 시각 (원본 · 앞의 것부터) — 몽타주·'마무리' 챕터 자리 (판정: 60분 레슨 30분쯤의 '감사합니다'에 몽타주·마무리 챕터 10개)."""
    lo = max(dur * 0.5, dur - max(CLOSE_TAIL, 0.15 * dur))
    return sorted(m["t"] for m in moms if m["kind"] == "closing" and m["t"] >= lo)


def _chapters(B, moms, topic):
    """챕터용 마커: 미리 보기 · 장 나눔 말 · 시범 묶음 · 마무리 (이름 20자 안)."""
    import upload
    pts = []
    first = next((e for e in B.events if e["kind"] == "title"), None)
    if B.main:
        pts.append((0.0, "미리 보기" if B.main[0]["start"] > 1 else f"{topic} 시작"))
        pts.append((first["t"] if first else B.main[0]["start"], f"{topic} 레슨"))
    fin = _final_closings(moms, float(B.info.get("duration") or 0))[:1]
    for m in moms:
        if m["kind"] not in ("section", "closing") or (m["kind"] == "closing" and m["t"] not in fin):  # 마무리 챕터는 하나만
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
    # 말 자막은 흐려지며 바뀌지 않고 바로 바뀜 (판정: 자막이 바뀌는 순간 반투명하게 흐려져 밝은 배경에서 안 읽힘 · 이어지는 자막마다 깜빡임)
    base["effect"] = "none"
    if c.get("box") and not c.get("karaoke"):  # 반투명 검은 상자 자막 (편집실 '박스형'과 같은 모양 · 밝은 배경에서도 읽히게 조금 진하게)
        base.update(bgOn=True, bg="#000000", bgOpacity=0.7, strokeW=10, effect="none")
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
    s["tactic"] = kinds.get("tactic", 0)
    s["text"] = (f"예능 자막 {caps} · 효과음 {s['sfx']} · 확대 {s['punch']} · 다시 보기 {s['replay']}" + (f" · 정지 {s['freeze']}" if s["freeze"] else "")
                 + (f" · 전술 그림 {s['tactic']}" if s["tactic"] else "")
                 + f" · 배경음악 {len(sections)}곡 · 길이 {mmss(total)}")
    return s


# ---------- 6. 후보 여러 개 ----------

MIN_RAW = 20.0        # 이보다 짧은 원본은 MSG 를 만들지 않음 (편집할 재료가 없음)
MIN_WORDS = 12        # 받아쓰기 낱말이 이보다 적으면 말 없는 영상으로 봄
MIN_SHORTS = 15.0     # 쇼츠 후보가 이보다 짧으면 만들지 않음 (스타일 화면 '쇼츠 길이'가 짧으면 min_shorts 로 낮춤)
MIN_SHORTS_PART = 0.75  # 짧은 '쇼츠 길이'(한 동작 10~20초 · 레퍼런스): 가장 짧게의 이만큼까지 (정리 컷·쉼 컷 뒤에 줄어드는 몫)


def min_shorts():
    """이보다 짧은 MSG 쇼츠는 만들지 않음 — 기본 MIN_SHORTS(15초) · '쇼츠 길이'가 한 동작(10~20초)·짧은 레퍼런스면 가장 짧게 × MIN_SHORTS_PART
    (BR-291 · 리뷰: 15초를 그대로 두면 10~20초 후보가 정리 컷 뒤 9~14초가 되어 고른 길이로는 쇼츠가 거의 안 나옴) · 기본·기술 하나·설명은 15초 그대로."""
    return min(MIN_SHORTS, editor.shorts_len()[0] * MIN_SHORTS_PART)


def _stop_min():
    """포인트로 끊지 않을 때 끝 다듬기(다음 순서 말 앞에서 끝냄)가 남겨야 할 원본 길이 — 기본 20초 · '쇼츠 길이'가 짧으면 그 가장 짧게."""
    return min(20.0, editor.shorts_len()[0])
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _check_input(info, segs, sig):
    """MSG 로 만들 수 없는 원본이면 MsgError (쉬운 안내): 너무 짧음 · 말이 거의 없음 · 음악 위에 잘못 들은 한 줄뿐(받아쓰기 착각)."""
    dur = float(info.get("duration") or 0)
    if dur < MIN_RAW:
        raise MsgError(f"원본이 {dur:.0f}초라 너무 짧아요. MSG 는 {MIN_RAW:.0f}초 넘는 촬영본으로 만들어 주세요")
    words = _words([x for x in segs if str(x.get("text") or "").strip()])
    if len(words) < MIN_WORDS:
        raise MsgError("이 영상에는 말소리가 거의 없어요. MSG 는 감독님이 말하는 레슨 영상에 자막·효과를 넣어요 (말 없는 경기 영상은 편집실에서 직접 편집해 주세요)")
    sp = (sig.get("tags") or {}).get("speech") or []
    if sp:  # 소리 모델이 말소리를 거의 못 들었는데 받아쓰기만 있으면 (음악 위 '시청해 주셔서 감사합니다' 같은 착각)
        step = float(sig.get("tagStep") or 0.48)
        talk = sum(step for v in sp if v >= 0.5)
        if talk < 5.0 and len(words) < 25:
            raise MsgError("말소리를 거의 찾지 못했어요 (음악이나 잡음 위의 받아쓰기는 틀린 것일 수 있어요). 감독님 말이 들리는 영상으로 만들어 주세요")


def _ai_lines(name, moms, segs, log, junk=()):
    """클로드 재미 자막 (켰을 때만) → ({moment_key: {kind, text}}, 더할 'talk' 순간). 못 쓰면 ({}, []) — 정해 둔 문구로 계속."""
    import claude_cli
    import msgwrite
    if claude_cli.status().get("state") != "ready":
        log("  클로드 로그인이 안 돼 있어 재미 자막은 정해 둔 문구로 만들어요 (스튜디오의 '클로드 계정으로 쓰기')")
        return {}, []
    core.set_progress(label=LABEL, item=name, pct=66, detail="클로드가 재미 자막을 쓰는 중")
    try:
        return msgwrite.write(str(core.adir(name) / "msg_ai_lines.json"), moms, lines_of(segs), _title_of(name), cancel=editor.CANCEL, log=log,
                              junk=junk)
    except claude_cli.ClaudeError as e:
        if e.kind == "cancel":
            raise MsgError("MSG 후보 만들기를 멈췄어요") from None
        log(f"  재미 자막은 정해 둔 문구로 만들어요 · {e}")
    except msgwrite.WriteError as e:
        log(f"  {e}")
    return {}, []


def build_variants(name, specs, intensity="보통", kinds=("long",), log=print, proofread=False, writer=False, first=0, windows=None):
    """스타일 여러 개(최대 3) × 형식 → 새 편집본 후보 + 필요한 미디어. 같은 원본·스타일·양이면 늘 같은 결과.
    intensity: '담백'·'보통'·'듬뿍' 하나 또는 '모두' (스타일 하나를 양 3단계로 · 한 번에 비교).
    proofread: '클로드로 자막 오타 고치기'를 켰을 때 (사용자 클로드 계정으로 대사 글만 보냄) · writer: '클로드로 재미 자막 쓰기'를 켰을 때.
    first: 이미 있는 MSG 후보 수 (후보 글자를 이어서 D, E… · 두 번째로 만들어도 'MSG 후보 A'가 또 생기지 않게).
    지금 받아쓰기로 만든 자막은 결과의 captions 하나로 (편집실이 프로젝트 자막에 반영 · 예전처럼 후보마다 같은 자막(msgCaps)을 붙이면
    60분 원본 세 후보에서 결과가 3MB 가까이 됨).
    captionsOld: MSG 가 다시 듣기 전 받아쓰기로 만든 자막 (프로젝트 자막을 사용자가 고쳤는지 보려고 · 다시 들은 적이 없으면 None).
    windows: 쇼츠 구간 목록 (끝까지 자동으로 · editor.recommend 쇼츠) — 주면 쇼츠 형식은 구간마다 하나씩 (결과 편집본 seq['msgWin'] = 그 번호).
    없으면 쇼츠는 하나 (MSG 가 고른 구간 · v2.16.0 그대로).
    결과 notes: 만들지 않은 후보와 그 까닭 [{label, why, win}] (쇼츠가 min_shorts() 초 안 될 때 · 편집실 알림에 보임 · D-267)."""
    if intensity != "모두" and intensity not in INTENSITY:
        raise ValueError("MSG 양은 담백·보통·듬뿍 중에서 골라 주세요")
    specs = [s for s in (specs or []) if isinstance(s, dict) and s.get("name")][:3]
    if not specs:
        raise ValueError("스타일을 하나 이상 골라 주세요")
    levels = list(INTENSITY) if intensity == "모두" else [intensity]
    if len(levels) > 1:
        specs = specs[:1]
    info = editor.media_info(name)
    _check()
    ensure_transcript(name, log, proofread)
    sig = signals(name, log)
    segs = respread(editor._segments_of(name), sig.get("blobs"))
    _check_input(info, segs, sig)
    if sig.get("voicePk") is not None and float(sig["voicePk"]) < QUIET_VOICE:
        log(f"  말소리가 아주 작아요 ({sig['voicePk']:.0f} dBFS) · 효과음·배경음악도 그만큼 작게 맞추고 내보낼 때 전체를 키워요 (마이크를 가까이 두면 더 깨끗해요)")
    caps = editor._captions_of(segs, info)
    old = None
    bak = core.adir(name) / ORIG_TRANSCRIPT
    if bak.exists():
        try:
            old = editor._captions_of([x for x in json.loads(bak.read_text(encoding="utf-8")) if str(x.get("text") or "").strip()], info)
        except (OSError, ValueError):
            old = None
    moms = moments(sig, segs)
    ai = {}
    if writer:
        ai, talk = _ai_lines(name, moms, segs, log, sig.get("junk") or ())
        moms = sorted(moms + talk, key=lambda m: m["t"])
    _check()
    seqs, media, notes = [], {}, []
    jobs = [(spec, lv) for spec in specs for lv in levels]
    n = 0
    for k, (spec, lv) in enumerate(jobs):
        _check()
        st = resolve(spec)
        for fmt, wi in [(f, w) for f in kinds for w in (range(len(windows)) if f == "shorts" and windows else [None])]:
            core.set_progress(label=LABEL, item=name, pct=70 + int(28 * k / len(jobs)), detail=f"후보 만드는 중 ({k + 1}/{len(jobs)})"
                              + (f" · 쇼츠 {wi + 1}/{len(windows)}" if wi is not None else ""))
            label = f"MSG 후보 {LETTERS[(first + n) % len(LETTERS)]} · {st['label']} · {lv}" + (" · 쇼츠" if fmt == "shorts" else "") \
                + (f" {wi + 1}" if wi is not None else "")
            seed = _seed(sig["sig"], st["label"], lv, fmt) + (wi or 0)
            seq, md = compile_seq(name, info, sig, segs, moms, st, lv, fmt, seed, label, log, caps, ai, windows[wi] if wi is not None else None)
            _check()
            if fmt == "shorts" and editor.seq_total(seq) < min_shorts():
                # (초는 내림 — 14.6초를 '15초뿐이에요 (15초 넘어야 해요)'로 쓰지 않게) · 결과 notes 로도 돌려줌 (편집실 알림·끝까지 자동으로 · D-267)
                why = f"쇼츠 후보는 만들지 않았어요 · 재미 순간이 모인 구간이 {int(editor.seq_total(seq))}초뿐이에요 ({min_shorts():g}초 넘어야 해요)"
                log("  " + why)
                notes.append({"label": label, "why": why, "win": wi})
                continue
            if wi is not None:
                seq["msgWin"] = wi
            seqs.append(seq)
            for m in md:
                media[m["id"]] = m
            log(f"  {label} · {seq['msg']['summary']['text']}")
        n += 1
    _check()  # 마지막 후보를 만드는 동안 멈추기(✕)를 눌렀으면 후보를 돌려주지 않음 (편집실이 넣지 않게)
    if not seqs:
        raise MsgError("MSG 후보를 만들 재료가 모자라요 (재미 순간이 있는 구간이 너무 짧아요)")
    return {"sequences": seqs, "media": list(media.values()), "moments": len(moms), "captions": caps, "captionsOld": old, "notes": notes}
