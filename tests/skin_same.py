"""기본·은평 스타일 쇼츠가 풋살사관학교 스킨(D-520~D-524) 전과 같은지 보는 지문 — tests/test_academy_skin.py SameAsBeforeTest 가 씀.

실행: python3 tests/skin_same.py <저장소 폴더> → 표준 출력에 JSON {이름: sha256} (그 폴더의 코드로 셈 · 이 파일은 그 코드의 옛 함수만 씀).
tests/fixtures/academy_same.json 은 스킨 기능 전 코드(v2.25.0 · main 9b02131)로 이렇게 만든 값이다 — 기본·은평 배치를 일부러 바꾸는 다른 작업이면 그 코드로 다시 만든다.
지문에 넣는 것: 가편집 쇼츠(기본 · 은평 · 은평 + 덧붙임 · 띠 그림·로고 끔 · 배운 스타일 있음/없음) · 내보내기 구간 필터 그래프와 입력(배치 6가지 × 1080·720·4K) ·
ASS(기본·은평 편집본) · MSG 쇼츠(기본 · 은평 · 은평 + 덧붙임 × 듬뿍·담백 · 프리셋 3개 · tests/make_msg_fixture 영상).
우연한 값(id · 임시 폴더 · 바탕 그림·MSG 미디어 파일 지문 이름 · 영상 파일마다 다른 MSG 씨앗)과 이 PC 에 따라 다른 것(KFA 글꼴 · 얼굴·소리·선수 찾기 모델)은
빼거나 고정한다."""
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(sys.argv[1]).resolve() if __name__ == "__main__" else None
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rough_e12.json"
NAME = "20261008_MSGRAW01_퍼스트 터치 레슨.mp4"


def digests(root):
    sys.path.insert(0, str(root))
    import core
    import editor
    import style
    tmp = Path(tempfile.mkdtemp(prefix="스킨 전후 "))
    work = tmp / "작업"
    dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    n = {"k": 0}

    def nid():
        n["k"] += 1
        return f"id{n['k']:05d}"
    patches = [mock.patch.multiple(core, **dirs), mock.patch.object(style, "STYLES", work / "styles"),
               mock.patch.object(editor, "_sound_onsets", lambda nm, w: []), mock.patch.object(editor, "_nid", nid),
               mock.patch.object(editor, "ep_font", lambda wait=True: None)]
    for p in patches:
        p.start()
    try:
        fx = json.loads(FIXTURE.read_text(encoding="utf-8"))["videos"]["MSGRAW01"]
        d = core.adir(NAME)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(fx["transcript"], ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps(fx["analysis"], ensure_ascii=False), encoding="utf-8")
        info = {"duration": fx["duration"], "width": 1920, "height": 1080, "fps": 30.0}
        rough = []
        for lay, extras, pics in (("", False, True), ("eunpyeong", False, True), ("eunpyeong", True, True), ("eunpyeong", True, False)):
            style.set_shorts_layout(lay)
            style.set_card_extras(series=extras, long_line=extras)
            style.set_card_pics(band=pics, logo=pics)
            for st in (None, {"captionPos": "top", "captionColor": "#1A1A1A"}, {"captionPos": "bottom"}):
                n["k"] = 0
                rough.append(editor.auto_sequences(NAME, info, st, ("shorts",)))
        seg = []
        md = {"main": {"id": "main", "kind": "video", "src": "videos", "file": "x.mp4", "dur": 10, "w": 1920, "h": 1080, "fps": 30.0}}
        style.set_card_pics(band=True, logo=True)
        lays = [editor.BOX_LAYOUT, editor.EP_LAYOUT, editor.ep_layout(), dict(editor.ep_layout(), vpos=0.4807),
                {"mode": "blur", "bar": "#000000", "zoom": 1.35, "vpos": 0.5, "cropTop": 0.0, "cropBottom": 0.0}, {"mode": "fill"}]
        for lay in lays:
            for W, H in ((1080, 1920), (720, 1280), (2160, 3840)):
                n["k"] = 0
                a, b = editor._pair(0, 2, start=0), editor._pair(3, 5, start=2)
                b[0]["fx"] = {"scale": {"v": 140, "k": []}}
                q = editor._new_seq("쇼츠", "shorts", a + b, layout=lay, captionStyle=dict(editor.SHORTS_STYLE),
                                    trans=[{"id": "x", "track": "V1", "a": a[0]["id"], "b": b[0]["id"], "type": "dissolve", "dur": 0.6, "align": "center"}])
                with tempfile.TemporaryDirectory() as t, mock.patch.object(editor, "media_path", lambda f, s: Path(t) / "x.mp4"):
                    (Path(t) / "x.mp4").write_bytes(b"0")
                    for k, (f0, f1) in enumerate(((0, 30), (45, 75), (100, 130))):
                        args, final, cnt = editor._build_segment(q, md, W, H, 30, f0, f1, editor.valid_trans(q), Path(t), k)
                        txt = (Path(t) / f"fc{k}.txt").read_text(encoding="utf-8")
                        norm = lambda s: re.sub(r"epbg_[0-9a-f]{16}\.png", "epbg_X.png", s.replace(t, "TMP"))  # noqa: E731
                        seg.append([[norm(x) for x in args], final, cnt, norm(txt)])
        ass = []
        for qs in (rough[0], rough[3], rough[6]):
            for q in qs[:2]:
                proj = dict(q, captions=[{"id": "c1", "start": 0.5, "end": 3.0, "text": "공을 멈추는 게 아니라 다음 동작을 준비하는 거예요"},
                                         {"id": "c2", "start": 3.2, "end": 5.0, "text": "트래핑할 때는"}])
                ass.append(editor.build_ass(proj, 1080, 1920))
        h = lambda x: hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()  # noqa: E731
        out = {"rough": h(rough), "segments": h(seg), "ass": h(ass), "counts": [len(rough), sum(len(x) for x in rough), len(seg), len(ass)]}
    finally:
        for p in reversed(patches):
            p.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    out.update(msg_digest())
    return out


def msg_digest():
    """MSG 쇼츠 지문 (MsgWork 시험 영상 · 얼굴·소리 모델은 MsgWork 가 끔 · 선수 찾기는 끔 · 씨앗은 영상 지문을 빼고)."""
    import editor
    import msg
    import style
    from make_msg_fixture import MsgWork
    n = {"k": 0}

    def nid():
        n["k"] += 1
        return f"id{n['k']:05d}"

    def seed(*parts):
        return int(hashlib.sha1("|".join(map(str, parts[1:])).encode("utf-8")).hexdigest()[:8], 16)
    w = MsgWork()
    patches = [mock.patch.object(editor, "_nid", nid), mock.patch.object(editor, "ep_font", lambda wait=True: None), mock.patch.object(msg, "_seed", seed),
               mock.patch.object(msg, "_track_play", lambda *a, **k: [])]
    for p in patches:
        p.start()
    try:
        res = []
        specs = [{"kind": "preset", "name": k} for k in msg.PRESETS][:3]
        for lay, ex in (("", False), ("eunpyeong", False), ("eunpyeong", True)):
            style.set_shorts_layout(lay)
            style.set_card_extras(series=ex, long_line=ex)
            for inten in ("듬뿍", "담백"):
                n["k"] = 0
                qs = msg.build_variants(w.name, specs, inten, ("shorts",), lambda m: None)["sequences"]
                res.append(re.sub(r"msg_[0-9a-f]{8}", "msg_X", json.dumps(qs, ensure_ascii=False, sort_keys=True).replace(str(w.tmp), "TMP")))
        return {"msg": hashlib.sha256("\n".join(res).encode("utf-8")).hexdigest(), "msgCounts": [len(res), sum(r.count('"plan":') for r in res)]}
    finally:
        for p in reversed(patches):
            p.stop()
        w.close()


if __name__ == "__main__":
    print(json.dumps(digests(ROOT)))
