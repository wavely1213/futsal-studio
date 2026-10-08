"""컷이 많은 롱폼 내보내기: 같은 원본에서 이어지는 짧은 구간들을 ffmpeg 하나로 묶어 만듦.

왜: 구간마다 ffmpeg 를 새로 띄우면 컷마다 (프로세스·x264 준비 + 긴 원본 파일 열기 + 앞 키프레임부터 다시 풀기)가 더 듦
(30분 1080p 원본 · 150컷 측정: 컷당 약 0.7~0.9 CPU초 · 대부분은 파일 열기와 키프레임부터 풀기).
묶으면 원본을 한 번 열고 앞에서부터 이어서 풂 → 다시 찾아가기·다시 풀기가 없음.

같은 그림이 나오게: 구간마다 만드는 필터 그래프(editor._build_segment)를 그대로 씀.
구간마다 ffmpeg 가 하던 '-ss 시작 -t 길이' (정확한 찾아가기 = 시작보다 앞 프레임 버리기)만 같은 뜻의 trim 으로 바꿔
원본 하나를 split 으로 나눠 줌. 그 밖에 바꾸는 곳은 둘뿐:
- split 앞에 원본 픽셀 형식을 못박음 (format=원본 형식): split 은 나가는 길 모두에 한 형식을 강요해서, 한 구간이라도
  색보정(rgb24)이 있으면 다른 구간까지 rgb 를 거쳐 색이 살짝 달라졌음
- 자막은 묶음 끝에서 한 번만 (자막 필터 하나가 글꼴을 따로 불러 구간마다 약 25MB · 15컷이면 ffmpeg 하나 1.2GB):
  구간 그래프 끝이 'settb=1/fps,setpts=PTS+시작프레임,subtitles=…' 이라, 이어 붙인 뒤 'N+첫 시작 프레임' 이 같은 시각 = 같은 자막 그림
나머지는 라벨 이름만 바꾸고 마지막에 concat 으로 잇기만 함.

묶는 조건 (아니면 예전처럼 구간 하나씩):
- 구간 입력이 원본 영상 하나뿐 (겹치기·사진·위 트랙·전환이 없는 구간) · 불투명도 키프레임(sendcmd) 없음
- HDR(색 바꾸기 zscale) 구간은 안 묶음: 갈래마다 4K 색 바꾸기 상태가 따로라 8컷 묶음 ffmpeg 하나가 약 2GB (8GB 노트북에서 위험)
- 원본 픽셀 형식을 알아낸 영상만 (위 format 못박기)
- 같은 원본 · 다음 구간이 앞 구간들이 쓴 범위 끝(입력 꼬리 0.3초 포함) 뒤에서 시작 — 다시 보기처럼 같은 범위를 또 쓰면 split 이
  그 프레임을 concat 앞에 다 쌓아 메모리가 커짐 (2컷 1080p 941MB vs 따로 154MB) · 그 사이가 GAP_SEC 이하 (그 이상은 찾아가는 게 쌈)
- 구간 하나는 MAX_SEG_SEC 이하 · 한 묶음은 MAX_CUTS 구간 · MAX_SEC 초 이하 (동시 처리·진행률·메모리)
"""
import re

import core

MAX_CUTS = 8      # 묶음 하나의 구간 수 (구간마다 필터 상태가 따로라 ffmpeg 하나 메모리가 구간당 약 6MB 늘어남 · 8개 484MB vs 1개 435MB)
MAX_SEC = 30.0
MAX_SEG_SEC = 12.0
GAP_SEC = 3.0
OVERLAP_SEC = 0.4  # 구간 입력 꼬리(0.3초)·반 프레임 일찍 찾아가기만큼은 겹쳐도 됨 (이어 자른 컷)
_LABEL = re.compile(r"\[([A-Za-z]+\d+)\]")
_SUB = re.compile(r",settb=1/(\d+),setpts=PTS\+(\d+),subtitles=([^,;\[\]]+),setpts=PTS-STARTPTS(?=\[[A-Za-z]+\d+\]\s*$)")
_PIX = {}


def pix_fmt(path):
    """원본 영상의 픽셀 형식 (예: yuv420p) · 모르면 None (그 원본은 안 묶음)."""
    if path not in _PIX:
        try:
            err = core.run([core.ffmpeg(), "-hide_banner", "-i", str(path)]).stderr or ""
            m = re.search(r"Stream #0:\d+[^\n]*?: Video: [^,\n]+, ([a-z0-9_]+)", err)
            _PIX[path] = m.group(1) if m else None
        except Exception:  # noqa: BLE001
            _PIX[path] = None
    return _PIX[path]


def single_input(args):
    """구간 입력이 '[-hwaccel X] -ss 시작 -t 길이 -i 원본' 하나뿐이면 (시작, 길이, 원본) · 아니면 None."""
    if len(args) == 8 and args[0] == "-hwaccel":
        args = args[2:]
    if len(args) == 6 and args[0] == "-ss" and args[2] == "-t" and args[4] == "-i":
        try:
            return float(args[1]), float(args[3]), args[5]
        except ValueError:
            return None
    return None


def plan(builds, fc_texts, fps, pix=None):
    """builds[k] = (args, final, n) · fc_texts[k] = 구간 k 필터 그래프 글 → 묶음 목록 [[k, ...], ...] (순서대로 · 모든 k 한 번씩).
    pix(원본 경로) → 픽셀 형식 또는 None (None 이면 그 원본은 안 묶음 · pix 를 안 주면 모두 안다고 봄 = 시험용)."""
    units, cur = [], []
    end = 0.0  # 지금 묶음이 원본에서 쓴 가장 늦은 시각

    def ok(k):
        args, _, n = builds[k]
        si = single_input(args)
        t = fc_texts[k]
        return si is not None and args[0] != "-hwaccel" and n <= MAX_SEG_SEC * fps and "sendcmd" not in t and "@" not in t \
            and t.count("[0:v]") == 1 and "zscale" not in t and (pix is None or pix(si[2]) is not None)

    for k in range(len(builds)):
        if not ok(k):
            if cur:
                units.append(cur)
                cur = []
            units.append([k])
            continue
        s, t, p = single_input(builds[k][0])
        if cur:
            pp = single_input(builds[cur[-1]][0])[2]
            tot = sum(builds[j][2] for j in cur) + builds[k][2]
            if p == pp and s >= end - OVERLAP_SEC and s - end <= GAP_SEC and len(cur) < MAX_CUTS and tot <= MAX_SEC * fps:
                cur.append(k)
                end = max(end, s + t)
                continue
            units.append(cur)
        cur = [k]
        end = s + t
    if cur:
        units.append(cur)
    return units


def merge(ks, builds, fc_texts, fps, pix=None):  # fc_texts: {k: 글} 또는 목록
    """묶음 ks → (ffmpeg 입력 인자, 필터 그래프 글, 마지막 라벨, 프레임 수). 구간들 그래프는 라벨 이름만 바꿔 그대로 씀.
    pix: 원본 픽셀 형식 → split 앞에 못박음 (구간마다 ffmpeg 하나일 때와 같은 변환 경로)."""
    ins = [single_input(builds[k][0]) for k in ks]
    s0 = ins[0][0]
    end = max(s + t for s, t, _ in ins)
    fc = ["[0:v]%ssplit=%d%s" % (f"format={pix}," if pix else "", len(ks), "".join(f"[in_{k}]" for k in ks))]
    finals = []
    # 자막은 묶음 끝에서 한 번만 — 구간 끝 줄이 모두 'settb=1/fps,setpts=PTS+시작프레임,subtitles=…,setpts=PTS-STARTPTS' 일 때만.
    # 구간 k 의 j 번째 프레임 시각 = (시작 프레임 + j)/fps → 이어 붙인 뒤 '첫 시작 프레임 + N' 과 같은 시각 = 같은 자막 그림
    subs = [_SUB.search(fc_texts[k]) for k in ks]
    hoist = all(subs) and len({m.group(3) for m in subs}) == 1 and all(m.group(1) == str(fps) for m in subs)
    for k, (s, t, _), m in zip(ks, ins, subs):
        txt = fc_texts[k]
        if hoist:
            txt = txt[:m.start()] + txt[m.end():]
        txt = _LABEL.sub(lambda m: f"[{m.group(1)}_{k}]", txt)
        # 구간마다 하던 정확한 찾아가기(-ss s -t t: s 앞·s+t 뒤 프레임 버림) = 이어서 푼 원본에서 같은 범위만 남기기
        txt = txt.replace("[0:v]", f"[in_{k}]trim=start={s - s0:.6f}:end={s + t - s0:.6f},", 1)
        fc.append(txt)
        finals.append(f"[{builds[k][1]}_{k}]")
    n = sum(builds[k][2] for k in ks)
    out = f"run_{ks[0]}"
    # 구간들은 각각 0초부터 정확히 n 프레임 → 이어 붙이고 시각을 프레임 번호로 다시 매김 (출력 -r 이 프레임을 더하거나 빼지 않게)
    tail = f",setpts=N+{subs[0].group(2)},subtitles={subs[0].group(3)},setpts=PTS-STARTPTS" if hoist else ""
    fc.append("".join(finals) + f"concat=n={len(ks)}:v=1:a=0,settb=1/{fps},setpts=N{tail}[{out}]")
    return ["-ss", f"{s0:.6f}", "-t", f"{end - s0 + 0.05:.3f}", "-i", ins[0][2]], ";\n".join(fc), out, n


ENABLED = True  # 문제가 생기면 False → 예전처럼 구간마다 ffmpeg 하나


def units(builds, tmp, fps):
    """export 용: 구간들(이미 tmp 에 fc{k}.txt 를 써 둠) → 만들 단위 목록 [[k, ...]]."""
    if not ENABLED or len(builds) < 2:
        return [[k] for k in range(len(builds))]
    texts = [(tmp / f"fc{k}.txt").read_text(encoding="utf-8") for k in range(len(builds))]
    return plan(builds, texts, fps, pix_fmt)


def unit_args(ks, builds, tmp, fps):
    """단위 하나 → (입력 인자, 마지막 라벨, 프레임 수, 필터 그래프 파일 이름)."""
    if len(ks) == 1:
        args, final, n = builds[ks[0]]
        return args, final, n, f"fc{ks[0]}.txt"
    texts = {k: (tmp / f"fc{k}.txt").read_text(encoding="utf-8") for k in ks}
    args, txt, final, n = merge(ks, builds, texts, fps, pix_fmt(single_input(builds[ks[0]][0])[2]))
    name = f"fcrun{ks[0]}.txt"
    (tmp / name).write_text(txt, encoding="utf-8")
    return args, final, n, name
