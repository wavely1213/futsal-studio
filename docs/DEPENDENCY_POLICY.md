# DEPENDENCY_POLICY.md — 의존성 정책

> **목적**: 라이브러리·패키지의 추가·업데이트·제거 기준. 1인 개발에서 의존성은
> "설치는 1분, 유지보수는 평생"이므로 신중하게 관리한다.
> 이 앱은 의존성이 **비개발자 사용자의 Windows PC에 pip로 설치**되므로 한 번 잘못 넣으면 고치기 어렵다.

---

## 1. 새 의존성 추가 기준

추가 전에 순서대로 자문한다:

1. **표준 라이브러리/기존 의존성으로 해결되는가?** → 되면 추가하지 않는다. 이 프로젝트는 서버(`http.server`)·화면(단일 파일 HTML)·업데이트(`urllib`·`zipfile`)를 표준 라이브러리로 만든다(`DECISION_LOG.md`).
2. **직접 구현이 50줄 이내인가?** → 그 정도면 직접 구현을 우선 검토한다 (단, 보안·암호화·파싱은 예외 — 검증된 라이브러리 사용).
3. 추가한다면 건강성 확인: 최근 1년 내 릴리스, 알려진 심각 취약점 없음, 다운로드 수·유지보수 상태, 라이선스.
4. 이 프로젝트의 추가 기준은 다음과 같다.
   - **Windows용 휠이 있어야 한다**: 사용자 PC에는 컴파일러가 없어 소스 빌드가 필요한 패키지는 설치에 실패한다. 사용자 Python 버전(python.org 설치본, 고정 안 됨)용 휠이 있는지도 본다.
   - **크기**: 사용자가 처음 설치할 때 모두 받는다. 큰 패키지(예: PyTorch)는 피하고 onnxruntime·CPU 경로를 우선한다.
   - **`updater.py`는 표준 라이브러리만 쓴다.** 어떤 의존성도 넣지 않는다(모든 실행이 거치는 문, `ARCHITECTURE.md` 7절 4번).
   - 화면 HTML에는 프레임워크·외부 스크립트를 넣지 않는다(`CODING_STANDARDS.md` 7절).
   - 무겁거나 선택적인 패키지는 함수 안에서 import한다. 없거나 실패하면 예전 방식으로 계속한다.
   - pip 밖에서 받는 것(모델·실행 파일)도 의존성이다. 고정 주소, 크기·sha256 확인, 실패 시 대체 경로를 갖춰야 한다(`SECURITY_GUIDELINES.md` 5번).

## 2. 승인 규칙

- **AI는 새 런타임 의존성을 임의로 추가하지 않는다.** 추가가 필요하면: 이유 + 후보 1~2개 + 크기/라이선스를 한 줄씩 제시하고 승인 후 설치한다.
  - `requirements.txt`의 줄, 처음 쓸 때 받는 모델·실행 파일, 화면이 불러오는 CDN 자원이 모두 여기에 해당한다.
- 개발 의존성(테스트·린트 도구 등)도 동일하되, 이미 쓰는 도구의 플러그인 수준은 사후 보고로 갈음할 수 있다. 개발 도구는 `requirements.txt`에 넣지 않는다. 넣으면 사용자 PC에 설치된다.
- 프레임워크·DB·인증 등 **핵심 스택 교체는 반드시 `DECISION_LOG.md` 기록 + 사전 승인.**
- **`requirements.txt`를 바꾸면 다음 업데이트 때 모든 사용자 PC가 pip를 다시 돌린다**(`.req_hash` 비교). pip가 실패하면 그 업데이트 전체가 되돌려진다. 바꿀 때는 다음을 지킨다.
  - 깨끗한 venv에서 `pip install -r requirements.txt`가 되는지 확인한다.
  - 배포 노트에 "구성요소 설치(몇 분)"를 적는다.
  - v1.7.1 이하 PC는 업데이트 때 무조건 pip를 돌린다.

## 3. 버전 관리

- 락파일: **없다.** `requirements.txt`(7줄)가 유일한 목록이고 반드시 커밋한다.
- 버전 지정: 현재 **버전을 지정하지 않는다**(설치 시점의 최신을 받음). 상한·고정도 없다.
  - 예외: `faster-whisper>=1.1.0` 하한 하나 (단어 시각·`hotwords`가 있는 버전, D-019). 상한은 없다.
  - **`yt-dlp[default]`는 고정하지 않는다.** YouTube가 자주 바뀌어 앱이 직접 최신으로 올린다.
    - 켤 때 3일마다 올린다.
    - YouTube가 막으면 그 자리에서 1회 올린다(`core.update_engine`, `pip install -U`).
  - 다른 패키지는 새 설치 PC·pip를 다시 돌린 PC가 그때의 최신 메이저를 그대로 받는다. 그래서 PC마다 버전이 다를 수 있다.
  - 특정 버전에서 깨지는 것이 확인되면 그 패키지에만 상한을 다는 방안을 제시하고, 소유자가 결정한다. 결정은 `DECISION_LOG.md`에 남긴다.
- Python: 사용자가 python.org에서 받은 버전을 쓴다. 다른 문서는 Python 버전 규칙을 여기서 참조한다.
  - **설치가 받는 범위 (2026-10-07 기본값, D-033)**: 3.10~3.14 · 64비트(x64, `sysconfig.get_platform() == "win-amd64"`). `시작하기 (Windows).bat` 은 `py -3.13 → 3.12 → 3.11 → 3.14 → 3.10` 중 범위 안의 첫 것을 쓰고(`setup_check.py`), 3.15(휠 없음)·ARM64·32비트(ctranslate2·onnxruntime 휠 없음)는 거절하고 'Windows installer (64-bit)' 3.13 을 안내한다. 범위를 바꾸면 `setup_check.PY_MIN`·`PY_MAX`·README 를 함께.
  - 코드는 3.10 이상 기능을 쓴다(`tempfile.TemporaryDirectory(ignore_cleanup_errors=…)` — `updater.py` 업데이트 설치·`--selftest`, `core.py` Deno 설치).
  - 3.10 은 2026-10 지원이 끝나 yt-dlp 가 곧 뺄 예정이다. 그 뒤 3.10 PC 는 pip 가 예전 yt-dlp 에서 멈추므로 앱이 알아채 '새 Python 설치 → bat 다시 실행'을 안내하고(`core._engine_needs_newer_python`), bat 은 더 새 Python 이 있으면 `.venv` 를 옮겨 두고 새로 만든다.
  - 최소 지원 버전의 최종 확정은 소유자 몫이다 (`KNOWN_ISSUES.md` I-022).
  - 막 나온 Python에는 onnxruntime·ctranslate2(faster-whisper) 휠이 늦게 나올 수 있다 → 휠이 나온 뒤에 `PY_MAX` 를 올린다. 그 전에는 bat 이 '너무 새로 나옴 → 3.14 함께 설치'로 안내하고 독립 설치 파일 주소(`setup_check.PY_DIRECT`, bat 과 같은 주소 · 2026-10 기준 3.14.8)를 연다. 새 'Python 설치 관리자'가 있으면 3.14 를 그것으로 설치한다 (D-058).
  - ctranslate2·onnxruntime 은 Microsoft Visual C++ 재배포 패키지(msvcp140·msvcp140_1)가 필요하다 (Python·휠에 없음). bat 이 없으면 설치한다(D-033). `msvc-runtime` 같은 pip 패키지로 대신하지 않는다 (venv 에서는 DLL 을 찾지 못함).
  - pip 는 23.3 이상으로 올려 둔다(bat·업데이트 · Windows 에서 켜진 앱이 쓰는 .pyd 를 못 지워도 설치를 실패로 끝내지 않음, D-032).
- **메이저 업그레이드는 승인 필요** + 변경사항(breaking changes) 요약 보고 후 진행.
  - 버전을 고정하지 않으므로 주요 패키지(faster-whisper·onnxruntime·numpy·pywebview·pillow)의 새 메이저 소식을 알게 되면 먼저 보고한다.
  - 개발 PC에서 단위 + 영향받는 e2e로 확인한 결과를 함께 보고한다.
- 마이너/패치 업데이트는 테스트 통과를 확인하고 진행, 결과 보고.

## 4. 라이선스

- 허용: MIT, Apache-2.0, BSD, ISC 등 permissive 라이선스.
- **GPL 계열 등 카피레프트는 추가 전 반드시 보고** — 서비스 성격에 따라 소유자가 판단한다.
  - 현재 해당 사항: imageio-ffmpeg 자체는 BSD-2지만, 휠에 든 ffmpeg 실행 파일은 `--enable-gpl --enable-version3` 빌드다(개발 PC Linux 휠에서 확인, Windows 휠은 미확인).
  - 앱은 ffmpeg를 저장소·배포 zip에 넣지 않는다. pip로 설치된 것을 별도 프로세스로 실행만 한다.
  - ffmpeg를 앱에 직접 묶는 방식으로 바꾸려면 먼저 보고한다.
- 함께 배포하는 글꼴(Pretendard·Black Han Sans·Do Hyeon)은 OFL이고, 라이선스 파일을 `fonts/`에 함께 둔다. 글꼴을 추가할 때도 라이선스 파일을 같이 넣는다.
- 처음 쓸 때 받는 모델도 라이선스를 확인한다. 얼굴·표정 모델(ONNX model zoo UltraFace·FER+)은 MIT다(`face.py` 머리말). <!-- TODO: 누끼 모델(rembg 릴리스 v0.0.0의 BiRefNet-general-bb_swin_v1_tiny·u2net_human_seg)과 Whisper 모델(Hugging Face `mobiuslabsgmbh/faster-whisper-large-v3-turbo`)의 라이선스 확인·기록 -->
- 라이선스 불명 패키지는 사용하지 않는다.

## 5. 보안·정리

- 취약점 점검: 현재 자동 점검이 없다(GitHub Dependabot·`pip-audit` 미설정). 심각(critical/high) 취약점 발견 시 즉시 보고. <!-- TODO: 취약점 점검 방식 결정 (소유자) -->
- 사용자 PC의 yt-dlp는 3일마다 저절로 최신이 된다. Deno도 처음 설치할 때 최신 릴리스를 받는다. 다른 패키지는 `requirements.txt`가 바뀌기 전까지 그대로다.
- 취약점 패치 업데이트는 승인 없이 진행 가능 (테스트 통과 확인 + 보고).
- 더 이상 쓰지 않는 의존성을 발견하면 `KNOWN_ISSUES.md`에 기록 (제거는 승인 후). 2026-10-06 기준 `requirements.txt`의 7개는 모두 쓰고 있다.

## 6. 현재 핵심 의존성 목록

**pip (`requirements.txt`)** — 라이선스는 2026-10-06 개발 PC에 설치된 버전의 메타데이터 기준

| 패키지 | 용도 | 선택 이유 | 라이선스 |
|---|---|---|---|
| `yt-dlp[default]` | 채널 목록·영상 받기 (`core.list_videos`·`download`) | YouTube 변화에 가장 빨리 대응한다. `[default]`에 YouTube 해석 부품(yt-dlp-ejs)이 들어 있다 | Unlicense |
| `faster-whisper` (`>=1.1.0`) | 한국어 받아쓰기 (`core.analyze`, CPU int8, 기본 `large-v3-turbo`, 단어 시각·용어 힌트) | GPU 없이 CPU로 빠르다. PyTorch가 필요 없다 | MIT |
| `imageio-ffmpeg` | ffmpeg 실행 파일 (`core.ffmpeg()`, `FUTSAL_FFMPEG`로 대체 가능) | 사용자가 ffmpeg를 따로 설치하지 않아도 된다. ffprobe는 없어서 `ffmpeg -i` 출력을 읽는다 | BSD-2 (실행 파일은 GPL 빌드, 4번) |
| `pywebview` | 전용 앱 창 (Windows WebView2) | 브라우저 탭 대신 앱처럼 열고, 표준 서버 + HTML을 그대로 쓴다 | BSD-3 |
| `pillow` | 그림 처리 (썸네일 올리기 회전·축소, 장면 점수, 누끼 마스크, 얼굴 입력) | 사실상 표준이다 | MIT-CMU |
| `onnxruntime` | 누끼·얼굴·표정 모델 실행 (CPU) | PyTorch 없이 가볍다. Windows 휠이 있다 | MIT |
| `numpy` | 프레임·소리 계산 (스타일 배우기, 장면 점수, 모델 입력·출력) | onnxruntime·faster-whisper와 함께 쓴다 | BSD-3 외 |

**pip 밖에서 받는 것** (처음 쓸 때 사용자 폴더로, 실패해도 앱은 계속)

| 대상 | 받는 곳 → 저장 위치 | 용도 |
|---|---|---|
| Deno (≥ 2.3, Windows만 자동) | GitHub denoland/deno · dl.deno.land → `~/.futsal-studio/bin` | yt-dlp-ejs가 YouTube JS를 해석할 때 쓰는 실행기 |
| Whisper 모델 `large-v3-turbo` | Hugging Face `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (faster-whisper 기본 동작) | 받아쓰기 |
| 누끼 모델 BiRefNet(약 220MB)·u2net_human_seg(약 170MB) | GitHub danielgatis/rembg 릴리스 → `~/.futsal-studio/models` | 썸네일 배경 지우기 |
| 얼굴 UltraFace RFB-320·표정 FER+ (약 36MB) | ONNX model zoo 고정 커밋 → `~/.futsal-studio/models` | 썸네일 장면 고르기 표정 점수 |
| PP-OCRv5 mobile 글자 찾기(4.8MB)·한국어 읽기(13.5MB)·글자 목록 (Apache-2.0, RapidOCR ONNX 변환본) | ModelScope RapidAI/RapidOCR `v3.9.2` 태그 (예비: Hugging Face monkt/paddleocr-onnx 고정 커밋, 주소별 크기·sha256) → `~/.futsal-studio/models` | 영상 기획 분석: 레퍼런스 화면 자막 읽기 (`avmodels.ocr`) |
| YAMNet (16MB, Apache-2.0 표시, tf2onnx 변환본) | Hugging Face zeropointnine/yamnet-onnx 고정 커밋 → `~/.futsal-studio/models` | 영상 기획 분석: 웃음·환호·음악·효과음 (`avmodels.tags`) |
| Claude Code CLI (선택, 사용자가 설치) | Anthropic 공식 설치 명령(`irm https://claude.ai/install.ps1 \| iex`) → `%USERPROFILE%\.local\bin\claude.exe` | 클로드 계정으로 기획 판단 (D-021). 앱이 직접 받거나 묶지 않음 |
| Pretendard 웹폰트 CSS (v1.3.9 고정) | jsDelivr CDN (`ui.html`만) | 메인 화면 글꼴. 편집실·썸네일은 로컬 `fonts/` |
| cloudflared 2026.10.0 (Apache-2.0, Windows amd64 약 55MB / 386 약 38MB) | GitHub cloudflare/cloudflared 릴리스 고정 주소 · 크기·sha256 고정(`tunnel.CF_ASSETS`, 2026-10-07 공식 릴리스 노트와 맞춤) → `~/.futsal-studio/bin/cloudflared.exe` | 휴대폰으로 보기의 빠른 터널 (D-027). 원격 접속을 처음 켤 때만 받음 · 자동 업데이트 끔 · 6개월쯤마다 고정 판을 올린다(`KNOWN_ISSUES.md` I-037) |

**pip 에는 없지만 코드가 직접 쓰는 것**: `pycryptodomex`(`Cryptodome.Cipher.AES` GCM — 휴대폰으로 보기의 비콘·짝짓기 암호, BSD·퍼블릭 도메인). `yt-dlp[default]`의 의존성으로 모든 PC 에 깔려 있어 `requirements.txt`에 넣지 않았다(넣으면 모든 PC 가 pip 를 다시 돌림 · 넣을지는 소유자 결정, D-027). `remote.py`가 함수 안에서만 불러오고, 없으면 원격 접속만 켜지지 않는다.
**직접 만든 것**: QR 만들기(`qr.py`, 표준 라이브러리 · ISO 18004 바이트 모드). 라이브러리를 들이면 새 런타임 의존성이 되므로 직접 썼다(암호·파싱이 아님). 개발 PC 의 `segno` 와 칸을 하나하나 비교해 확인한다(`tests.test_qr`).

**개발 전용** (`requirements.txt`에 넣지 않음): Playwright(Python·Chromium, e2e 묶음), 시스템 `ffmpeg`·`ffprobe`(`ed2_test.py` 결과 확인), node(`thumb.html` JS 문법 확인 · ≥ 20 이면 `tests.test_remote_proto` 휴대폰 페이지 맞물림 시험), git·bash(`release.sh`), pyflakes/ruff(린트, `CODING_STANDARDS.md` 3번), segno(BSD, `tests.test_qr` 의 QR 칸 비교 · 없으면 건너뜀).
