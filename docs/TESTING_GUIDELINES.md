# TESTING_GUIDELINES.md — 테스트 지침

> **목적**: 무엇을, 어떻게, 얼마나 테스트할지 정의한다. 1인 개발에서 테스트는
> "미래의 나 + AI가 마음 놓고 코드를 바꾸게 해주는 안전망"이다.
> **갱신 시점**: 테스트 도구·전략이 바뀔 때.

---

## 1. 테스트 도구와 실행

- 테스트 프레임워크: 표준 `unittest` (+ `unittest.mock`). pytest 는 쓰지 않는다. 화면 e2e 는 Playwright(Python, Chromium).
- 전체 실행 (저장소 폴더에서): `python3 -m unittest discover -s tests` — 약 450개, 개발 환경에서 4분 남짓. 실제 ffmpeg(imageio-ffmpeg)로 짧은 시험 영상을 만들어 돌린다.
- 단일 파일 실행: `python3 -m unittest tests.test_update` / 하나만: `python3 -m unittest tests.test_update.InstallTests.test_hash_mismatch_leaves_app_unchanged`
- 테스트 파일 위치·네이밍: `tests/test_<영역>.py` (평평한 구조), 고정 자료는 `tests/fixtures/`. `tests/` 는 배포 목록에서 빠진다.

| 파일 | 대상 |
|---|---|
| `test_update.py` | `updater.py`(설치·되돌리기·실행기·selftest) · `release.sh` 지문 · `core` 다운로드 엔진/Deno |
| `test_bundle.py` | `bundle.py` 촬영본 묶기 + `/api/bundle` |
| `test_source.py` | `source.py` 영상 출처: 채널 주소 꼴별 판단 · 받을 때 기록(가짜 yt-dlp) · 예전 영상 찾기(채널 목록 기억·가짜 조회·멈추기) · 직접 고르기 · 채널별 묶기·개수·이름 바뀜·색 · 깨진 기록 파일 · 잠깐 못 읽은 기록 덮어쓰지 않기 · 제목 짐작(조회 뒤) · `/c/`·`/user/` 주소 채널 id 로 바꾸기 · 막히면 쉬기 · 이상한 기록 한 줄 · `/api/source`·`/api/state`·`/api/list` |
| `test_upload.py` | `upload.py`·`hooks.py` 올리기 키트 (챕터·글자 수 규칙·제목 틀) + `/api/upload/*` |
| `test_faces.py` | `face.py` 얼굴·표정 점수 · `thumb.frame_candidates` 장면 고르기 |
| `test_takes.py` (2차 작업 중, 커밋 전) | `takes.find_junk` NG 테이크·슬레이트·말더듬 + `editor.recommend` 회귀 (`fixtures/takes_before.json`) |
| `test_style_content.py` | 영상 기획 분석 `plan.py`: 자막 6종 분류·색 이름·인트로 유형 5가지·장르·재미 정도·여러 영상 합치기·화자 군집 · 정답 영상(`make_fixture.make_plan_fixture`)으로 티저·타이틀·정지·슬로 리플레이·자막 종류(OCR 모델이 있을 때) · 모델 없을 때 어림 표시·캐시·멈추기 · 가편집 인트로 티저·강조 자막(꺼지면 예전과 같음) · `avmodels` 실패 표시 · `claude_cli`(가짜 claude: 성공·로그인·한도·시간 초과·멈추기·예전 판 옵션·환경 변수·임시 폴더) · `/api/claude/*`·`/api/style/plan_*` · 검토 회귀(`TestReview*`): 긴 장면·처음/끝 같은 자리는 티저 아님, 대사를 따라 크게 띄운 강조 자막, 레슨 대사의 흔한 낱말, 강조 자막 낱말 조각 금지, 인사 뒤 진행 질문, 갈린 판단 요약·가편집 끔, 대결 형식·잔디 구장, 티저 장면 고르기, Claude 프롬프트 본편 표본, `--help` 실패 시 안전 옵션 유지·예전 판 안내·표준 오류 폭주, 소리 없는 영상 기록 재사용·깨진 모델·내려받기 멈춤, 일치 점수에서 기획 값 제외, 작업 중 붙여 넣기 거절 |
| `test_refs_library.py` | 학습용 영상 `refs.py`: 기록 파일(바꿔 끼우기·깨진 파일·잘못된 줄·잠김이면 안 덮어씀) · 보관함·편집실·출처 목록과 섞이지 않음 · 이름 → 파일·분석 폴더(`core.video_file`·`adir`, 보관함이 먼저, 기록이 깨져도 보관함은 그대로) · Windows 폴더 이름 · 보관함과 같은 채널 색 · 기록 없는 파일 다시 기록 · 학습용 영상으로 배우기(진짜 ffmpeg · 분석은 학습용 폴더에) · 배운 뒤 파일 지우기 → 남은 기록으로 다시 배움 · 바뀐 파일은 안 지움 · 채널 스타일 다시 배우기(예전 영상 포함) · 지우기(영상·채널) · 보관함 → 학습용 옮기기(한글·띄어쓰기·자모 이름, 분석 폴더, 우리 채널은 확인 뒤, 잠긴 파일 다시 시도·포기하면 되돌림, 기록 실패 복구) · 검토 고침(옛 `analysis/<stem>`이 있어도 학습용 기록이 먼저·옛 받아쓰기 따라옴, 이미 있는 영상은 받은 것으로 안 셈, 편집실 프로젝트에서 쓰는 영상은 안 옮김, 확인하고 옮긴 원본은 배운 파일 지우기·자동 지우기 제외·지우기는 한 번 더 확인, 기록이 깨져도 원본 표시 유지, 옮기면 `archive.txt`에서 빼고 되돌리면 넣음·출처 유지, 받는 폴더 잠김 → `locked` 알림·다음 목록에서 옮김, 오래 기다리기, 기록 판이 바뀌면 지운 영상은 못 씀, 채널 지우기는 기록 없는 파일을 안 지움, 기록 없는 폴더는 그 이름의 채널, 추천 방향의 채널 id) · 추천 채널 JSON · 받기(가짜 yt-dlp: 받는 폴더·archive·sources.json 분리, 보관함에 있는 영상 건너뜀, 다음 인기 영상, 방향, 막힘 → 엔진 최신화·쉬운 안내, 쿠키는 고를 때만, 보관함 받기는 그대로) · `/api/refs/*`(옮기기 전 편집 중 안내·원본 지우기 재확인·빈 선택 거절·되돌리기) |
| `test_msg_sfx.py` | `sfxlib.py` 효과음·배경음악: 실은 파일이 모두 `sfx/LICENSE.txt`에 CC0·주소·sha256·원래 파일과 함께 · 만드는 소리가 늘 같은 바이트·길이·최대 -3 dBFS·직류 없음 · 미디어 폴더 준비(두 번째는 안 만듦) · 배경음악 길이·평균 크기·이음새 · 음악으로 들림(YAMNet 있을 때) |
| `test_msg_render.py` | MSG 렌더 재료(`editor.py`↔`editor.html`): 쾅 찍기·흔들기 ASS 명령과 내보낸 프레임 크기·기울기 · 기울기 부호 · 글꼴(검은고딕·도현) 크기 비율과 미리보기 폭 · noCaps · 컷으로 나뉜 같은 자막은 한 덩어리 · 순간만 큰 소리가 섞여도 목표 LUFS |
| `test_msg_moments.py` | `msg.signals`·`moments`: 정답 시험 원본(`make_msg_fixture`)에서 순간 80% 넘게 · NG 안에는 없음 · 공 소리만(말·웃음 아님) · 말 없는 시범 살리기 · 캐시 · 받아쓰기 없을 때 안내 · 문장 나누기 · 강조 글자(통째 옮기지 않기·잘린 조각) · 낱말 사전 |
| `test_msg_styles.py` | 기본 스타일 4개 · 배운 값 범위로 묶기 · 섞은 스타일 저장(`styles/섞기`)·좋아요 고르기·최근 바꾼 것·지운 스타일은 기본값 · 배운 스타일 목록과 안 섞임 · 잘못된 이름 · `/api/style/presets`·`mix`·`mix_pick`·`/api/edit/msg` |
| `test_msg_compile.py` | `msg.build_variants`(기본 스타일 3개 × 양 3단계 × 롱폼·쇼츠): V1·트랙 겹침 없음 · 전환 유효 · 있는 미디어만 · 사건 기록의 재료 id · 같은 입력 같은 결과 · 양이 많을수록 사건 많음 · 담백 제한 · 말 자막 자리 피하기 · 쇼츠(엔드 화면 없음) · 실제 내보내기 + 자동 검수 |
| `test_msg_round2.py` | MSG round2 판정 회귀: 엔드 화면(마무리 인사 위·원본 이어 붙이기·없으면 움직이는 장면, 정지 사진·빈 상자 없음) · 담백(티저·다시 보기 없음, 첫 장면 위 글자) · 다큐 제목 바로 · 낱말 시각(뭉개진 낱말·몰아 적은 묶음·추임새 뒤·잡음 덩어리) · 가짜 쉼 안 자름 · 뭉개진 낱말로 시작하는 컷 · 끊긴 말 · 시범 가장자리 목소리 · 짧은 조각 · 강조 구절 · 숫자 세기 · 시범 예고 · 개그 글자 되풀이 없음 · 뿌듯 · 정지 화면 글자 · 다시 보기와 티저 · 놓친 시도 알림 · 챌린지 음악 · 스타일별 말 자막 · 얼굴 피하기 |
| `test_msg_integration.py` | 썸네일 장면 후보의 MSG 추천 장면 · 올리기 키트 제목 후보의 MSG 훅 · MSG 마커 이름 챕터 |
| `test_captions.py` (2차 작업 중, 커밋 전) | `captions` 자막 나누기·용어 사전·낱말 경계 고치기 · `core.analyze`(단어 시각·힌트·모델 한 번만·절전 막기, 가짜 faster_whisper) · 노래방 `\kf` · 단어 추임새 컷 · `/api/dict` |

- **e2e 묶음은 저장소 밖** 관리자 작업 공간 `$SCRATCH`(경로는 `AGENTS.md` 5번)에 있다. 각 묶음은 저장소를 복사한 시험 앱(`config.json` 제외)과 자기 작업 폴더를 쓴다. 시험 앱의 `config.json` 은 `update_manifest_url` 이 닿지 않는 주소(`http://127.0.0.1:1/…`)라 실제 업데이트를 시도하지 않는다.
- e2e 에는 Playwright(Python·Chromium) 외에 시스템 `ffmpeg`·`ffprobe`(편집실 결과 확인)와 numpy 가 필요하다. `style_test.py` 는 Chromium 실행 파일 경로가 코드에 박혀 있다.

| 묶음 | 대상 | 시험 앱 띄우기 → 실행 |
|---|---|---|
| `ed2_test.py` | 편집실 (`editor.html`·`editor.py`·내보내기, 193개 확인) | `python3 $SCRATCH/ed2_test.py [--no-export] [--backend-only]` — 스스로 `$SCRATCH/edtest2/srv.sh` 를 불러 저장소를 `edtest2/app` 으로 복사(`config.json`·`tests` 제외)하고 8766 포트에 띄운다. 다른 포트로 따로 돌리려면 `bash $SCRATCH/ed2_clone.sh <이름> <포트>` 로 폴더를 만든 뒤 `ED_DIR=<그 폴더> ED_PORT=<포트> python3 $SCRATCH/ed2_test.py` (`ED_REPO` 로 저장소 위치 지정) |
| `th2_test.py` | 썸네일 편집기 (`thumb.html`·`thumb.py`) | `bash $SCRATCH/th_run.sh` (저장소를 `thtest/app` 에 복사하고 8765 포트에 띄움, 작업 폴더 `thtest/work`) → `python3 $SCRATCH/th2_test.py <스크린샷 폴더> [--cut]` (`--cut` 은 누끼까지). 다른 시험 앱이면 `TH_PORT=<포트> TH_OUTDIR=<그 앱의 out 폴더>` |
| `style_test.py` | 스타일 배우기 화면 (`ui.html`·`style.py`·`plan.py`·`claude_cli.py`) | `th_run.sh` 로 띄운 같은 시험 앱(8765, 보관함에 `STYLETEST01·02` 영상이 있음) → `python3 $SCRATCH/style_test.py <스크린샷 폴더>`. 기획 분석·클로드 카드까지: `bash $SCRATCH/plan_run.sh`(8911, `plantest/app`, 기획 정답 영상·예전 스타일·가짜 claude) → `STYLE_PORT=8911 PLAN_DIR=$SCRATCH/plantest python3 $SCRATCH/style_test.py <폴더>` |

| `refs_e2e/refs_ui_test.py` | 학습용 영상 화면 (`ui.html` 보관함 옮기기 제안·확인 흐름, 스타일 배우기의 학습용 영상: 채널 칸·고르기·배우기·배운 파일 지우기·다시 배우기·지우기, 채널 추가·추천 채널·방향 요청, 보관함 영상도 보기, 편집 목록 분리, [취소]는 아무것도 안 옮김, 편집실 프로젝트에서 쓰는 영상 안내, 막힘 → 이 화면 크롬 설정, 원본 지우기 재확인·보관함으로 되돌리기 · 53개 확인) | `bash $SCRATCH/refs_e2e/run.sh` (저장소 클론을 `refs_e2e/app` 으로 복사해 8931 에 띄우고 보관함에 다른 채널 4개·우리 영상·촬영본을 넣음 · `REFS_REPO`·`REFS_PORT` 로 바꿈) → `python3 $SCRATCH/refs_e2e/refs_ui_test.py <스크린샷 폴더>` (받기 요청은 가로채서 인터넷을 쓰지 않음) |

| `msg_fix/ui/msg_ui_test.py` | MSG 화면 (`editor.html` '재미있게 자동 편집'·`ui.html` 기본 스타일·스타일 섞기 · 26개 확인) | `bash $SCRATCH/msg_fix/ui/serve.sh 8942 fresh` (저장소 작업 트리를 `msg_fix/ui/app` 에 복사 · 작업 폴더에 MSG 시험 원본 3개) → `python3 $SCRATCH/msg_fix/ui/msg_ui_test.py 8942` |

- MSG 품질 평가·샘플(저장소 밖, 개발용): 시험 원본 만들기 `bash $SCRATCH/msg_fix/rebuild.sh`(TTS·NASA PD·CC0 소리 → `msg_fix/fixwork` + large-v3-turbo 받아쓰기) → `python3 $SCRATCH/msg_eval/run.py <라운드> [--judge]` → `$SCRATCH/msg_out/<라운드>/` 영상·시트·`report.html`(검수·LUFS·사건/분·무음/분 · `--judge` 는 사용자 클로드 계정으로 원본↔편집본 비교 판정, 라운드당 25번 안).
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
