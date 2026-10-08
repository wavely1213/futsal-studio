"""받기를 튼튼하게: 유튜브 영상 형식 고르기(H.264 먼저) · 모델 받기 길이 확인·이어받기 · 예전에 받다 끊긴 반쪽 모델.

- 영상: yt-dlp 기본 정렬은 AV1 > VP9 > H.264 라 같은 1080p 면 '399+140'(AV1)을 골랐다 → 편집실 미리보기·내보내기가 무거움.
  이제 같은 해상도·fps 면 H.264(avc1) 먼저, 없으면 지금 규칙 그대로 (진짜 yt-dlp 의 형식 고르기에 가짜 형식 목록을 넣어 확인).
- 모델: updater.download 는 받은 길이를 Content-Length 와 비교하지 않아, 40MB 중 36MB 에서 끊긴 누끼 모델이 완성본으로 저장되고
  이후 늘 그 파일을 써서 누끼가 계속 고장 났다. 다시 받아도 0바이트부터였다 (Range 없음).
  이제 길이가 모자라면 오류 · 받은 데까지 남겨 그 자리부터 이어받음(Range·If-Range) · 예전에 남은 반쪽 ONNX 는 지우고 다시 받음.
인터넷은 쓰지 않는다 (테스트 안에서 띄운 http.server 가 중간에 끊거나 이어받기를 흉내 냄).
실행: python3 -m unittest tests.test_downloads
"""
import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
import detect  # noqa: E402
import thumb  # noqa: E402
import updater  # noqa: E402

try:
    import yt_dlp
except ImportError:  # 개발 PC 에 없으면 형식 고르기 시험은 건너뜀
    yt_dlp = None


def onnx_like(n=300_000):
    """ONNX 꼴(protobuf) 파일: ir_version 칸 + 큰 graph 칸 하나 (진짜 모델처럼 맨 위 칸 길이가 파일 끝까지)."""
    body = bytes(range(256)) * (n // 256 + 1)
    body = body[:n]
    ln, v = b"", n
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            ln += bytes([b | 0x80])
        else:
            ln += bytes([b])
            break
    return b"\x08\x07" + b"\x3a" + ln + body


OPSET = b"\x42\x04\x0a\x00\x10\x0b"  # 맨 위 8번 칸 opset_import {domain "", version 11} — 진짜 ONNX 처럼 graph 뒤에 오는 작은 칸 (6바이트)


def last_field_start(data):
    """맨 위 protobuf 칸들 중 마지막 칸이 시작하는 자리 (그 앞에서 자르면 칸 경계라 thumb.model_whole 은 모양만 보고 온전으로 봄)."""
    pos = last = 0
    while pos < len(data):
        last = pos
        key, i = thumb._varint(data, pos)
        wire = key & 7
        if wire == 0:
            pos = thumb._varint(data, i)[1]
        elif wire == 2:
            n, i = thumb._varint(data, i)
            pos = i + n
        else:
            pos = i + (8 if wire == 1 else 4)
    return last


class Srv:
    """파일 하나를 주는 가짜 서버. cut: 응답마다 이만큼만 보내고 연결을 끊음 (Content-Length 는 다 보낼 것처럼).
    ranges: Range 를 받아 줌 · etag 가 바뀌면 If-Range 가 안 맞아 처음부터(200).
    ifrange=False: If-Range 를 무시하는 CDN (바뀐 파일이어도 206) · etag=None: ETag 를 안 보냄."""

    def __init__(self, payload):
        self.payload, self.etag, self.cut, self.ranges, self.ifrange, self.log = payload, '"v1"', None, True, True, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rng, ifr = self.headers.get("Range"), self.headers.get("If-Range")
                outer.log.append({"path": self.path, "range": rng, "ifRange": ifr})
                data, start = outer.payload, 0
                if rng and outer.ranges and (ifr is None or ifr == outer.etag or not outer.ifrange):
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                part = data[start:]
                self.send_header("Content-Length", str(len(part)))
                if outer.etag:
                    self.send_header("ETag", outer.etag)
                self.end_headers()
                send = part if outer.cut is None else part[:outer.cut]
                outer.log[-1]["sent"] = len(send)
                self.wfile.write(send)
                self.wfile.flush()
                self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(unittest.TestCase):
    PAYLOAD = onnx_like()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="받기 시험 "))
        self.srv = Srv(self.PAYLOAD)

    def tearDown(self):
        self.srv.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class UpdaterDownloadTests(Base):
    def test_cut_connection_is_an_error_not_a_file(self):
        """재현: 90% 에서 끊김 → 예전에는 오류 없이 반쪽 파일을 돌려줌 · 이제 IncompleteRead (받기 오류 NET_ERRORS 안)."""
        self.srv.cut = len(self.PAYLOAD) * 9 // 10
        dest = self.tmp / "m.part"
        with self.assertRaises(http.client.IncompleteRead) as cm:
            updater.download(self.srv.url + "/m.onnx", dest)
        self.assertIsInstance(cm.exception, updater.NET_ERRORS)
        self.assertEqual(updater._why(cm.exception), "받는 도중 연결이 끊겼어요")

    def test_resume_continues_from_where_it_stopped(self):
        """이어받기: 끊긴 뒤 다시 받으면 Range·If-Range 로 남은 부분만 받아 이어 붙임 → 같은 파일 · 기록은 지움."""
        dest = self.tmp / "m.part"
        self.srv.cut = 100_000
        with self.assertRaises(http.client.IncompleteRead):
            updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.assertEqual(dest.stat().st_size, 100_000)
        meta = json.loads(updater._resume_meta(dest).read_text(encoding="utf-8"))
        self.assertEqual((meta["total"], meta["etag"]), (len(self.PAYLOAD), '"v1"'))
        self.srv.cut = None
        seen = []
        updater.download(self.srv.url + "/m.onnx", dest, lambda got, total: seen.append((got, total)), resume=True)
        self.assertEqual(dest.read_bytes(), self.PAYLOAD)
        self.assertEqual(self.srv.log[-1]["range"], "bytes=100000-")
        self.assertEqual(self.srv.log[-1]["ifRange"], '"v1"')
        self.assertEqual(self.srv.log[-1]["sent"], len(self.PAYLOAD) - 100_000, "남은 부분만 받음")
        self.assertGreater(seen[0][0], 100_000, "진행 표시는 이미 받은 것까지 셈")
        self.assertEqual(seen[-1], (len(self.PAYLOAD), len(self.PAYLOAD)))
        self.assertFalse(updater._resume_meta(dest).exists())

    def test_changed_server_file_restarts_from_zero(self):
        """서버 파일이 바뀌었으면(ETag 다름) If-Range 가 안 맞아 서버가 처음부터(200) → 이어 붙이지 않고 새로 씀."""
        dest = self.tmp / "m.part"
        self.srv.cut = 50_000
        with self.assertRaises(http.client.IncompleteRead):
            updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.srv.cut, self.srv.etag = None, '"v2"'
        self.srv.payload = onnx_like(200_000)
        updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.assertEqual(dest.read_bytes(), self.srv.payload)

    def test_cdn_ignoring_if_range_does_not_splice(self):
        """검토 재현: If-Range 를 무시하는 CDN 이 같은 크기의 바뀐 파일 뒷부분을 206 으로 주면 예전에는 두 판을 이어 붙였다
        (지문 없는 누끼 모델은 그대로 설치) → 이제 응답 ETag 가 기록과 다르면 처음부터 받음."""
        dest = self.tmp / "m.part"
        self.srv.cut = 100_000
        with self.assertRaises(http.client.IncompleteRead):
            updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        new = bytes(255 - b for b in self.PAYLOAD)  # 같은 크기 · 다른 내용
        self.srv.cut, self.srv.etag, self.srv.payload, self.srv.ifrange = None, '"v2"', new, False
        updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.assertEqual(self.srv.log[1]["range"], "bytes=100000-", "이어받기를 시도했지만")
        self.assertIsNone(self.srv.log[-1]["range"], "ETag 가 달라 처음부터 다시")
        self.assertEqual(dest.read_bytes(), new, "섞인 파일이 아님")
        self.assertFalse(updater._resume_meta(dest).exists())

    def test_no_validator_no_resume(self):
        """서버가 ETag·Last-Modified 를 주지 않았으면 같은 파일인지 알 수 없음 → Range 없이 처음부터 (섞일 위험이 없게)."""
        dest = self.tmp / "m.part"
        self.srv.cut, self.srv.etag = 100_000, None
        with self.assertRaises(http.client.IncompleteRead):
            updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.srv.cut = None
        updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.assertIsNone(self.srv.log[-1]["range"])
        self.assertEqual(dest.read_bytes(), self.PAYLOAD)

    def test_server_without_ranges_restarts(self):
        """이어받기를 모르는 서버(Range 무시 · 200) → 처음부터 다시 써서 같은 파일."""
        dest = self.tmp / "m.part"
        self.srv.cut = 70_000
        with self.assertRaises(http.client.IncompleteRead):
            updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.srv.cut, self.srv.ranges = None, False
        updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.assertEqual(dest.read_bytes(), self.PAYLOAD)

    def test_416_restarts(self):
        """남은 파일이 서버 파일보다 길다고 기록이 잘못됐으면(416) 처음부터."""
        dest = self.tmp / "m.part"
        dest.write_bytes(b"x" * 10)
        updater._resume_meta(dest).write_text(json.dumps({"url": self.srv.url + "/m.onnx", "total": 10 ** 9, "etag": '"v1"'}), encoding="utf-8")
        self.srv.payload = b"\x08\x07"
        updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.assertEqual(dest.read_bytes(), b"\x08\x07")

    def test_other_url_does_not_resume(self):
        """다른 주소에서 받던 반쪽(서버마다 변환본이 다를 수 있음)에는 이어 붙이지 않음."""
        dest = self.tmp / "m.part"
        dest.write_bytes(b"\xff" * 1000)
        updater._resume_meta(dest).write_text(json.dumps({"url": "https://other.example/m", "total": len(self.PAYLOAD)}), encoding="utf-8")
        updater.download(self.srv.url + "/m.onnx", dest, resume=True)
        self.assertIsNone(self.srv.log[-1]["range"])
        self.assertEqual(dest.read_bytes(), self.PAYLOAD)

    def test_without_resume_no_side_file(self):
        """업데이트·Deno·연결 도구 받기(resume 안 씀)는 옆 기록을 만들지 않음 · 길이 확인은 똑같이."""
        dest = self.tmp / "u.zip"
        updater.download(self.srv.url + "/u.zip", dest)
        self.assertEqual(dest.read_bytes(), self.PAYLOAD)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["u.zip"])


class FetchModelTests(Base):
    def setUp(self):
        super().setUp()
        self.models = self.tmp / "모델 폴더"
        self.p = mock.patch.object(thumb, "MODELS", self.models)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        super().tearDown()

    def fetch(self, **kw):
        return thumb.fetch_model("m.onnx", [self.srv.url + "/m.onnx"], "누끼 준비 중", "모델 받는 중", **kw)

    def test_drop_in_the_middle_resumes_in_same_press(self):
        """한 번 끊겨도 같은 누름 안에서 그 자리부터 이어받아 완성 (처음부터 다시 받지 않음)."""
        self.srv.cut = len(self.PAYLOAD) * 9 // 10
        orig = updater.download

        def once(url, dest, progress=None, timeout=30, resume=False):  # 첫 요청만 끊김
            try:
                return orig(url, dest, progress, timeout, resume)
            finally:
                self.srv.cut = None
        with mock.patch.object(updater, "download", side_effect=once):
            p = self.fetch()
        self.assertEqual(p.read_bytes(), self.PAYLOAD)
        self.assertEqual([x["range"] for x in self.srv.log], [None, f"bytes={len(self.PAYLOAD) * 9 // 10}-"])
        self.assertEqual(sorted(x.name for x in self.models.iterdir()), ["m.onnx"], "반쪽·기록 파일 없음")

    def test_flaky_network_never_leaves_broken_model(self):
        """계속 끊기는 와이파이: 한 번 누를 때 RESUME_TRIES 번 이어받고 실패 → 모델은 없음(반쪽을 완성본으로 안 둠) ·
        받은 데까지 .part 로 남아 다음에 누르면 그 자리부터 · 결국 다 받으면 같은 파일."""
        self.srv.cut = 40_000
        with self.assertRaises(http.client.IncompleteRead):
            self.fetch()
        self.assertFalse((self.models / "m.onnx").exists())
        part = self.models / "m.part"
        self.assertEqual(part.stat().st_size, 40_000 * thumb.RESUME_TRIES)
        self.assertEqual(len(self.srv.log), thumb.RESUME_TRIES)
        presses = 1
        while not (self.models / "m.onnx").exists():
            presses += 1
            self.assertLess(presses, 10)
            try:
                self.fetch()
            except http.client.IncompleteRead:
                pass
        self.assertEqual((self.models / "m.onnx").read_bytes(), self.PAYLOAD)
        self.assertTrue(all(x["sent"] <= 40_000 for x in self.srv.log), "받은 것을 다시 받지 않음")
        self.assertEqual(sum(x["sent"] for x in self.srv.log), len(self.PAYLOAD))
        self.assertEqual(sorted(x.name for x in self.models.iterdir()), ["m.onnx"])

    def test_missing_url_and_cancel_leave_nothing(self):
        """없는 주소(404)·멈추기(✕)는 남은 것 없이 (이어받을 일이 아님)."""
        self.srv.cut = 30_000
        with self.assertRaises(http.client.IncompleteRead):
            self.fetch()
        self.srv.cut = None
        with self.assertRaises(thumb.DownloadCancelled):
            self.fetch(cancel=lambda: True)
        self.assertEqual(list(self.models.iterdir()), [])

    def test_server_sends_broken_model_not_installed(self):
        """받은 길이는 맞지만 ONNX 끝이 모자란 파일(서버 쪽 반쪽·섞인 파일 · 누끼 모델은 지문이 없음) → 제자리에 두지 않고 지움."""
        self.srv.payload = self.PAYLOAD[:-5000]
        with self.assertRaises(OSError) as cm:
            self.fetch()
        self.assertIn("온전하지 않아요", str(cm.exception))
        self.assertEqual(list(self.models.iterdir()), [], "반쪽 모델·.part·기록 없음")

    def test_disk_full_drops_partial(self):
        """검토 재현: 디스크가 꽉 차서(Errno 28) 실패 → 예전에는 와이파이 끊김처럼 다시 하고 .part + .resume 를 남김 (최대 220MB) ·
        이제 다시 하지 않고 지움."""
        calls = []

        def full(url, dest, progress=None, timeout=30, resume=False):
            calls.append(url)
            Path(dest).write_bytes(b"x" * 1000)
            updater._resume_meta(dest).write_text(json.dumps({"url": url, "total": 10 ** 6, "etag": '"v1"'}), encoding="utf-8")
            if progress:
                progress(1000, 10 ** 6)
            raise OSError(28, "No space left on device")
        with mock.patch.object(updater, "download", side_effect=full):
            with self.assertRaises(OSError):
                self.fetch()
        self.assertEqual(len(calls), 1, "꽉 찬 디스크는 다시 받아 봐야 소용없음")
        self.assertEqual(list(self.models.iterdir()), [])
        e = OSError(0, "x")
        e.winerror = 112  # Windows ERROR_DISK_FULL
        self.assertTrue(thumb._disk_error(e))
        self.assertFalse(thumb._disk_error(http.client.IncompleteRead(b"", 5)))

    def test_old_partials_are_swept(self):
        """다시 누르지 않아 오래 남은 받다 만 모델(.part + .resume)은 다른 모델을 받을 때 지움 · 최근 것은 그대로 (다음에 이어받게)."""
        import os
        import time
        self.models.mkdir(parents=True)
        old, new = self.models / "옛.part", self.models / "새.part"
        for f in (old, new):
            f.write_bytes(b"x" * 10)
            updater._resume_meta(f).write_text("{}", encoding="utf-8")
        ago = time.time() - (thumb.PART_DAYS + 1) * 86400
        for f in (old, updater._resume_meta(old)):
            os.utime(f, (ago, ago))
        self.fetch()
        self.assertEqual(sorted(x.name for x in self.models.iterdir()), ["m.onnx", "새.part", "새.part.resume"])

    def test_whole_looking_model_of_wrong_size_is_replaced(self):
        """검토 재현(합침 D-078): 맨 위 칸 경계에서 끊긴 파일·다른 판은 모양(model_whole)이 맞아 제자리에 있으면 늘 그대로 썼다 →
        크기를 알면(얼굴·선수·공·글자·소리 모델) 다르면 지우고 다시 받음 · 주소마다 다른 크기(변환본)도 · 모르면(누끼 모델) 모양만 보고 그대로."""
        self.models.mkdir(parents=True)
        other = onnx_like(1000)
        (self.models / "m.onnx").write_bytes(other)
        self.assertTrue(thumb.model_whole(self.models / "m.onnx"))
        self.assertEqual(self.fetch().read_bytes(), other, "크기를 모르면 그대로")
        self.assertEqual(self.srv.log, [])
        p = self.fetch(size=len(self.PAYLOAD))
        self.assertEqual(p.read_bytes(), self.PAYLOAD)
        self.assertEqual(len(self.srv.log), 1)
        self.assertEqual(self.fetch(size=len(self.PAYLOAD)), p)
        url = self.srv.url + "/m.onnx"
        self.assertEqual(thumb.fetch_model("m.onnx", [(url + "?다른", 5, None), (url, len(self.PAYLOAD), None)], "준비 중", "받는 중"), p)
        self.assertEqual(len(self.srv.log), 1, "맞는 크기면 다시 받지 않음")

    def test_legacy_truncated_model_is_replaced(self):
        """예전 판이 남긴 반쪽 모델(끝이 모자란 ONNX): 있으면 바로 쓰던 것을 지우고 다시 받음 → 누끼가 계속 고장 나지 않음."""
        self.models.mkdir(parents=True)
        (self.models / "m.onnx").write_bytes(self.PAYLOAD[: len(self.PAYLOAD) * 9 // 10])
        p = self.fetch()
        self.assertEqual(p.read_bytes(), self.PAYLOAD)
        self.assertEqual(len(self.srv.log), 1)
        n = len(self.srv.log)
        self.assertEqual(self.fetch(), p)
        self.assertEqual(len(self.srv.log), n, "온전한 모델은 다시 받지 않음")


class ModelWholeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="모델 확인 "))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, data, name="m.onnx"):
        p = self.tmp / name
        p.write_bytes(data)
        return thumb.model_whole(p)

    def test_whole_and_truncated(self):
        full = onnx_like(5000)
        self.assertTrue(self.check(full))
        for cut in (1, 2, 100, len(full) // 2, len(full) - 1):
            self.assertFalse(self.check(full[:cut]), cut)
        self.assertFalse(self.check(b""), "빈 파일")

    def test_unknown_shapes_are_not_judged(self):
        """ONNX 가 아닌 파일·모르는 꼴은 지우지 않음 (멀쩡한 파일을 잘못 지우지 않게)."""
        self.assertTrue(self.check(b"\x08\x07" + b"\x3b\x00", "x.onnx"))  # 그룹 칸
        self.assertTrue(self.check(b"some words", "dict.txt"))
        self.assertTrue(thumb.model_whole(self.tmp / "없음.onnx") is False)

    def test_real_models_if_present(self):
        """이 PC 에 받아 둔 진짜 모델이 있으면: 온전 → 참, 1바이트만 모자라도 거짓."""
        real = [p for p in (Path.home() / ".futsal-studio" / "models").glob("*.onnx") if p.stat().st_size < 60_000_000][:3]
        if not real:
            self.skipTest("받아 둔 모델이 없어요")
        for p in real:
            self.assertTrue(thumb.model_whole(p), p.name)
            q = self.tmp / p.name
            with open(p, "rb") as f, open(q, "wb") as g:
                g.write(f.read(p.stat().st_size - 1))
            self.assertFalse(thumb.model_whole(q), p.name)


REAL_YOLOX = Path.home() / ".futsal-studio" / "models" / "yolox_nano.onnx"
YOLOX_SIZE, YOLOX_SHA = detect.SIZE_B, detect.SHA  # 시험이 바꿔 끼우기 전 값


class DetectModelTests(Base):
    """합침(D-078): 선수·공 찾기(detect.py · YOLOX-nano, AI 추천 썸네일)도 같은 모델 받기 — 이어받기 · 받은 길이·지문 · ONNX 끝까지 확인 ·
    예전 반쪽은 지우고 다시. 진짜 YOLOX 가 이 PC 에 있으면 그 바이트를 그대로 내려 줌 (없으면 ONNX 꼴 가짜)."""

    def setUp(self):
        real = REAL_YOLOX.is_file() and REAL_YOLOX.stat().st_size == YOLOX_SIZE
        self.PAYLOAD = REAL_YOLOX.read_bytes() if real else onnx_like(200_000) + OPSET
        super().setUp()
        self.models = self.tmp / "모델 폴더"
        sha = updater.sha256(REAL_YOLOX) if real else __import__("hashlib").sha256(self.PAYLOAD).hexdigest()
        self.ps = [mock.patch.object(thumb, "MODELS", self.models), mock.patch.object(detect, "URL", self.srv.url + "/yolox_nano.onnx"),
                   mock.patch.object(detect, "SIZE_B", len(self.PAYLOAD)), mock.patch.object(detect, "SHA", sha),
                   mock.patch.dict(detect._SESS, clear=True), mock.patch.dict(detect._FAIL, {"t": 0.0}),
                   mock.patch.dict(sys.modules, {"onnxruntime": FakeOrt})]  # 세션은 흉내 (받기·확인만 봄)
        for x in self.ps:
            x.start()

    def tearDown(self):
        for x in reversed(self.ps):
            x.stop()
        super().tearDown()

    def test_cut_download_resumes_and_is_checked_whole(self):
        """받다 끊기면 같은 누름 안에서 그 자리부터 (Range) · 다 받은 뒤 크기·지문·ONNX 끝까지 확인 → 제자리 · ready 참."""
        self.srv.cut = len(self.PAYLOAD) // 2
        orig = updater.download

        def once(url, dest, progress=None, timeout=30, resume=False):
            self.assertTrue(resume, "선수·공 모델도 이어받기를 켬")
            try:
                return orig(url, dest, progress, timeout, resume)
            finally:
                self.srv.cut = None
        with mock.patch.object(updater, "download", side_effect=once):
            self.assertFalse(detect.ready())
            self.assertTrue(detect.ensure())
        self.assertEqual((self.models / detect.FILE).read_bytes(), self.PAYLOAD)
        self.assertEqual([x["range"] for x in self.srv.log], [None, f"bytes={len(self.PAYLOAD) // 2}-"])
        self.assertTrue(detect.ready())
        self.assertEqual(sorted(x.name for x in self.models.iterdir()), [detect.FILE], "반쪽·기록·실패 표시 없음")

    def test_legacy_half_model_is_not_ready_and_is_replaced(self):
        """예전에 남은 반쪽 YOLOX(끝이 모자람): ready 거짓(장면 캐시 지문이 '모델 있음'으로 속지 않음) → ensure 가 지우고 다시 받음."""
        self.models.mkdir(parents=True)
        (self.models / detect.FILE).write_bytes(self.PAYLOAD[: len(self.PAYLOAD) * 3 // 4])
        self.assertFalse(detect.ready())
        self.assertTrue(detect.ensure())
        self.assertEqual((self.models / detect.FILE).read_bytes(), self.PAYLOAD)
        self.assertEqual(len(self.srv.log), 1)

    def test_cut_at_field_boundary_is_not_ready_and_is_replaced(self):
        """검토 재현(합침 뒤): 맨 위 칸 경계에서 끊긴 YOLOX(진짜는 3,659,407B 중 graph 가 끝나는 3,659,401B · 마지막 opset 칸만 없음)는
        model_whole 이 모양만 보고 참 → ready 참·fetch_model 도 그대로 써서 onnxruntime 이 거절하고 10분마다 실패만 했다 →
        크기(SIZE_B)도 봄: ready 거짓 · ensure 가 지우고 다시 받음."""
        cut = last_field_start(self.PAYLOAD)
        self.assertGreater(cut, len(self.PAYLOAD) // 2)
        self.models.mkdir(parents=True)
        (self.models / detect.FILE).write_bytes(self.PAYLOAD[:cut])
        self.assertTrue(thumb.model_whole(self.models / detect.FILE), "모양만으로는 못 가림 (그래서 크기도 봄)")
        self.assertFalse(detect.ready())
        self.assertTrue(detect.ensure())
        self.assertEqual((self.models / detect.FILE).read_bytes(), self.PAYLOAD)
        self.assertEqual(len(self.srv.log), 1)
        self.assertTrue(detect.ready())

    def test_server_sends_wrong_file_is_not_installed(self):
        """서버가 다른 파일(길이는 같고 내용이 다름)을 주면 지문에서 걸려 제자리에 두지 않음 · 조용히 포기(얼굴·피부 어림으로 계속)."""
        self.srv.payload = bytes(len(self.PAYLOAD))
        self.assertFalse(detect.ensure())
        self.assertFalse((self.models / detect.FILE).exists())
        self.assertFalse((self.models / "yolox_nano.part").exists())

    @unittest.skipUnless(REAL_YOLOX.is_file(), "받아 둔 YOLOX 가 없어요")
    def test_real_yolox_is_judged_whole(self):
        """이 PC 의 진짜 yolox_nano.onnx: 지문이 detect.SHA 와 같으면 온전 · 1바이트·절반만 있어도 반쪽."""
        self.assertEqual(updater.sha256(REAL_YOLOX), YOLOX_SHA)
        self.assertTrue(thumb.model_whole(REAL_YOLOX))
        data = REAL_YOLOX.read_bytes()
        self.models.mkdir(parents=True)
        for cut in (len(data) - 1, len(data) // 2, 100):
            (self.models / detect.FILE).write_bytes(data[:cut])
            self.assertFalse(thumb.model_whole(self.models / detect.FILE), cut)
            self.assertFalse(detect.ready(), cut)


class FakeOrt:
    """onnxruntime 흉내 (detect.ensure 가 부르는 것만)."""

    class SessionOptions:
        pass

    @staticmethod
    def InferenceSession(data, so=None, providers=None):
        return object()


@unittest.skipUnless(yt_dlp, "yt-dlp 가 없어요")
class FormatSortTests(unittest.TestCase):
    """진짜 yt-dlp 의 형식 고르기에 가짜 형식 목록 (137 avc1 · 399 av01 · 248 vp9 · 136 avc1 720p · 140 m4a)."""

    FORMATS = [
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 4400},
        {"format_id": "399", "ext": "mp4", "vcodec": "av01.0.08M.08", "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 2100},
        {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 2600},
        {"format_id": "136", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "width": 1280, "fps": 30, "tbr": 2000},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128},
    ]

    def pick(self, opts, drop=()):
        fmts = [dict(f, url=f"http://127.0.0.1:1/{f['format_id']}", protocol="https") for f in self.FORMATS if f["format_id"] not in drop]
        info = {"id": "abcdefghijk", "title": "t", "formats": fmts, "extractor": "youtube", "extractor_key": "Youtube",
                "webpage_url": "http://127.0.0.1:1/w"}
        with yt_dlp.YoutubeDL(dict(opts, quiet=True, simulate=True)) as y:
            return y.process_ie_result(info, download=False)["format_id"]

    def test_old_rule_picked_av1(self):
        """재현: 예전 형식 고르기(정렬 없음)는 AV1."""
        old = {"format": core.format_opts()["format"]}
        self.assertEqual(self.pick(old), "399+140")

    def test_h264_first_at_same_resolution(self):
        self.assertEqual(self.pick(core.format_opts()), "137+140")

    def test_no_h264_at_top_resolution_keeps_resolution(self):
        """1080p H.264 가 없으면 지금 규칙 그대로 (720p H.264 로 떨어지지 않음)."""
        self.assertEqual(self.pick(core.format_opts(), drop=("137",)), "399+140")

    def test_height_limit_still_applies(self):
        self.assertEqual(self.pick(core.format_opts(720)), "136+140")

    def test_download_passes_sort_to_ytdlp(self):
        seen = {}

        class Y:
            def __init__(self, opts):
                seen.update(opts)

            def download(self, urls):
                pass

            def close(self):
                pass
        tmp = Path(tempfile.mkdtemp(prefix="받기 "))
        try:
            with mock.patch.object(core, "_yt", return_value=type("M", (), {"YoutubeDL": Y})), mock.patch.object(core, "ensure_deno"), \
                    mock.patch.object(core, "VIDEOS", tmp):
                core.download(["abcdefghijk"], lambda m: None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(seen["format_sort"], ["res", "fps", "vcodec:h264"])


if __name__ == "__main__":
    unittest.main()
