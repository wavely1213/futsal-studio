# PROJECT_CONTEXT.md — 프로젝트 맥락

> **목적**: AI가 프로젝트의 배경·목표·기술 환경을 빠르게 파악하는 문서.
> 새 세션 시작 시, 또는 "왜 이렇게 되어 있지?"라는 의문이 들 때 읽는다.
> **갱신 시점**: 프로젝트 방향·스택·구조가 바뀔 때마다 즉시.

---

## 1. 프로젝트 개요

- **이름**: 풋살사관학교 스튜디오 (저장소 `wavely1213/futsal-studio`, 브랜치 `main`)
- **한 줄 설명**: 유튜브 채널 '풋살사관학교'를 되살리기 위한 Windows 전용 로컬 영상 제작 앱. 영상 받기 → 받아쓰기·편집점 → 자동 가편집(롱폼·쇼츠) → 편집실 → 썸네일 → 올리기 키트까지 한 앱에서 한다.
- **개발 형태**: 1인 개발 + AI 협업
- **현재 단계**: 운영 중. 사용자 PC에 배포되어 자동 업데이트로 버전이 올라감 (현재 버전은 `version.txt`, 작성 시점 v1.8.0). 2차 기능(batch 2)을 개발하고 있음.

## 2. 목표와 대상

- **해결하려는 문제**: 최경진 감독(소유자와 50/50 파트너)과 함께 채널을 되살리려면 촬영본을 롱폼·쇼츠로 빠르고 꾸준히 만들어 올려야 한다. 그런데 팀에 편집 전문가가 없다. 그래서 받기부터 올리기까지를 사용자 PC 안에서, AI 사용료 없이(규칙 기반 + 로컬 모델) 자동화한다.
- **최종 목표**: 유명 유튜버의 편집 스타일을 배워서 그 스타일대로 스스로 편집하는 도구.
- **대상 사용자**: 기술을 잘 모르는 한국인. **Windows PC만 지원**한다 (소유자 결정: "윈도우만 신경 써도 되고"). 화면 문구는 쉬운 한국어 존댓말(해요체)로 쓴다.
- **핵심 기능 (우선순위순)**:
  1. **소재 확보·분석**: 우리 채널이나 레퍼런스 채널의 영상 목록을 조회수 순으로 받는다(yt-dlp). 이어서 한국어로 받아쓰고(faster-whisper) 편집점(무음·음량 피크)을 찾는다.
  2. **자동 가편집**: 편집점 찾기가 끝나면 롱폼(16:9) 정리본 1개와 쇼츠(9:16) 1~3개를 바로 만든다. 배운 스타일이 있으면 그 스타일대로 만든다.
  3. **스타일 배우기**: 레퍼런스 영상에서 컷 리듬·펀치인·말 사이 공백·자막 위치와 색·LUFS를 배워 스타일 프로필로 저장한다. 최종 목표의 핵심 기능이다.
  4. **편집실 (프리미어식)**: 멀티 트랙, 도구, 키프레임, 전환, 오디오를 다룬다. 내보내기(mp4·Premiere XML·SRT) 후 영상을 자동 검수한다.
  5. **썸네일 편집기 (포토샵식)**: 롱폼 1280×720과 쇼츠 1080×1920을 따로 만든다. 템플릿 자동 제작, 얼굴·표정 점수로 장면 고르기, 누끼를 지원한다.
  6. **촬영본 묶기, 올리기 키트**: 나눠 찍힌 촬영 파일을 한 영상으로 묶는다. 올리기 키트는 제목 후보·설명·챕터·태그를 만들고 썸네일 규격을 확인한다.
  7. **안전한 자동 업데이트**: 받은 파일을 확인하고, 문제가 생기면 이전 버전으로 되돌린다. 다운로드 엔진(yt-dlp)도 자동으로 관리한다.
- **명시적으로 하지 않을 것 (Non-goals)**:
  - Windows 외 OS 지원. Mac용 코드 경로와 `시작하기 (Mac).command`가 남아 있지만 검증·지원 대상이 아니다.
  - 웹 프레임워크·번들러·빌드 도구 도입. 화면은 단일 파일 HTML, 서버는 표준 라이브러리만 쓴다.
  - 사용자 계정 정보로 YouTube 봇 검사를 자동 우회하는 것. 쿠키는 사용자가 브라우저를 직접 고를 때만 쓴다.
  - 유료 AI API 호출. Claude는 사용자가 '복사' 버튼으로 프롬프트를 복사해 claude.ai에 붙여 넣는 방식으로만 쓴다.
  - 인터넷 공개 서버·다중 사용자·로그인. 앱은 127.0.0.1 전용 1인 로컬 앱이다.
  - 영상 업로드 자동화. 앱은 올리기 키트를 만들고 YouTube Studio를 여는 데까지만 한다.
  - 이 항목들의 결정 이유는 `DECISION_LOG.md`에 있다.

## 3. 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| 언어 | Python 3 + 바닐라 HTML/CSS/JS | 코드가 3.10 이상 기능을 쓴다. 최소 지원 버전은 미정 (`DEPENDENCY_POLICY.md` 3번). |
| 프론트엔드 | 단일 파일 HTML 3개: `ui.html`(스튜디오), `editor.html`(편집실), `thumb.html`(썸네일) | 프레임워크·빌드 없음. pywebview 전용 창(Windows는 WebView2)에 띄우고, 창을 못 열면 브라우저로 연다 |
| 백엔드 | Python 표준 라이브러리 `ThreadingHTTPServer` (`app.py`) | `127.0.0.1:FUTSAL_PORT`(기본 8765)에만 bind. Host 헤더를 검사. 긴 작업은 `start_job` 하나씩 처리 |
| 데이터베이스 | 없음. 작업 폴더의 JSON·미디어 파일에 저장 | 작업 폴더는 `config.json`의 `workspace`, 비어 있으면 `~/풋살사관학교_작업` |
| 인증 | 없음 (로컬 1인용) | 대신 Host·Origin 검사와 파일 이름 검사를 함 (`ARCHITECTURE.md` 6절) |
| 배포/호스팅 | GitHub `main`의 `manifest.json` + 배포 커밋 zip | 사용자 PC의 `updater.py`가 받아서 확인한 뒤 설치. 서버 호스팅은 없음 |
| 기타 (결제, 알림 등) | 영상: ffmpeg(`imageio-ffmpeg` 번들, ffprobe 없음) · 다운로드: `yt-dlp[default]` + Deno · 받아쓰기: `faster-whisper`(`large-v3-turbo`, CPU int8, 한국어) · 이미지·추론: Pillow, numpy, onnxruntime · 폰트: Pretendard, Black Han Sans, Do Hyeon(OFL, `fonts/`) | 의존성 목록은 `requirements.txt`, 정책은 `DEPENDENCY_POLICY.md` |

> 스택 변경은 반드시 `DECISION_LOG.md`에 이유와 함께 기록한다.

## 4. 저장소 구조 (최상위)

```
app.py              앱 진입점: HTTP 서버·API 라우팅·작업 실행(start_job)·pywebview 창·바탕화면 바로가기
updater.py          실행기(--launch)·안전한 업데이트·되돌리기·--selftest (표준 라이브러리만)
core.py             설정·작업 폴더 경로·ffmpeg·진행률·목록/다운로드(yt-dlp·Deno)·받아쓰기/편집점·업데이트 진입
editor.py           편집실 백엔드: 프로젝트 저장/백업·자동 가편집·추천·내보내기(mp4/Premiere XML/SRT)
thumb.py            썸네일 백엔드: 장면 후보·캡처·누끼·디자인 저장·이미지 내보내기
face.py             얼굴·표정 점수 (ONNX 모델은 처음 쓸 때 내려받음)
style.py            스타일 배우기 → WORK/styles/<이름>.json
plan.py             영상 기획 분석 (인트로·장르·형식·자막·재미 판단) → 스타일의 plan
avmodels.py         기획 분석 모델 (화면 글자 OCR · 소리 종류 YAMNet, 처음 쓸 때 받음)
claude_cli.py       클로드 계정으로 쓰기 (사용자 PC의 Claude Code CLI 호출)
qa.py               내보낸 영상 자동 검수
bundle.py           촬영본 여러 파일 → 한 영상
upload.py           올리기 키트 (제목·설명·챕터·태그·썸네일 확인)
hooks.py            제목 후보 (우리 채널 제목 패턴·풋살 주제어)
source.py           영상 출처 구분 (풋살사관학교·다른 채널(채널별)·내 촬영본) → videos/sources.json
refs.py             학습용 영상 (스타일 배우기 전용 · 편집용 보관함과 따로) → WORK/refs/<채널>/ · refs/refs.json
ref_channels.json   추천 채널 51곳·방향 A/B/C 추천 영상 (2026-10-07 조사 · 읽기만)
takes.py            NG 테이크·슬레이트·말더듬 찾기 (2차 작업 중, 아직 커밋 전)
ui.html             스튜디오 화면 (1 소재 찾기 ~ 7 올리기)
editor.html         편집실 화면 (프리미어식)
thumb.html          썸네일 편집기 화면 (포토샵식). 빌드 산출물이며 원본은 저장소 밖에 있음 (아래 주의)
fonts/              자막·썸네일 폰트 + OFL 라이선스
tests/              단위 테스트 (unittest). 배포 목록에서 빠짐
docs/               개발 지침·기록 문서 (이 문서 포함)
config.json         기본 설정 (channel_url·workspace·update_manifest_url). 사용자 PC에서는 업데이트가 덮어쓰지 않음
version.txt         현재 버전 (release.sh가 씀)
manifest.json       배포 안내 + 파일별 sha256 (release.sh가 씀)
release.sh          배포 스크립트
requirements.txt    pip 의존성
시작하기 (Windows).bat   첫 설치(.venv·pip) 후 updater.py --launch 실행
시작하기 (Mac).command   지원 대상 아님
icon.ico / icon.png 앱 아이콘
```

- **저장소 밖에 있는 것 (주의)**: 아래 파일은 저장소가 아니라 리드 개발 환경의 scratchpad(`$SCRATCH`, 경로는 `AGENTS.md` 5번)에 있다. 잃어버릴 위험이 있다 (`KNOWN_ISSUES.md` I-018).
  - 썸네일 화면 원본: `thumb_v2_head.html` + `tv2/p1_core.js` … `p7_auto.js`. 이것을 `build_thumb.sh`로 이어 붙여 저장소의 `thumb.html`을 만든다 (`ARCHITECTURE.md` 7절 6번).
  - E2E(Playwright) 테스트: `ed2_test.py`(편집실), `th2_test.py`(썸네일), `style_test.py`(스타일 배우기), `refs_e2e/refs_ui_test.py`(학습용 영상). 실행 방법은 `TESTING_GUIDELINES.md` 1번에 있다.
  - 작업 목록: `backlog.json`(순위별 기능·범위·버린 것), `batch1_result.json`(1차 결과).
- 상세 모듈 구조와 의존 방향은 `ARCHITECTURE.md` 참고.

## 5. 실행 환경

- **로컬 개발 환경 준비**:
  1. `git clone https://github.com/wavely1213/futsal-studio && cd futsal-studio`
  2. `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
  3. `python3 app.py --browser`로 실행한다(전용 창 없이 브라우저로). 작업 폴더는 `config.json`의 `workspace`를 따른다. 저장소의 `config.json`은 배포 파일이고 새로 설치하는 PC가 그대로 받으므로 고치지 않는다. 테스트용 작업 폴더가 필요하면 저장소를 다른 폴더로 복사하고, 그 복사본의 `config.json`만 바꿔서 실행한다. 리드의 e2e 서버가 이 방식을 쓴다.
  - 사용자 PC(Windows)에서는 `시작하기 (Windows).bat`이 `.venv` 생성, `pip install -r requirements.txt`, `pythonw updater.py --launch`를 차례로 한다. 그다음부터는 바탕화면·시작 메뉴 아이콘으로 실행한다(역시 `updater.py --launch`를 거침).
- **필요한 환경변수**: 시크릿은 없다. 모두 선택 사항이다.
  - `FUTSAL_PORT`: 로컬 서버 포트 (기본 8765)
  - `FUTSAL_FFMPEG`: ffmpeg 실행 파일을 직접 지정 (기본은 imageio-ffmpeg 번들)
  - `FUTSAL_CLAUDE`: Claude Code 실행 파일을 직접 지정 (기본은 PATH·공식 설치 위치에서 찾음. 시험의 가짜 claude도 이것으로)
  - `FUTSAL_RESTART`, `FUTSAL_VIA_UPDATER`: 내부용. 재시작·실행기 경유를 표시하며 직접 설정하지 않는다.
  - `RELEASE_TRAILER`: `release.sh`가 커밋 메시지 끝에 붙일 줄
  - `TH_PORT`·`TH_OUTDIR`(`th2_test.py`), `ED_PORT`·`ED_DIR`·`ED_REPO`(`ed2_test.py`): 저장소 밖 e2e용 (`TESTING_GUIDELINES.md` 1번)
- **실행 명령어**: `AGENTS.md` 5번 항목과 동일하게 유지
- **배포 방법**: 수동 배포다. 소유자 승인 후 `./release.sh X.Y.Z "변경 내용 한 줄"`을 실행한다. 이 스크립트가 버전·파일별 sha256을 적은 **배포 커밋**과, zip 주소를 그 커밋에 고정하는 두 번째 커밋을 만들어 `main`에 푸시한다. 서버 호스팅은 없다.
  - 배포 절차·주의(깨끗한 클론, 태그 403 등): `DEVELOPMENT_RULES.md` 8번
  - 사용자 PC 쪽 업데이트 흐름(확인·되돌리기): `ARCHITECTURE.md` 6절 '업데이트 흐름'
  - 사람이 읽는 안내: `README.md`의 "(관리자용) 새 버전 배포 방법"

## 6. 외부 서비스·연동

| 서비스 | 용도 | 키 관리 위치 |
|---|---|---|
| YouTube (yt-dlp) | 채널·영상 목록, 영상 받기(편집용 보관함 · 학습용 영상 `refs.add_channel`·`add_direction`), 출처를 모르는 예전 영상의 정보만 조회(`source._lookup`, 보관함을 열 때 뒤에서 2초 간격·한 번에 40개까지, 실패한 영상은 하루 뒤에) | 키 없음. 쿠키는 사용자가 '크롬 로그인 정보로 받기'를 켤 때만 그 브라우저에서 읽음 (`cookiesfrombrowser`) |
| PyPI (pip) | yt-dlp를 3일마다 최신으로, YouTube가 막으면 그 자리에서 한 번 더. `requirements.txt`가 바뀐 업데이트 때 설치 | 없음 |
| GitHub (raw·archive) | 업데이트 안내 `manifest.json`과 배포 커밋 zip | 없음 (공개 저장소). 배포 푸시는 개발 PC의 git 자격 증명 |
| GitHub denoland/deno · dl.deno.land | Deno(yt-dlp-ejs용 JS 실행기)를 Windows에 자동 설치 → `~/.futsal-studio/bin`. sha256 확인 | 없음 |
| GitHub danielgatis/rembg releases | 누끼 모델 (BiRefNet 약 220MB / u2net_human_seg 약 170MB) → `~/.futsal-studio/models` | 없음 |
| ModelScope RapidAI/RapidOCR (v3.9.2 태그) · Hugging Face monkt/paddleocr-onnx·zeropointnine/yamnet-onnx (고정 커밋) | 영상 기획 분석의 화면 글자 읽기(PP-OCRv5 글자 찾기·한국어 읽기·글자 목록)와 소리 종류(YAMNet), 약 35MB → `~/.futsal-studio/models`. 크기·sha256 확인, 실패하면 10분 쉬고 어림 규칙으로 계속 | 없음 |
| Anthropic (사용자 PC의 Claude Code CLI 경유) | 스타일 카드의 [클로드로 더 깊게 보기]를 누를 때만: 기획 판단·레퍼런스 대사 발췌(최대 약 6000자)·장면 그림 최대 8장을 사용자 본인 클로드 계정으로 보냄 (D-021). [설치하기]는 공식 설치 명령(`irm https://claude.ai/install.ps1 \| iex`)을 보이는 창에서 실행 | 키 없음. 로그인은 사용자가 Claude Code 창에서 직접. 선택한 로그인 코드는 `~/.futsal-studio/claude_token` |
| ONNX model zoo (github.com/onnx/models, 고정 커밋) | 얼굴(UltraFace RFB-320)·표정(FER+) 모델. 크기·sha256을 확인하고, 실패하면 조용히 예전 점수로 계속 | 없음 |
| Hugging Face Hub | faster-whisper가 받아쓰기 모델을 처음 한 번 받음 (라이브러리 기본 동작) | 없음 |
| jsDelivr CDN | `ui.html`의 Pretendard 웹폰트. 편집실·썸네일은 로컬 `fonts/`를 씀 | 없음 |
| i.ytimg.com | 소재 찾기 목록의 영상 미리보기 그림 | 없음 |
| YouTube Studio · claude.ai | 브라우저로 열기, 사용자가 프롬프트를 복사해 붙여 넣기만 함 (API 호출 없음) | 없음 |

## 7. 참고 링크

- 기획 문서: 저장소 안에는 없다. 기능 우선순위·범위는 저장소 밖 `$SCRATCH/backlog.json`(리드의 작업 목록)에 있다. 사용자 안내와 관리자 배포 방법은 `README.md`에 있다.
- 디자인: 별도 디자인 파일 없음. 화면 HTML 자체가 기준이다. 편집실은 Premiere Pro, 썸네일은 Photoshop의 화면 구성과 단축키를 따른다.
- 운영 대시보드: 없음. 저장소는 https://github.com/wavely1213/futsal-studio 이다. 사용자 PC에서 문제가 생기면 작업 폴더의 `studio.log`를 받아서 본다.
