"""편집실 자막은 대사 소리를 따라감 (D-283·D-284) — 저장소 폴더에서 python3 -m unittest tests.test_caption_follow_audio

예전에는 자막이 V1 의 원본 영상 클립만 따라가서 L 컷(영상은 다음 장면, 소리는 앞말)에서 안 들리는 말이 뜨고 들리는 말엔 자막이 없었고,
B롤로 V1 을 덮은 동안 자막이 사라졌고, 보관함의 다른 영상을 이어 붙이면 그 부분 자막이 없었다.
- editor.caption_items · timeline_captions (내보내기·SRT·올리기 키트) 와 editor.html capItems · capsTL (미리보기) 이 같은 규칙인지 node 로 견줌
- 다른 영상 자막 editor.media_captions (그 영상 프로젝트 자막 → 없으면 받아쓰기) · media_bundle 의 captions · 올리기 키트 예비 길(upload.build_kit)
- 손대지 않은 V1·A1 짝 편집본은 예전과 같음 · 거꾸로·다시 보기(noCaps)·소리 끈 클립·트랙·효과음 트랙은 자막 없음."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core  # noqa: E402
import editor  # noqa: E402
from tests import test_captions  # noqa: E402

NODE = shutil.which("node")


def V(i, media, start, a, b, link=None, **kw):
    return dict({"id": i, "track": "V1", "media": media, "start": start, "in": a, "out": b, "speed": 1, "rev": False, "link": link,
                 "reframe": 0.5, "fit": "auto", "fx": {}, "color": {}}, **kw)


def A(i, media, start, a, b, link=None, track="A1", **kw):
    return dict({"id": i, "track": track, "media": media, "start": start, "in": a, "out": b, "speed": 1, "rev": False, "link": link,
                 "fx": {}, "gain": 0, "fadeIn": 0, "fadeOut": 0, "mute": False}, **kw)


CAPS = [  # 원본 시각 자막 (MSGRAW01 처럼)
    {"id": "c1", "start": 25.0, "end": 27.0, "text": "공을 먼저 보세요"},
    {"id": "c2", "start": 34.1, "end": 36.0, "text": "디딤발은 공 옆으로"},
    {"id": "c3", "start": 36.2, "end": 37.2, "text": "한 뼘 정도"},
    {"id": "c4", "start": 97.5, "end": 99.5, "text": "그제서야 어디로 갈지"},
    {"id": "c5", "start": 100.5, "end": 102.5, "text": "생각하시거든요"},
]
OTHER = [{"id": "o1", "start": 85.2, "end": 86.6, "text": "패스하고 바로 움직여요"}, {"id": "o2", "start": 87.0, "end": 88.0, "text": "좋아요"}]


def proj(items, tracks=None, media=None, fmt="long", caps=None):
    q = {"format": fmt, "items": items, "tracks": tracks or editor.default_tracks(), "captions": CAPS if caps is None else caps}
    if media is not None:
        q["media"] = media
    return q


def lcut():
    # L 컷: 영상은 10초에 다음 장면(원본 97초), 앞말 소리(A1)는 13.3초까지 이어짐 → 다음 장면 소리는 13.3초부터(원본 100.3초)
    return [V("v1", "main", 0, 24.0, 34.0, "LA"), A("a1", "main", 0, 24.0, 37.3, "LA"),
            V("v2", "main", 10.0, 97.0, 103.6, "LB"), A("a2", "main", 13.3, 100.3, 103.6, "LB")]


def broll():
    # B롤 덮어쓰기: V1 6~12초는 다른 영상 장면(소리 없음), 말(A1)은 계속
    return [V("v1", "main", 0, 24.0, 30.0, "L1"), A("a1", "main", 0, 24.0, 48.0, "L1"),
            V("vb", "brl", 6.0, 20.0, 26.0), V("v3", "main", 12.0, 36.0, 48.0, "L1")]


def append_other():
    return [V("v1", "main", 0, 24.24, 30.72, "L1"), A("a1", "main", 0, 24.24, 30.72, "L1"),
            V("v2", "oth", 6.48, 84.97, 88.33, "L2"), A("a2", "oth", 6.48, 84.97, 88.33, "L2")]


MEDIA = [{"id": "main", "kind": "video", "src": "videos", "file": "main.mp4"}, {"id": "brl", "kind": "video", "src": "assets", "file": "b.mp4"},
         {"id": "oth", "kind": "video", "src": "videos", "file": "20261008_MSGRAW02_패스 앤 무브 드릴.mp4"}]


def texts(caps):
    return [c["text"] for c in caps]


def rnd(caps):
    return [[round(c["start"], 2), round(c["end"], 2), c["text"]] for c in caps]


class CaptionItemsTest(unittest.TestCase):
    def test_lcut_follows_audio(self):
        caps = editor.timeline_captions(proj(lcut()))
        self.assertEqual(texts(caps), ["공을 먼저 보세요", "디딤발은 공 옆으로", "한 뼘 정도", "생각하시거든요"])
        # 들리는 앞말(원본 34.1초~)은 영상이 바뀐 10초 뒤에도 그 소리 자리(10.1초~)에
        self.assertAlmostEqual(caps[1]["start"], 10.1, 3)
        self.assertAlmostEqual(caps[3]["start"], 13.5, 3)  # 원본 100.5초 = 13.3 + 0.2
        self.assertNotIn("그제서야 어디로 갈지", texts(caps))  # 영상만 보이고 소리는 없는 말

    def test_broll_keeps_captions(self):
        caps = editor.timeline_captions(proj(broll()))
        self.assertIn("공을 먼저 보세요", texts(caps))
        c = next(c for c in caps if c["text"] == "디딤발은 공 옆으로")
        self.assertAlmostEqual(c["start"], 10.1, 3)  # B롤 6~12초 위 (원본 34.1초 = 타임라인 10.1초)

    def test_unlinked_dialog_track_voiceover(self):
        tr = editor.default_tracks()
        tr.append({"id": "A4", "k": "a", "role": "dialog", "mute": False, "solo": False, "vol": 0.0})
        caps = editor.timeline_captions(proj([V("v", "brl", 0, 0, 10), A("x", "main", 2.0, 25.0, 27.0, track="A4")], tracks=tr))
        self.assertEqual(rnd(caps), [[2.0, 4.0, "공을 먼저 보세요"]])

    def test_exclusions(self):
        base = [A("a", "main", 0, 24.0, 28.0)]
        self.assertEqual(len(editor.timeline_captions(proj(base))), 1)
        for kw in ({"mute": True}, {"rev": True}, {"noCaps": True}):
            self.assertEqual(editor.timeline_captions(proj([A("a", "main", 0, 24.0, 28.0, **kw)])), [], kw)
        tr = editor.default_tracks()
        next(t for t in tr if t["id"] == "A1")["mute"] = True
        self.assertEqual(editor.timeline_captions(proj(base, tracks=tr)), [])
        # 효과음 트랙(대사 역할 + voiceFx false) · 음악 트랙에 놓은 원본 소리는 자막 없음
        tr = editor.default_tracks()
        next(t for t in tr if t["id"] == "A2").update(role="dialog", voiceFx=False)
        self.assertEqual(editor.timeline_captions(proj([A("s", "main", 0, 24.0, 28.0, track="A2")], tracks=tr)), [])
        self.assertEqual(editor.timeline_captions(proj([A("m", "main", 0, 24.0, 28.0, track="A3")])), [])
        # 소리 클립(다른 트랙의 음악이라도)이 있으면 V1 영상만 있는 곳에는 자막 없음 (말소리를 따라감)
        self.assertEqual(editor.timeline_captions(proj([V("v", "main", 0, 24.0, 28.0), A("m", "main", 0, 24.0, 28.0, track="A3")])), [])

    def test_video_only_sequence_keeps_v1_rule(self):
        """소리 클립이 하나도 없는 편집본(영상만)은 따라갈 소리가 없으니 예전처럼 V1 원본 영상 — 예전 규칙 그대로."""
        items = [V("a", "main", 0, 24.0, 28.0), V("r", "main", 4, 33.0, 37.0, rev=True), V("b", "main", 8, 97.0, 100.0), V("o", "brl", 11, 0, 3)]
        out = editor.timeline_captions(proj(items))
        self.assertEqual(out, _old_rule(proj(items)))
        self.assertEqual(texts(out), ["공을 먼저 보세요", "그제서야 어디로 갈지"])

    def test_a1_always_counts(self):
        tr = editor.default_tracks()
        next(t for t in tr if t["id"] == "A1")["role"] = "music"
        self.assertEqual(len(editor.timeline_captions(proj([A("a", "main", 0, 24.0, 28.0)], tracks=tr))), 1)

    def test_pairs_unchanged(self):
        """손대지 않은 V1·A1 짝 편집본(가편집·MSG)은 예전 V1 규칙과 같은 자막."""
        items = []
        for k, (a, b) in enumerate([(24.0, 26.0), (26.0, 30.0), (33.5, 37.5), (97.0, 103.0)]):
            items += editor._pair(a, b, start=sum(y - x for x, y in [(24.0, 26.0), (26.0, 30.0), (33.5, 37.5), (97.0, 103.0)][:k]))
        new = editor.timeline_captions(proj(items))
        old = _old_rule(proj(items))
        self.assertEqual(new, old)
        self.assertTrue(new)
        # 쇼츠 형식(쇼츠형 나누기)도 같음
        self.assertEqual(editor.timeline_captions(proj(items, fmt="shorts")), _old_rule(proj(items, fmt="shorts")))

    def test_msg_call_without_tracks(self):
        """MSG 가 만들며 부를 때(tracks 없음)는 기본 트랙 — A1 소리 클립을 따라감."""
        items = editor._pair(24.0, 28.0)
        self.assertEqual(texts(editor.timeline_captions({"items": items, "captions": CAPS, "format": "long"})), ["공을 먼저 보세요"])

    def test_same_audio_on_two_dialog_tracks_once(self):
        tr = editor.default_tracks()
        next(t for t in tr if t["id"] == "A2")["role"] = "dialog"
        caps = editor.timeline_captions(proj([A("a", "main", 0, 24.0, 28.0), A("b", "main", 0, 24.0, 28.0, track="A2")], tracks=tr))
        self.assertEqual(rnd(caps), [[1.0, 3.0, "공을 먼저 보세요"]])

    def test_overlap_duplicate_keeps_words_once(self):
        caps = [{"id": "w", "start": 25.0, "end": 27.0, "text": "공을 먼저 보세요",
                 "words": [{"w": "공을", "s": 25.0, "e": 25.5}, {"w": "먼저", "s": 25.6, "e": 26.1}, {"w": "보세요", "s": 26.2, "e": 26.9}]}]
        tr = editor.default_tracks()
        next(t for t in tr if t["id"] == "A2")["role"] = "dialog"
        # A1 은 앞 두 낱말, A2 는 같은 자리에서 뒤 두 낱말까지 (0.3초 늦게 시작해 겹침)
        its = [A("a", "main", 0, 24.0, 26.15), A("b", "main", 1.3, 25.3, 28.0, track="A2")]
        out = editor.timeline_captions(proj(its, tracks=tr, caps=caps))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "공을 먼저 보세요")
        self.assertEqual([w["w"] for w in out[0]["words"]], ["공을", "먼저", "보세요"])


def _old_rule(proj):
    """v2.19 의 V1 규칙 (비교용 · 기준 커밋 701bc49 의 editor.timeline_captions 를 그대로 옮김)."""
    res, total = [], editor.seq_total(proj)
    vids = sorted([it for it in proj["items"] if it["track"] == "V1" and it["media"] == "main" and not it.get("rev") and not it.get("noCaps")],
                  key=lambda x: x["start"])
    shorts = editor.SHORTS_SPLIT and proj.get("format") == "shorts"
    for cap0 in proj.get("msgCaps") or proj["captions"]:
        for cap, ws in (editor._shorts_parts(cap0) if shorts else [(cap0, editor._cap_words(cap0))]):
            toks = str(cap.get("text") or "").split()
            for it in vids:
                a, b = max(cap["start"], it["in"]), min(cap["end"], it["out"])
                if b - a > 0.05:
                    s, e = editor.i_tl(it, a), min(editor.i_tl(it, b), total)
                    if e - s > 0.04:
                        keep = [k for k, w in enumerate(ws) if it["in"] <= (float(w["s"]) + float(w["e"])) / 2 < it["out"]] if ws else None
                        if keep is not None and not keep:
                            continue
                        c = {"start": s, "end": e, "text": cap["text"] if keep is None or len(keep) == len(ws) else " ".join(toks[k] for k in keep)}
                        if ws:
                            c["words"] = [{"w": ws[k].get("w", ""), "s": editor.i_tl(it, float(ws[k]["s"])), "e": editor.i_tl(it, float(ws[k]["e"]))} for k in keep]
                            c.update(_keep=keep, _n=len(ws), _toks=toks, _full=cap["text"])
                        c["_k"] = id(cap)
                        res.append(c)
    out = []
    for c in sorted(res, key=lambda x: x["start"]):
        p = out[-1] if out else None
        if p is not None and p["_k"] == c["_k"] and abs(c["start"] - p["end"]) <= editor.CAP_JOIN:
            p["end"] = max(p["end"], c["end"])
            if "words" in p and "words" in c:
                p["words"] = p["words"] + c["words"]
            else:
                p.pop("words", None)
            if "_keep" in p and "_keep" in c:
                p["_keep"] = sorted(set(p["_keep"]) | set(c["_keep"]))
                p["text"] = " ".join(p["_toks"][k] for k in p["_keep"]) if len(p["_keep"]) < p["_n"] else p["_full"]
            continue
        out.append(c)
    for k, c in enumerate(out):
        need = max(editor.CAP_MIN, len(re.sub(r"\s+", "", c["text"])) / editor.CAP_CPS)
        if c["end"] - c["start"] < need:
            n = out[k + 1] if k + 1 < len(out) else None
            if n is not None and n["start"] - c["start"] < need and n["end"] - c["start"] >= need + editor.CAP_MIN:
                n["start"] = c["start"] + need
            c["end"] = max(c["end"], min(c["start"] + need, n["start"] if n is not None else total))
    for c in out:
        for k in ("_k", "_keep", "_n", "_toks", "_full"):
            c.pop(k, None)
    return out


class OtherMediaTest(unittest.TestCase):
    """보관함의 다른 영상 클립 → 그 영상 자막 (그 영상 프로젝트 자막 → 없으면 받아쓰기)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="자막 소리 "))
        work = self.tmp / "작업"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.p = [mock.patch.multiple(core, **dirs), mock.patch.object(editor, "PROJECTS", work / "projects")]
        for x in self.p:
            x.start()
        (work / "projects").mkdir()
        self.name = MEDIA[2]["file"]
        (core.VIDEOS / self.name).write_bytes(b"x")
        editor._MCAPS.clear()

    def tearDown(self):
        for x in self.p:
            x.stop()
        editor._MCAPS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put_transcript(self):
        d = core.adir(self.name)
        d.mkdir(parents=True, exist_ok=True)
        segs = [{"start": 85.2, "end": 86.6, "text": "패스하고 바로 움직여요"}, {"start": 87.0, "end": 88.0, "text": "좋아요"}]
        (d / "transcript.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
        (d / "analysis.json").write_text(json.dumps({"silences": []}), encoding="utf-8")

    def test_project_captions_first(self):
        (editor.PROJECTS / f"{core.adir(self.name).name}.json").write_text(json.dumps({"captions": OTHER}, ensure_ascii=False), encoding="utf-8")
        caps = editor.timeline_captions(proj(append_other(), media=MEDIA))
        self.assertEqual(texts(caps), ["공을 먼저 보세요", "패스하고 바로 움직여요", "좋아요"])
        c = caps[1]
        self.assertAlmostEqual(c["start"], 6.48 + 85.2 - 84.97, 3)
        self.assertEqual(editor.media_captions(self.name), OTHER)

    def test_transcript_when_no_project(self):
        self.put_transcript()
        with mock.patch.object(editor, "media_info", lambda n: {"duration": 120, "width": 1920, "height": 1080, "fps": 30, "hdr": None}):
            caps = editor.timeline_captions(proj(append_other(), media=MEDIA))
            again = editor.media_captions(self.name)
        self.assertEqual(texts(caps)[1:], ["패스하고 바로 움직여요", "좋아요"])
        self.assertIs(again, editor.media_captions(self.name))  # 파일이 그대로면 다시 안 만듦 (자막 id 도 그대로)

    def test_no_transcript_no_media_list(self):
        self.assertEqual(editor.media_captions(self.name), [])
        self.assertEqual(editor.media_captions(self.name, "assets"), [])
        self.assertEqual(editor.media_captions("없는 영상.mp4"), [])
        # media 목록이 없으면(올리기 키트의 예전 길) 다른 영상 자막은 없음 · 원본 자막은 그대로
        (editor.PROJECTS / f"{core.adir(self.name).name}.json").write_text(json.dumps({"captions": OTHER}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(texts(editor.timeline_captions(proj(append_other()))), ["공을 먼저 보세요"])

    def test_media_bundle_has_captions(self):
        (editor.PROJECTS / f"{core.adir(self.name).name}.json").write_text(json.dumps({"captions": OTHER}, ensure_ascii=False), encoding="utf-8")
        ent = {"id": "x", "kind": "video", "src": "videos", "file": self.name, "dur": 120, "w": 1920, "h": 1080, "fps": 30, "audio": True}
        with mock.patch.object(editor, "media_entry", lambda f, src="videos": dict(ent, src=src)), \
                mock.patch.object(editor, "waveform", lambda *a, **k: None), mock.patch.object(editor, "thumbs", lambda *a, **k: None):
            self.assertEqual(editor.media_bundle(self.name)["captions"], OTHER)
            self.assertNotIn("captions", editor.media_bundle(self.name, "assets"))


class KitFallbackTest(test_captions.WorkDir):
    """올리기 키트를 내보낸 SRT 없이 만들 때(upload.build_kit 예비 길)도 이어 붙인 보관함 영상 클립 자막이 들어감 (내보내기 SRT 와 같은 규칙)."""

    def test_kit_without_srt_uses_other_media_captions(self):
        import upload
        name = self.video("가로 강의.mp4", "320x180", 15.0)
        self.transcript(name, test_captions.sentence_segs())
        proj = editor.load_project(name)
        other = self.video("다른 영상.mp4", "320x180", 6.0)
        q = next(s for s in proj["sequences"] if s["format"] == "long")
        t = editor.seq_total(q)
        proj["media"] = list(proj.get("media") or []) + [{"id": "oth", "kind": "video", "src": "videos", "file": other, "dur": 6.0, "audio": True}]
        q["items"] += [V("ov", "oth", t, 0.0, 4.0, "LO"), A("oa", "oth", t, 0.0, 4.0, "LO")]
        editor._ppath(name).write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        said = [{"id": "o1", "start": 0.5, "end": 2.5, "text": "다른 영상에서 한 말은 여기 있어요."}]
        with mock.patch.object(editor, "media_captions", lambda f, src="videos": said if f == other else []):
            kit = upload.build_kit(name, q["id"], save=False)
        self.assertIn("다른 영상에서 한 말은 여기 있어요", kit["prompt"])
        self.assertIn(test_captions.SENTS[0].rstrip("."), kit["prompt"])


@unittest.skipUnless(NODE, "node 가 없어요")
class PreviewParityTest(unittest.TestCase):
    """미리보기 editor.html capsTL 이 내보내기 timeline_captions 와 같은 자막을 내는지 (node 로 그대로 실행)."""

    def run_js(self, cases):
        src = (ROOT / "editor.html").read_text(encoding="utf-8")
        out = []
        for name in ("const FPS =", "const EPS =", "const sp =", "const iEnd =", "const iLen =", "const mt =", "const tlOf =", "const aTracks ="):
            i = src.index(name)
            out.append(src[i:src.index("\n", i)])
        i = src.index("function capWords(")
        out.append(src[i:src.index("// 한 줄 자막이 화면 밖으로", i)])
        prog = "let S, P, ver = 0;\nconst total = () => Math.max(0, ...S.items.map(iEnd));\n" + "\n".join(out) + """
const inp = JSON.parse(require('fs').readFileSync(0, 'utf8')), res = [];
for (const c of inp) {
  S = { items: c.items, tracks: c.tracks, format: c.format }; P = { captions: c.captions };
  for (const k in MCAPS) delete MCAPS[k]; Object.assign(MCAPS, c.mcaps || {}); ver++;
  res.push(capsTL().map(x => [x.s, x.e, x.text, x.mid]));
}
process.stdout.write(JSON.stringify(res));"""
        r = subprocess.run([NODE, "-e", prog], input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_parity(self):
        tr_m = editor.default_tracks()
        next(t for t in tr_m if t["id"] == "A1")["mute"] = True
        tr_v = editor.default_tracks()
        tr_v.append({"id": "A4", "k": "a", "role": "dialog", "mute": False})
        tr_d = editor.default_tracks()
        next(t for t in tr_d if t["id"] == "A2")["role"] = "dialog"
        cases = [proj(lcut()), proj(broll()), proj(lcut(), fmt="shorts"), proj(broll(), tracks=tr_m),
                 proj([V("v", "brl", 0, 0, 10), A("x", "main", 2.0, 25.0, 27.0, track="A4"), A("r", "main", 5, 33.0, 38.0, rev=True)], tracks=tr_v),
                 proj([A("a", "main", 0, 24.0, 28.0), A("b", "main", 0, 24.0, 28.0, track="A2")], tracks=tr_d),
                 proj(append_other(), media=MEDIA), proj([V("a", "main", 0, 24.0, 28.0), V("b", "main", 8, 97.0, 100.0)])]
        js = self.run_js([dict(c, mcaps={"oth": OTHER}) for c in cases])
        with mock.patch.object(editor, "media_captions", lambda f, src="videos": OTHER if f == MEDIA[2]["file"] else []):
            for c, j in zip(cases, js):
                py = editor.timeline_captions(c)
                self.assertEqual([[round(x["start"], 4), round(x["end"], 4), x["text"]] for x in py],
                                 [[round(a, 4), round(b, 4), t] for a, b, t, _ in j], c["items"][0]["id"])
        self.assertEqual([m for *_, m in js[-2]], ["main", "oth", "oth"])
        self.assertTrue(all(js[k] for k in (0, 1, 2, 4, 5, 7)))
        self.assertEqual(js[3], [])  # 대사 트랙(A1) 소리를 끄면 자막도 없음


if __name__ == "__main__":
    unittest.main()
