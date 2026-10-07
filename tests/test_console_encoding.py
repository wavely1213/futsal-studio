"""Windows cp949 콘솔에서 쪼개진 한글 자모가 든 파일 이름을 기록해도 작업이 멈추지 않는지 (실제 오류: '정형ᄃ.mp4')."""
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402

NAME = "20190806_1Pc-eQlUdnU_뭉쳐야 찬다 정형ᄃ.mp4"


class ConsoleEncoding(unittest.TestCase):
    def cp949_stream(self):
        return io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")

    def test_log_survives_cp949_console(self):
        out = self.cp949_stream()
        with mock.patch.object(sys, "stdout", out):
            with self.assertRaises(UnicodeEncodeError):  # 고치기 전과 같은 상황
                print(f"스타일 분석 · {NAME}")
            app.log(f"스타일 분석 · {NAME}")  # 예외 없이 지나가야 함
        self.assertEqual(app.LOG[-1], f"스타일 분석 · {NAME}")

    def test_reconfigure_to_utf8(self):
        out, err = self.cp949_stream(), self.cp949_stream()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            app._utf8_console()
            print(NAME)
            print(NAME, file=sys.stderr)
            sys.stdout.flush()
            self.assertEqual(sys.stdout.encoding.lower().replace("-", ""), "utf8")
        self.assertIn(NAME.encode("utf-8"), out.buffer.getvalue())

    def test_no_console(self):  # pythonw: stdout 이 None
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            app._utf8_console()
            app.log(NAME)


if __name__ == "__main__":
    unittest.main()
