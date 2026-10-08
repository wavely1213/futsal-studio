"""컷 묶어 만들기(exportplan) 테스트 — python3 -m unittest tests.test_exportplan

인코더를 빼고(framemd5: 필터 그래프가 내놓은 프레임 그대로) 비교:
구간마다 ffmpeg 하나(예전 방식)로 만든 모든 프레임 == 이어지는 구간들을 ffmpeg 하나로 묶어 만든 모든 프레임 (한 프레임도 다르지 않음).
자막(서서히 나타나기 포함)·빠르게·거꾸로·고정 확대·30fps/25fps 원본·키프레임 간격이 긴 원본을 섞음. 그리고 실제 내보내기의 프레임 수."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core
import editor
import exportplan
from tests.test_export import ExportBase, item, nframes

FF = core.ffmpeg()


def make(path, rate, dur, gop):
    r = core.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x180:r={rate}:d={dur}", "-f", "lavfi", "-i", f"sine=f=440:d={dur}",
                  "-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
    assert r.returncode == 0, r.stderr[-300:]


def md5s(args, fc_name, final, n, cwd):
    p = subprocess.run([FF, "-v", "error"] + args + [editor._fc_opt(), fc_name, "-map", f"[{final}]", "-frames:v", str(n), "-f", "framemd5", "-"],
                       cwd=cwd, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-800:]
    return [ln.rsplit(",", 1)[1].strip() for ln in p.stdout.splitlines() if ln and not ln.startswith("#")]


class ExportPlanTest(ExportBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        make(cls.assets / "long30.mp4", 30, 40, 250)  # 키프레임 간격 약 8.3초 (롱폼 원본처럼)
        make(cls.assets / "p25.mp4", 25, 20, 50)
        cls.media = cls.media + [
            {"id": "main", "kind": "video", "src": "assets", "file": "long30.mp4", "dur": 40.0, "w": 320, "h": 180, "fps": 30.0, "audio": True},
            {"id": "p25", "kind": "video", "src": "assets", "file": "p25.mp4", "dur": 20.0, "w": 320, "h": 180, "fps": 25.0, "audio": True}]

    def cut_proj(self, name):
        items, pos = [], 0.0
        # 무음 잘라내기처럼: 원본 2초 쓰고 0.5초 건너뛰기 · 중간에 빠르게 · 거꾸로 · 고정 확대 · 25fps 원본
        # 색보정 구간(rgb24) 이 묶음 안에 있어도 다른 구간 색이 그대로인지 · 9.4~11.0333 (49프레임) 다음 구간은 220프레임째
        # (=7.3333초 · 예전 'PTS+7.3333/TB' 가 219 로 버림돼 자막이 한 프레임 일찍 나오던 시작)
        spec = [("main", 1.0, 3.0, {}), ("main", 3.5, 5.0, {"color": {"exp": 0.3, "sat": 120}}), ("main", 5.5, 7.3, {"speed": 1.5}),
                ("main", 8.0, 9.0, {"rev": True}),
                ("main", 9.4, 11.0 + 1 / 30, {"fx": {"scale": {"v": 130, "k": []}}}), ("main", 11.2, 13.0, {}), ("p25", 2.0, 4.0, {}),
                ("p25", 4.3, 6.1, {}), ("main", 30.0, 31.5, {}), ("main", 31.9, 33.4, {})]
        for k, (m, a, b, extra) in enumerate(spec):
            it = item(f"c{k}", m, "V1", pos, a, b)
            it.update(extra)
            items.append(it)
            pos += (b - a) / float(it.get("speed") or 1)
        # 프레임 사이에 걸치는 시각 (서서히 나타나기 알파가 1ms 만 어긋나도 프레임이 달라짐)
        caps = [{"start": 1.013 + 1.371 * j, "end": 1.013 + 1.371 * j + 1.117, "text": f"자막 {j} 번째 줄"} for j in range(20)]
        st = dict(editor.LONG_STYLE, effect="fade")
        return self.proj(name, items, captions=caps, captionsOn=True, captionStyle=st,
                         info={"duration": 40.0, "width": 320, "height": 180, "fps": 30.0})

    def test_runs_are_frame_identical(self):
        proj = editor.migrate_seq(self.cut_proj("묶음 비교"))
        media = {m["id"]: m for m in proj["media"]}
        fps, W, H = 30, 640, 360
        tmp = Path(tempfile.mkdtemp(dir=core.OUT))
        try:
            ass = editor.build_ass(proj, 1920, 1080)
            self.assertGreaterEqual(ass.count("Dialogue:"), 5)
            self.assertIn("\\fad", ass)
            (tmp / "subs.ass").write_text(ass, encoding="utf-8")
            shutil.copytree(editor.FONTS, tmp / "fonts")
            segs = editor._segments(proj, 0, editor.seq_total(proj), fps, [], media)
            self.assertIn(220, [a for a, _ in segs])
            builds = [editor._build_segment(proj, media, W, H, fps, f0, f1, [], tmp, k) for k, (f0, f1) in enumerate(segs)]
            units = exportplan.units(builds, tmp, fps)
            self.assertEqual([k for u in units for k in u], list(range(len(segs))))
            self.assertTrue(any(len(u) > 1 for u in units), units)
            run = next(u for u in units if len(u) > 1)
            txt = exportplan.unit_args(run, builds, tmp, fps)
            self.assertEqual((tmp / txt[3]).read_text(encoding="utf-8").count("subtitles="), 1)  # 자막 필터는 묶음 끝 하나
            self.assertIn("format=yuv420p,split=", (tmp / txt[3]).read_text(encoding="utf-8"))  # 원본 형식 못박기
            self.assertTrue(any("lutrgb" in (tmp / f"fc{k}.txt").read_text(encoding="utf-8") for k in run), run)  # 색보정 구간도 같이 묶임
            self.assertGreaterEqual(len(units), 3, units)  # 다른 원본(25fps) · 30초로 건너뛰는 곳은 따로 묶음
            old, new = [], []
            for k, (a, f, n) in enumerate(builds):
                old += md5s(a, f"fc{k}.txt", f, n, tmp)
            for u in units:
                a, f, n, fcf = exportplan.unit_args(u, builds, tmp, fps)
                got = md5s(a, fcf, f, n, tmp)
                self.assertEqual(len(got), n, u)
                new += got
            self.assertEqual(len(old), sum(b - a for a, b in segs))
            diff = [i for i, (x, y) in enumerate(zip(old, new)) if x != y]
            self.assertEqual(diff, [], f"다른 프레임 {len(diff)}개")
            self.assertEqual(len(old), len(new))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_frame_count(self):
        proj = self.cut_proj("묶음 내보내기")
        total = editor.seq_total(editor.migrate_seq(dict(proj)))
        out, _ = self.export(proj)
        self.assertEqual(nframes(core.OUT / out[0]), int(round(total * 30)))
        with mock.patch.object(exportplan, "ENABLED", False):
            out2, _ = self.export(proj)
        self.assertEqual(nframes(core.OUT / out2[0]), int(round(total * 30)))

    def test_plan_limits(self):
        def b(ss, t, n, path="a.mp4"):
            return (["-ss", f"{ss:.6f}", "-t", f"{t:.3f}", "-i", path], "v1", n)
        builds = [b(0, 2.3, 60), b(2.5, 2.3, 60), b(9.0, 2.3, 60), b(9.1, 2.3, 60, "b.mp4"), b(1.0, 2.3, 60, "b.mp4"),
                  (["-loop", "1", "-i", "x.png"], "v1", 60), b(20, 2.3, 60), b(22.4, 2.3, 60)]
        texts = ["[0:v]null[v1]"] * len(builds)
        self.assertEqual(exportplan.plan(builds, texts, 30), [[0, 1], [2], [3], [4], [5], [6, 7]])
        texts[1] = "[0:v]sendcmd=f=x.cmd,colorchannelmixer@o1=aa=1[v1]"
        self.assertEqual(exportplan.plan(builds, texts, 30)[:3], [[0], [1], [2]])
        # 다시 보기(같은 범위를 또 씀)는 안 묶음: split 이 겹친 프레임을 다 쌓아 메모리가 커짐 · 이어 자른 컷(꼬리 0.3초 겹침)은 묶음
        rep = [b(10.0, 11.5, 300), b(12.0, 10.0, 300), b(21.98, 2.3, 60), b(24.0, 2.3, 60)]
        self.assertEqual(exportplan.plan(rep, ["[0:v]null[v1]"] * 4, 30), [[0], [1, 2, 3]])
        # HDR(색 바꾸기)·그래픽카드 풀기·픽셀 형식을 모르는 원본은 안 묶음
        hdr = [b(0, 2.3, 60), b(2.5, 2.3, 60)]
        self.assertEqual(exportplan.plan(hdr, ["[0:v]zscale=t=linear,null[v1]"] * 2, 30), [[0], [1]])
        hw = [(["-hwaccel", "d3d11va"] + b(0, 2.3, 60)[0], "v1", 60), (["-hwaccel", "d3d11va"] + b(2.5, 2.3, 60)[0], "v1", 60)]
        self.assertEqual(exportplan.plan(hw, ["[0:v]null[v1]"] * 2, 30), [[0], [1]])
        self.assertEqual(exportplan.plan(hdr, ["[0:v]null[v1]"] * 2, 30, lambda p: None), [[0], [1]])
        self.assertEqual(exportplan.plan(hdr, ["[0:v]null[v1]"] * 2, 30, lambda p: "yuv420p"), [[0, 1]])
        many = [b(2.5 * k, 2.3, 60) for k in range(40)]
        u = exportplan.plan(many, ["[0:v]null[v1]"] * 40, 30)
        self.assertTrue(all(len(x) <= exportplan.MAX_CUTS and len(x) * 60 <= exportplan.MAX_SEC * 30 for x in u))


if __name__ == "__main__":
    unittest.main()
