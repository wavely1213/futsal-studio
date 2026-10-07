# TESTING_GUIDELINES.md — 테스트 지침

> **목적**: 무엇을, 어떻게, 얼마나 테스트할지 정의한다. 1인 개발에서 테스트는
> "미래의 나 + AI가 마음 놓고 코드를 바꾸게 해주는 안전망"이다.
> **갱신 시점**: 테스트 도구·전략이 바뀔 때.

---

## 1. 테스트 도구와 실행

- 테스트 프레임워크: 표준 `unittest` (+ `unittest.mock`). pytest 는 쓰지 않는다. 화면 e2e 는 Playwright(Python, Chromium).
- 전체 실행 (저장소 폴더에서): `python3 -m unittest discover -s tests` — 약 140개, 개발 환경에서 1분 남짓. 실제 ffmpeg(imageio-ffmpeg)로 짧은 시험 영상을 만들어 돌린다.
- 단일 파일 실행: `python3 -m unittest tests.test_update` / 하나만: `python3 -m unittest tests.test_update.InstallTests.test_hash_mismatch_leaves_app_unchanged`
- 테스트 파일 위치·네이밍: `tests/test_<영역>.py` (평평한 구조), 고정 자료는 `tests/fixtures/`. `tests/` 는 배포 목록에서 빠진다.

| 파일 | 대상 |
|---|---|
| `test_update.py` | `updater.py`(설치·되돌리기·실행기·selftest) · `release.sh` 지문 · `core` 다운로드 엔진/Deno |
| `test_bundle.py` | `bundle.py` 촬영본 묶기 + `/api/bundle` |
| `test_upload.py` | `upload.py`·`hooks.py` 올리기 키트 (챕터·글자 수 규칙·제목 틀) + `/api/upload/*` |
| `test_faces.py` | `face.py` 얼굴·표정 점수 · `thumb.frame_candidates` 장면 고르기 |
| `test_takes.py` (2차 작업 중, 커밋 전) | `takes.find_junk` NG 테이크·슬레이트·말더듬 + `editor.recommend` 회귀 (`fixtures/takes_before.json`) |
| `test_captions.py` (2차 작업 중, 커밋 전) | `captions` 자막 나누기·용어 사전·낱말 경계 고치기 · `core.analyze`(단어 시각·힌트·모델 한 번만·절전 막기, 가짜 faster_whisper) · 노래방 `\kf` · 단어 추임새 컷 · `/api/dict` |

- **e2e 묶음은 저장소 밖** 관리자 작업 공간 `$SCRATCH`(경로는 `AGENTS.md` 5번)에 있다. 각 묶음은 저장소를 복사한 시험 앱(`config.json` 제외)과 자기 작업 폴더를 쓴다. 시험 앱의 `config.json` 은 `update_manifest_url` 이 닿지 않는 주소(`http://127.0.0.1:1/…`)라 실제 업데이트를 시도하지 않는다.
- e2e 에는 Playwright(Python·Chromium) 외에 시스템 `ffmpeg`·`ffprobe`(편집실 결과 확인)와 numpy 가 필요하다. `style_test.py` 는 Chromium 실행 파일 경로가 코드에 박혀 있다.

| 묶음 | 대상 | 시험 앱 띄우기 → 실행 |
|---|---|---|
| `ed2_test.py` | 편집실 (`editor.html`·`editor.py`·내보내기, 193개 확인) | `python3 $SCRATCH/ed2_test.py [--no-export] [--backend-only]` — 스스로 `$SCRATCH/edtest2/srv.sh` 를 불러 저장소를 `edtest2/app` 으로 복사(`config.json`·`tests` 제외)하고 8766 포트에 띄운다. 다른 포트로 따로 돌리려면 `bash $SCRATCH/ed2_clone.sh <이름> <포트>` 로 폴더를 만든 뒤 `ED_DIR=<그 폴더> ED_PORT=<포트> python3 $SCRATCH/ed2_test.py` (`ED_REPO` 로 저장소 위치 지정) |
| `th2_test.py` | 썸네일 편집기 (`thumb.html`·`thumb.py`) | `bash $SCRATCH/th_run.sh` (저장소를 `thtest/app` 에 복사하고 8765 포트에 띄움, 작업 폴더 `thtest/work`) → `python3 $SCRATCH/th2_test.py <스크린샷 폴더> [--cut]` (`--cut` 은 누끼까지). 다른 시험 앱이면 `TH_PORT=<포트> TH_OUTDIR=<그 앱의 out 폴더>` |
| `style_test.py` | 스타일 배우기 화면 (`ui.html`·`style.py`) | `th_run.sh` 로 띄운 같은 시험 앱(8765 고정, 보관함에 `STYLETEST01·02` 영상이 있음) → `python3 $SCRATCH/style_test.py <스크린샷 폴더>` |

- `th_run.sh`·`style_test.py` 는 8765 를 쓰므로, 개발용으로 띄운 앱(기본 8765)과 동시에 돌리지 않는다.

## 2. 테스트 전략 (우선순위)

1. **핵심 로직 단위·통합 테스트** — 업데이트·되돌리기, 저장, 유튜브 규칙 계산, 묶기처럼 틀리면 사용자 데이터나 실행이 망가지는 것. 최우선. 진짜 ffmpeg 와 몇 초짜리 합성 영상으로 결과 파일까지 확인한다.
2. **API 통합 테스트** — 테스트 안에서 `ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)` 를 띄워 주요 경로의 정상 + 대표 실패(잘못된 이름·다른 작업 중 409 등)를 확인한다 (`test_upload` RouteTests, `test_bundle` TestBundleRoute).
3. **E2E** — 편집실·썸네일·스타일 배우기 화면은 Playwright 묶음으로. 그 화면이나 짝이 되는 `.py` 를 고쳤을 때 해당 묶음을 돌린다.

UI 스냅샷·사소한 getter 테스트처럼 유지비용 대비 가치가 낮은 테스트는 만들지 않는다.
현재 빈 곳: `editor.py`·`style.py`·`qa.py`·`core` 분석/러프컷은 저장소 안 단위 테스트가 없고 저장소 밖 e2e 에만 기대고 있다 (`KNOWN_ISSUES.md` I-020).

## 3. 무엇을 반드시 테스트하는가

- 사용자 데이터·실행이 걸린 로직: 업데이트 설치·되돌리기·파일 삭제(`config.json`·`.venv`·작업 폴더는 절대 안 건드림), 편집본 저장(임시 파일 → 교체, 백업, 판 번호 충돌), 편집점 다시 찾기 때 사용자 편집본 보존
- 도메인 규칙 (`DOMAIN_KNOWLEDGE.md`의 비즈니스 규칙 각각 — 쇼츠 9:16·3분, 챕터 규칙, 제목·설명·태그 글자 수, 썸네일 크기·용량)
- 경계값·빈 입력·중복 요청 등 엣지 케이스
- Windows 사정: 한글·띄어쓰기·대괄호(`[꿀팁]`)가 든 이름과 경로, 파일 잠금(`PermissionError` 를 흉내 내 재시도 확인), 이름 끝의 공백·점
- 네트워크 실패: 인터넷 없음, 받다 끊김, 지문 불일치, 응답 없는 서버
- 수정한 버그 — **버그 수정 시 재발 방지 테스트를 반드시 함께 작성한다** (예: `test_upload` ReviewRegressionTests)

## 4. 테스트 작성 규칙

- 패턴: Arrange(준비) → Act(실행) → Assert(검증). 한 테스트에 하나의 행위만 검증.
- 테스트 이름은 "조건 + 기대 결과"가 드러나게. 기존 관례는 영어 snake_case + 한국어 docstring: `test_hash_mismatch_leaves_app_unchanged`, `test_unchanged_requirements_zero_pip_calls`.
- 파일 맨 위 docstring 에 무엇을 테스트하는지와 실행 명령을 적는다.
- 테스트 간 독립성 유지 — 실행 순서·공유 상태에 의존하지 않는다. 시험 영상처럼 만들기 비싼 것만 `setUpClass` 로 공유한다.
- **진짜 작업 폴더·사용자 폴더를 건드리지 않는다.** 작업 폴더는 테스트마다 임시 폴더(한글·띄어쓰기 포함, 예: `"풋살 작업 폴더"`)를 만들고 `mock.patch.object` 로 `core.WORK`·`VIDEOS`·`ANALYSIS`·`OUT`(+ 필요하면 `editor.PROJECTS`, `thumb.MODELS`, `core.ENGINE_HOME`)을 바꾼다. 업데이트 테스트는 `core.py` 를 임시 폴더에 복사해 따로 불러온다(`load_core`).
- 외부 서비스는 모킹한다: 인터넷은 쓰지 않는다 (`urllib.request.urlopen` 을 막거나, 테스트 안의 `http.server`(8851~8855)로 manifest·zip 을 내려 줌), pip 는 `core.run` 을 가짜로. DB 는 없다 — 저장소는 위의 임시 작업 폴더다.
- 시험 영상은 ffmpeg lavfi(`testsrc`·`sine`·`aevalsrc`)로 짧고 작게 만든다 (몇 초, 320×240 수준).
- 선택 구성요소가 없으면 건너뛴다: `unittest.skipUnless(조건, "한국어 이유")` (얼굴 모델, node, git·bash 등).
- 테스트를 통과시키기 위해 프로덕션 코드에 테스트 전용 분기를 넣지 않는다. (`FUTSAL_PORT`·`FUTSAL_RESTART` 같은 환경 변수와 `updater --selftest` 는 실제 기능이다)

## 5. AI 작업 시 테스트 규칙

1. 기능 구현 완료 보고 전에 **관련 테스트를 실제로 실행**하고 결과를 보고한다 (단위 전체 + 해당 e2e 묶음, 몇 개 중 몇 개 통과).
2. 기존 테스트가 깨지면: 내 변경이 원인인지 먼저 확인한다. 명세가 바뀌어 테스트 수정이 필요한 경우에만 테스트를 고치고, 그 이유를 보고한다. **통과를 위해 assert를 약화시키는 것은 금지.**
3. 테스트가 없는 기존 코드를 수정할 때: 수정 부분에 대한 최소한의 테스트를 먼저 만들고(가능하면) 수정한다. 불가능하면 미검증 리스크를 보고한다.
4. 커버리지 수치를 목표로 무의미한 테스트를 양산하지 않는다. 기준: 커버리지는 재지 않는다 — 대신 3번 목록이 빠짐없이 테스트돼 있어야 한다.
5. **배포 전**: 단위 전체 + 영향받는 e2e 묶음이 통과해야 한다. `release.sh` 는 `tests.test_update` 만 자동으로 돌린다.
6. 개발 환경은 Linux 다. Windows 에서만 드러나는 동작은 테스트가 통과해도 "Windows 미검증"으로 보고한다 (대상은 `DEVELOPMENT_RULES.md` 5번).
