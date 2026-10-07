# TESTING_GUIDELINES.md — 테스트 지침

> **목적**: 무엇을, 어떻게, 얼마나 테스트할지 정의한다. 1인 개발에서 테스트는
> "미래의 나 + AI가 마음 놓고 코드를 바꾸게 해주는 안전망"이다.
> **갱신 시점**: 테스트 도구·전략이 바뀔 때.

---

## 1. 테스트 도구와 실행

- 테스트 프레임워크: 표준 `unittest` (+ `unittest.mock`). pytest 는 쓰지 않는다. 화면 e2e 는 Playwright(Python, Chromium).
- 전체 실행 (저장소 폴더에서): `python3 -m unittest discover -s tests` — 약 570개, 개발 환경에서 5분 남짓. 실제 ffmpeg(imageio-ffmpeg)로 짧은 시험 영상을 만들어 돌린다.
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
| `test_strategy.py` | 채널 전략 `strategy.py`: 저장(바꿔 끼우기·깨진 state .bad·잠기면 안 덮어씀·기록의 깨진 줄·해시 파일 이름) · RSS 파서(우리·쪼살·쌈바 원본 `fixtures/strategy/rss_*.xml`, DOCTYPE·깨진·빈·너무 큰 RSS) · yt-dlp 목록 정리(기본·한국어 목록은 조회수 없음·탭 묶음·잘못된 줄) · 새로 고침(가짜 목록·RSS: 채널마다 저장·기록, 예절 대기, 3일 건너뛰기·모두 다시, 막히면 6시간 쉼+RSS 계속, 3곳 연달아 실패, 멈추기, 전체 훑기 400개·30일·한국어 원제, 쇼츠 탭 없음 = 0, RSS 404·429 한 번 더·User-Agent, 다시 시작한 날·직접 고친 날 유지, 설명 원문·연락처 안 남김) · 통계·주기·활동 상태 · 한국어 제목·제목 공식(각 3개 이상)·주제어(풀이말 빼기)·시리즈·고정 해시태그 · 비교 데이터만으로 화면 묶음·가져올 점(8분류·점수 0~100·같은 id)·할 일(멱등·쓰임·5/7단계)·숨기기 · 채널 넣기(YouTube 주소·@핸들만, 중복·우리 채널 거절, 추천 채널 이름) · 방향 초안·전략 검사·저장 · 30/60/90 · 점검 피드백(계획 70%·2배 영상·예측 띠·반올림)·지난 예측 맞춤·알림 7일·스튜디오 숫자 · Claude 붙여 넣기 형식·프롬프트(6000자·지어내지 말 것)·가짜 claude · 가능성 캐시 해시·기본 증가 g0 · `/api/strategy/*`(읽기·잘못된 주소 400·409·붙여 넣기 거절·Host·Origin) · `core.channel_listing`·`list_videos` 주소 규칙 · `strategy_seed.json` 형식 |
| `test_strategy_forecast.py` | 가능성 모델 `forecast.py`: 가짜 비교 채널 40곳에서 a·b·τ·σ·cL·cS 되찾기·뒤로 빼고 시험한 적중률 70~90% · 단조성(공통 난수, b·e=0 이면 정확히) · 많이 올리면 한 편 보통 조회는 늘지 않음(e≤0) · 올리지 않으면 그대로·기본 증가 · 범위·5 단위·극단 문구·이미 달성 · 같은 입력 같은 출력 · 민감도 부호·순서 · 새 채널·비교 채널 없음/1곳·이상한 값 · 기록 거꾸로 시험 퍼짐 배수 [1,2] · numpy 없을 때 한국어 안내 · Brier (정확한 숫자는 비교하지 않음) · 검토 보강(D-026): 반올림한 두 끝으로 민감도 글 · 우리 영상 근거가 적으면 믿을 만함 낮음과 까닭·한 편 빼 보기 · 근거가 많으면 높음/보통 가능 · 새 채널·비교 자료 없음은 모두 낮음 · 지금 속도·방향 비교(같은 운 · 개수가 같으면 같은 %) · 전환 흔들림·탄력성 기준점 보정 · 쇼츠 전환 범위 끝 표시 |
| `test_strategy_review.py` | 채널 전략 검토 보강(D-026): 새로 고침(막혀 RSS 만 받은 채널은 쉬는 시간 뒤 다시 받을 차례·기록 줄에 그날 구독자만·처음 막혀도 비교 데이터 숫자 유지·인터넷 끊김은 6시간 쉬지 않음·없는 채널은 연달아 세지 않음·429 는 막힘·채널 하나의 저장 실패는 그 채널만·쉬는 시간 기록이 잠겨도 계속·원제 목록에서 막히면 바로 쉼·'이 채널만'은 10분·[지금 다시 시도]·끊긴 RSS 응답·오류 종류) · 점검(계획대로 한 주는 '안 되는 것' 없음·반올림 단위·띠를 날짜로 이어서·바로 다시 점검은 바꿈·짧은 기간은 판단 안 함·처음 저장한 날이 시작·출발 기록 없으면 구독자 변화 없음·조회수는 영상 나이로 P25/P50·스튜디오 숫자는 기간이 맞을 때만·제목 줄이기·지난 예측 맞춤) · 가져올 점(권위형·대상 지정 정규식·주제어는 두 채널 이상 또는 풋살 용어·TOPIC_STOP·비슷한 때 올린 영상 대비 배수·N탄 이름 괄호·조사 메모 조각·점수 상한과 퍼짐·계획에 이미 있어요) · 성공 솔루션(먼저 할 것에 % 없음·쇼츠 조언 어긋남 없음·시나리오만 %) · 가능성 연결(입력이 시계에 따라 바뀌지 않음·동시 요청은 한 번만 계산·GET 이 state.json 을 쓰지 않음·줄인 채널 자료·미리 보기·지금 속도) · `/api/strategy/preview`·`/api/strategy/pause` |
| `test_caption_oneline.py` | 한 줄 자막(D-023): 한도·말끝에서 끊기·용어 안 나눔·자투리 합치기 · 단어 시각 없을 때 글자 수대로(조용한 곳에 맞춤) · `editor.oneline_captions`(고친 글 그대로·다른 값 유지·두 번 눌러도 같음) · 내보내기 ASS 한 줄·노래방 `\kf`·글자 줄이기 · 문장부호 없는 말의 끊는 곳(소유자 문장·코칭 문단: 기대는 말·꾸미는 말·붙여 읽는 풀이말·두 문장) · 길이 없는/거꾸로 된 자막·0.5초 자투리·긴 낱말·주소 · 버튼이 짧은 자막(단어 시각·줄바꿈) 그대로 · 쇼츠 나눌 곳 고를 때 = 보여 줄 때 · 미리보기 JS(`capParts`·`capFit`·노래방 `effect`)를 node 로 돌려 `editor.py` 와 비교(node 없으면 건너뜀) · `/api/edit/oneline`·`/api/edit/open`의 `capLong` |
| `test_captions.py` (2차 작업 중, 커밋 전) | `captions` 자막 나누기·용어 사전·낱말 경계 고치기 · `core.analyze`(단어 시각·힌트·모델 한 번만·절전 막기, 가짜 faster_whisper) · 노래방 `\kf` · 단어 추임새 컷 · `/api/dict` |

- **e2e 묶음은 저장소 밖** 관리자 작업 공간 `$SCRATCH`(경로는 `AGENTS.md` 5번)에 있다. 각 묶음은 저장소를 복사한 시험 앱(`config.json` 제외)과 자기 작업 폴더를 쓴다. 시험 앱의 `config.json` 은 `update_manifest_url` 이 닿지 않는 주소(`http://127.0.0.1:1/…`)라 실제 업데이트를 시도하지 않는다.
- e2e 에는 Playwright(Python·Chromium) 외에 시스템 `ffmpeg`·`ffprobe`(편집실 결과 확인)와 numpy 가 필요하다. `style_test.py` 는 Chromium 실행 파일 경로가 코드에 박혀 있다.

| 묶음 | 대상 | 시험 앱 띄우기 → 실행 |
|---|---|---|
| `ed2_test.py` | 편집실 (`editor.html`·`editor.py`·내보내기, 193개 확인) | `python3 $SCRATCH/ed2_test.py [--no-export] [--backend-only]` — 스스로 `$SCRATCH/edtest2/srv.sh` 를 불러 저장소를 `edtest2/app` 으로 복사(`config.json`·`tests` 제외)하고 8766 포트에 띄운다. 다른 포트로 따로 돌리려면 `bash $SCRATCH/ed2_clone.sh <이름> <포트>` 로 폴더를 만든 뒤 `ED_DIR=<그 폴더> ED_PORT=<포트> python3 $SCRATCH/ed2_test.py` (`ED_REPO` 로 저장소 위치 지정) |
| `th2_test.py` | 썸네일 편집기 (`thumb.html`·`thumb.py`) | `bash $SCRATCH/th_run.sh` (저장소를 `thtest/app` 에 복사하고 8765 포트에 띄움, 작업 폴더 `thtest/work`) → `python3 $SCRATCH/th2_test.py <스크린샷 폴더> [--cut]` (`--cut` 은 누끼까지). 다른 시험 앱이면 `TH_PORT=<포트> TH_OUTDIR=<그 앱의 out 폴더>` |
| `style_test.py` | 스타일 배우기 화면 (`ui.html`·`style.py`·`plan.py`·`claude_cli.py`) | `th_run.sh` 로 띄운 같은 시험 앱(8765, 보관함에 `STYLETEST01·02` 영상이 있음) → `python3 $SCRATCH/style_test.py <스크린샷 폴더>`. 기획 분석·클로드 카드까지: `bash $SCRATCH/plan_run.sh`(8911, `plantest/app`, 기획 정답 영상·예전 스타일·가짜 claude) → `STYLE_PORT=8911 PLAN_DIR=$SCRATCH/plantest python3 $SCRATCH/style_test.py <폴더>` |

| `cape2e/cap_e2e.py` | 자막 한 줄씩 나누기 (`editor.html` 자막 탭 버튼·안내, 미리보기 한 줄·화면 안, 큰 글씨 쇼츠, 미리보기 = 내보내기(`timeline_captions`·ASS·SRT), Ctrl+Z·다시 실행, 검색, 두 번 누름, 다시 열면 안내 없음 · 29개 확인 · 전/후 스크린샷 5장) | `bash $SCRATCH/cape2e/setup.sh` (a4226ec 앱과 저장소 작업 트리(`CAP_REPO`)를 `cape2e/old`·`new` 로 복사, 예전 받아쓰기 가로 영상) → `python3 $SCRATCH/cape2e/cap_e2e.py` (예전 앱 8951 로 프로젝트를 만들고 새 앱 8952 로 검사 · 스크린샷 `cape2e/shots`) |
| `strat_e2e/strat_ui_test.py` | 채널 전략 화면 (`ui.html` 8단계 · `strategy.py`·`forecast.py`): 탭 6개·요약 카드·알림 점·배너, 우리 줄 고정·정렬·펼치기·분류 칩·분류 카드, 채널 추가(잘못된 주소 안내)·빼기, 새로 고침/모두 다시 요청, 방향 초안(확인)·주당 개수·저장 → 가능성 다시 계산·다시 시작한 날, 가져올 점 → 작업으로 만들기 → 할 일·5·7단계 상자·숨기기·직접 넣기, 30/60/90·먼저 할 것·Claude 붙여 넣기, 가능성(정직 안내·이미 달성·범위 막대·소수점 없음·민감도·그림 툴팁·실제 기록 점·계산 방법), 점검(세 칸·계획 vs 실제·예측 띠·반올림 안내·기록 2개·추이 그림·알림 끄기), 고른 탭 기억, 빈 작업 폴더 + 새 채널 + 비교 데이터 없음(자동 기록 요청·빈 상태·기본값), 좁은 창 800px·다크 모드(가로 넘침 없음) · 검토 보강(처음 여는 탭·이번 주 할 것·계획대로면/지금 속도면·방향 비교·미리 보기·가져올 점 위 8개+더 보기·조사 메모 따로·먼저 할 것에 % 없음·계획을 바꾸면·믿을 만함 까닭·바로 다시 점검은 확인 후 바꿈·스튜디오 기간·휴대폰 390px 탭 줄바꿈·그래프 글자·표 펼침·연달아 실패 안내와 [지금 다시 시도]·점 뜻) · 124개 확인 · 스크린샷 | `bash $SCRATCH/strat_e2e/run_all.sh` (저장소(`STRAT_REPO`, 기본 `$SCRATCH/feat_strategy`)를 `strat_e2e/full`·`empty`·`narrow` 로 복사해 8981·8982·8983 에 띄움 · 8981·8983 은 실제로 새로 고친 자료 `strat_e2e/live/work` + 기록 4주 + 8일 전 점검) → `python3 $SCRATCH/strat_e2e/strat_ui_test.py <스크린샷 폴더>` (새로 고침 요청은 가로채서 인터넷을 쓰지 않음). 실제 데이터 확인은 `bash $SCRATCH/strat_e2e/run.sh live 8984 <경쟁 채널을 정한 작업 폴더>` → `python3 $SCRATCH/strat_e2e/live_ui.py <폴더>` (인터넷 · 채널 12곳 약 4분) · 비교 데이터 다시 만들기는 `python3 tests/make_strategy_seed.py --work <새로 고친 작업 폴더> [--research <조사 원자료>]` 뒤 `tests.test_strategy` |
| `refs_e2e/refs_ui_test.py` | 학습용 영상 화면 (`ui.html` 보관함 옮기기 제안·확인 흐름, 스타일 배우기의 학습용 영상: 채널 칸·고르기·배우기·배운 파일 지우기·다시 배우기·지우기, 채널 추가·추천 채널·방향 요청, 보관함 영상도 보기, 편집 목록 분리, [취소]는 아무것도 안 옮김, 편집실 프로젝트에서 쓰는 영상 안내, 막힘 → 이 화면 크롬 설정, 원본 지우기 재확인·보관함으로 되돌리기 · 53개 확인) | `bash $SCRATCH/refs_e2e/run.sh` (저장소 클론을 `refs_e2e/app` 으로 복사해 8931 에 띄우고 보관함에 다른 채널 4개·우리 영상·촬영본을 넣음 · `REFS_REPO`·`REFS_PORT` 로 바꿈) → `python3 $SCRATCH/refs_e2e/refs_ui_test.py <스크린샷 폴더>` (받기 요청은 가로채서 인터넷을 쓰지 않음) |

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
