"""E12 받아쓰기 옵션 — 저장소 폴더에서 python3 -m unittest tests.test_whisper_opts

앞 구간 글을 이어 받지 않음(condition_on_previous_text=False · D-103): 같은 문장을 한 번 더 쓰거나 하지 않은 끝말을 지어내던 것.
'정확하게'(large-v3-turbo)만 — '빠르게'(small)는 A/B 에서 오히려 나빠져 예전 그대로 (I-101).
A/B(정답 대사와 글자 오류율)는 개발 증거 $SCRATCH/e12_evidence/asr 에 (실제 모델이 필요해 단위 시험에는 넣지 않음)."""
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import captions  # noqa: E402
import core  # noqa: E402
import numpy  # noqa: E402,F401 — core.analyze 가 불러 씀: 가짜 faster_whisper 로 sys.modules 를 되돌려도 numpy 는 남게 (두 번 불러오면 오류)


class WhisperOptsTest(unittest.TestCase):
    def test_no_condition_on_previous_text(self):
        class Model:
            hf_tokenizer = None

            def transcribe(self, audio, language=None, word_timestamps=False, initial_prompt=None, hotwords=None,
                           condition_on_previous_text=True, hallucination_silence_threshold=None, **kw):
                pass

        turbo, small, other = Model(), Model(), Model()
        with mock.patch.dict(core._WHISPER, {("large-v3-turbo", 4): turbo, ("small", 4): small}, clear=True):
            opts = core._whisper_opts(turbo, captions.default_dict())
            self.assertIs(opts["condition_on_previous_text"], False)  # '정확하게': 앞 구간 글을 이어 받지 않음 (같은 문장 두 번·지어낸 끝말)
            self.assertNotIn("hallucination_silence_threshold", opts)
            # '빠르게'(small)는 A/B 에서 오히려 나빠져 예전 그대로 · 이름을 모르는 모델도 그대로 (faster-whisper 기본값)
            self.assertNotIn("condition_on_previous_text", core._whisper_opts(small, captions.default_dict()))
            self.assertNotIn("condition_on_previous_text", core._whisper_opts(other, captions.default_dict()))
            self.assertEqual(core._model_name(small), "small")

        class OldModel:  # 그 옵션을 모르는 받아쓰기(흉내 모델 등)에는 넘기지 않음
            hf_tokenizer = None

            def transcribe(self, audio, language=None, word_timestamps=False, initial_prompt=None):
                pass

        old = OldModel()
        with mock.patch.dict(core._WHISPER, {("large-v3-turbo", 4): old}, clear=True):
            self.assertNotIn("condition_on_previous_text", core._whisper_opts(old, captions.default_dict()))

    def test_analyze_passes_it_to_the_model(self):
        """편집점 찾기(core.analyze)가 실제로 그 옵션으로 받아씀 — 앞 문장을 되풀이하던 받아쓰기 흉내 (D-103)."""
        tmp = Path(tempfile.mkdtemp(prefix="받아쓰기 옵션 "))
        work = tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        calls = []

        def W(a, b, w):
            return types.SimpleNamespace(start=a, end=b, word=w, probability=0.9)

        class Model:
            def __init__(self, *a, **k):
                self.hf_tokenizer = None

            def transcribe(self, audio, language=None, vad_filter=False, word_timestamps=False, initial_prompt=None, hotwords=None,
                           condition_on_previous_text=True, **kw):  # **kw: 성능 묶음의 vad_parameters 등 (합쳐도 깨지지 않게)
                calls.append(condition_on_previous_text)
                said = [types.SimpleNamespace(start=0.2, end=1.5, text=" 리턴 패스는 원터치로 돌려줘야 해요.",
                                              words=[W(0.2, 0.6, " 리턴"), W(0.6, 0.9, " 패스는"), W(0.9, 1.2, " 원터치로"), W(1.2, 1.5, " 돌려줘야 해요.")])]
                if condition_on_previous_text:  # 앞 구간 글을 이어 받으면 같은 문장을 한 번 더 · 끝에 하지 않은 말
                    said += [types.SimpleNamespace(start=1.5, end=1.5, text=" 리턴 패스는 원터치로 돌려줘야 해요.", words=[W(1.5, 1.5, " 리턴")]),
                             types.SimpleNamespace(start=1.6, end=1.9, text=" 다음 영상에서 만나요", words=[W(1.6, 1.9, " 만나요")])]
                return iter(said), types.SimpleNamespace(duration=2.0)
        name = "옵션 시험.mp4"
        try:
            with mock.patch.multiple(core, **dirs), mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=Model)}), \
                    mock.patch.dict(core._WHISPER, clear=True):
                r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=10:d=2",
                              "-f", "lavfi", "-i", "sine=f=440:sample_rate=16000:d=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                              "-c:a", "aac", str(dirs["VIDEOS"] / name)])
                self.assertEqual(r.returncode, 0, r.stderr)
                core.analyze(name, lambda m: None)
                segs = json.loads((core.adir(name) / "transcript.json").read_text(encoding="utf-8"))
        finally:
            core.set_progress()
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(calls, [False])
        self.assertEqual([s["text"] for s in segs], ["리턴 패스는 원터치로 돌려줘야 해요."])

    def test_small_model_keeps_default(self):
        """'빠르게'(small)로 편집점 찾기를 하면 condition_on_previous_text 를 넘기지 않음 (예전 그대로 · I-101)."""
        tmp = Path(tempfile.mkdtemp(prefix="받아쓰기 옵션 small "))
        work = tmp / "풋살 작업 폴더"
        dirs = {"WORK": work, "VIDEOS": work / "videos", "ANALYSIS": work / "analysis", "OUT": work / "out"}
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        got = []

        class Model:
            def __init__(self, *a, **k):
                self.hf_tokenizer = None

            def transcribe(self, audio, language=None, vad_filter=False, word_timestamps=False, initial_prompt=None, hotwords=None,
                           condition_on_previous_text=True, **kw):
                got.append(kw.get("condition_on_previous_text", condition_on_previous_text))
                return iter([]), types.SimpleNamespace(duration=2.0)
        name = "빠르게 시험.mp4"
        try:
            with mock.patch.multiple(core, **dirs), mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=Model)}), \
                    mock.patch.dict(core._WHISPER, clear=True):
                r = core.run([core.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=10:d=2",
                              "-f", "lavfi", "-i", "sine=f=440:sample_rate=16000:d=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                              "-c:a", "aac", str(dirs["VIDEOS"] / name)])
                self.assertEqual(r.returncode, 0, r.stderr)
                core.analyze(name, lambda m: None, model="small")
        finally:
            core.set_progress()
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(got, [True])


if __name__ == "__main__":
    unittest.main()
