"""썸네일 퀄리티 시험 작업 폴더 만들기 (개발용 · 배포 안 됨): python3 tests/e2e/mkwork.py <작업폴더> <시험 영상 폴더> [--with-thumbtest <THUMBTEST01 작업폴더>] [--with-asr]
시험 영상은 tests/e2e/mkstock.py 로 다시 만들 수 있음 (원본 목록·라이선스는 그 파일 머리말).
--with-asr (판정 4회차): 지어낸 대사 대신 실제 받아쓰기(tests/fixtures/thumb_asr — Whisper 로 받아쓴 레슨 말, 문구 규칙을 만든 사람이 쓰지 않은 글)를
  스톡 영상에 붙인 묶음 THQASR00001~3 을 더함 (장면은 스톡과 같고 문구만 다름 — 실제 받아쓰기에서 문구 품질을 따로 봄).
시험 영상(THQSTOCK001~006)은 라이선스가 허락된 스톡 클립을 이어 붙인 것 — Mixkit 무료 라이선스 축구·풋살 클립, Wikimedia Commons
'cienfuegos.webm'(CC BY 3.0), 'codigo_part.webm'(퍼블릭 도메인 180~300초). 저장소에는 영상을 넣지 않는다(용량) — 이름만 맞추면 됨:
  20261007_THQSTOCK001_풋살 경기 하이라이트 해설.mp4 (1280×720) · 002_1대1 돌파 이렇게 하세요 · 003_유소년 드리블 훈련 (720p) ·
  004_발바닥 드래그 기본기 (1080p) · 005_코치 인터뷰 실전 전술 (1080p) · 006_슈팅 연습 세로 영상 (1080×1920).
영상을 videos/ 에 연결하고, 받아쓰기·편집점 분석(지어낸 시험용 대사)을 analysis/ 에 넣는다. 판정 3회차: 시험 자산이 지워져 재현이 안 됐음 → 저장소로 옮김."""
import json, os, shutil, sys
from pathlib import Path
LINES = {
 "THQSTOCK001": [(2, "오늘은 실제 풋살 경기에서 나온 장면을 같이 볼게요"), (9, "여기서 오프더볼 움직임이 진짜 중요해요"), (17, "공간을 보고 먼저 움직이는 선수가 이겨요"),
                 (28, "수비가 따라오면 반대쪽으로 빠지세요"), (41, "이 장면 대박이에요 골키퍼가 완전히 속았어요"), (55, "패스 앤 무브 이것만 알면 플랩 레벨업 바로 됩니다"),
                 (70, "경기 끝나고 감독님 인터뷰도 들어볼게요"), (88, "오프더볼의 비밀은 시야예요")],
 "THQSTOCK002": [(1, "1대1 돌파 이렇게 하세요"), (7, "수비 앞에서 속도를 줄였다가 확 치고 나가는 거예요"), (15, "상체 페인트로 수비를 먼저 속이세요"),
                 (24, "이거 진짜 중요해요 첫 터치를 수비 반대쪽으로"), (33, "이것만 알면 1대1 무조건 이겨요")],
 "THQSTOCK003": [(1, "오늘은 콘 드리블 훈련을 해볼게요"), (10, "발 안쪽 바깥쪽을 번갈아 쓰세요"), (22, "고개 들고 드리블하는 습관이 제일 중요해요"),
                 (35, "아이들도 일주일만 하면 확 달라져요"), (47, "1분만 투자하세요 드리블 실력이 늘어요")],
 "THQSTOCK004": [(1, "풋살 기본기 중에 제일 중요한 발바닥 드래그"), (12, "발바닥으로 공을 끌어서 수비를 속이는 기술이에요"), (25, "슛하는 척하다가 드래그로 방향을 바꾸세요"),
                 (40, "실수하는 분들이 많은데 무게중심이 뒤에 있으면 안 돼요"), (56, "이거 하나만 제대로 해도 수비가 못 따라와요"), (70, "꿀팁 하나 더 드릴게요")],
 "THQSTOCK005": [(2, "감독으로서 제일 강조하는 건 수비 전환이에요"), (18, "공을 뺏기면 3초 안에 압박하세요"), (35, "프레스를 어떻게 거는지가 실력 차이예요"),
                 (55, "선수들한테 늘 말하는 비밀이 있어요"), (75, "공간을 먼저 보는 습관"), (95, "이게 국가대표와 동호인의 차이예요")],
 "THQSTOCK006": [(1, "슈팅 이렇게 차면 무조건 들어가요"), (8, "디딤발 위치가 핵심이에요"), (16, "발등 정확히 맞추세요"), (24, "골키퍼 반대쪽 구석을 보세요"), (31, "대박 들어갔어요")],
}
PEAKS = {"THQSTOCK001": [41.5, 60, 92], "THQSTOCK002": [9, 26], "THQSTOCK003": [24, 40], "THQSTOCK004": [27, 58], "THQSTOCK005": [36, 96], "THQSTOCK006": [10, 32]}
work = Path(sys.argv[1])
V = Path(sys.argv[2])
for d in ("videos", "analysis", "out", "thumbnails"):
    (work / d).mkdir(parents=True, exist_ok=True)
for f in sorted(V.glob("*.mp4")):
    dst = work / "videos" / f.name
    if not dst.exists():
        os.symlink(f.resolve(), dst)
    vid = f.name.split("_")[1]
    a = work / "analysis" / f.stem
    a.mkdir(parents=True, exist_ok=True)
    segs = [{"start": t, "end": t + 3.5, "text": s} for t, s in LINES.get(vid, [])]
    (a / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    (a / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": [{"time": t, "rms_db": -4} for t in PEAKS.get(vid, [])]}), encoding="utf-8")
ASR = {"THQASR00001": ("퍼스트 터치 레슨", "MSGRAW01_퍼스트 터치 레슨", "THQSTOCK003"), "THQASR00002": ("패스 앤 무브 드릴", "MSGRAW02_패스 앤 무브 드릴", "THQSTOCK001"),
       "THQASR00003": ("퍼스트 터치 강의", "CAPTEST001_퍼스트 터치 강의", "THQSTOCK002")}
if "--with-asr" in sys.argv:
    fx = Path(__file__).resolve().parents[1] / "fixtures" / "thumb_asr"
    for vid, (title, fixture, base) in ASR.items():
        src = next(iter(sorted(V.glob(f"*_{base}_*.mp4"))), None)
        if not src:
            continue
        n = f"20261007_{vid}_{title}"
        dst = work / "videos" / (n + ".mp4")
        if not dst.exists():
            os.symlink(src.resolve(), dst)
        a = work / "analysis" / n
        a.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fx / f"{fixture}.json", a / "transcript.json")
        (a / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": []}), encoding="utf-8")
if "--with-thumbtest" in sys.argv:
    src = Path(sys.argv[sys.argv.index("--with-thumbtest") + 1])
    n = "20200320_THUMBTEST01_썸네일 테스트"
    if not (work / "videos" / (n + ".mp4")).exists():
        shutil.copy2(src / "videos" / (n + ".mp4"), work / "videos" / (n + ".mp4"))
    a = work / "analysis" / n
    a.mkdir(parents=True, exist_ok=True)
    for fn in ("transcript.json", "analysis.json"):
        shutil.copy2(src / "analysis" / n / fn, a / fn)
print("ok", work)
