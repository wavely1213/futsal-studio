"""보관함에 들어오는 촬영본 살피기 테스트 (D-460~D-464 · BR-270~BR-272).

- 폴더째 넣은 촬영본(휴대폰·SD카드 날짜 폴더): intake.nested 폴더별 영상 수 · intake.unnest 보관함 바로 안으로 꺼내기
  (겹치는 이름에 폴더 이름 · 복사 중·잠긴 파일은 그대로 · 빈 폴더만 지움) · /api/state 의 nested · /api/unnest
- 화질: intake.quality_flag(카카오톡 이름 · 720p 이하 낮은 비트레이트) · intake.media(뒤에서 읽어 둠) · 실제 ffmpeg 로 읽기
- 화면(ui.html, node): 한 촬영의 조각 판단(shootPieces) · [편집점 찾기]가 먼저 묶기를 권함 · 해상도·비트레이트 글 · 폴더 줄
실행: 저장소 폴더에서 python3 -m unittest tests.test_footage_intake
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import core  # noqa: E402
import intake  # noqa: E402


QUIET = lambda m: None  # noqa: E731


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="폴더째 넣은 촬영본 "))
        work = self.tmp / "풋살 작업 폴더"
        self.dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.videos = self.dirs["VIDEOS"]
        self.patches = [mock.patch.object(core, k, v) for k, v in self.dirs.items()]
        self.patches += [mock.patch.object(intake, "_writer_open", return_value=False)]
        for p in self.patches:
            p.start()
        intake._NEST.clear()
        intake._SEEN.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        intake._NEST.clear()
        intake._SEEN.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, rel, size=100, age=3600):
        p = self.videos / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:
            f.truncate(size)
        t = time.time() - age
        os.utime(p, (t, t))
        return p


class NestedTests(Base):
    def test_videos_in_subfolders_are_counted_per_top_folder(self):
        self.put("바로 안.mp4")
        self.put("10월5일 레슨/IMG_5001.MOV", 2_000_000)
        self.put("10월5일 레슨/IMG_5002.MOV", 3_000_000)
        self.put("10월5일 레슨/사진.JPG")
        self.put("SD카드/DCIM/100MEDIA/DJI_0001.MP4")
        self.put("SD카드/PRIVATE/AVCHD/BDMV/STREAM/00001.MTS")  # 캠코더: 깊은 폴더 · 못 쓰는 형식도 셈 (꺼내면 [MP4로 바꾸기])
        self.put("바꾸기 전 원본/00009.MTS")  # 앱이 쓰는 폴더
        self.put(".숨김/a.mp4")
        self.put("사진만/b.jpg")
        got = intake.nested(self.videos)
        self.assertEqual([(r["folder"], r["count"], r["copying"]) for r in got], [("10월5일 레슨", 2, 0), ("SD카드", 2, 0)])
        self.assertEqual(got[0]["size_mb"], 5.0)
        self.assertEqual([v["name"] for v in core.local_videos()], ["바로 안.mp4"], "목록은 예전처럼 바로 안의 영상만")

    def test_still_copying_files_are_counted(self):
        self.put("폰/IMG_1.MOV")
        self.put("폰/IMG_2.MOV", age=0)  # 방금 바뀜
        self.assertEqual(intake.nested(self.videos)[0]["copying"], 1)

    def test_result_is_remembered_for_a_moment_unless_fresh(self):
        self.put("폰/IMG_1.MOV")
        self.assertEqual(intake.nested(self.videos)[0]["count"], 1)
        self.put("폰/IMG_2.MOV")
        self.assertEqual(intake.nested(self.videos)[0]["count"], 1, "1초마다 부르는 목록 → 잠깐 기억")
        self.assertEqual(intake.nested(self.videos, fresh=True)[0]["count"], 2)

    def test_depth_and_entry_limits(self):
        self.put("a/1/2/3/4/5/6/7/deep.mp4")
        self.assertEqual(intake.nested(self.videos), [], "NEST_DEPTH 밑은 보지 않음")
        for i in range(30):
            self.put(f"많음/{i:03d}.jpg")
        self.put("많음/zz.mp4")
        with mock.patch.object(intake, "NEST_MAX", 20):
            self.assertEqual(intake.nested(self.videos, fresh=True), [], "살펴볼 수를 넘으면 그만 봄 (목록이 느려지지 않게)")

    @unittest.skipIf(sys.platform == "win32", "심볼릭 링크 권한")
    def test_symlinked_folder_is_not_followed(self):
        real = self.tmp / "밖 폴더"
        real.mkdir()
        (real / "x.mp4").write_bytes(b"0")
        os.symlink(real, self.videos / "링크")
        self.assertEqual(intake.nested(self.videos), [])


class UnnestTests(Base):
    def test_moves_videos_out_and_keeps_other_files(self):
        self.put("10월5일 레슨/IMG_5001.MOV", 1234)
        self.put("10월5일 레슨/깊이/IMG_5002.mp4")
        self.put("10월5일 레슨/사진.JPG")
        logs = []
        r = intake.unnest(self.videos, "10월5일 레슨", logs.append)
        self.assertEqual(sorted(m["name"] for m in r["moved"]), ["IMG_5001.MOV", "IMG_5002.mp4"])
        self.assertEqual(r["kept"], [])
        self.assertEqual((self.videos / "IMG_5001.MOV").stat().st_size, 1234)
        self.assertTrue((self.videos / "10월5일 레슨" / "사진.JPG").exists(), "사진은 그대로")
        self.assertFalse((self.videos / "10월5일 레슨" / "깊이").exists(), "비운 폴더는 지움")
        self.assertEqual(sorted(v["name"] for v in core.local_videos()), ["IMG_5001.MOV", "IMG_5002.mp4"])
        self.assertEqual(intake.nested(self.videos), [], "꺼낸 뒤 줄이 바로 사라짐")
        self.assertTrue(any("IMG_5001.MOV" in x for x in logs))

    def test_empty_top_folder_is_removed(self):
        self.put("폰/IMG_1.MOV")
        intake.unnest(self.videos, "폰", QUIET)
        self.assertFalse((self.videos / "폰").exists())

    def test_same_name_gets_folder_name_and_never_overwrites(self):
        self.put("IMG_0001.MOV", 10)
        self.put("10월5일/IMG_0001.MOV", 20)
        self.put("10월6일/img_0001.mov", 30)  # Windows 는 대소문자를 안 가림
        intake.unnest(self.videos, "10월5일", QUIET)
        r = intake.unnest(self.videos, "10월6일", QUIET)
        self.assertEqual((self.videos / "IMG_0001.MOV").stat().st_size, 10, "원래 영상은 그대로")
        self.assertEqual((self.videos / "IMG_0001 (10월5일).MOV").stat().st_size, 20)
        self.assertEqual(r["moved"], [{"name": "img_0001 (10월6일).mov", "from": "10월6일/img_0001.mov"}])

    def test_same_stem_or_old_work_gets_folder_name(self):
        self.put("IMG_0002.mp4")  # 확장자만 다른 영상 → 분석 폴더 이름이 겹침
        (self.dirs["ANALYSIS"] / "IMG_0003").mkdir()  # 지운 영상의 받아쓰기가 남은 이름 → 남의 작업을 이어 받지 않게
        (self.dirs["WORK"] / "projects").mkdir()
        (self.dirs["WORK"] / "projects" / "IMG_0004.json").write_text("{}", encoding="utf-8")
        for n in ("IMG_0002.MOV", "IMG_0003.MOV", "IMG_0004.MOV", "IMG_0005.MOV"):
            self.put(f"폰/{n}")
        r = intake.unnest(self.videos, "폰", QUIET)
        self.assertEqual(sorted(m["name"] for m in r["moved"]),
                         ["IMG_0002 (폰).MOV", "IMG_0003 (폰).MOV", "IMG_0004 (폰).MOV", "IMG_0005.MOV"])

    def test_two_same_names_in_one_folder_tree(self):
        self.put("SD/DCIM/100MEDIA/DJI_0001.MP4", 1)
        self.put("SD/DCIM/101MEDIA/DJI_0001.MP4", 2)
        r = intake.unnest(self.videos, "SD", QUIET)
        self.assertEqual(sorted(m["name"] for m in r["moved"]), ["DJI_0001 (101MEDIA).MP4", "DJI_0001.MP4"])

    def test_copying_and_locked_files_stay(self):
        self.put("폰/a.MOV")
        self.put("폰/b.MOV")
        self.put("폰/c.MOV")
        import updater
        orig = updater.replace_retry

        def rep(src, dst, secs=None):
            if Path(src).name == "c.MOV":
                raise PermissionError(32, "다른 프로그램이 쓰는 중")
            return orig(src, dst, secs)
        with mock.patch.object(intake, "busy", side_effect=lambda p: Path(p).name == "b.MOV"), \
                mock.patch.object(updater, "replace_retry", side_effect=rep):
            r = intake.unnest(self.videos, "폰", lambda m: None)
        self.assertEqual([m["name"] for m in r["moved"]], ["a.MOV"])
        self.assertEqual(sorted((k["name"], k["why"]) for k in r["kept"]), [("폰/b.MOV", "copying"), ("폰/c.MOV", "locked")])
        self.assertTrue((self.videos / "폰" / "b.MOV").exists() and (self.videos / "폰" / "c.MOV").exists())

    def test_bad_folder_names_refused(self):
        self.put("바꾸기 전 원본/x.MTS")
        for bad in ("", "..", "../videos", "바꾸기 전 원본", "없는 폴더", "a/b"):
            with self.assertRaises(FileNotFoundError, msg=bad):
                intake.unnest(self.videos, bad, QUIET)


class QualityTests(Base):
    def test_flags(self):
        f = intake.quality_flag
        self.assertEqual(f("KakaoTalk_20261008_160233.mp4", None), "kakao", "이름만으로도")
        self.assertEqual(f("kakaotalk_1.mp4", {"w": 1920, "h": 1080, "kbps": 9000}), "kakao")
        self.assertEqual(f("IMG.mp4", {"w": 1280, "h": 720, "kbps": 2100}), "low")
        self.assertEqual(f("IMG.mp4", {"w": 720, "h": 1280, "kbps": 2100}), "low", "세로 영상")
        self.assertIsNone(f("IMG.mp4", {"w": 1280, "h": 720, "kbps": 6500}), "폰 원본 720p")
        self.assertIsNone(f("IMG.mp4", {"w": 1920, "h": 1080, "kbps": 2000}), "1080p 는 알리지 않음")
        self.assertEqual(f("IMG.mp4", {"w": 640, "h": 360, "kbps": 9000}), "low", "480p 밑은 비트레이트와 상관없이")
        self.assertIsNone(f("IMG.mp4", {"w": 1280, "h": 720, "kbps": 0}), "비트레이트를 모르면 알리지 않음")
        self.assertIsNone(f("IMG.mp4", {}))

    def test_media_is_read_in_background_then_shown(self):
        self.put("KakaoTalk_1.mp4")
        self.put("IMG_1.MOV")
        self.put("들어오는 중.mp4", age=0)
        calls = []
        info = {"w": 1280, "h": 720, "kbps": 2000, "dur": 12.0, "created": 1.7e9}

        def fake(p):
            calls.append(Path(p).name)
            return dict(info)
        with mock.patch.object(intake, "_probe_info", side_effect=fake):
            rows = [{"name": "KakaoTalk_1.mp4"}, {"name": "IMG_1.MOV"}, {"name": "들어오는 중.mp4", "copying": True}]
            intake.media(rows, self.videos)
            self.assertNotIn("media", rows[1], "처음엔 기다리지 않음")
            self.assertEqual(rows[0]["lowq"], "kakao")
            for _ in range(100):
                if not intake._INFO_RUN["on"]:
                    break
                time.sleep(0.02)
            rows = [{"name": "KakaoTalk_1.mp4"}, {"name": "IMG_1.MOV"}, {"name": "들어오는 중.mp4", "copying": True}]
            intake.media(rows, self.videos)
        self.assertEqual(rows[1]["media"], info)
        self.assertEqual(rows[1]["lowq"], "low")
        self.assertNotIn("media", rows[2])
        self.assertEqual(sorted(calls), ["IMG_1.MOV", "KakaoTalk_1.mp4"], "복사 중인 파일은 안 읽음 · 한 번씩만")
        intake._INFO.clear()

    def test_real_file_resolution_bitrate_and_shot_time(self):
        ff = core.ffmpeg()
        p = self.videos / "KakaoTalk_20261008_160233.mp4"
        r = core.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3", "-f", "lavfi", "-i",
                      "sine=frequency=300:duration=3", "-shortest", "-c:v", "libx264", "-b:v", "1500k", "-c:a", "aac", "-b:a", "96k",
                      "-metadata", "creation_time=2026-10-05T07:30:00.000000Z", str(p)])
        if r.returncode:
            self.skipTest("ffmpeg 로 시험 영상을 못 만듦")
        got = intake._probe_info(p)
        self.assertEqual((got["w"], got["h"]), (1280, 720))
        self.assertTrue(500 < got["kbps"] < intake.LOW_KBPS, got)
        self.assertAlmostEqual(got["dur"], 3.0, delta=0.2)
        self.assertEqual(got["created"], 1791185400.0)
        self.assertEqual(intake.quality_flag("IMG_1.mp4", got), "low")
        self.assertEqual(intake._probe_info(self.videos / "없음.mp4"), {})


class RouteTests(Base):
    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(app, "PORT", port), mock.patch.object(app, "LOGFILE", self.tmp / "studio.log"),
                   mock.patch.object(intake, "_probe_info", return_value={})]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for _ in range(100):
            if not intake._INFO_RUN["on"]:
                break
            time.sleep(0.02)
        for p in self.p2:
            p.stop()
        intake._INFO.clear()
        super().tearDown()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_state_lists_folder_and_unnest_moves(self):
        self.put("10월5일 레슨/IMG_5001.MOV")
        self.put("KakaoTalk_1.mp4")
        code, s = self.call("/api/state?since=0")
        self.assertEqual(code, 200)
        self.assertEqual(s["nested"], [{"folder": "10월5일 레슨", "count": 1, "size_mb": 0.0, "copying": 0}])
        by = {v["name"]: v for v in s["local"]}
        self.assertEqual(by["KakaoTalk_1.mp4"]["lowq"], "kakao")
        self.assertIsInstance(by["KakaoTalk_1.mp4"]["mtime"], int)
        code, r = self.call("/api/unnest", {"folder": "없는 폴더"})
        self.assertEqual(code, 404)
        code, r = self.call("/api/unnest", {"folder": "10월5일 레슨"})
        self.assertEqual((code, r["ok"], [m["name"] for m in r["moved"]]), (200, True, ["IMG_5001.MOV"]))
        code, s = self.call("/api/state?since=0")
        self.assertEqual(s["nested"], [])
        self.assertIn("IMG_5001.MOV", {v["name"] for v in s["local"]})
        self.assertFalse(self.app.JOB["name"], "작업 자리를 놓음")

    def test_unnest_refused_while_job_runs(self):
        self.put("폰/IMG_1.MOV")
        self.app.JOB["name"] = "편집점 찾기"
        try:
            code, r = self.call("/api/unnest", {"folder": "폰"})
        finally:
            self.app.JOB["name"] = None
        self.assertEqual(code, 409)
        self.assertTrue((self.videos / "폰" / "IMG_1.MOV").exists())

    def test_route_is_classified_for_remote(self):
        import rstudio
        self.assertEqual(rstudio.classify("POST", "/api/unnest"), rstudio.WRITE)


NODE_UI = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const cut = (a, b) => { const i = src.indexOf(a), j = src.indexOf(b, i); if (i < 0 || j < 0) throw new Error("못 찾음 " + a); return src.slice(i, j); };
const els = {};
const el = id => els[id] || (els[id] = { id, hidden: true, value: "", textContent: "", innerHTML: "", disabled: false, scrollIntoView() {}, onclick: null });
const $ = el, esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const srcOf = v => (v && v.source) || { kind: "unknown" };
const state = { job: null, local: [], nested: [] };
const calls = [];
const showFail = (f, jb) => calls.push(["fail", f, jb]);
const clearFail = p => calls.push(["clear", p]);
const job = (path, body) => calls.push(["job", path, body]);
const confirm = () => true, prompt = () => "촬영본", toast = m => calls.push(["toast", m]);
const segVal = () => "small", againMsg = () => "", api = async () => ({}), analyzed = () => {}, bundleDone = () => {};
let lastLocalKey = "", poll = () => {};
eval(cut('function analyzeNames(names, changedFile, separate) {', 'function againMsg(') + cut('function bundleNames(names) {', '/* 끝: 2-1d */')
  + 'globalThis.T = { analyzeNames, shootPieces, qualityOf, nestRows, bundleNames };');
const day = Date.UTC(2026, 9, 5, 3, 0, 0) / 1000;  // 그날 낮 (어느 시간대든 같은 날 안)
const mk = (name, mtime, extra) => ({ name, mtime, source: { kind: "footage" }, ...extra });
const out = {};
out.twoClose = T.shootPieces([mk("IMG_1.MOV", day), mk("IMG_2.MOV", day + 300)]);
out.one = T.shootPieces([mk("IMG_1.MOV", day)]);
out.otherDay = T.shootPieces([mk("IMG_1.MOV", day), mk("IMG_2.MOV", day + 86400)]);
out.farApart = T.shootPieces([mk("IMG_1.MOV", day), mk("IMG_2.MOV", day + 7200)]);
out.longPieces = T.shootPieces([mk("A.MP4", day, { media: { created: day, dur: 1500 } }), mk("B.MP4", day + 1600, { media: { created: day + 1600, dur: 1500 } })]);
out.mixedCreated = T.shootPieces([mk("A.MP4", day, { media: { created: day - 30000 } }), mk("B.MP4", day + 60)]);  // 둘 다 알 때만 촬영 시각
out.otherSource = T.shootPieces([mk("IMG_1.MOV", day), { ...mk("IMG_2.MOV", day + 60), source: { kind: "other" } }]);
out.analyzed = T.shootPieces([mk("IMG_1.MOV", day), mk("IMG_2.MOV", day + 60, { analyzed: true })]);
out.bundled = T.shootPieces([mk("묶음_20261005_촬영본.mp4", day), mk("IMG_2.MOV", day + 60)]);
out.noTime = T.shootPieces([mk("IMG_1.MOV", 0), mk("IMG_2.MOV", day)]);
state.local = [mk("IMG_1.MOV", day), mk("IMG_2.MOV", day + 300), { ...mk("유튜브.mp4", day + 400), source: { kind: "own" } }];
calls.length = 0; T.analyzeNames(["IMG_1.MOV", "IMG_2.MOV"]);
const hint = calls.find(c => c[0] === "fail");
out.hint = hint && { kind: hint[1].kind, actions: hint[1].actions, labels: hint[1].labels, msg: hint[1].msg, jobs: calls.filter(c => c[0] === "job").length };
calls.length = 0; hint[2].on.separate();
out.separate = calls.filter(c => c[0] === "job").map(c => [c[1], c[2].names]);
calls.length = 0; hint[2].on.bundle();
out.bundle = calls.filter(c => c[0] === "job").map(c => [c[1], c[2].names]);
calls.length = 0; T.analyzeNames(["IMG_1.MOV", "유튜브.mp4"]);
out.mixedJob = calls.filter(c => c[0] === "job").map(c => c[1]);
calls.length = 0; T.analyzeNames(["IMG_1.MOV", "IMG_2.MOV"], true);
out.changedJob = calls.filter(c => c[0] === "job").map(c => c[1]);
out.qKakaoOther = T.qualityOf({ name: "KakaoTalk_1.mp4", lowq: "kakao", source: { kind: "other" }, media: { w: 1280, h: 720, kbps: 2100 } });
out.qLowOwn = T.qualityOf({ name: "x.mp4", lowq: "low", source: { kind: "own" }, media: { w: 1280, h: 720, kbps: 2100 } });
out.qLowFoot = T.qualityOf({ name: "x.mp4", lowq: "low", source: { kind: "footage" }, media: { w: 3840, h: 2160, kbps: 45000 } });
out.qNone = T.qualityOf({ name: "x.mp4" });
out.rowReady = T.nestRows([{ folder: "10월5일 레슨", count: 3, size_mb: 1500, copying: 0 }]);
out.rowWait = T.nestRows([{ folder: "폰<b>", count: 2, size_mb: 10, copying: 1 }]);
console.log(JSON.stringify(out));
"""


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node 없음")
        r = subprocess.run(["node", "-e", NODE_UI, str(REPO / "ui.html")], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr)
        cls.o = json.loads(r.stdout)

    def test_shoot_pieces(self):
        o = self.o
        self.assertTrue(o["twoClose"])
        self.assertTrue(o["longPieces"], "긴 조각은 길이만큼 더 떨어져도 한 촬영")
        self.assertTrue(o["mixedCreated"], "촬영 시각을 하나만 알면 둘 다 파일 시각으로")
        for k in ("one", "otherDay", "farApart", "otherSource", "analyzed", "bundled", "noTime"):
            self.assertFalse(o[k], k)

    def test_analyze_suggests_bundle_first(self):
        h = self.o["hint"]
        self.assertEqual((h["kind"], h["actions"], h["jobs"]), ("pieces", ["bundle", "separate"], 0))
        self.assertEqual(h["labels"], {"bundle": "묶고 편집점 찾기", "separate": "따로 찾기"})
        self.assertIn("한 촬영의 조각 같아요", h["msg"])
        self.assertEqual(self.o["separate"], [["/api/analyze", ["IMG_1.MOV", "IMG_2.MOV"]]])
        self.assertEqual(self.o["bundle"], [["/api/bundle", ["IMG_1.MOV", "IMG_2.MOV"]]])
        self.assertEqual(self.o["mixedJob"], ["/api/analyze"], "내 촬영본이 아닌 영상이 섞이면 예전처럼")
        self.assertEqual(self.o["changedJob"], ["/api/analyze"], "파일이 바뀐 영상 다시 찾기는 묻지 않음")

    def test_quality_text_and_warnings(self):
        o = self.o
        self.assertEqual(o["qKakaoOther"]["txt"], "1280×720 · 2.1Mbps")
        self.assertIn("카카오톡", o["qKakaoOther"]["warn"])
        self.assertEqual(o["qLowOwn"]["warn"], "", "받은 유튜브 영상은 화질을 알리지 않음")
        self.assertIn("원본 파일로", o["qLowFoot"]["warn"])
        self.assertEqual(o["qLowFoot"]["txt"], "3840×2160 · 45Mbps")
        self.assertEqual(o["qNone"], {"txt": "", "warn": ""})

    def test_folder_rows(self):
        r, w = self.o["rowReady"], self.o["rowWait"]
        self.assertIn("'10월5일 레슨' 폴더", r)
        self.assertIn("영상 3개가 폴더 안에 있어요", r)
        self.assertIn("1.5GB", r)
        self.assertEqual(r.count('data-unnest="10월5일 레슨"'), 2, "줄 안 글 단추 + 오른쪽 단추 (좁은 화면은 오른쪽 단추가 숨음)")
        self.assertIn("아직 복사 중이에요 (1개)", w)
        self.assertIn('data-wait="1" disabled', w)
        self.assertNotIn("<b>", w, "폴더 이름은 글로만")


if __name__ == "__main__":
    unittest.main()
