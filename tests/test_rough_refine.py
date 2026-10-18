"""자동 가편집 다듬기 (BR-290~BR-294 · D-480~D-484) — 저장소 폴더에서 python3 -m unittest tests.test_rough_refine

- 말 자막 (BR-290): 배운 자막 비율이 25% 밑이어도 쇼츠는 늘 켬 · 롱폼은 '작게 켜기'(LONG_SMALL) 기본 · 스타일 카드에서 켜기·작게 켜기·끄기
  (스타일 파일 longCaps · 다시 배워도 그대로 · 형식을 나눠 배운 스타일도) · 진짜 app.Handler 길 · 원격 길 표
- 쇼츠 길이 (BR-291): 기본 지금처럼 20~55초 · 한 동작 10~20 · 기술 하나 30~60 · 설명 60~120 · 레퍼런스(첫 가편집 스타일의 쇼츠 길이) ·
  editor.recommend 가 그 길이로 · 길 GET/POST · 스타일 목록에 실림
- 티저 (BR-292): 제목은 그 장면에서 하는 말 · 말 없는 시범이면 영상 주제 · 미리 보기 클립은 noCaps (말 자막·SRT 에 그 말이 없음) · '미리보기' 표시
- 강조 큰 글씨 (BR-293): '핵심·중요·무조건·절대' 말의 핵심 구절 먼저 · 인사·끝인사는 안 띄움 · 쇼츠 1~2개 (위 큰 제목·말 자막 줄과 안 겹침 ·
  은평 스타일 쇼츠는 그대로)
- 확대 컷 (BR-294): 얼굴 상자 기준점(위 1/3 · 머리 위 여유 · 0~1 안) · 원본이 이어진 확대 조각은 한 기준점 · 얼굴 모델·영상이 없으면 가운데 ·
  48장 한도 · 시범 컷은 확대 안 함 · 강조 글씨 자리 셈도 그 기준점으로. 영상·인터넷은 쓰지 않음 (얼굴 찾기는 가짜)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import style  # noqa: E402

INFO = {"duration": 240.0, "width": 1920, "height": 1080, "fps": 30.0}


class Base(unittest.TestCase):
    name = "퍼스트 터치 레슨.mp4"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="가편집 다듬기 "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        work = self.tmp / "작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        ps = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(style, "STYLES", work / "styles"),
                                                                         mock.patch.object(editor, "_sound_onsets", lambda n, w: [])]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def put(self, segs, name=None):
        name = name or self.name
        d = core.adir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": [], "loud_peaks": []}), encoding="utf-8")
        return name

    def lesson(self, n=3):
        """강의 받아쓰기 (인사 → 설명·콕 집은 말 → 끝인사) × n."""
        lines = ["안녕하세요. 여러분. 오늘은 퍼스트 터치에 대해서 알아볼게요.", "자 첫 번째 포인트는 디딤발이에요.",
                 "인사이드 패스할 때 공을 앞에 두세요.", "디딤발 위치가 진짜 핵심이에요.", "트래핑을 연습해 볼 건데요 공을 잘 보세요.",
                 "두 번째 포인트는 시선이에요.", "무조건 발목을 고정하고 차세요.", "퍼스트 터치 할 때 몸을 열어요.",
                 "세 번째는 마무리예요.", "오늘은 여기까지 할게요. 감사합니다."]
        segs, t = [], 1.0
        for x in lines * n:
            segs.append({"start": round(t, 2), "end": round(t + 4.0, 2), "text": x})
            t += 6.5
        return self.put(segs), {"duration": t + 2.0, "width": 1920, "height": 1080, "fps": 30.0}


# ---------- 말 자막 (BR-290) ----------

class CaptionModeTest(Base):
    def prof(self, ratio, **kw):
        return dict({"pauseP75": 0.4, "zoomCutsPerMin": 0.0, "medianShot": 3.0, "avgShot": 3.2, "cutsPerMin": 18.0, "avgZoom": 1.0,
                     "captionRatio": ratio, "captionPos": "bottom",
                     "captionColor": "#FFFFFF", "lufs": -14.0, "curve3": [3, 3, 3]}, **kw)

    def test_params(self):
        self.assertEqual(style._params(self.prof(0.07))["longCaps"], "small")   # 레슨 영상으로 배운 스타일 (재현: 0.07 → 예전엔 끔)
        self.assertFalse(style._params(self.prof(0.07))["captions"])            # 설명·점수용 값은 그대로
        self.assertEqual(style._params(self.prof(0.65))["longCaps"], "on")
        self.assertEqual(style._params(self.prof(0.65, longCaps="off"))["longCaps"], "off")
        self.assertEqual(style._params(self.prof(0.07, longCaps="nope"))["longCaps"], "small")
        self.assertEqual(style._with_user({"a": 1}, {"longCaps": "off", "tempoOn": False}), {"a": 1, "longCaps": "off", "tempoOn": False})

    def test_auto_sequences(self):
        name, info = self.lesson(1)
        def by(st):
            return {q["format"]: q for q in editor.auto_sequences(name, info, st)}
        q = by({"captions": False})  # 예전 스타일 값(longCaps 없음)도 롱폼 작게 · 쇼츠 켬
        self.assertTrue(q["long"]["captionsOn"])
        self.assertEqual(q["long"]["captionStyle"]["size"], editor.LONG_SMALL)
        self.assertTrue(q["shorts"]["captionsOn"])
        q = by({"captions": False, "longCaps": "off"})
        self.assertFalse(q["long"]["captionsOn"])
        self.assertTrue(q["shorts"]["captionsOn"], "쇼츠는 늘 켬")
        q = by({"captions": True, "longCaps": "on"})
        self.assertEqual((q["long"]["captionsOn"], q["long"]["captionStyle"]["size"]), (True, editor.LONG_STYLE["size"]))
        q = by(None)  # 스타일 없는 가편집은 예전과 같음
        self.assertEqual((q["long"]["captionsOn"], q["long"]["captionStyle"]["size"]), (True, editor.LONG_STYLE["size"]))

    def test_set_and_relearn_keep(self):
        style.STYLES.mkdir(parents=True, exist_ok=True)
        f = style.STYLES / "레슨.json"
        f.write_text(json.dumps(self.prof(0.07), ensure_ascii=False), encoding="utf-8")
        style.set_long_caps("레슨", "off")
        self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["longCaps"], "off")
        self.assertEqual(style.edit_params(json.loads(f.read_text(encoding="utf-8")))["longCaps"], "off")
        with self.assertRaises(ValueError):
            style.set_long_caps("레슨", "big")
        with self.assertRaises(style.StyleMissing):
            style.set_long_caps("없는 스타일", "on")
        fm = {"long": self.prof(0.05), "shorts": self.prof(0.8)}
        f.write_text(json.dumps(dict(self.prof(0.07), formats=fm, longCaps="on"), ensure_ascii=False), encoding="utf-8")
        p = style.edit_params(json.loads(f.read_text(encoding="utf-8")))
        self.assertEqual((p["byFormat"]["long"]["longCaps"], p["longCaps"]), ("on", "on"), "형식을 나눠 배워도 고른 값")

    def test_route_and_remote_table(self):
        from test_windows_compat import Server

        class T(Server):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            style.STYLES.mkdir(parents=True, exist_ok=True)
            (style.STYLES / "레슨.json").write_text(json.dumps(self.prof(0.07), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(t.call("/api/style/long_caps", {"name": "레슨", "mode": "off"}), (200, {"ok": True, "mode": "off"}))
            st = next(s for s in t.call("/api/style/list")[1]["styles"] if s["name"] == "레슨")
            self.assertEqual(st["params"]["longCaps"], "off")
            self.assertEqual(t.call("/api/style/long_caps", {"name": "레슨", "mode": "x"})[0], 400)
            self.assertEqual(t.call("/api/style/long_caps", {"name": "없음", "mode": "on"})[0], 404)
        finally:
            t.doCleanups()
        import rstudio
        self.assertEqual(rstudio.POLICY.get(("POST", "/api/style/long_caps")), rstudio.WRITE)


    def score(self, prof, chosen=None):
        """score_video (영상·화면 살펴보기는 가짜 · 가편집·프로필·거리 셈은 진짜) → (점수 결과, auto_sequences 에 준 params)."""
        name, info = self.lesson(2)
        if chosen:
            prof = dict(prof, longCaps=chosen)
        st = {"name": "레슨", "profile": prof, "params": style.edit_params(prof)}
        seen, real = {}, editor.auto_sequences

        def auto(n, i, params, kinds):
            seen.update(params)
            return real(n, i, params, kinds)
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        with mock.patch.object(style, "_score_inputs", return_value=(st, segs)), mock.patch.object(style, "_cached_events", return_value={}), \
                mock.patch.object(editor, "media_info", return_value=info), mock.patch.object(editor, "auto_sequences", side_effect=auto):
            return style.score_video("레슨", name), seen

    def test_score_unchanged_when_not_chosen(self):
        """리뷰: 롱폼 말 자막을 고르지 않은 자막 7% 스타일 — 점수는 예전처럼 자막 끈 가편집으로 매김 ('자막' 100점 그대로 · 'small' 이면 약 9점)."""
        r, seen = self.score(self.prof(0.07))
        self.assertEqual(seen["longCaps"], "off")
        self.assertLess(r["rough"]["captionRatio"], 0.1)
        self.assertEqual(r["parts"]["자막"], 100)
        r, seen = self.score(self.prof(0.65))
        self.assertEqual(seen["longCaps"], "on")
        r, seen = self.score(self.prof(0.07), chosen="small")   # 고른 값이면 그 값으로 만든 가편집을 매김
        self.assertEqual(seen["longCaps"], "small")
        self.assertGreater(r["rough"]["captionRatio"], 0.3)
        self.assertLess(r["parts"]["자막"], 50)
        self.assertEqual(style._params(self.prof(0.07))["longCaps"], "small", "가편집 자체는 '작게 켜기' 그대로")


# ---------- 쇼츠 길이 (BR-291) ----------

class ShortsLenTest(Base):
    def rough(self, durs, fmt="shorts"):
        style.STYLES.mkdir(parents=True, exist_ok=True)
        refs = [{"source": f"ref{k}.mp4", "duration": d, "format": fmt} for k, d in enumerate(durs)]
        (style.STYLES / "쇼츠 배움.json").write_text(json.dumps({"refs": refs}, ensure_ascii=False), encoding="utf-8")
        style.set_rough_default("쇼츠 배움")

    def test_modes(self):
        self.assertEqual(style.shorts_len(), (20.0, 55.0))   # 기본은 예전 그대로
        for m, want in (("loop", (10.0, 20.0)), ("point", (30.0, 60.0)), ("explain", (60.0, 120.0)), ("", (20.0, 55.0))):
            self.assertEqual(style.set_shorts_len(m), m)
            self.assertEqual(style.shorts_len(), want, m)
            self.assertEqual(editor.shorts_len(), want, m)
        with self.assertRaises(ValueError):
            style.set_shorts_len("long")
        style._shorts_len_file().write_text("{", encoding="utf-8")
        self.assertEqual(style.shorts_len_mode(), "")

    def test_ref_mode(self):
        style.set_shorts_len("ref")
        self.assertEqual(style.shorts_len(), (20.0, 55.0), "첫 가편집 스타일이 없으면 지금처럼")
        self.rough([15.0, 18.0, 30.0])
        st = style.shorts_len_state()
        self.assertEqual((st["mode"], st["min"], st["max"]), ("ref", 11.0, 27.0))   # 보통 18초 × 0.6 · × 1.5
        self.assertEqual((st["ref"]["n"], st["ref"]["median"]), (3, 18.0))
        self.rough([100.0, 110.0])
        self.assertEqual(style.shorts_len(), (63.0, 120.0))
        self.rough([300.0], fmt="long")
        self.assertEqual(style.shorts_len(), (20.0, 55.0), "쇼츠 레퍼런스가 없으면 지금처럼")

    def test_recommend_follows(self):
        name, info = self.lesson(4)
        base = editor.recommend(name)
        self.assertTrue(base["shorts"])
        for r in base["shorts"]:
            self.assertLessEqual(r["end"] - r["start"], 55.0 + 1.0)
        style.set_shorts_len("loop")
        loop = editor.recommend(name)
        self.assertTrue(loop["shorts"])
        for r in loop["shorts"]:
            self.assertLessEqual(r["end"] - r["start"], 20.0 + 1.0, r)
        self.assertEqual(editor.recommend(name, 20.0, 55.0)["shorts"], base["shorts"], "길이를 주면 그 길이")
        style.set_shorts_len("explain")
        for r in editor.recommend(name)["shorts"]:
            self.assertGreaterEqual(r["end"] - r["start"], 60.0 - 1.0, r)
        seqs = [q for q in editor.auto_sequences(name, info, kinds=("shorts",))]
        self.assertTrue(seqs)
        for q in seqs:
            self.assertGreaterEqual(editor.seq_total(q), 50.0)

    def test_route(self):
        from test_windows_compat import Server

        class T(Server):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            self.assertEqual(t.call("/api/style/shorts_len")[1]["mode"], "")
            c, r = t.call("/api/style/shorts_len", {"mode": "loop"})
            self.assertEqual((c, r["ok"], r["mode"], r["min"], r["max"]), (200, True, "loop", 10.0, 20.0))
            self.assertEqual(t.call("/api/style/list")[1]["shortsLen"]["mode"], "loop")
            self.assertEqual(t.call("/api/style/shorts_len", {"mode": "x"})[0], 400)
        finally:
            t.doCleanups()
        import rstudio
        self.assertEqual(rstudio.POLICY.get(("POST", "/api/style/shorts_len")), rstudio.WRITE)
        self.assertEqual(rstudio.POLICY.get(("GET", "/api/style/shorts_len")), rstudio.READ)


    def test_msg_follows_length(self):
        """리뷰: '한 동작 (10~20초)'이면 MSG(재미있게 자동 편집·끝까지 자동으로) 쇼츠의 가장 짧게 15초도 낮춤 — 정리 컷 뒤 9~14초 쇼츠가
        '15초 넘어야 해요'로 다 빠지지 않게 · 기본·기술 하나·설명은 15초 그대로."""
        import msg
        from test_msg_round7 import M
        self.assertEqual((msg.min_shorts(), msg._stop_min()), (15.0, 20.0))
        for m, want in (("loop", (7.5, 10.0)), ("point", (15.0, 20.0)), ("explain", (15.0, 20.0)), ("", (15.0, 20.0))):
            style.set_shorts_len(m)
            self.assertEqual((msg.min_shorts(), msg._stop_min()), want, m)
        style.set_shorts_len("ref")
        self.rough([18.0])   # 보통 18초 → 11~27초
        self.assertAlmostEqual(msg.min_shorts(), 8.25)

        # 쇼츠 하나 = 포인트 하나: 다음 포인트 앞에서 끊고 남는 컷 9.8초 → 한 동작이면 끊음 (기본은 15초가 안 돼 안 끊음 · D-267 그대로)
        rec = {"shorts": [{"start": 100.0, "end": 160.0, "cuts": [{"in": 100.0, "out": 105.0}, {"in": 110.0, "out": 113.0}, {"in": 118.0, "out": 160.0}]}],
               "tidy": []}
        moms = [M("play", 104.0, score=2.0), M("section", 120.0, "자, 네 번째 포인트는 몸 방향이에요.", 120.0, 122.0)]
        style.set_shorts_len("")
        self.assertEqual(msg._shorts_window(rec, moms, 200.0, [], points=True)[-1]["out"], 160.0)
        style.set_shorts_len("loop")
        self.assertAlmostEqual(msg._shorts_window(rec, moms, 200.0, [], points=True)[-1]["out"], 119.8)

        # 후보 만들기: 정리 컷 뒤 12.4초 쇼츠 — 기본은 빼고 까닭(15초), 한 동작이면 남김
        def fake(name, info, sig, segs, moms, st, lv, fmt, seed, label, log, caps, ai, win):
            dur = 60.0 if fmt == "long" else 12.4
            return {"id": fmt, "items": [{"track": "V1", "start": 0.0, "in": 0.0, "out": dur, "speed": 1.0}], "msg": {"summary": {"text": ""}}}, []

        def run():
            with mock.patch.multiple(msg, compile_seq=fake, ensure_transcript=lambda *a: None, _check_input=lambda *a: None,
                                     signals=lambda *a, **k: {"blobs": [], "sig": "s"}, moments=lambda *a, **k: [], _check=lambda: None), \
                    mock.patch.object(editor, "media_info", lambda n: {"duration": 120.0}), mock.patch.object(editor, "_segments_of", lambda n: []), \
                    mock.patch.object(editor, "_captions_of", lambda *a: []):
                return msg.build_variants("v.mp4", [{"kind": "preset", "name": "예능 MSG형"}], "보통", ("long", "shorts"), lambda m: None)
        style.set_shorts_len("")
        res = run()
        self.assertEqual([q["id"] for q in res["sequences"]], ["long"])
        self.assertIn("12초뿐이에요 (15초 넘어야 해요)", res["notes"][0]["why"])
        style.set_shorts_len("loop")
        res = run()
        self.assertEqual(([q["id"] for q in res["sequences"]], res["notes"]), (["long", "shorts"], []))

        # 끊어서 짧아지면 끊지 않고 다시 만드는 셈(D-267)도 같은 가장 짧게로: 끊은 11초는 한 동작이면 그대로
        calls = []

        def once(*a):
            calls.append(a[12].get("pointStop", True))
            dur = 11.0 if a[12].get("pointStop", True) else 40.0
            return ({"items": [{"track": "V1", "start": 0.0, "in": 0.0, "out": dur, "speed": 1.0}], "titles": [], "shapes": [],
                     "msg": {"events": [], "summary": {"kinds": {}}}}, [], 0, 0)
        st = msg.resolve({"kind": "preset", "name": "예능 MSG형"})
        with mock.patch.object(msg, "_compile_once", once):
            q, _ = msg.compile_seq("v.mp4", {"duration": 300.0}, {}, [], [M("play", 10.0)], st, "보통", "shorts", 1, "x", lambda m: None)
        self.assertEqual((calls, editor.seq_total(q)), ([True], 11.0))

# ---------- 티저 (BR-292) ----------

class TeaserTest(Base):
    def segs(self):
        return [{"start": 10.0, "end": 14.0, "text": "오늘은 퍼스트 터치를 해 볼게요."},
                {"start": 30.0, "end": 33.0, "text": "봤죠? 회전이 하나도 없어요."},
                {"start": 50.0, "end": 54.0, "text": "트래핑은 이렇게 발바닥으로 받아요."},
                {"start": 102.0, "end": 105.0, "text": "다른 장면에서 하는 말이에요 퍼스트 터치 꿀팁"}]

    def test_title_from_clip(self):
        s = self.segs()
        self.assertEqual(editor.teaser_title(s, 29.5, 33.5), "회전이 하나도 없어요")   # 그 장면 말 (앞 반응 말 '봤죠?'는 뗌)
        self.assertEqual(editor.teaser_title(s, 49.5, 53.5), "트래핑!")             # 그 장면 말의 풋살 용어
        self.assertEqual(editor.teaser_title(s, 70.0, 74.0), "퍼스트 터치!")       # 말 없는 시범: 영상 주제
        self.assertEqual(editor.teaser_title([], 0.0, 4.0), editor.TEASER_FALLBACK)
        self.assertEqual(editor.teaser_title(s, 13.0, 17.0), "퍼스트 터치!", "장면에 반도 안 걸친 말은 그 장면 말이 아님")

    def test_teaser_clip_no_captions_and_srt(self):
        name, info = self.lesson(2)
        q = editor.auto_sequences(name, info, {"introTeaser": {"on": True, "sec": 4}}, ("long",))[0]
        v0 = min((it for it in q["items"] if it["track"] == "V1"), key=lambda it: it["start"])
        pair = [it for it in q["items"] if it["link"] == v0["link"]]
        self.assertEqual(len(pair), 2)
        self.assertTrue(all(it.get("noCaps") for it in pair), "미리 보기 V1·A1 둘 다")
        ln = editor.i_end(v0)
        segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        cap_proj = dict(q, captions=editor._captions_of(segs, info), info=info)
        tl = editor.timeline_captions(cap_proj)
        self.assertTrue(tl)
        self.assertGreaterEqual(min(c["start"] for c in tl), ln - 1e-6, "티저 동안 말 자막 없음")
        files = editor.srt_files(cap_proj, "롱폼", 0.0, editor.seq_total(q), 30)
        first = files[0][1].split("\n")[1]
        self.assertGreaterEqual(editor._srt_secs(first.split(" --> ")[0]), ln - 0.05, first)
        titles = {t.get("plan"): t for t in q["titles"]}
        self.assertEqual(titles["teaserTag"]["text"], "미리보기")
        self.assertEqual((titles["teaserTag"]["start"], titles["teaserTag"]["dur"]), (0.0, titles["teaser"]["dur"]))
        said = " ".join(s["text"] for s in segs if min(v0["out"], s["end"]) - max(v0["in"], s["start"]) >= 0.5 * (s["end"] - s["start"]))
        if said:
            self.assertIn(editor._norm(titles["teaser"]["text"]).rstrip("!"), editor._norm(said) + editor._norm("퍼스트 터치 꿀팁 트래핑 디딤발"))
        W, H = editor.frame_size("long")
        tb, gb = editor.text_box(titles["teaser"]["text"], titles["teaser"]["style"], W, H), editor.text_box("미리보기", editor.TEASER_TAG, W, H)
        self.assertLess(gb[3], tb[1], "미리보기 표시는 큰 제목 위")

    def test_old_args_still_work(self):
        tidy = [{"in": 0.0, "out": 40.0}, {"in": 50.0, "out": 60.0}]
        out, titles = editor._intro_teaser(editor._items_from_cuts(tidy), {"shorts": []}, tidy, 4.0, self.segs(), None, tag=False)
        self.assertEqual([t["plan"] for t in titles], ["teaser"])


# ---------- 강조 큰 글씨 (BR-293) ----------

class EmphasisTest(Base):
    def test_key_label(self):
        k = editor.key_label
        self.assertEqual(k("인사이드 패스는 디딤발 위치가 핵심이에요"), "디딤발 위치 핵심!")
        self.assertEqual(k("돌파는 타이밍이 중요해요 상대 중심을 보세요"), "돌파 타이밍 중요!")
        self.assertEqual(k("핵심은 공을 끝까지 보는 거예요"), "공을 끝까지 보는 거!")
        self.assertEqual(k("무조건 발목을 고정하세요."), "무조건 발목을 고정하세요!")
        self.assertEqual(k("디딤발이 공이랑 너무 가까우면 터치가 무조건 길어져요."), "터치가 무조건 길어져요!")
        self.assertEqual(k("인사이드 패스할 때 이게 핵심이에요"), "인사이드 패스 핵심!")
        self.assertEqual(k("이게 진짜 핵심이에요."), "진짜 핵심!")          # 재현: 예전엔 None (MSG 강조 글자와 같은 꼴)
        self.assertEqual(k("월패스는 이 타이밍이 생명이에요."), "월패스 타이밍 생명!")
        self.assertEqual(k("이 타이밍이 생명이에요"), "타이밍 생명!")
        self.assertEqual(k("패스할 때 이 각도가 중요해요"), "패스 각도 중요!")
        self.assertEqual(k("이 차이가 정말 중요해요."), "차이가 정말 중요!")
        self.assertEqual(k("이 부분이 정말 중요합니다"), "정말 중요!")
        self.assertEqual(k("포인트는 패스하고 절대 멈추지 않는 거예요."), "패스하고 절대 멈추지 않는 거!")   # 뒤 말 전체 (자른 조각 아님)
        self.assertIsNone(k("핵심은 공을 받기 전에 미리 주변을 둘러보고 몸을 여는 거예요"), "길면 자르지 않고 안 띄움")
        self.assertIsNone(k("자, 첫번째 포인트."), "순서 말은 콕 집은 말이 아님")
        self.assertIsNone(k("트래핑을 연습해 볼 건데요"), "콕 집은 말이 아님")

    def test_key_label_lead_ins(self):
        """리뷰: 감독님이 늘 쓰는 이끄는 말('중요한 게 뭐냐면요' · '핵심은 세 가지예요' · '자 오늘 핵심 정리할게요')은 강조 큰 글씨가 아님 —
        이끄는 말 뒤에 내용이 있으면 그 내용 ('중요한 게 뭐냐면요, 디딤발 위치예요' → '디딤발 위치!')."""
        k = editor.key_label
        for t in ("중요한 게 뭐냐면요", "중요한 건 이거예요", "핵심은 여기 있어요", "자, 정리하면 핵심은 세 가지예요.", "자 오늘 핵심 정리할게요",
                  "여기서 중요한 포인트", "중요한 건 뭐냐", "핵심은 두 가지예요", "중요한 거 하나만 말씀드릴게요", "두 가지가 핵심이에요",
                  "세 번째가 중요해요", "자 여기서 핵심", "핵심은 포인트예요", "뭐가 핵심이냐면요"):
            self.assertIsNone(k(t, terms=[]), t)
        self.assertEqual(k("중요한 게 뭐냐면요, 디딤발 위치예요.", terms=[]), "디딤발 위치!")
        self.assertEqual(k("여기서 디딤발이 핵심이에요", terms=[]), "디딤발 핵심!")
        self.assertEqual(k("오늘 핵심은 디딤발이에요", terms=[]), "디딤발!")
        self.assertEqual(k("중요한 건 타이밍이에요", terms=[]), "타이밍!")
        # 이끄는 말은 다음 문장(콕 집은 말이 아니면 기술 이름 규칙)으로 — 예전: 100초에 '뭐냐면요!'
        items = editor._items_from_cuts([{"in": 0.0, "out": 200.0}])
        segs = [{"start": 100.0, "end": 104.0, "text": "중요한 게 뭐냐면요"}, {"start": 104.5, "end": 108.0, "text": "디딤발을 공 옆에 두는 거예요."}]
        out = editor._emphasis_titles(items, segs, 4.0, "#FFE14D", captions_on=True)
        self.assertNotIn("뭐냐면요!", [t["text"] for t in out])
        out = editor._emphasis_titles(items, segs + [{"start": 130.0, "end": 134.0, "text": "자 오늘 핵심 정리할게요"},
                                                     {"start": 160.0, "end": 164.0, "text": "디딤발 위치가 진짜 핵심이에요."}], 4.0, "#FFE14D", captions_on=True)
        got = [t["text"] for t in out]
        self.assertIn("디딤발 위치 핵심!", got)
        self.assertNotIn("자 오늘 핵심!", got)   # (짧은 말의 강조 낱말만 '핵심!'은 예전 emphasis_label 규칙 그대로)

    def test_sentences(self):
        seg = {"start": 0.0, "end": 9.0, "text": "안녕하세요 여러분. 오늘은 퍼스트 터치예요. 이게 진짜 핵심이에요."}
        self.assertEqual([x for x, _ in editor._sentences(seg)], ["안녕하세요 여러분.", "오늘은 퍼스트 터치예요.", "이게 진짜 핵심이에요."])
        ws = [{"w": w, "s": float(i), "e": i + 0.8} for i, w in enumerate("자, 첫번째 포인트. 이게 진짜 핵심이에요.".split())]
        self.assertEqual(editor._sentences({"start": 0.0, "end": 6.0, "words": ws, "text": ""}), [("자, 첫번째 포인트.", 0.0), ("이게 진짜 핵심이에요.", 3.0)])

    def test_greeting_skipped_and_key_first(self):
        # 재현: 인사 '안녕하세요. 여러분. 오늘은 퍼스트 터치에 대해서…'가 '퍼스트 터치!'로 3.3초에 뜸 · '핵심이에요' 문장은 빠짐
        segs = [{"start": 2.0, "end": 6.0, "text": "안녕하세요. 여러분. 오늘은 퍼스트 터치에 대해서 알아볼게요."},
                {"start": 20.0, "end": 24.0, "text": "트래핑을 연습해 볼 건데요 공을 잘 보세요."},
                {"start": 26.0, "end": 30.0, "text": "디딤발 위치가 진짜 핵심이에요."},
                {"start": 50.0, "end": 54.0, "text": "오늘은 여기까지 할게요. 퍼스트 터치 꼭 연습하세요."}]
        items = editor._items_from_cuts([{"in": 0.0, "out": 60.0}])
        out = editor._emphasis_titles(items, segs, 4.0, "#FFE14D", captions_on=True)
        self.assertEqual([t["text"] for t in out], ["디딤발 위치 핵심!"])
        self.assertAlmostEqual(out[0]["start"], 26.0, delta=0.01)
        # 여러 문장을 묶은 받아쓴 구간 (MSGRAW01 24.6초 구간 재현): 그 안의 콕 집은 문장 · 그 문장 때에
        long_seg = [{"start": 24.0, "end": 44.0, "text": "자, 첫번째 포인트. 공이 오기 전에 고개를 들어서 주변을 먼저 보세요. 수비가 바로 붙어버리니까요. "
                                                         "이게 진짜 핵심이에요. 공을 받기 전에 다음 동작이 이미 정해져 있어야 해요."}]
        out = editor._emphasis_titles(items, segs[:1] + long_seg, 4.0, "#FFE14D", captions_on=True)
        self.assertEqual([t["text"] for t in out], ["진짜 핵심!"])
        self.assertGreater(out[0]["start"], 33.0, "그 문장 때 (구간 시작 24초가 아님)")
        # 말 자막이 켜져 있으면 말을 그대로 옮긴 긴 구절('패스하고 절대 멈추지 않는 거!')은 안 띄움 (자막과 같은 글 두 번 · MSGRAW02 85.5초 재현)
        dup = [{"start": 20.0, "end": 24.0, "text": "포인트는 패스하고 절대 멈추지 않는 거예요."}]
        self.assertEqual([t["text"] for t in editor._emphasis_titles(items, dup, 4.0, "#FFE14D", captions_on=False)], ["패스하고 절대 멈추지 않는 거!"])
        self.assertNotIn("패스하고 절대 멈추지 않는 거!", [t["text"] for t in editor._emphasis_titles(items, dup, 4.0, "#FFE14D", captions_on=True)])

    def test_shorts_get_one_or_two(self):
        name, info = self.lesson(3)
        st = {"emphasisTitles": {"perMin": 3.3, "color": "#FFE14D"}}
        W, H = editor.frame_size("shorts")
        seqs = editor.auto_sequences(name, info, st, ("shorts",))
        self.assertTrue(seqs)
        n_with = 0
        for q in seqs:
            em = [t for t in q["titles"] if t.get("plan") == "emphasis"]
            hook = q["titles"][0]
            total = editor.seq_total(q)
            self.assertLessEqual(len(em), 1 if total < editor.SHORTS_EMPH_TWO else 2, q["name"])
            n_with += bool(em)
            hb = editor.text_box(hook["text"], hook["style"], W, H)
            cb = editor._cap_band(q["captionStyle"], H)
            for t in em:
                b = editor.text_box(t["text"], t["style"], W, H)
                self.assertGreaterEqual(t["start"], editor.TOPIC_SEC + 1.0 - 1e-6)
                self.assertNotEqual(editor._norm(t["text"]), editor._norm(hook["text"]))
                self.assertFalse(editor._overlap((b[1], b[3]), (hb[1], hb[3])), (t["text"], b, hb))
                self.assertFalse(editor._overlap((b[1], b[3]), cb), (t["text"], b, cb))
                self.assertTrue(0.0 <= b[0] and b[2] <= 1.0, b)
                self.assertIn(t["style"]["size"], editor.SHORTS_EMPH_SIZES)
                self.assertTrue(editor._one_line(t["text"], t["style"], W) or t["style"]["size"] == editor.SHORTS_EMPH_SIZES[-1], "한 줄")
            for a, b in zip(em, em[1:]):
                self.assertGreaterEqual(b["start"] - a["start"], min(editor.EMPH_GAP, max(6.0, total / 3)) - 1e-6)
        self.assertGreater(n_with, 0, "쇼츠에도 강조 큰 글씨")
        plain = editor.auto_sequences(name, info, {}, ("shorts",))
        self.assertFalse([t for q in plain for t in q["titles"] if t.get("plan") == "emphasis"], "기획 분석이 강조를 안 켜면 그대로")

    def test_eunpyeong_shorts_untouched(self):
        name, info = self.lesson(3)
        st = {"emphasisTitles": {"perMin": 3.3, "color": "#FFE14D"}}
        with mock.patch.object(editor, "shorts_card_on", return_value=True):
            seqs = editor.auto_sequences(name, info, st, ("shorts",))
        self.assertTrue(seqs)
        self.assertFalse([t for q in seqs for t in q["titles"] if t.get("plan") == "emphasis"])


# ---------- 확대 컷 기준점 (BR-294) ----------

class ZoomAnchorTest(Base):
    def test_anchor_math(self):
        talk = [0.43, 0.15, 0.13, 0.27]   # 말하는 장면 얼굴 (가운데 0.285)
        ax, ay = editor.zoom_anchor(talk, 1.25)
        self.assertAlmostEqual(ax, 0.495, places=3)
        top_after = ay + 1.25 * (talk[1] - editor.FACE_PAD[1] - ay)
        self.assertGreaterEqual(top_after, editor.ZOOM_HEAD_PAD - 1e-6, "머리 위가 화면 안")
        centre = 0.5 + 1.25 * (talk[1] - editor.FACE_PAD[1] - 0.5)
        self.assertGreater(top_after, centre, "가운데 기준보다 머리 위 여유가 큼")
        low = [0.42, 0.42, 0.16, 0.3]     # 앉은 사람: 얼굴이 가운데 아래
        ax, ay = editor.zoom_anchor(low, 1.25)
        face_after = ay + 1.25 * (low[1] + low[3] / 2 - ay)
        self.assertLess(face_after, low[1] + low[3] / 2, "얼굴을 위로 올림")
        self.assertTrue(0.0 <= ay <= 1.0)
        self.assertEqual(editor.zoom_anchor([0.9, 0.0, 0.2, 0.2], 1.3)[1], 0.0, "0~1 안 (화면을 다 덮게)")
        self.assertEqual(editor.zoom_anchor(talk, 1.0), [0.5, 0.5])

    def cuts(self):
        return [{"in": 0.0, "out": 4.0, "zoom": False}, {"in": 6.0, "out": 9.0, "zoom": True}, {"in": 9.0, "out": 11.0, "zoom": True, "speed": 1.1},
                {"in": 20.0, "out": 24.0, "zoom": False}, {"in": 30.0, "out": 34.0, "zoom": True}]

    def fake(self, box=(0.43, 0.15, 0.13, 0.27)):
        import face

        def fill(name, ts, cache):
            for t in ts:
                cache[t] = [[0.05, 0.6, 0.05, 0.08], list(box)]
            return cache
        return [mock.patch.object(face, "ready", return_value=True), mock.patch.object(editor, "_face_ready", return_value=True),
                mock.patch.object(editor, "_faces_at", side_effect=fill)]

    def test_face_anchors(self):
        (core.VIDEOS / self.name).write_bytes(b"x")
        ps = self.fake()
        for p in ps:
            p.start()
        try:
            out = editor.face_anchors(self.name, self.cuts(), 1.25)
            seen = editor._faces_at.call_args[0][1]
        finally:
            for p in ps:
                p.stop()
        want = editor.zoom_anchor([0.43, 0.15, 0.13, 0.27], 1.25)   # 가장 큰 얼굴
        self.assertEqual([c.get("anc") for c in out], [None, want, want, None, want])
        self.assertEqual(seen, [8.5, 32.0], "이어진 확대 조각은 묶음 가운데 한 장면")
        items = editor._items_from_cuts(out, zoom=1.25)
        v = [it for it in items if it["track"] == "V1"]
        self.assertEqual(v[1]["fx"]["anchor"], {"v": want, "k": []})
        self.assertNotIn("anchor", v[0]["fx"])
        # 얼굴 자리 셈(강조 글씨)도 그 기준점 둘레로
        with mock.patch.object(editor, "_face_ready", return_value=True), \
                mock.patch.object(editor, "_faces_at", side_effect=lambda n, ts, c: c.update({t: [[0.43, 0.15, 0.13, 0.27]] for t in ts}) or c):
            fs = editor._faces_on_screen(self.name, items, v[1]["start"] + 0.5, v[1]["start"] + 2.0)
        self.assertAlmostEqual(fs[0][1], want[1] + (0.15 - want[1]) * 1.25, places=6)

    def test_no_model_or_video_is_centre(self):
        cuts = self.cuts()
        self.assertEqual(editor.face_anchors(self.name, cuts, 1.25), cuts, "영상이 없으면 그대로")
        (core.VIDEOS / self.name).write_bytes(b"x")
        with mock.patch.object(editor, "_face_ready", return_value=False), mock.patch.object(editor, "_faces_at") as fa:
            self.assertEqual(editor.face_anchors(self.name, cuts, 1.25), cuts)
        fa.assert_not_called()
        with mock.patch.object(editor, "_face_ready", return_value=True), mock.patch.object(editor, "_faces_at", side_effect=lambda n, ts, c: c):
            self.assertEqual(editor.face_anchors(self.name, cuts, 1.25), cuts, "얼굴을 못 보면 가운데")
        self.assertEqual(editor.face_anchors(self.name, cuts, 1.0), cuts, "확대 없음")

    def test_frame_cap(self):
        (core.VIDEOS / self.name).write_bytes(b"x")
        cuts = [{"in": 3.0 * k, "out": 3.0 * k + 2, "zoom": True} for k in range(120)]
        ps = self.fake()
        for p in ps:
            p.start()
        try:
            out = editor.face_anchors(self.name, cuts, 1.2)
            seen = editor._faces_at.call_args[0][1]
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(len(seen), editor.ZOOM_FACE_MAX)
        self.assertTrue(all(c.get("anc") for c in out), "가까운(6초 안) 장면 얼굴")

    def test_demo_cut_not_zoomed(self):
        cuts = [{"in": 0.0, "out": 5.0}, {"in": 7.0, "out": 12.0}, {"in": 14.0, "out": 19.0}, {"in": 21.0, "out": 26.0}]
        demo = lambda a, b: a == 14.0  # noqa: E731
        plain = editor._shots(cuts, None, lambda a, b: [], lambda a, b: True)
        calm = editor._shots(cuts, None, lambda a, b: [], lambda a, b: True, demo)
        self.assertEqual([c["zoom"] for c in plain], [False, True, False, True])
        self.assertEqual([c["zoom"] for c in calm], [False, True, False, True])
        cuts2 = [{"in": 0.0, "out": 5.0}, {"in": 7.0, "out": 12.0}, {"in": 14.0, "out": 19.0}]
        self.assertEqual([c["zoom"] for c in editor._shots(cuts2, None, lambda a, b: [], lambda a, b: True, lambda a, b: a == 7.0)],
                         [False, False, True], "시범 컷은 원래 크기 · 다음 점프 컷에서 다시 확대")

    def test_long_rough_cut_uses_anchor(self):
        name, info = self.lesson(1)
        (core.VIDEOS / name).write_bytes(b"x")
        ps = self.fake()
        for p in ps:
            p.start()
        try:
            q = editor.auto_sequences(name, info, None, ("long",))[0]
        finally:
            for p in ps:
                p.stop()
        z = [it for it in q["items"] if it["track"] == "V1" and (it.get("fx") or {}).get("scale")]
        self.assertTrue(z)
        self.assertTrue(all(it["fx"].get("anchor") for it in z), "확대 컷마다 얼굴 기준점")


if __name__ == "__main__":
    unittest.main()
