"""클로드 아껴 쓰기 (D-390~D-395) 테스트 — 가짜 claude(파이썬 스크립트, FUTSAL_CLAUDE)로.
저장소 폴더에서: python3 -m unittest tests.test_claude_saver
- 옵션은 'claude --help' 에 있을 때만: 짧은 지시(--system-prompt) · --max-turns · 그림 바로 보내기(stream-json 입출력 + --verbose)
- 일마다 모델 (대부분 sonnet · 채널 전략만 opus) · 끄면 예전 명령 그대로 (모두 opus · 기본 지시 · 그림은 폴더의 Read)
- 그림은 긴 변 1568 · 1.15MP 안으로 (작은 그림은 바이트 그대로) · 예전 판·실행 중 실패는 Read 로
- 저장해 둔 대답: 같은 물음은 claude 를 다시 안 부름 · 오류·멈춤·모양 틀린 대답은 저장 안 함 · 400개/30MB 넘으면 오래된 것부터
- 쓴 양: 결과 JSON 의 usage·modelUsage → 작업 기록 한 줄 · 날마다 합계(숫자만 · 프롬프트·대답 없음)"""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import claude_cli  # noqa: E402

POSIX = unittest.skipIf(sys.platform == "win32", "가짜 claude 는 실행 권한이 있는 스크립트 (Linux·Mac)")

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import base64, io, json, os, sys
    args = sys.argv[1:]
    help_mode = os.environ.get("FAKE_HELP", "new")
    mode = os.environ.get("FAKE_MODE", "ok")
    if args[:1] == ["--help"]:
        print("Usage: claude [options]\\n  -p, --print\\n  --output-format <format>  \\"text\\", \\"json\\", or \\"stream-json\\"\\n  --tools <tools...>")
        print("  --no-session-persistence\\n  --strict-mcp-config\\n  --setting-sources <s>\\n  --permission-prompts <target>\\n  --restricted")
        print("  --system-prompt-snapshot <on|off>\\n  --verbose")
        if help_mode != "old":
            print("  --system-prompt <prompt>  System prompt\\n  --input-format <format>  \\"text\\" (default), or \\"stream-json\\"")
        if help_mode == "new":
            print("  --max-turns <turns>")
        print("Commands:\\n  auth   Manage authentication")
        sys.exit(0)
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
        sys.exit(0)
    if "-p" not in args:
        sys.exit(2)
    raw = sys.stdin.read()
    rec = {"args": args, "files": sorted(os.listdir(".")), "stdin_len": len(raw)}
    if "--input-format" in args:
        msgs = [json.loads(x) for x in raw.splitlines() if x.strip()]
        rec["msgs"] = len(msgs)
        rec["type"], rec["role"] = msgs[0].get("type"), msgs[0]["message"].get("role")
        blocks = []
        for b in msgs[0]["message"]["content"]:
            if b["type"] == "image":
                data = base64.b64decode(b["source"]["data"])
                d = {"type": "image", "media": b["source"]["media_type"], "kind": b["source"]["type"], "bytes": len(data),
                     "sha": __import__("hashlib").sha256(data).hexdigest()}
                try:
                    from PIL import Image
                    d["size"] = list(Image.open(io.BytesIO(data)).size)
                except Exception:
                    pass
                blocks.append(d)
            else:
                blocks.append({"type": b["type"], "text": b.get("text")})
        rec["blocks"] = blocks
    else:
        rec["prompt"] = raw
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\\n")
    if mode == "badinline" and "--input-format" in args:
        sys.stderr.write("Error: something about input\\n")
        sys.exit(1)
    if mode == "limit":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429, "result": "usage limit reached",
                          "usage": {"input_tokens": 5, "output_tokens": 0}}))
        sys.exit(1)
    model = args[args.index("--model") + 1]
    reply = os.environ.get("FAKE_REPLY", '{"fix": []}')
    res = {"type": "result", "subtype": "success", "is_error": False, "result": reply, "num_turns": 1,
           "usage": {"input_tokens": 3, "cache_creation_input_tokens": 500, "cache_read_input_tokens": 200, "output_tokens": 40},
           "modelUsage": {"claude-" + model + "-test": {"inputTokens": 3, "outputTokens": 40, "cacheReadInputTokens": 200,
                                                        "cacheCreationInputTokens": 500, "costUSD": 0.01}}}
    if "--output-format" in args and args[args.index("--output-format") + 1] == "stream-json":
        print(json.dumps({"type": "system", "subtype": "init", "tools": [], "model": model}))
        print(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}}, ensure_ascii=False))
    print(json.dumps(res, ensure_ascii=False))
''')


def jpeg(w, h, color=(40, 120, 60)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im)
    for i in range(16):  # 장면 시트처럼 번호 상자
        x, y = (i % 4) * (w // 4), (i // 4) * (h // 4)
        d.rectangle([x, y, x + 52, y + 44], fill=(255, 210, 0))
        d.text((x + 8, y + 2), str(i + 1), fill=(0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


@POSIX
class SaverBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="아껴 쓰기 "))
        self.exe = self.tmp / "claude"
        self.exe.write_text(FAKE, encoding="utf-8")
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "calls.jsonl"
        self.env = mock.patch.dict(os.environ, {"FUTSAL_CLAUDE": str(self.exe), "FAKE_CLAUDE_LOG": str(self.log),
                                                "FAKE_HELP": "new", "FAKE_MODE": "ok"})
        self.env.start()
        self.home = mock.patch.object(claude_cli, "HOME", self.tmp / "home")
        self.home.start()
        claude_cli._FEAT.clear()
        claude_cli._INLINE_BAD.clear()
        claude_cli.reset_status()

    def tearDown(self):
        self.env.stop()
        self.home.stop()
        claude_cli._FEAT.clear()
        claude_cli._INLINE_BAD.clear()
        claude_cli.reset_status()
        claude_cli.job_end()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def img(self, name, w, h):
        f = self.tmp / name
        f.write_bytes(jpeg(w, h))
        return f

    @staticmethod
    def arg(a, flag):
        return a[a.index(flag) + 1]


class TestFlagsAndModels(SaverBase):
    def test_new_cli_text_flags(self):
        r = claude_cli.run("받아쓰기 고쳐 주세요", task="proofread")
        self.assertEqual(r["model"], "claude-sonnet-test")
        c = self.calls()[-1]
        a = c["args"]
        self.assertEqual(self.arg(a, "--model"), "sonnet")
        self.assertEqual(self.arg(a, "--system-prompt"), claude_cli.system_prompt("proofread"))
        self.assertEqual(self.arg(a, "--max-turns"), str(claude_cli.MAX_TURNS))
        self.assertEqual(self.arg(a, "--tools"), "", "그림이 없으면 도구 없음 (그대로)")
        self.assertEqual(self.arg(a, "--output-format"), "json")
        self.assertNotIn("--input-format", a)
        for flag in ("--no-session-persistence", "--strict-mcp-config", "--restricted"):
            self.assertIn(flag, a, "안전 옵션 그대로")
        self.assertEqual(self.arg(a, "--setting-sources"), "")
        self.assertEqual(self.arg(a, "--permission-mode"), "dontAsk")
        self.assertEqual(c["prompt"], "받아쓰기 고쳐 주세요", "프롬프트는 stdin 그대로")
        self.assertNotIn("받아쓰기 고쳐", " ".join(a))
        self.assertEqual(c["files"], [])

    def test_feature_detection(self):
        f = claude_cli.features()
        self.assertTrue(f["system_prompt"] and f["max_turns"] and f["stream_in"])
        os.environ["FAKE_HELP"] = "nomax"
        claude_cli._FEAT.clear()
        f = claude_cli.features()
        self.assertTrue(f["system_prompt"] and f["stream_in"])
        self.assertFalse(f["max_turns"])
        claude_cli.run("x", task="copy")
        self.assertNotIn("--max-turns", self.calls()[-1]["args"], "도움말에 없는 옵션은 넣지 않음")
        os.environ["FAKE_HELP"] = "old"
        claude_cli._FEAT.clear()
        f = claude_cli.features()
        self.assertFalse(f["system_prompt"] or f["stream_in"], "--system-prompt-snapshot 만 있으면 짧은 지시 없음")

    def test_model_map(self):
        self.assertEqual({k: claude_cli.model_for(k, True) for k in claude_cli.TASK_MODEL},
                         {"proofread": "sonnet", "writer": "sonnet", "frames": "sonnet", "copy": "sonnet", "judge": "sonnet", "plan": "sonnet",
                          "strategy": "opus"})
        self.assertEqual(claude_cli.model_for("copy", False), "opus", "끄면 모두 Opus")
        self.assertEqual(claude_cli.model_for(None, True), "opus")
        claude_cli.run("전략", task="strategy")
        self.assertEqual(self.arg(self.calls()[-1]["args"], "--model"), "opus", "채널 전략 '더 깊게'는 Opus")

    def test_untasked_call_is_old_command(self):
        """일 이름 없이 부르면(예전 호출부·시험) 예전 명령 그대로 · 저장 안 함."""
        claude_cli.run("x")
        claude_cli.run("x")
        self.assertEqual(len(self.calls()), 2)
        a = self.calls()[-1]["args"]
        self.assertEqual(self.arg(a, "--model"), "opus")
        self.assertNotIn("--system-prompt", a)
        self.assertNotIn("--max-turns", a)

    def test_setting_off_is_old_behaviour(self):
        self.assertTrue(claude_cli.save_on(), "기본은 켬")
        claude_cli.set_save(False)
        self.assertFalse(claude_cli.save_on())
        big = self.img("시트.jpg", 1800, 1032)
        old = claude_cli.build_cmd(str(self.exe), True, claude_cli.features())
        claude_cli.run("장면 점수", images=[(big, "frames.jpg")], task="frames")
        claude_cli.run("장면 점수", images=[(big, "frames.jpg")], task="frames")
        cs = self.calls()
        self.assertEqual(len(cs), 2, "끄면 저장해 둔 대답도 안 씀")
        self.assertEqual(cs[-1]["args"], old[1:] if old[0] == str(self.exe) else old, "예전 명령 그대로 (Opus · 기본 지시 · Read)")
        self.assertEqual(self.arg(cs[-1]["args"], "--tools"), "Read")
        self.assertEqual(cs[-1]["files"], ["frames.jpg"])
        self.assertEqual(cs[-1]["prompt"], "장면 점수")
        self.assertFalse((claude_cli.HOME / claude_cli.CACHE_DIR).exists())
        claude_cli.set_save(True)
        self.assertTrue(claude_cli.save_on())

    def test_system_prompts_cmd_safe(self):
        """claude.cmd 는 cmd.exe 를 거침 — 한 줄 · 큰따옴표·&|<>^%!() 없음 · 짧게."""
        for t in list(claude_cli.SYS_TASK) + [None]:
            for inline in (False, True):
                s = claude_cli.system_prompt(t, inline)
                self.assertFalse(set(s) & set('"&|<>^%!()\n\r'), s)
                self.assertLess(len(s), 400)

    def test_cmd_exe_path_with_space_skips_system_prompt(self):
        f = {"system_prompt": True, "max_turns": True, "stream_in": True, "tools": True}
        with mock.patch.dict(os.environ, {"COMSPEC": "cmd.exe"}):
            a = claude_cli.build_cmd(r"C:\Users\kim\AppData\Roaming\npm\claude.cmd", False, f, model="sonnet", system="짧은 지시")
            self.assertIn("--system-prompt", a)
            b = claude_cli.build_cmd(r"C:\Users\Kim Lee\AppData\Roaming\npm\claude.cmd", False, f, model="sonnet", system="짧은 지시")
            self.assertNotIn("--system-prompt", b, "따옴표가 셋 이상이면 cmd 가 떼어 냄 → 넣지 않음")
            self.assertEqual(self.arg(b, "--tools"), "Read", "빈 인자를 못 넘김 (그대로)")
            self.assertIn("--max-turns", b)


class TestImages(SaverBase):
    def test_stream_json_payload(self):
        big = self.img("장면 시트.jpg", 1800, 1032)
        r = claude_cli.run("frames.jpg 를 Read 도구로 열어 보세요. 점수", images=[(big, "frames.jpg")], task="frames")
        self.assertEqual(r["text"], '{"fix": []}', "stream-json 출력의 result 줄")
        c = self.calls()[-1]
        a = c["args"]
        self.assertEqual(self.arg(a, "--input-format"), "stream-json")
        self.assertEqual(self.arg(a, "--output-format"), "stream-json")
        self.assertIn("--verbose", a)
        self.assertEqual(self.arg(a, "--tools"), "", "그림을 메시지에 붙이면 Read 도구가 필요 없음")
        self.assertEqual(self.arg(a, "--system-prompt"), claude_cli.system_prompt("frames", True))
        self.assertIn("붙어 있", self.arg(a, "--system-prompt"))
        self.assertEqual(c["files"], [], "폴더에 그림을 두지 않음")
        self.assertEqual((c["msgs"], c["type"], c["role"]), (1, "user", "user"))
        bl = c["blocks"]
        self.assertEqual([b["type"] for b in bl], ["text", "image", "text"])
        self.assertEqual(bl[0]["text"], "[그림 frames.jpg]")
        self.assertEqual((bl[1]["kind"], bl[1]["media"]), ("base64", "image/jpeg"))
        w, h = bl[1]["size"]
        self.assertLessEqual(max(w, h), claude_cli.IMG_SIDE)
        self.assertLessEqual(w * h, claude_cli.IMG_PIXELS)
        self.assertGreater(w, 1300, "번호가 읽히는 크기 (16장 시트)")
        self.assertAlmostEqual(w / h, 1800 / 1032, places=2)
        self.assertEqual(bl[2]["text"], "frames.jpg 를 Read 도구로 열어 보세요. 점수", "프롬프트 내용은 그대로")

    def test_small_images_keep_bytes(self):
        small = self.img("small.jpg", 168, 94)
        full = self.img("full.jpg", 1280, 720)
        claude_cli.run("평가", images=[(small, "small.jpg"), (full, "full.jpg")], task="judge")
        bl = self.calls()[-1]["blocks"]
        import hashlib
        shas = [b["sha"] for b in bl if b["type"] == "image"]
        self.assertEqual(shas, [hashlib.sha256(small.read_bytes()).hexdigest(), hashlib.sha256(full.read_bytes()).hexdigest()],
                         "한도 안의 그림은 다시 저장하지 않음 (평가 그림 화질 그대로)")
        self.assertEqual([b["text"] for b in bl if b["type"] == "text"], ["[그림 small.jpg]", "[그림 full.jpg]", "평가"])

    def test_old_cli_falls_back_to_read(self):
        os.environ["FAKE_HELP"] = "old"
        big = self.img("시트.jpg", 1800, 1032)
        claude_cli.run("점수", images=[(big, "frames.jpg")], task="frames")
        c = self.calls()[-1]
        a = c["args"]
        self.assertNotIn("--input-format", a)
        self.assertNotIn("--system-prompt", a, "예전 판: 기본 지시 그대로")
        self.assertEqual(self.arg(a, "--tools"), "Read")
        self.assertEqual(self.arg(a, "--model"), "sonnet", "모델은 일마다")
        self.assertEqual(c["files"], ["frames.jpg"])
        self.assertEqual(c["prompt"], "점수")

    def test_inline_failure_retries_with_read(self):
        os.environ["FAKE_MODE"] = "badinline"
        big = self.img("시트.jpg", 900, 500)
        r = claude_cli.run("점수", images=[(big, "frames.jpg")], task="frames")
        self.assertEqual(r["text"], '{"fix": []}')
        cs = self.calls()
        self.assertEqual(len(cs), 2)
        self.assertIn("--input-format", cs[0]["args"])
        self.assertNotIn("--input-format", cs[1]["args"])
        self.assertEqual(cs[1]["files"], ["frames.jpg"])
        self.assertIn(str(self.exe), claude_cli._INLINE_BAD)
        claude_cli.run("점수 2", images=[(big, "frames.jpg")], task="frames")
        self.assertNotIn("--input-format", self.calls()[-1]["args"], "이번 실행 동안은 바로 Read 로")

    def test_prep_image_png_and_garbage(self):
        nm, data, mt = claude_cli.prep_image("x.jpg", b"\xff\xd8jpg")
        self.assertEqual((nm, data, mt), ("x.jpg", b"\xff\xd8jpg", "image/jpeg"), "그림이 아니면 그대로")
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (3000, 1000), (1, 2, 3, 128)).save(buf, "PNG")
        nm, data, mt = claude_cli.prep_image("a.png", buf.getvalue())
        self.assertEqual(mt, "image/png")
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.format, "PNG")
            self.assertLessEqual(max(im.size), claude_cli.IMG_SIDE)


class TestCacheAndUsage(SaverBase):
    def test_cache_hit_avoids_second_call(self):
        r1 = claude_cli.run("같은 물음", task="copy")
        r2 = claude_cli.run("같은 물음", task="copy")
        self.assertEqual(len(self.calls()), 1, "두 번째는 저장해 둔 대답")
        self.assertFalse(r1["cached"])
        self.assertTrue(r2["cached"])
        self.assertEqual((r2["text"], r2["model"]), (r1["text"], r1["model"]))
        claude_cli.run("다른 물음", task="copy")
        claude_cli.run("같은 물음", task="writer")
        self.assertEqual(len(self.calls()), 3, "물음·일(모델)이 다르면 새로")
        for f in (claude_cli.HOME / claude_cli.CACHE_DIR).glob("*.json"):
            self.assertNotIn("같은 물음", f.read_text(encoding="utf-8"), "프롬프트는 남기지 않음")

    def test_image_bytes_and_version_in_key(self):
        a = self.img("a.jpg", 320, 180)
        claude_cli.run("점수", images=[(a, "frames.jpg")], task="frames")
        claude_cli.run("점수", images=[(a, "frames.jpg")], task="frames")
        self.assertEqual(len(self.calls()), 1)
        a.write_bytes(jpeg(320, 180, (200, 10, 10)))
        claude_cli.run("점수", images=[(a, "frames.jpg")], task="frames")
        self.assertEqual(len(self.calls()), 2, "그림이 바뀌면 새로")
        with mock.patch.object(claude_cli, "_app_ver", return_value="9.99"):
            claude_cli.run("점수", images=[(a, "frames.jpg")], task="frames")
        self.assertEqual(len(self.calls()), 3, "앱 판(앞 두 자리)이 바뀌면 새로")
        self.assertRegex(claude_cli._app_ver(), r"^\d+\.\d+$")

    def test_errors_and_bad_answers_not_cached(self):
        os.environ["FAKE_MODE"] = "limit"
        with self.assertRaises(claude_cli.ClaudeError) as cm:
            claude_cli.run("한도", task="copy")
        self.assertEqual(cm.exception.kind, "limit")
        os.environ["FAKE_MODE"] = "ok"
        claude_cli.run("한도", task="copy")
        self.assertEqual(len(self.calls()), 2, "오류는 저장하지 않음")

        def bad(t):
            raise ValueError("모양이 달라요")
        r = claude_cli.run("모양", task="copy", check=bad)
        self.assertFalse(r["cached"])
        claude_cli.run("모양", task="copy", check=bad)
        self.assertEqual(len(self.calls()), 4, "확인에 걸린 대답은 저장하지 않음 (다시 누르면 새로)")

    def test_cancel_not_cached(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(claude_cli.ClaudeError) as cm:
            claude_cli.run("멈춤", task="copy", cancel=ev)
        self.assertEqual(cm.exception.kind, "cancel")
        self.assertFalse(list((claude_cli.HOME / claude_cli.CACHE_DIR).glob("*.json")) if (claude_cli.HOME / claude_cli.CACHE_DIR).exists() else [])

    def test_cache_bounds_lru(self):
        with mock.patch.object(claude_cli, "CACHE_MAX_N", 3):
            keys = [f"{i:040x}" for i in range(5)]
            for i, k in enumerate(keys):
                claude_cli.cache_put(k, f"대답 {i}", "m")
                os.utime(claude_cli._cache_dir() / f"{k}.json", (1000 + i, 1000 + i))
            self.assertIsNotNone(claude_cli.cache_get(keys[2]))  # 다시 씀 → 가장 최근
            claude_cli.cache_put("f" * 40, "새 대답", "m")
            left = sorted(p.stem for p in claude_cli._cache_dir().glob("*.json"))
            self.assertEqual(left, sorted([keys[2], keys[4], "f" * 40]))
        with mock.patch.object(claude_cli, "CACHE_MAX_BYTES", 200):
            claude_cli.cache_put("e" * 40, "가" * 100, "m")
            left = list(claude_cli._cache_dir().glob("*.json"))
            self.assertEqual([p.stem for p in left], ["e" * 40], "한도를 넘으면 오래된 것부터 모두 지움")
            self.assertIsNotNone(claude_cli.cache_get("e" * 40), "막 넣은 것은 남음")

    def test_usage_job_line_and_daily(self):
        claude_cli.job_begin()
        r = claude_cli.run("첫 물음", task="copy")
        self.assertEqual(r["usage"], {"input": 3, "output": 40, "cacheRead": 200, "cacheWrite": 500, "total": 743})
        claude_cli.run("첫 물음", task="copy")
        claude_cli.run("둘째", task="proofread")
        line = claude_cli.job_end()
        self.assertEqual(line, "클로드 2번 · 약 1천 토큰 · 아껴 쓰기 (저장해 둔 대답 1번 다시 씀)")
        self.assertIsNone(claude_cli.job_end(), "끝난 뒤에는 없음")
        t = claude_cli.usage_today()
        self.assertEqual((t["calls"], t["hits"], t["total"], t["output"]), (2, 1, 1486, 80))
        raw = (claude_cli.HOME / claude_cli.USAGE_FILE).read_text(encoding="utf-8")
        self.assertNotIn("물음", raw, "숫자만")
        d = json.loads(raw)
        for v in d["days"].values():
            self.assertTrue(all(isinstance(x, int) for x in v.values()))
        claude_cli.set_save(False)
        claude_cli.job_begin()
        claude_cli.run("셋째", task="copy")
        self.assertEqual(claude_cli.job_end(), "클로드 1번 · 약 743 토큰 · 예전처럼 (모두 Opus)")
        claude_cli.job_begin()
        self.assertIsNone(claude_cli.job_end(), "클로드를 안 쓴 작업은 줄 없음")

    def test_usage_days_bounded(self):
        f = claude_cli.HOME / claude_cli.USAGE_FILE
        claude_cli._write_json(f, {"v": 1, "days": {f"2025-01-{i:02d}": {"calls": 1} for i in range(1, 32)} | {"2024-12-31": {"calls": 9}}})
        claude_cli._record({"total": 5})
        days = json.loads(f.read_text(encoding="utf-8"))["days"]
        self.assertEqual(len(days), claude_cli.USAGE_DAYS)
        self.assertNotIn("2024-12-31", days)
        self.assertIn(claude_cli._today(), days)

    def test_usage_of_shapes(self):
        self.assertEqual(claude_cli.usage_of({"usage": {"input_tokens": 1, "output_tokens": 2}})["total"], 3)
        mu = {"modelUsage": {"a": {"inputTokens": 1, "outputTokens": 1}, "claude-haiku": {"inputTokens": 10, "outputTokens": 0}},
              "usage": {"input_tokens": 999}}
        self.assertEqual(claude_cli.usage_of(mu)["total"], 12, "모델마다 더함 (usage 보다 먼저)")
        self.assertEqual(claude_cli.usage_of(None)["total"], 0)
        self.assertEqual(claude_cli.usage_of({"usage": {"input_tokens": "x", "output_tokens": True}})["total"], 0)

    def test_tokens_ko(self):
        self.assertEqual([claude_cli.tokens_ko(n) for n in (0, 850, 1499, 12345, 99_999, 250_000)], ["0", "850", "1천", "12천", "10만", "25만"])


class TestCallers(SaverBase):
    def test_proofread_reuses_answer_for_unchanged_transcript(self):
        import proofread
        os.environ["FAKE_REPLY"] = '{"fix":[{"i":1,"t":"공을 놓칠까요?"}]}'
        segs = [{"start": 0, "end": 2, "text": "공으로 칠까요?", "words": [{"w": "공으로", "s": 0, "e": 1, "p": 1}, {"w": "칠까요?", "s": 1, "e": 2, "p": 1}]}]
        new, n, model = proofread.proofread(segs, "퍼스트 터치")
        self.assertEqual((n, new[0]["text"], model), (1, "공을 놓칠까요?", "claude-sonnet-test"))
        self.assertEqual(self.arg(self.calls()[-1]["args"], "--model"), "sonnet")
        new2, n2, _ = proofread.proofread(segs, "퍼스트 터치")
        self.assertEqual(len(self.calls()), 1, "같은 받아쓰기는 다시 안 보냄")
        self.assertEqual(new2, new)

    def test_proofread_bad_shape_not_cached(self):
        import proofread
        os.environ["FAKE_REPLY"] = "모르겠어요"
        segs = [{"start": 0, "end": 1, "text": "가나다"}]
        for _ in range(2):
            with self.assertRaises(proofread.ProofError):
                proofread.proofread(segs)
        self.assertEqual(len(self.calls()), 2)


@POSIX
class TestAppRoutes(SaverBase):
    def setUp(self):
        super().setUp()
        import app
        import core
        from http.server import ThreadingHTTPServer
        self.app = app
        self.wtmp = Path(tempfile.mkdtemp(prefix="아껴 쓰기 경로 "))
        work = self.wtmp / "작업"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        port = self.srv.server_address[1]
        self.p2 = [mock.patch.object(core, k, v) for k, v in dirs.items()] + [mock.patch.object(app, "PORT", port),
                                                                             mock.patch.object(app, "LOGFILE", work / "studio.log")]
        for p in self.p2:
            p.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.p2:
            p.stop()
        shutil.rmtree(self.wtmp, ignore_errors=True)
        super().tearDown()

    def call(self, path, body=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_status_and_toggle(self):
        code, j = self.call("/api/claude/status?refresh=1")
        self.assertEqual((code, j["state"], j["save"]), (200, "ready", True))
        self.assertEqual(j["today"]["calls"], 0)
        self.assertEqual(self.call("/api/claude/save", {"on": "아니"})[0], 400)
        code, j = self.call("/api/claude/save", {"on": False})
        self.assertEqual((code, j["ok"], j["save"]), (200, True, False))
        self.assertFalse(claude_cli.save_on())
        self.assertFalse(self.call("/api/claude/status")[1]["save"])
        self.call("/api/claude/save", {"on": True})
        self.assertTrue(claude_cli.save_on())
        import rstudio
        self.assertEqual(rstudio.POLICY[("POST", "/api/claude/save")], rstudio.WRITE)

    def test_job_logs_one_line(self):
        def job():
            claude_cli.run("물음", task="copy")
            claude_cli.run("물음", task="copy")
            return {"ok": True}
        n0 = len(self.app.LOG)
        self.assertTrue(self.app.start_job("시험 작업", job))
        for _ in range(300):
            if not self.app.JOB["name"]:
                break
            time.sleep(0.05)
        new = self.app.LOG[n0:]
        self.assertIn("클로드 1번 · 약 743 토큰 · 아껴 쓰기 (저장해 둔 대답 1번 다시 씀)", new)
        self.assertNotIn("물음", " ".join(new), "프롬프트는 기록에 없음")
        n1 = len(self.app.LOG)
        self.assertTrue(self.app.start_job("클로드 없는 작업", lambda: {"ok": True}))
        for _ in range(300):
            if not self.app.JOB["name"]:
                break
            time.sleep(0.05)
        self.assertFalse([x for x in self.app.LOG[n1:] if x.startswith("클로드")])


if __name__ == "__main__":
    unittest.main()
