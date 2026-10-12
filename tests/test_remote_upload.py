"""밖에서 작업 — 내 기기에서 올리기(조각 · 이어 올리기) · 완성본 받기 (D-232) — python3 -m unittest tests.test_remote_upload

개발 모드 원격 리스너 + app.StudioHandler (tests.test_remote_studio.StudioBase) 로 진짜 HTTP 요청을 보낸다.
"""
import hashlib
import io
import socket
import sys
import time
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import editor  # noqa: E402
import rstudio  # noqa: E402
import source  # noqa: E402
from test_remote_studio import DEV, StudioBase, quote  # noqa: E402

DU = namedtuple("DU", "total used free")
BIG = DU(1 << 44, 0, 1 << 43)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def probe_of(data):
    return sha(data[:1 << 20] + data[-(1 << 20):])


class UploadBase(StudioBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(rstudio, "disk_free", return_value=BIG)
        self.du = p.start()
        self.addCleanup(p.stop)
        self.login()

    def init(self, name="촬영 1.mp4", data=b"", target="videos", lm=1700000000000, **kw):
        body = {"name": name, "size": len(data), "lastModified": lm, "probe": probe_of(data), "target": target}
        body.update(kw)
        return self.jreq("POST", "/s/upload/init", body)

    def chunk(self, uid, off, data, digest=None, ctype=None):
        return self.jreq("POST", f"/s/upload/chunk?id={uid}&offset={off}", raw=data, ctype=ctype,
                         headers={"X-FS-Sha256": digest or sha(data)})

    def send_all(self, uid, data, start=0, size=1000):
        off = start
        while off < len(data):
            st, j = self.chunk(uid, off, data[off:off + size])
            self.assertEqual(st, 200, j)
            off = j["received"]
        return off


class UploadTest(UploadBase):
    def test_full_upload_into_library(self):
        data = bytes(range(256)) * 20
        st, j = self.init(data=data)
        self.assertEqual(st, 200, j)
        self.assertEqual((j["received"], j["size"], j["chunk"]), (0, len(data), rstudio.CHUNK))
        uid = j["id"]
        self.assertRegex(uid, r"^[0-9a-f]{32}$")
        part = self.work / "uploads" / f"{uid}.part"
        self.assertTrue(part.exists())
        self.assertEqual(self.send_all(uid, data), len(data))
        with mock.patch.object(source, "mark_footage") as mark:
            st, j = self.jreq("POST", "/s/upload/finish", {"id": uid})
        self.assertEqual((st, j), (200, {"ok": True, "name": "촬영 1.mp4", "target": "videos"}))
        self.assertEqual((core.VIDEOS / "촬영 1.mp4").read_bytes(), data)
        mark.assert_called_once_with("촬영 1.mp4", "local")
        self.assertFalse(part.exists())
        self.assertFalse((self.work / "uploads" / f"{uid}.json").exists())
        self.assertTrue(any(f"원격 · {DEV} · 보관함에 올림 · 촬영 1.mp4" in x for x in self.lines))
        # 같은 이름이 또 오면 덮어쓰지 않음
        st, j = self.init(data=data, lm=1)
        self.send_all(j["id"], data)
        with mock.patch.object(source, "mark_footage"):
            self.assertEqual(self.jreq("POST", "/s/upload/finish", {"id": j["id"]})[1]["name"], "촬영 1_1.mp4")

    def test_resume_same_file_and_probe_mismatch(self):
        data = b"a" * 5000
        uid = self.init(data=data)[1]["id"]
        self.send_all(uid, data[:2000])
        st, j = self.init(data=data)
        self.assertEqual((j["id"], j["received"]), (uid, 2000))  # 같은 파일 → 받은 곳부터
        other = b"b" * 5000  # 이름·크기·시각이 같지만 내용이 다름 → 처음부터 (옛 조각은 버림)
        st, j = self.init(data=other)
        self.assertNotEqual(j["id"], uid)
        self.assertEqual(j["received"], 0)
        self.assertFalse((self.work / "uploads" / f"{uid}.part").exists())

    def test_offset_and_integrity(self):
        data = b"x" * 3000
        uid = self.init(data=data)[1]["id"]
        st, j = self.chunk(uid, 100, data[:100])
        self.assertEqual((st, j["received"]), (409, 0))
        st, j = self.chunk(uid, 0, data[:1000], digest=sha(b"other"))
        self.assertEqual((st, j["received"]), (400, 0))
        self.assertEqual((self.work / "uploads" / f"{uid}.part").stat().st_size, 0)  # 깨진 조각은 아무것도 안 씀
        st, j = self.chunk(uid, 0, data[:1000], digest="nothex")
        self.assertEqual(st, 400)
        st, j = self.chunk(uid, 0, data[:1000], ctype="application/json")
        self.assertEqual(st, 415)
        self.send_all(uid, data[:2500])
        st, j = self.chunk(uid, 2500, b"y" * 600)  # 크기보다 많이
        self.assertEqual(st, 400)
        st, j = self.jreq("POST", "/s/upload/finish", {"id": uid})
        self.assertEqual((st, j["received"]), (409, 2500))  # 아직 다 안 옴

    def test_short_body_writes_nothing(self):
        data = b"z" * 4000
        uid = self.init(data=data)[1]["id"]
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        req = (f"POST /s/upload/chunk?id={uid}&offset=0 HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nCookie: {self.cookie}\r\n"
               f"Origin: {self.origin}\r\nX-FS-CSRF: {self.csrf}\r\nContent-Type: application/octet-stream\r\n"
               f"X-FS-Sha256: {sha(data[:1000])}\r\nContent-Length: 1000\r\n\r\n").encode() + data[:400]
        s.sendall(req)
        s.shutdown(socket.SHUT_WR)  # 끊김 (지하철·와이파이 바뀜)
        resp = s.recv(4096)
        s.close()
        self.assertIn(b" 400 ", resp.split(b"\r\n")[0])
        self.assertEqual(self.jreq("GET", f"/s/upload/status?id={uid}")[1]["received"], 0)
        self.assertEqual((self.work / "uploads" / f"{uid}.part").stat().st_size, 0)

    def test_disk_full_507(self):
        self.du.return_value = DU(100 << 30, 0, 1 << 30)  # 1GB 남음 (여유 2GB 보다 작음)
        st, j = self.init(data=b"q" * 100)
        self.assertEqual((st, j["code"]), (507, "disk"))
        self.du.return_value = BIG
        data = b"q" * 3000
        uid = self.init(data=data)[1]["id"]
        with mock.patch.object(rstudio, "UP_RECHECK", 1000):
            self.send_all(uid, data[:1000])
            self.du.return_value = DU(100 << 30, 0, 1 << 20)
            st, j = self.chunk(uid, 1000, data[1000:2000])
        self.assertEqual((st, j["code"], j["received"]), (507, "disk", 1000))

    def test_names_and_extensions(self):
        d = b"v" * 10
        for name, want in (("../../evil.mp4", "evil.mp4"), ("..\\..\\x.mov", "x.mov"), ("CON.mp4", "_CON.mp4"),
                           ("a‮b\n.mp4", "ab.mp4"), ('a:b*c?.MTS', "a_b_c_.MTS")):
            st, j = self.init(name=name, data=d, lm=hash(name) % 1000)
            self.assertEqual((st, j.get("name")), (200, want), name)
            self.jreq("POST", "/s/upload/cancel", {"id": j["id"]})
        for name in ("run.exe", "a.json", "noext", "", ".mp4", "x.png"):
            st, j = self.init(name=name, data=d)
            self.assertIn(st, (400, 415), name)
        self.assertEqual(self.init(name="logo.png", data=d, target="assets")[0], 200)
        self.assertEqual(self.init(name="a.mp4", data=d, target="../videos")[0], 400)
        self.assertEqual(self.init(name="a.mp4", data=d, size=0)[0], 400)
        self.assertEqual(self.init(name="a.mp4", data=d, size=rstudio.UP_MAX + 1)[0], 400)
        self.assertEqual(self.init(name="a.mp4", data=d, probe="x")[0], 400)

    def test_limits(self):
        ids = []
        for i in range(2):
            st, j = self.init(name=f"{i}.mp4", data=b"d" * 10)
            self.assertEqual(st, 200)
            ids.append(j["id"])
        st, j = self.init(name="2.mp4", data=b"d" * 10)
        self.assertEqual((st, j["code"]), (429, "queue"))  # 기기마다 2개 → 화면은 실패가 아니라 '차례를 기다리는 중' (D-237)
        self.assertIn("[그만두기]", j["error"])
        self.jreq("POST", "/s/upload/cancel", {"id": ids[0]})
        self.assertEqual(self.init(name="2.mp4", data=b"d" * 10)[0], 200)
        up = self.svc.studio.uploads
        self.assertEqual(rstudio.UP_ACTIVE, 5 * 60)  # 멈춰 두거나 그만 둔 올리기는 5분이면 수에서 빠짐 (예전 30분)
        for r in up.items.values():
            r["updated"] -= rstudio.UP_ACTIVE + 1
        self.assertEqual(self.init(name="3.mp4", data=b"d" * 10)[0], 200)
        st, j = self.jreq("GET", "/s/session")
        self.assertEqual((j["upMax"], j["chunkMax"]), (rstudio.UP_PER_DEVICE, rstudio.CHUNK_MAX))  # 화면이 줄 세우기·조각 크기에 씀

    def test_nfd_name_stored_nfc(self):
        """맥·아이폰의 풀어 쓴 한글(NFD) 이름 → 모아 쓴(NFC) 이름으로 보관함에 (같은 제목이 둘이 되지 않게 · 편집실 가져오기도)."""
        import unicodedata
        data = b"n" * 3000
        nfd = unicodedata.normalize("NFD", "퍼스트터치연습.mp4")
        self.assertNotEqual(nfd, "퍼스트터치연습.mp4")
        st, j = self.init(name=nfd, data=data)
        self.assertEqual((st, j["name"]), (200, "퍼스트터치연습.mp4"))
        self.assertEqual(j["chunkMax"], rstudio.CHUNK_MAX)
        self.send_all(j["id"], data)
        with mock.patch.object(source, "mark_footage"):
            st, j = self.jreq("POST", "/s/upload/finish", {"id": j["id"]})
        self.assertEqual(j["name"], "퍼스트터치연습.mp4")
        names = [x.name for x in core.VIDEOS.iterdir() if x.name.endswith(".mp4") and "퍼" in unicodedata.normalize("NFC", x.name)]
        self.assertEqual(names, ["퍼스트터치연습.mp4"])
        self.assertTrue(unicodedata.is_normalized("NFC", names[0]))
        # 같은 제목을 NFC 로 다시 올리면 같은 이름으로 보고 _1 을 붙임 (둘이 똑같이 보이는 다른 파일이 되지 않음)
        st, j = self.init(name="퍼스트터치연습.mp4", data=data, lm=5)
        self.send_all(j["id"], data)
        with mock.patch.object(source, "mark_footage"):
            self.assertEqual(self.jreq("POST", "/s/upload/finish", {"id": j["id"]})[1]["name"], "퍼스트터치연습_1.mp4")
        # 편집실 가져오기(작은 파일 · /api/edit/upload)도 같은 규칙
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 6), (0, 0, 200)).save(buf, "PNG")
        png = buf.getvalue()
        e = editor.save_upload(unicodedata.normalize("NFD", "로고 그림.png"), io.BytesIO(png), len(png))
        self.assertEqual(e["file"], "로고 그림.png")
        self.assertTrue((editor.ASSETS / "로고 그림.png").exists())

    def test_assets_finish_returns_media_entry(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 6), (200, 0, 0)).save(buf, "PNG")
        data = buf.getvalue()
        uid = self.init(name="로고.png", data=data, target="assets")[1]["id"]
        self.send_all(uid, data)
        st, j = self.jreq("POST", "/s/upload/finish", {"id": uid})
        self.assertEqual(st, 200, j)
        self.assertEqual((j["src"], j["file"], j["kind"]), ("assets", "로고.png", "image"))
        self.assertEqual((editor.ASSETS / "로고.png").read_bytes(), data)

    def test_pending_cancel_persistence_and_revoke(self):
        data = b"p" * 3000
        uid = self.init(data=data)[1]["id"]
        self.send_all(uid, data[:1500])
        st, j = self.jreq("GET", "/s/upload/pending")
        self.assertEqual([(x["id"], x["received"]) for x in j["uploads"]], [(uid, 1500)])
        # 앱을 껐다 켜도 (기록을 파일에서 다시 읽음 · 받은 양 = 조각 파일 크기)
        self.svc.studio.uploads = rstudio.Uploads(self.svc)
        self.assertEqual(self.jreq("GET", f"/s/upload/status?id={uid}")[1]["received"], 1500)
        self.send_all(uid, data, start=1500)
        # 다른 기기의 올리기 번호로는 못 씀
        self.assertEqual(self.jreq("GET", "/s/upload/status?id=" + "0" * 32)[0], 404)
        self.assertEqual(self.jreq("POST", "/s/upload/cancel", {"id": uid})[1], {"ok": True})
        self.assertFalse((self.work / "uploads" / f"{uid}.part").exists())
        uid = self.init(data=data, lm=5)[1]["id"]
        self.svc.revoke([self.cred["device"]["id"]])
        self.assertFalse((self.work / "uploads" / f"{uid}.part").exists())  # 기기를 끊으면 그 기기의 조각도

    def test_sweep_old(self):
        uid = self.init(data=b"s" * 100)[1]["id"]
        self.svc.studio.uploads.sweep(time.time() + rstudio.UP_KEEP + 10)
        self.assertFalse((self.work / "uploads" / f"{uid}.part").exists())

    def test_chunk_too_big_413(self):
        uid = self.init(data=b"t" * (rstudio.CHUNK_MAX + 10))[1]["id"]
        st, j = self.jreq("POST", f"/s/upload/chunk?id={uid}&offset=0", raw=b"t" * 10,
                          headers={"X-FS-Sha256": sha(b"t" * 10), "Content-Length": str(rstudio.CHUNK_MAX + 1)})
        self.assertEqual(st, 413)


class DownloadTest(UploadBase):
    def setUp(self):
        super().setUp()
        self.data = bytes(range(256)) * 400
        (self.out / "퍼스트 터치_롱폼.mp4").write_bytes(self.data)
        (self.out / "키트_올리기.txt").write_text("제목: 꿀팁", encoding="utf-8")
        (self.out / ".hidden.mp4").write_bytes(b"h")
        (self.out / "x.json").write_text("{}", encoding="utf-8")

    def test_outputs_list(self):
        st, j = self.jreq("GET", "/s/outputs")
        names = [o["name"] for o in j["outputs"]]
        self.assertEqual(set(names), {"퍼스트 터치_롱폼.mp4", "키트_올리기.txt"})
        v = next(o for o in j["outputs"] if o["kind"] == "video")
        self.assertEqual((v["size"], v["preview"]), (len(self.data), False))

    def test_download_attachment_range_and_resume(self):
        path = "/s/dl?f=" + quote("퍼스트 터치_롱폼.mp4")
        st, hd, out = self.req("GET", path)
        self.assertEqual((st, out), (200, self.data))
        cd = self.hget(hd, "Content-Disposition")[0]
        self.assertTrue(cd.startswith("attachment; "))
        self.assertIn("filename*=UTF-8''" + quote("퍼스트 터치_롱폼.mp4"), cd)
        etag = self.hget(hd, "ETag")[0]
        self.assertTrue(self.hget(hd, "Last-Modified"))
        st, hd, out = self.req("GET", path, headers={"Range": "bytes=50000-", "If-Range": etag})
        self.assertEqual((st, out), (206, self.data[50000:]))  # 크기 상한 없이 끝까지 (이어 받기)
        self.assertEqual(self.hget(hd, "Content-Range"), [f"bytes 50000-{len(self.data) - 1}/{len(self.data)}"])
        st, hd, out = self.req("GET", path, headers={"Range": "bytes=50000-", "If-Range": '"old"'})
        self.assertEqual((st, len(out)), (200, len(self.data)))  # 파일이 바뀌었으면 처음부터
        self.assertEqual(self.req("GET", path, headers={"Range": f"bytes={len(self.data) + 5}-"})[0], 416)

    def test_download_refuses_outside_and_hidden(self):
        for f in (".hidden.mp4", "x.json", "../studio.log", "..%2Fstudio.log", "없음.mp4", ""):
            self.assertEqual(self.req("GET", "/s/dl?f=" + quote(f))[0], 404, f)
        self.assertEqual(self.req("GET", "/s/dl?f=" + quote(str(self.out / "키트_올리기.txt")))[0], 404)

    def test_log_download_scrubbed(self):
        (self.work / "studio.log").write_text(f"작업 폴더 · {self.work}\nok\n", encoding="utf-8")
        with mock.patch.object(rstudio.Path, "home", return_value=self.work.parent):
            st, hd, out = self.req("GET", "/s/dl?log=1")
        self.assertEqual(st, 200)
        self.assertIn("filename*=UTF-8''studio.log", self.hget(hd, "Content-Disposition")[0])
        self.assertNotIn(str(self.work.parent), out.decode())
        self.assertIn("~/", out.decode())

    def test_preview(self):
        name = "퍼스트 터치_롱폼.mp4"
        self.assertEqual(self.req("GET", "/s/preview?f=" + quote(name))[0], 404)
        pv = editor.out_preview_path(name)
        pv.parent.mkdir(parents=True, exist_ok=True)
        pv.write_bytes(b"PV" * 100)
        st, hd, out = self.req("GET", "/s/preview?f=" + quote(name), headers={"Range": "bytes=0-3"})
        self.assertEqual((st, out), (206, b"PVPV"))
        self.assertTrue(self.hget(hd, "Content-Disposition")[0].startswith("inline"))
        self.assertTrue(next(o for o in self.jreq("GET", "/s/outputs")[1]["outputs"] if o["name"] == name)["preview"])
        with mock.patch.object(editor, "out_preview", return_value={"ok": True}) as mk:
            st, j = self.jreq("POST", "/s/outputs/preview", {"f": name})
            self.assertEqual(st, 200, j)
            self.wait_idle()
        mk.assert_called_once()
        self.assertEqual(self.app.DONE[j["jobId"]]["by"], f"밖에서 · {DEV}")
        self.assertEqual(self.jreq("POST", "/s/outputs/preview", {"f": "키트_올리기.txt"})[0], 404)


if __name__ == "__main__":
    unittest.main()
