"""MSG 편집본 만들기 (msg.plan_events · compile_seq · build_variants, D-140) — 저장소 폴더에서 python3 -m unittest tests.test_msg_compile
시험 원본으로 기본 스타일 × 양을 만들어 불변식을 확인: V1 클립 겹침 없음 · 전환이 모두 유효 · 클립이 있는 미디어만 씀 ·
끼워 넣은 장면(다시 보기·티저)은 말 자막을 다시 안 띄움 · 사건 기록의 재료 id 가 실제로 있음 · 같은 입력이면 같은 결과 ·
양이 많을수록 사건이 많음 · 담백은 속마음·효과 글자·흔들기 없음 · 글자는 말 자막 자리를 피함 · 30초 남짓 실제 내보내기 + 자동 검수."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import msg  # noqa: E402
import qa  # noqa: E402
from make_msg_fixture import MsgWork  # noqa: E402

STYLES = [{"kind": "preset", "name": n} for n in ("예능 MSG형", "담백 레슨형", "다큐 감성형")]


def strip_ids(x):
    """id(무작위)만 빼고 비교 (재료의 순서·값·시각은 같아야 함)."""
    if isinstance(x, dict):
        return {k: strip_ids(v) for k, v in x.items() if k not in ("id", "link", "a", "b") or not isinstance(v, str)}
    if isinstance(x, list):
        return [strip_ids(v) for v in x]
    return x


class Compile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MsgWork()
        cls.res = {k: msg.build_variants(cls.w.name, STYLES, k, ("long", "shorts"), lambda m: None) for k in msg.INTENSITY}

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def seqs(self):
        for k, r in self.res.items():
            for q in r["sequences"]:
                yield k, q, r

    def test_timeline_invariants(self):
        for k, q, r in self.seqs():
            media = {"main"} | {m["id"] for m in r["media"]}
            v1 = sorted([it for it in q["items"] if it["track"] == "V1"], key=lambda x: x["start"])
            for a, b in zip(v1, v1[1:]):
                self.assertLessEqual(editor.i_end(a), b["start"] + 0.02, (q["name"], a, b))
            for tr in {t["id"] for t in q["items"]}:
                pass
            for it in q["items"]:
                self.assertIn(it["media"], media, q["name"])
                self.assertGreater(it["out"] - it["in"], 0.0)
            by_track = {}
            for it in q["items"]:
                by_track.setdefault(it["track"], []).append(it)
            for t, its in by_track.items():
                its.sort(key=lambda x: x["start"])
                for a, b in zip(its, its[1:]):
                    self.assertLessEqual(editor.i_end(a), b["start"] + 0.02, (q["name"], t))
            self.assertEqual(len(editor.valid_trans(q)), len(q["trans"]), q["name"])
            self.assertEqual(q["auto"], "msg")
            self.assertTrue(q["name"].startswith("MSG 후보 "))
            tids = {t["id"] for t in q["titles"]} | {s["id"] for s in q["shapes"]} | {it["id"] for it in q["items"]} | {t["id"] for t in q["trans"]}
            for e in q["msg"]["events"]:
                for key in ("titles", "shapes", "items", "trans"):
                    for x in e["refs"].get(key, []):
                        self.assertIn(x, tids, (q["name"], e["kind"], key))
            total = editor.seq_total(q)
            for t in q["titles"]:
                self.assertLessEqual(t["start"] + t["dur"], total + 0.05, (q["name"], t["text"]))

    def test_inserts_do_not_repeat_speech_captions(self):
        """다시 보기·티저는 noCaps → 같은 말 자막이 두 번 나오지 않음."""
        info = editor.media_info(self.w.name)
        caps = editor._captions_of(editor._segments_of(self.w.name), info)
        for k, q, r in self.seqs():
            ins = [it for it in q["items"] if it["track"] == "V1" and it["media"] == "main" and it.get("noCaps")]
            if any(e["kind"] == "replay" for e in q["msg"]["events"]):
                self.assertTrue(ins, q["name"])
            tl = editor.timeline_captions(dict(q, captions=caps))
            texts = [c["text"] for c in tl]
            for c in caps:
                n = sum(1 for x in texts if x == c["text"])
                self.assertLessEqual(n, max(1, sum(1 for it in q["items"] if it["track"] == "V1" and it["media"] == "main" and not it.get("noCaps")
                                                   and it["in"] < c["end"] and c["start"] < it["out"])), (q["name"], c["text"]))

    def test_deterministic(self):
        again = msg.build_variants(self.w.name, STYLES[:1], "보통", ("long",), lambda m: None)
        a, b = again["sequences"][0], self.res["보통"]["sequences"][0]
        self.assertEqual(strip_ids(a["msg"]["summary"]), strip_ids(b["msg"]["summary"]))
        self.assertEqual([(t["text"], t["start"]) for t in a["titles"]], [(t["text"], t["start"]) for t in b["titles"]])
        self.assertEqual([(i["track"], i["start"], i["in"], i["out"]) for i in a["items"]], [(i["track"], i["start"], i["in"], i["out"]) for i in b["items"]])

    def test_intensity_is_monotone_and_mild_is_mild(self):
        def n_events(k, i):
            return len([e for e in self.res[k]["sequences"][i]["msg"]["events"] if e["kind"] not in ("bgm", "chapters")])
        for i in (0,):  # 예능 MSG형 롱폼
            self.assertLessEqual(n_events("담백", i), n_events("보통", i))
            self.assertLessEqual(n_events("보통", i), n_events("듬뿍", i))
            self.assertLess(n_events("담백", i), n_events("듬뿍", i))
        for q in self.res["담백"]["sequences"]:
            kinds = {e["kind"] for e in q["msg"]["events"]}
            self.assertFalse(kinds & {"inner", "fx", "shake", "montage"}, (q["name"], kinds))

    def test_texts_avoid_caption_band_and_two_at_once(self):
        for k, q, r in self.seqs():
            cy = q["captionStyle"]["y"]
            on_still = {x for e in q["msg"]["events"] if e["kind"] in ("title", "end", "freeze") for x in e["refs"].get("titles", [])}  # 정지 그림 위 (말 자막 없음)
            msgs = [t for t in q["titles"] if t.get("msg") and t["id"] not in on_still]
            for t in msgs:
                if t["style"].get("bgOn") or abs(t["style"]["y"] - cy) >= 0.15:
                    continue
                self.fail((q["name"], t["text"], t["style"]["y"], cy))
            for t in msgs:
                on = [u for u in msgs if u["start"] < t["start"] + 0.01 < u["start"] + u["dur"]]
                self.assertLessEqual(len(on), 3, (q["name"], t["text"], [u["text"] for u in on]))

    def test_shorts_are_vertical_without_end_screen(self):
        sh = [q for k, q, r in self.seqs() if q["format"] == "shorts"]
        self.assertTrue(sh)
        for q in sh:
            self.assertFalse(any(e["kind"] in ("end", "title", "montage") for e in q["msg"]["events"]), q["name"])
            self.assertLessEqual(editor.seq_total(q), 180)
            hk = [e for e in q["msg"]["events"] if e["kind"] == "hook"]
            self.assertEqual(len(hk), 1, q["name"])
            t = next(t for t in q["titles"] if t["id"] in hk[0]["refs"]["titles"])
            self.assertEqual((t["start"], t["dur"]), (0.0, 2.5))
            self.assertLess(t["style"]["y"], 0.3)

    def test_music_stops_before_punchline_only_when_generous(self):
        """듬뿍: 펀치라인 바로 앞에서 배경음악이 0.45초 비었다가 다시 (보통·담백은 그대로 이어짐)."""
        def gap_near(q, t):
            mus = [it for it in q["items"] if it["track"] in ("A3", "A4")]
            return not any(it["start"] <= t < editor.i_end(it) for it in mus)
        for k in ("보통", "듬뿍"):
            q = self.res[k]["sequences"][0]
            pl = [e for e in q["msg"]["events"] if e["kind"] == "inner" and ("웃음" in e["why"] or "농담" in e["why"])]
            if not pl:
                continue
            self.assertEqual(gap_near(q, pl[0]["t"] - 0.2), k == "듬뿍", (k, pl[0]))

    def test_export_smoke_and_auto_check(self):
        """예능 MSG형 · 보통 롱폼을 실제로 내보내 자동 검수: 고칠 것 0 · 소리 크기 목표대로 · 길이가 편집본과 같음."""
        q = self.res["보통"]["sequences"][0]
        info = editor.media_info(self.w.name)
        proj = dict(q, captions=editor._captions_of(editor._segments_of(self.w.name), info), info=info, source=self.w.name,
                    media=[{"id": "main", "kind": "video", "src": "videos", "file": self.w.name, "dur": info["duration"], "w": info["width"],
                            "h": info["height"], "fps": info.get("fps", 30.0), "audio": True}] + self.res["보통"]["media"])
        out = editor.export(self.w.name, proj, {"preset": "small", "hw": False, "xml": False, "srt": False}, lambda m: None)
        f = core.OUT / out[0]
        rep = qa.check_video(f, "long", proj["master"])
        self.assertEqual(rep["bad"], 0, rep)
        self.assertAlmostEqual(rep["duration"], editor.seq_total(q), delta=0.15)
        self.assertFalse([i for i in rep["items"] if i["title"] in ("소리 크기", "소리 깨짐 가능", "검은 화면") and i["lv"] != "ok"], rep)


if __name__ == "__main__":
    unittest.main()
