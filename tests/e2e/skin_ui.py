"""풋살사관학교 스킨 화면 e2e (BR-320~BR-325 · D-520~D-524 · 개발용 · 배포 안 됨 · unittest 가 줍지 않음) — 마지막 줄 'N/N 통과' 면 통과.

실행: SKIN_SRC=<시험 작업 폴더> [SKIN_REPO=<저장소>] [SKIN_PORT=9491] [SKIN_OUT=<출력 폴더>] python3 tests/e2e/skin_ui.py
- SKIN_SRC: videos/ 와 analysis/<영상 이름>/ 이 있는 작업 폴더 — MSG 시험 원본 MSGRAW01(퍼스트 터치 레슨) (tests/e2e/ep_pics_ui.py 와 같은 폴더 ·
  리드 개발 환경은 `$SCRATCH/topic_ev2/work` · 영상은 저장소에 넣지 않음)
- 저장소를 SKIN_OUT/app 으로 복사(config.json 은 시험용)해 띄우고, 작업 폴더 SKIN_OUT/work 는 매번 새로 · 스크린샷·비교 그림 SKIN_OUT/shots
확인: 스타일 화면(쇼츠 화면 배치 '풋살사관학교 스킨' · 스킨 그림 보임 · 한글 이름 PNG 로 '스킨 바꾸기' · 잘못된 그림은 쉬운 말 · 처음 스킨으로) ·
가편집 쇼츠 화면 구성·글자 값 = 서버 · 편집실 미리보기 차례(스킨 그림은 영상 묶음 위 · 글자·도형 아래 · 화면 크기) · 미리보기 = 내보낸 장면(스킨 띠·흰 상자) ·
미리보기 영상 칸이 찢어진 종이 홈까지 덮음 · 흰 상자 = 미리보기 글 폭 + 양옆 14 · 화면 좌우 얼굴 따라가기(rfk) 미리보기 = 서버 셈 · 다른 템플릿은 스킨을 지움 ·
템플릿 '풋살사관학교 스킨'은 서버 값 ·
시퀀스 탭 스킨 안내는 화면 구성(풀샷 자르기·박스·블러)에 맞는 칸만 말함 · 큰 제목 글을 고치면 글자 크기·흰 상자가 따라옴 ·
주제 줄 글을 길게 고쳐도 한 줄 · 흰 상자 아래 · 영상 창 위 (D-539) · 큰 제목을 33글자로 고치면 흰 상자 두 줄(56 · 서버 셈 · 안 겹침 · 미리보기 = 내보내기 · D-540) ·
예전(v2.26.0) 한 줄 28 큰 제목 편집본을 열면 두 줄 53 · 둘째 줄도 48 · 바로 저장 · 다시 열면 알림 없음 (D-540 검토) ·
MSG 쇼츠 하이텐션형(노래방) 스킨 쇼츠도 말 자막은 흰 글씨 (미리보기·내보내기 · D-541) · '새 쇼츠 편집본으로 추가'는 스킨 바탕이면 KFA 글꼴 찾기를 안 기다림 · 바꾼 스킨으로 템플릿 · JS 오류 0."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(os.environ.get("SKIN_REPO") or Path(__file__).resolve().parents[2])
SRC = Path(os.environ.get("SKIN_SRC", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/topic_ev2/work"))
OUT = Path(os.environ.get("SKIN_OUT", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/skin_e2e"))
PORT = int(os.environ.get("SKIN_PORT", "9491"))
CHROME = os.environ.get("SKIN_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
APP, WORK, SHOTS = OUT / "app", OUT / "work", OUT / "shots"
N1 = "20261008_MSGRAW01_퍼스트 터치 레슨.mp4"
res = []
sys.path.insert(0, str(REPO))


def check(name, ok, info=""):
    res.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  · {info}" if info else ""), flush=True)


def api(path, body=None, timeout=300):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def wait_job(limit=900):
    for _ in range(limit * 2):
        st = api("/api/state?since=999999")
        if not st.get("job"):
            return st
        time.sleep(0.5)
    return api("/api/state?since=999999")


# ---- 시험 앱 · 작업 폴더 (이 시험이 띄운 앱만 끔) ----
for pid in os.popen("pgrep -f 'app.py --browser'").read().split():
    try:
        if os.readlink(f"/proc/{pid}/cwd").startswith(str(APP)):
            os.kill(int(pid), 15)
    except OSError:
        pass
time.sleep(1)
shutil.rmtree(WORK, ignore_errors=True)
shutil.rmtree(APP, ignore_errors=True)
(WORK / "videos").mkdir(parents=True)
src = (SRC / "videos" / N1).resolve()
try:
    os.link(src, WORK / "videos" / N1)
except OSError:
    os.symlink(src, WORK / "videos" / N1)
shutil.copytree(SRC / "analysis" / N1[:-4], WORK / "analysis" / N1[:-4], ignore=shutil.ignore_patterns("frames"))
SHOTS.mkdir(parents=True, exist_ok=True)
subprocess.run(f"cd '{REPO}' && tar --exclude=./tests --exclude=./.git --exclude=__pycache__ --exclude=./config.json -cf - . | (mkdir -p '{APP}' && cd '{APP}' && tar -xf -)",
               shell=True, check=True)
(APP / "config.json").write_text(json.dumps({"channel_url": "https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g", "workspace": str(WORK),
                                             "update_manifest_url": "http://127.0.0.1:1/none"}), encoding="utf-8")
srv = subprocess.Popen(["python3", "app.py", "--browser"], cwd=APP, env=dict(os.environ, FUTSAL_PORT=str(PORT), BROWSER="/bin/true"),
                       stdout=open(OUT / "app.log", "w"), stderr=subprocess.STDOUT)
for _ in range(120):
    try:
        api("/api/state")
        break
    except Exception:
        time.sleep(0.5)

# 한글·띄어쓰기 이름의 바꿀 스킨 (Windows 의 '내 그림\\내 채널 스킨 새 그림.png' 같은 파일) · 잘못된 그림 (가운데가 안 비었음)
from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402
PICK = OUT / "내 그림" / "내 채널 스킨 새 그림.png"
PICK.parent.mkdir(parents=True, exist_ok=True)
g = np.zeros((1920, 1080, 4), np.uint8)
g[:, :, 3] = 255
g[:576] = (200, 30, 160, 255)     # 위 띠 (자홍 · 창 위 끝 0.3)
g[1344:] = (20, 190, 200, 255)    # 아래 띠 (청록 · 창 아래 끝 0.7)
g[576:1344] = (0, 0, 0, 0)
Image.fromarray(g, "RGBA").save(PICK)
BAD = OUT / "내 그림" / "가운데가 안 빈 그림.png"
Image.new("RGBA", (1080, 1920), (90, 90, 90, 255)).save(BAD)

try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME)
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("dialog", lambda d: d.accept())
        ev, W = pg.evaluate, pg.wait_for_timeout

        # ---- 1. 스타일 화면: 쇼츠 화면 배치 · 스킨 줄 ----
        pg.goto(f"http://127.0.0.1:{PORT}/")
        W(1500)
        pg.locator('[data-p="style"]').first.click()
        W(1000)
        ev("document.getElementById('autoSet').scrollIntoView()")
        opts = ev("[...document.querySelectorAll('#shortsLayout option')].map(o => [o.value, o.textContent])")
        st0 = ev("""(() => ({w: $('skinImg').naturalWidth, h: $('skinImg').naturalHeight, src: $('skinImg').getAttribute('src'), reset: $('skinReset').hidden,
            text: $('autoSet').innerText}))()""")
        check("스타일 화면: '풋살사관학교 스킨 (초록 칠판 · 내 채널 스킨)' 고르기 · 기본은 지금처럼 · 설명 줄",
              ["academy", "풋살사관학교 스킨 (초록 칠판 · 내 채널 스킨)"] in opts and ev("$('shortsLayout').value") == "" and "풋살사관학교 스킨: 내 채널 스킨 그림" in st0["text"],
              json.dumps(opts, ensure_ascii=False))
        check("스킨 줄: 실은 스킨이 보임(1080×1920) · '스킨 바꾸기' · '처음 스킨으로'는 숨김",
              st0["w"] == 1080 and st0["h"] == 1920 and "academy.png" in st0["src"] and st0["reset"] and "스킨 바꾸기" in st0["text"],
              json.dumps({k: v for k, v in st0.items() if k != "text"}))
        pg.select_option("#shortsLayout", "academy")
        W(800)
        toast = ev("$('toast').textContent")
        check("쇼츠 화면 배치 '풋살사관학교 스킨' 저장 · 안내", api("/api/style/shorts_layout")["layout"] == "academy" and "풋살사관학교 스킨" in toast, toast)
        pg.locator("#autoSet").screenshot(path=str(SHOTS / "s1_style_skin.png"))

        with pg.expect_file_chooser() as fc:
            pg.click("#skinPick")
        fc.value.set_files(str(BAD))
        W(2000)
        toast = ev("$('toast').textContent")
        check("가운데가 안 빈 그림 → 쉬운 말 안내 · 설정 그대로", "가운데가 투명하게 비어 있는 9:16 그림(PNG)을 골라 주세요" in toast and api("/api/style/list")["skin"]["file"] == "", toast)

        with pg.expect_file_chooser() as fc:
            pg.click("#skinPick")
        fc.value.set_files(str(PICK))
        pg.wait_for_function("!document.getElementById('skinReset').hidden", timeout=30000)
        W(800)
        picked = api("/api/style/list")["skin"]
        copied = WORK / "styles" / "설정" / "스킨" / picked["file"]
        shown = ev("$('skinImg').getAttribute('src')")
        pg.locator("#autoSet").screenshot(path=str(SHOTS / "s2_style_picked.png"))
        ok_copy = copied.is_file() and np.asarray(Image.open(copied))[700, 500, 3] == 0 and np.asarray(Image.open(copied))[100, 500, 3] == 255
        check("'스킨 바꾸기'(한글 이름 PNG) → 작업 폴더에 복사(투명 그대로) · 그 스킨이 보임 · '처음 스킨으로' 보임",
              picked["file"].startswith("skin_") and ok_copy and picked["file"] in shown, f"{picked} {shown}")
        pg.click("#skinReset")
        W(800)
        back = api("/api/style/list")["skin"]
        check("'처음 스킨으로' → 실은 스킨 · 복사한 파일은 남음", back["file"] == "" and ev("$('skinReset').hidden") and copied.is_file()
              and "academy.png" in ev("$('skinImg').getAttribute('src')"), str(back))

        # ---- 2. 편집실: 가편집 쇼츠 = 스킨 (처음 열 때 만든 가편집) ----
        import editor  # noqa: E402 — 저장소의 셈 (서버와 같은 값인지)
        import style  # noqa: E402
        style.STYLES = WORK / "styles"   # 이 시험 작업 폴더의 설정·바꾼 스킨
        info = editor.skin_info()
        pg.goto(f"http://127.0.0.1:{PORT}/editor?name={urllib.request.quote(N1)}")
        pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=300000)
        W(1500)
        sq = ev("""P.sequences.filter(q => q.format === 'shorts').map(q => ({id: q.id, lay: q.layout, cap: q.captionStyle, titles: q.titles, shapes: q.shapes}))""")
        good = bool(sq)
        for q in sq:
            top = q["titles"][0]
            box = [g_ for g_ in q["shapes"] if g_.get("plan") == "card"]
            good = good and q["lay"] == info["layout"] and q["cap"]["y"] == editor.skin_cap_y(info["bottom"]) and {k_: q["cap"].get(k_) for k_ in editor.SKIN_CAP} == editor.SKIN_CAP \
                and top["style"] == editor.skin_title_style(top["text"]) \
                and len(box) == 1 and box[0]["radius"] == 17 and not any(t.get("plan") in ("series", "longline") for t in q["titles"])
            for kt in [t for t in q["titles"] if t.get("plan") == "topic"]:
                good = good and kt["style"] == editor.skin_topic_style(kt["text"], info["top"], kt["style"]["size"]) and kt["style"]["size"] <= top["style"]["size"] \
                    and (editor.skin_hug_w(kt["text"], kt["style"]) <= box[0]["w"] * 1080 * editor.SKIN_TOPIC_BOX + 1e-6   # (흰 상자보다 넓지 않은 글 · D-539 ·
                         or kt["style"] == editor.skin_topic_style(kt["text"], info["top"], top["style"]["size"]))       #  맞는 글이 없으면 예전 셈)
        check("가편집 쇼츠: 화면 구성 = 서버 skin_layout · 큰 제목·주제 줄·흰 상자·말 자막(0.717 · 흰 Black 96 · 테두리 10 · 상자 없음) = 서버 값 · 은평 덧붙임 없음", good,
              json.dumps([{"lay": q["lay"], "cap": {k_: q["cap"].get(k_) for k_ in ("y", "weight", "size", "strokeW", "bgOn", "effect")}} for q in sq], ensure_ascii=False)[:400])
        q0 = sq[0]
        # 화면 좌우 얼굴 따라가기 (BR-326): 가편집이 얼굴을 보고 정한 자리 · 따라 움직이는 클립은 미리보기(fitRect t) = 서버 fit_rect(원본 시각)
        rq = ev("""(() => { const out = []; for (const q of P.sequences.filter(q => q.format === 'shorts')) { S = q;
            for (const it of q.items.filter(i => i.track === 'V1' && i.media === 'main')) { const t = it.start + 0.37 * (iEnd(it) - it.start);
              out.push({ rf: it.reframe, rfk: it.rfk || null, it: { id: it.id, track: it.track, media: it.media, start: it.start, in: it.in, out: it.out, speed: it.speed || 1, rev: !!it.rev, reframe: it.reframe, rfk: it.rfk || null, fit: it.fit },
                         t, mx: fitRect(it, { kind: 'video', w: 1920, h: 1080 }, t).mx }); } }
            switchSeq(P.sequences.find(q => q.format === 'shorts').id); return out; })()""")
        moved = [x for x in rq if x["rf"] != 0.5 or x["rfk"]]
        same = all(abs(x["mx"] - editor.fit_rect({k: v for k, v in x["it"].items() if v is not None}, {"kind": "video", "w": 1920, "h": 1080},
                                                 {"format": "shorts", "layout": info["layout"]}, 1080, 1920,
                                                 editor.i_mt(x["it"], x["t"]))["mx"]) < 1e-6 for x in rq)
        check("화면 좌우 얼굴 따라가기: 가편집 쇼츠 V1 클립 자리를 얼굴로 정함(가운데 0.5 아닌 클립 · 따라 움직이는 키프레임) · 미리보기 자리 = 서버 셈",
              moved and same, f"{len(moved)}/{len(rq)} 클립 · 따라감 {sum(1 for x in rq if x['rfk'])}")
        ev(f"switchSeq('{q0['id']}')")
        W(1000)
        ev("seek(1.0)")
        W(2500)
        ev("selX = null; if (window.selI) selI.clear(); renderTimeline(); frameDirty = true")
        W(1000)
        z = ev("""(() => { const s = $('skinL'), L = $('layers'), O = $('ov'), F = $('frame'), cs = e => +getComputedStyle(e).zIndex;
            const kids = [...F.children], r = s.getBoundingClientRect(), f = F.getBoundingClientRect();
            const vids = [...document.querySelectorAll('#layers .lay')].filter(l => l.style.display !== 'none').length;
            const txt = [...document.querySelectorAll('#ov .olay')].filter(l => l.style.display !== 'none' && l.querySelector('.txt, .shp')).length;
            return {shown: s.style.display, src: s.getAttribute('src'), nw: s.naturalWidth, zs: cs(s), zl: cs(L), zo: cs(O), iL: kids.indexOf(L), iS: kids.indexOf(s), iO: kids.indexOf(O),
                    w: Math.round(r.width), h: Math.round(r.height), fw: Math.round(f.width), fh: Math.round(f.height), pe: getComputedStyle(s).pointerEvents, vids, txt}; })()""")
        check("미리보기 차례: 스킨 그림은 영상 묶음(#layers) 위 · 글자·도형(#ov) 아래 · 화면 크기 그대로 · 누름은 통과",
              z["shown"] == "block" and "academy.png" in (z["src"] or "") and z["nw"] == 1080 and z["zl"] < z["zs"] < z["zo"] and z["iL"] < z["iS"] < z["iO"]
              and abs(z["w"] - z["fw"]) <= 1 and abs(z["h"] - z["fh"]) <= 1 and z["pe"] == "none" and z["vids"] >= 1 and z["txt"] >= 1, json.dumps(z))
        # 미리보기 영상 칸(V1 클립 .lfg)이 스킨의 투명한 영상 창을 덮음 (시험 크로뮴은 H.264 를 못 풀어 영상 화소 대신 칸 자리로 · 1920 높이로 환산)
        fgr = ev("""(() => { const F = $('frame').getBoundingClientRect(), k = 1920 / F.height;
            const l = [...document.querySelectorAll('#layers .lay')].find(x => x.style.display !== 'none'); if (!l) return null;
            const r = l.querySelector('.lfg').getBoundingClientRect(); return {top: (r.top - F.top) * k, bottom: (r.bottom - F.top) * k}; })()""")
        pv = SHOTS / "e1_preview_1s.png"
        pg.locator("#frame").screenshot(path=str(pv))
        pg.screenshot(path=str(SHOTS / "e1_editor.png"))

        # ---- 3. 미리보기 = 내보낸 장면 (처음 3초 · 1080×1920) ----
        proj = ev("({...JSON.parse(JSON.stringify(S)), captions: P.captions, info: P.info, source: NAME, media: P.media})")
        before = set(os.listdir(WORK / "out")) if (WORK / "out").exists() else set()
        api("/api/edit/export", {"name": N1, "project": proj, "opts": {"preset": "youtube", "range": [0, 3], "video": True, "srt": False, "xml": False, "hw": False}})
        wait_job(600)
        mp4 = sorted([f for f in os.listdir(WORK / "out") if f.endswith(".mp4") and f not in before])
        ff = os.popen("python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())'").read().strip() or "ffmpeg"
        fr = SHOTS / "e1_export_1s.png"
        subprocess.run([ff, "-v", "error", "-y", "-ss", "1.0", "-i", str(WORK / "out" / mp4[-1]), "-frames:v", "1", str(fr)], check=True)
        a = np.asarray(Image.open(fr).convert("RGB")).astype(int)
        Hh, Ww = a.shape[:2]
        pvi = np.asarray(Image.open(pv).convert("RGB").resize((Ww, Hh), Image.LANCZOS)).astype(int)
        sk = np.asarray(Image.open(REPO / "skins" / "academy.png").convert("RGBA").resize((Ww, Hh), Image.LANCZOS)).astype(int)
        top_rows = (slice(0, int(0.088 * Hh)), slice(None))          # 흰 상자 위 (글자 없음)
        bot_rows = (slice(int(0.77 * Hh), Hh), slice(None))           # 아래 띠 (캐릭터·채널 이름)
        dex = [np.abs(a[r] - sk[r][..., :3])[sk[r][..., 3] == 255].mean() for r in (top_rows, bot_rows)]
        dpv = [np.abs(pvi[r] - a[r])[sk[r][..., 3] == 255].mean() for r in (top_rows, bot_rows)]
        check("내보낸 장면: 위·아래 띠 = 스킨 그림 (화소 평균 차이 < 6)", max(dex) < 6, f"{dex[0]:.2f}/{dex[1]:.2f}")
        check("미리보기 = 내보낸 장면 (스킨 띠 화소 평균 차이 < 10 / 255)", max(dpv) < 10, f"{dpv[0]:.2f}/{dpv[1]:.2f}")
        box = [g_ for g_ in q0["shapes"] if g_.get("plan") == "card"][0]
        bx = (slice(int((box["y"] + 0.006) * Hh), int((box["y"] + box["h"] - 0.006) * Hh)), slice(int(box["x"] * Ww) + 3, int(box["x"] * Ww) + 9))
        # (미리보기 그림은 화면 폭 250화소쯤이라 양옆 여백 14 칸은 흐려짐 → 미리보기는 상자 안 전체에서 흰 화소 몫으로)
        inner = (slice(int((box["y"] + 0.006) * Hh), int((box["y"] + box["h"] - 0.006) * Hh)), slice(int((box["x"] + 0.01) * Ww), int((box["x"] + box["w"] - 0.01) * Ww)))
        pv_white = float((pvi[inner].min(axis=2) > 200).mean())
        check("흰 상자는 스킨 위 (내보내기 상자 여백 흰색 · 미리보기 상자 안 절반 넘게 흰색(나머지는 주황 글자) · 그 자리 스킨은 칠판)",
              a[bx].min() > 225 and pv_white > 0.5 and sk[bx][..., :3].max() < 200 and sk[inner][..., :3].mean() < 150,
              f"내보내기 {a[bx].min()} · 미리보기 흰 몫 {pv_white:.2f} · 스킨 {sk[bx][..., :3].max()}")
        win = editor.skin_window("academy.png")
        mid = (slice(int((win["top"] + 0.05) * Hh), int((win["bottom"] - 0.12) * Hh)), slice(int(0.1 * Ww), int(0.9 * Ww)))
        sk_mid = sk[mid][..., 3].max()
        check("영상 창 안은 영상: 내보내기 창 화소는 영상(스킨 투명 · 한 색 아님) · 미리보기 영상 칸이 찢어진 종이 홈까지(385~1495 · 위아래 4화소 더) 덮음",
              a[mid].std() > 10 and sk_mid == 0 and fgr and fgr["top"] <= win["coverTop"] * 1920 - 3.5 and fgr["bottom"] >= win["coverBottom"] * 1920 + 3.5,
              f"표준편차 {a[mid].std():.1f} · 스킨 알파 {sk_mid} · 미리보기 영상 칸 {fgr and round(fgr['top'], 1)}~{fgr and round(fgr['bottom'], 1)} · "
              f"창 {win['top'] * 1920:.0f}~{win['bottom'] * 1920:.0f} · 덮을 곳 {win['coverTop'] * 1920:.0f}~{win['coverBottom'] * 1920:.0f}")
        Image.fromarray(np.concatenate([pvi, a], axis=1).astype(np.uint8)).resize((Ww, Hh // 2)).save(SHOTS / "e1_preview_vs_export.png")

        # ---- 4. 다른 템플릿은 스킨을 지움 · 다시 '풋살사관학교 스킨' ----
        ev("commit('템플릿'); TEMPLATES['위아래 검정 박스'](); changed(false)")
        W(1200)
        g1 = ev("({lay: S.layout, shown: $('skinL').style.display, card: S.shapes.filter(g => g.plan === 'card').length})")
        pg.locator("#frame").screenshot(path=str(SHOTS / "e2_other_template.png"))
        pg.click("#rtabs [data-rt='seq']")
        W(800)
        pg.locator("[data-tpl='풋살사관학교 스킨']").first.click()
        W(1500)
        g2 = ev("({lay: S.layout, cap: S.captionStyle, shown: $('skinL').style.display, card: S.shapes.filter(g => g.plan === 'card'), hint: !!$('pSkinHint'), toast: $('toast').textContent})")
        pg.locator("#frame").screenshot(path=str(SHOTS / "e3_template_skin.png"))
        check("다른 템플릿 → 스킨·흰 상자 없음 · '풋살사관학교 스킨' 단추 → 서버 값 그대로 다시 (상자 하나 · 말 자막 흰 Black 96 · 테두리 10 · 안내 줄)",
              "skin" not in g1["lay"] and g1["shown"] == "none" and g1["card"] == 0 and g2["lay"] == info["layout"] and g2["cap"]["y"] == editor.skin_cap_y(info["bottom"])
              and {k_: g2["cap"].get(k_) for k_ in editor.SKIN_CAP} == editor.SKIN_CAP
              and g2["shown"] == "block" and len(g2["card"]) == 1 and g2["card"][0]["radius"] == 17 and g2["hint"] and "풋살사관학교 스킨" in g2["toast"],
              json.dumps({"g1": g1, "g2": {k: v for k, v in g2.items() if k != "cap"}}, ensure_ascii=False)[:400])
        ev("commit('템플릿'); TEMPLATES['은평 스타일'](); changed(false)")
        W(800)
        g3 = ev("({lay: S.layout, shown: $('skinL').style.display})")
        check("'은평 스타일' → 스킨 지움 (은평 영상 창 win)", "skin" not in g3["lay"] and g3["lay"].get("win") and g3["shown"] == "none", json.dumps(g3))
        ev("commit('템플릿'); TEMPLATES['풋살사관학교 스킨'](); changed(false); doSave && doSave()")
        W(1500)

        # ---- 4b. 시퀀스 탭 스킨 안내는 보이는 칸만 말함 (검토: 풀샷 자르기에는 '영상 크기'·'박스 색' 칸이 없음) ----
        pg.click("#rtabs [data-rt='seq']")
        W(600)
        hints = {}
        for mode in ("fill", "blur", "box"):
            pg.click(f"#pLay [data-v='{mode}']")
            W(700)
            hints[mode] = ev("({hint: $('pSkinHint') ? $('pSkinHint').textContent : null, zoom: !!$('pZoom'), bar: !!$('pBar'), skin: !!S.layout.skin})")
        hf, hb, hx = hints["fill"], hints["blur"], hints["box"]
        check("스킨 안내: 풀샷 자르기는 '영상 크기'·'박스 색' 말 없이 '가로 맞춤+박스'·템플릿 안내 · 블러는 '영상 크기'만 · 박스는 둘 다 (말한 칸이 화면에 있음)",
              hf["hint"] and hf["skin"] and "영상 크기" not in hf["hint"] and "박스 색" not in hf["hint"] and "가로 맞춤+박스" in hf["hint"] and not hf["zoom"] and not hf["bar"]
              and hb["hint"] and "영상 크기" in hb["hint"] and "박스 색" not in hb["hint"] and hb["zoom"] and not hb["bar"]
              and hx["hint"] and "영상 크기" in hx["hint"] and "박스 색" in hx["hint"] and hx["zoom"] and hx["bar"], json.dumps(hints, ensure_ascii=False))
        ev("commit('템플릿'); TEMPLATES['풋살사관학교 스킨'](); changed(false)")
        W(800)

        # ---- 4c. 큰 제목 글을 고치면 글자 크기·흰 상자가 따라옴 (검토: 상자가 그대로라 긴 글이 칠판 위로 넘침) ----
        ev("seek(1.0)")
        W(1200)
        tid = ev("skinTop(S).id")
        ev(f"selectOverlay('title', '{tid}')")
        W(600)
        LONG = "트래핑 꿀팁 두 가지 정리"   # (한 줄 72 · 72 밑으로 줄어야 하는 글은 두 줄 — 4c-3)
        pg.locator("#pTitleText").fill(LONG)
        pg.locator("#pTitleText").dispatch_event("input")
        pg.locator("#pTitleText").blur()
        W(1200)
        e4 = ev(f"""(() => {{ const t = S.titles.find(x => x.id === '{tid}'), g = S.shapes.find(x => x.plan === 'card'), F = $('frame').getBoundingClientRect(), k = 1080 / F.width;
            const el = ovEls['t' + t.id], r = el && el.getBoundingClientRect();
            return {{ text: t.text, st: t.style, box: {{ x: g.x, w: g.w }}, txt: r && {{ left: (r.left - F.left) * k, right: (r.right - F.left) * k }} }}; }})()""")
        want_st = editor.skin_title_style(LONG)
        want_g = {}
        editor.skin_box_fit({"text": LONG, "style": want_st}, want_g)
        bl, br = e4["box"]["x"] * 1080, (e4["box"]["x"] + e4["box"]["w"]) * 1080
        tw = e4["txt"] and e4["txt"]["right"] - e4["txt"]["left"]
        check("큰 제목 글 고치기 → 글자 크기·자리 = 서버 skin_title_style · 흰 상자 = skin_box_fit · 미리보기 글이 상자 안 (양옆 여백 약 14 · 실제 글꼴 폭과 상자 표가 맞음)",
              e4["text"] == LONG and e4["st"]["size"] == want_st["size"] and e4["st"]["y"] == want_st["y"] and (e4["box"]["x"], e4["box"]["w"]) == (want_g["x"], want_g["w"])
              and e4["txt"] and e4["txt"]["left"] >= bl + 3 and e4["txt"]["right"] <= br - 3 and abs((br - bl) - tw - 2 * editor.SKIN_PAD) < 20,
              json.dumps({"st": {k: e4["st"][k] for k in ("size", "y")}, "box": [round(bl), round(br)], "txt": e4["txt"], "want": want_g}, ensure_ascii=False))
        pg.locator("#frame").screenshot(path=str(SHOTS / "e3b_title_edit.png"))
        ev("undo()")
        W(800)

        # ---- 4c-2. 주제 줄(두 번째 줄)을 길게 고쳐도 한 줄 · 흰 상자 아래 (검토 robust r2: 두 줄로 바뀌어 위로 늘며 흰 상자·큰 제목을 덮음 · D-539) ----
        # (주제 줄이 있는 가편집 쇼츠 — 첫 쇼츠는 큰 제목이 영상 주제라 주제 줄이 없을 수 있음)
        qk = ev("(() => { const q = P.sequences.find(q => q.format === 'shorts' && skinTop(q) && q.titles.some(x => x.plan === 'topic' && topicUnder(x, q.titles) === skinTop(q))); return q ? q.id : null; })()")
        kid = None
        if qk:
            ev(f"switchSeq('{qk}')")
            W(1000)
            ev("seek(1.0)")
            W(1200)
            kid = ev("(() => { const t = skinTop(S), k = t && S.titles.find(x => x.plan === 'topic' && topicUnder(x, S.titles) === t); return k ? k.id : null; })()")
        LONG_T = "퍼스트 터치 레슨 두 번째 포인트 디딤발 방향"
        e5 = None
        if kid:
            ev(f"selectOverlay('title', '{kid}')")
            W(600)
            pg.locator("#pTitleText").fill(LONG_T)
            pg.locator("#pTitleText").dispatch_event("input")
            pg.locator("#pTitleText").blur()
            W(1200)
            e5 = ev(f"""(() => {{ const k = S.titles.find(x => x.id === '{kid}'), t = skinTop(S), g = S.shapes.find(x => x.plan === 'card'), F = $('frame').getBoundingClientRect(), ky = 1920 / F.height;
                const el = ovEls['t' + k.id], r = el && el.getBoundingClientRect();
                return {{ text: k.text, st: k.style, big: t.style.size, box: {{ y: g.y, h: g.h }}, el: r && {{ top: (r.top - F.top) * ky, bottom: (r.bottom - F.top) * ky, h: r.height * ky }} }}; }})()""")
            pg.locator("#frame").screenshot(path=str(SHOTS / "e3c_topic_edit.png"))
            ev("undo()")
            W(800)
        want_k = editor.skin_topic_style(LONG_T, info["top"], e5["big"]) if e5 else None
        check("주제 줄 글을 길게 고치기 → 글자 크기·자리 = 서버 skin_topic_style(새 글) · 미리보기 한 줄 · 흰 상자 아래 · 영상 창 위 (D-539)",
              e5 and e5["text"] == LONG_T and (e5["st"]["size"], e5["st"]["y"]) == (want_k["size"], want_k["y"]) and e5["el"]
              and e5["el"]["h"] < 1.6 * e5["st"]["size"] and e5["el"]["top"] >= (e5["box"]["y"] + e5["box"]["h"]) * 1920 - 2 and e5["el"]["bottom"] <= info["top"] * 1920 + 2,
              json.dumps({"seq": qk, "e5": e5}, ensure_ascii=False)[:400])

        # ---- 4c-3. 큰 제목을 33글자로 고치면 흰 상자 두 줄 (소유자 2026-10-20 '흰 상자를 두 줄로' · I-537 · D-540 · 예전: 한 줄을 지키려고 28) ----
        # (주제 줄이 있는 쇼츠에서 — 상자·큰 제목·주제 줄이 서로·영상 창과 안 겹치는지)
        T33 = "패스하고 그 자리에 서 있으면 왜 안될까요? 끝까지 보세요!"
        ev(f"switchSeq('{qk or q0['id']}')")
        W(1000)
        ev("seek(1.0)")
        W(1200)
        bid = ev("skinTop(S).id")
        ev(f"selectOverlay('title', '{bid}')")
        W(600)
        pg.locator("#pTitleText").fill(T33)
        pg.locator("#pTitleText").dispatch_event("input")
        pg.locator("#pTitleText").blur()
        W(1500)
        e6 = ev(f"""(() => {{ const t = S.titles.find(x => x.id === '{bid}'), g = S.shapes.find(x => x.plan === 'card'), F = $('frame').getBoundingClientRect(), ky = 1920 / F.height, kx = 1080 / F.width;
            const k = S.titles.find(x => x.plan === 'topic' && topicUnder(x, S.titles) === t);
            const rc = el => {{ const r = el && el.getBoundingClientRect(); return r && {{ top: (r.top - F.top) * ky, bottom: (r.bottom - F.top) * ky, left: (r.left - F.left) * kx, right: (r.right - F.left) * kx }}; }};
            const el = ovEls['t' + t.id];
            return {{ text: t.text, st: t.style, box: {{ x: g.x, y: g.y, w: g.w, h: g.h }}, k: k && {{ text: k.text, st: k.style, el: rc(ovEls['t' + k.id]) }}, el: rc(el), shown: el && el.textContent }}; }})()""")
        ev("selX = null; if (window.selI) selI.clear(); renderTimeline(); frameDirty = true")   # (고른 표시 테두리·손잡이 없이)
        W(800)
        pv6 = SHOTS / "e3d_long_title.png"
        pg.locator("#frame").screenshot(path=str(pv6))
        want6 = editor.skin_title_style(T33)
        top6 = {"text": T33, "style": dict(want6)}
        g6 = {}
        editor.skin_box_fit(top6, g6)
        kt6 = None
        if e6["k"]:   # 서버 셈 (주제 줄은 고치기 전 모양 그대로 · 큰 제목 크기에 맞춤 = skinTopicStyle · 그다음 skin_stack)
            kt6 = {"text": e6["k"]["text"], "plan": "topic", "style": editor.skin_topic_style(e6["k"]["text"], info["top"], want6["size"])}
        editor.skin_stack(top6, g6, [kt6] if kt6 else [], info["top"], drop=False, cover=info["coverTop"])
        b6 = e6["box"]
        by0, by1, wtop = b6["y"] * 1920, (b6["y"] + b6["h"]) * 1920, info["top"] * 1920
        el6 = e6["el"] or {}
        ok_k = True
        if e6["k"]:
            ke = e6["k"]["el"] or {}
            ok_k = (e6["k"]["st"]["size"], e6["k"]["st"]["y"]) == (kt6["style"]["size"], kt6["style"]["y"]) and ke \
                and ke["top"] >= by1 - editor.SKIN_TOPIC_TUCK * e6["k"]["st"]["size"] - 2 and ke["bottom"] <= wtop - 2
        check("큰 제목을 33글자로 고치기 → 두 줄 56(예전 28 한 줄) = 서버 skin_title_style · 흰 상자 두 줄 높이(넓은 줄에 맞춤 · 서버 skin_stack) · 미리보기 큰 제목은 상자 안 두 줄 · "
              "주제 줄은 상자 아래 · 모두 영상 창 위 (안 겹침)",
              len(T33) == 33 and e6["text"] == T33 and e6["st"]["size"] == 56 and e6["st"].get("lines") == 2 and e6["st"]["size"] == want6["size"]
              and e6["st"]["y"] == top6["style"]["y"] and (b6["x"], b6["w"], b6["y"], b6["h"]) == (g6["x"], g6["w"], g6["y"], g6["h"]) and b6["h"] > editor.SKIN_BOX["h"]
              and el6 and el6["top"] >= by0 - 2 and el6["bottom"] <= by1 + 2 and el6["left"] >= b6["x"] * 1080 - 2 and el6["right"] <= (b6["x"] + b6["w"]) * 1080 + 2
              and (el6["bottom"] - el6["top"]) > 1.8 * 1.25 * 56 and "\n" in (e6["shown"] or "") and by1 < wtop - 10 and ok_k,
              json.dumps({"seq": qk, "st": {k: e6["st"].get(k) for k in ("size", "lines", "y")}, "box": b6, "want": g6, "el": el6, "k": e6["k"] and {"st": {k: e6["k"]["st"][k] for k in ("size", "y")}, "el": e6["k"]["el"]}},
                         ensure_ascii=False)[:600])
        # 미리보기 = 내보낸 장면 (큰 제목 주황 두 덩이가 같은 자리 · 흰 상자 안)
        proj6 = ev("({...JSON.parse(JSON.stringify(S)), captions: P.captions, info: P.info, source: NAME, media: P.media})")
        before = set(os.listdir(WORK / "out"))
        api("/api/edit/export", {"name": N1, "project": proj6, "opts": {"preset": "youtube", "range": [0, 2], "video": True, "srt": False, "xml": False, "hw": False}})
        wait_job(600)
        mp4 = sorted([f for f in os.listdir(WORK / "out") if f.endswith(".mp4") and f not in before])
        fr6 = SHOTS / "e3d_long_title_export.png"
        subprocess.run([ff, "-v", "error", "-y", "-ss", "1.0", "-i", str(WORK / "out" / mp4[-1]), "-frames:v", "1", str(fr6)], check=True)
        a6 = np.asarray(Image.open(fr6).convert("RGB")).astype(int)
        p6 = np.asarray(Image.open(pv6).convert("RGB").resize((a6.shape[1], a6.shape[0]), Image.LANCZOS)).astype(int)

        def bands(img):   # 흰 상자 줄(위아래 30화소 더) 안 주황 글자 덩이 (10줄 넘는 것 · 미리보기 흰 글자 가장자리의 색 번짐은 빼려고 한 줄에 9화소 넘게)
            r0 = max(0, int(by0) - 30)
            o = (np.abs(img[r0:int(by1) + 30] - (0xCE, 0x4F, 0x2F)).max(axis=2) < 60).sum(axis=1)
            rows = np.where(o > 8)[0] + r0
            if not len(rows):
                return []
            cut = np.where(np.diff(rows) > 6)[0]
            return [[int(r[0]), int(r[-1])] for r in np.split(rows, cut + 1) if r[-1] - r[0] >= 10]
        bx6, bp6 = bands(a6), bands(p6)
        check("큰 제목 두 줄: 내보낸 장면도 주황 두 덩이 · 흰 상자 안 · 미리보기와 같은 자리(줄마다 ±12화소)",
              len(bx6) == 2 and len(bp6) == 2 and bx6[0][0] >= by0 and bx6[1][1] <= by1 and all(abs(x[0] - y[0]) <= 12 and abs(x[1] - y[1]) <= 12 for x, y in zip(bx6, bp6)),
              f"내보내기 {bx6} · 미리보기 {bp6} · 상자 {by0:.0f}~{by1:.0f}")
        Image.fromarray(np.concatenate([p6[:560], a6[:560]], axis=1).astype(np.uint8)).save(SHOTS / "e3d_long_title_preview_vs_export.png")
        ev("undo()")
        W(800)
        if qk:
            ev(f"switchSeq('{q0['id']}')")
            W(1000)

        # ---- 4d. '새 쇼츠 편집본으로 추가' (스킨 바탕) — KFA 글꼴을 찾는 중이어도 기다리지 않음 (검토: 은평 스타일 바탕만 기다림) ----
        pg.click('[data-lt="rec"]')
        W(800)
        ev("(() => { const d = document.getElementById('recMore'); if (d && !d.open) d.open = true; })()")
        W(300)
        n0 = ev("P.sequences.length")
        base_skin = ev("skinOn(recBase().base) && epOn(recBase().base)")
        ev("""(() => { window._f0 = window.fetch; EPF = null; EPX.fontChecking = true;   // 글꼴 찾기가 끝나지 않는 PC 흉내 (늘 '찾는 중')
            window.fetch = (u, o) => String(u).includes('/api/style/card_font') ? Promise.resolve(new Response('{"checking": true}', { headers: { 'Content-Type': 'application/json' } })) : window._f0(u, o); })()""")
        t0 = time.time()
        pg.locator('[data-rec="0"]').click()
        try:
            pg.wait_for_function(f"P.sequences.length > {n0}", timeout=10000)
        except Exception:
            pass
        dt = time.time() - t0
        nq = ev("""(() => { const q = P.sequences[P.sequences.length - 1]; return { n: P.sequences.length, lay: q.layout, cap: q.captionStyle.y,
            card: q.shapes.filter(g => g.plan === 'card').length, top: q.titles[0] && q.titles[0].style }; })()""")
        ev("window.fetch = window._f0; EPX.fontChecking = false")
        check("'새 쇼츠 편집본으로 추가'(스킨 바탕 · KFA 글꼴을 찾는 중): 기다리지 않고 바로 스킨 편집본 (화면 구성 = 서버 · 흰 상자 하나 · 말 자막 0.717)",
              base_skin and nq["n"] == n0 + 1 and dt < 5 and nq["lay"] == info["layout"] and nq["card"] == 1 and nq["cap"] == editor.skin_cap_y(info["bottom"])
              and nq["top"] and nq["top"]["fill"] == "#CE4F2F", f"{dt:.1f}초 · " + json.dumps({k: v for k, v in nq.items() if k != "top"}, ensure_ascii=False)[:300])

        # ---- 4e. MSG '쇼츠 하이텐션형'(노래방) 스킨 쇼츠 — 말 자막은 흰 글씨 (소유자 2026-10-20 '스킨은 늘 흰 글씨' · I-530 · D-541) ----
        r = api("/api/edit/msg", {"name": N1, "styles": [{"kind": "preset", "name": "쇼츠 하이텐션형"}], "intensity": "듬뿍", "kinds": ["shorts"]})
        st7 = wait_job(1500)
        if st7.get("resultBig"):
            st7 = api("/api/state?since=999999&result=1")
        res7 = st7.get("result") or {}
        nm = 0
        if r.get("ok") and res7.get("sequences"):
            ev(f"msgAdd({json.dumps(res7, ensure_ascii=False)})")
            W(1500)
            nm = len(res7["sequences"])
        e7 = ev("""(() => { const c = capsTL().find(c => c.trk === 'C1' && c.e - c.s > 0.6 && c.s > 3); return { cs: S.captionStyle, lay: S.layout, msg: !!S.msg, c: c && { s: c.s, e: c.e, text: c.text } }; })()""")
        e7c = None
        if e7.get("c"):
            tc = (e7["c"]["s"] + e7["c"]["e"]) / 2
            ev(f"seek({tc})")
            W(2000)
            e7c = ev("""(() => { const els = [...document.querySelectorAll('#ov .txt')].filter(el => el._pick && el._pick.k === 'caption');
                return els.map(el => ({ color: getComputedStyle(el).color, spans: [...el.querySelectorAll('span')].map(s => getComputedStyle(s).color), text: el.textContent })); })()""")
            pg.locator("#frame").screenshot(path=str(SHOTS / "e5_msg_karaoke_preview.png"))
            proj7 = ev("({...JSON.parse(JSON.stringify(S)), captions: P.captions, info: P.info, source: NAME, media: P.media})")
            before = set(os.listdir(WORK / "out"))
            a0 = max(0.0, tc - 1.0)
            api("/api/edit/export", {"name": N1, "project": proj7, "opts": {"preset": "youtube", "range": [a0, a0 + 2.0], "video": True, "srt": False, "xml": False, "hw": False}})
            wait_job(900)
            mp4 = sorted([f for f in os.listdir(WORK / "out") if f.endswith(".mp4") and f not in before])
            fr7 = SHOTS / "e5_msg_karaoke_export.png"
            subprocess.run([ff, "-v", "error", "-y", "-ss", str(tc - a0), "-i", str(WORK / "out" / mp4[-1]), "-frames:v", "1", str(fr7)], check=True)
            a7 = np.asarray(Image.open(fr7).convert("RGB")).astype(int)
            cy = editor.skin_cap_y(info["bottom"]) * a7.shape[0]
            band = a7[int(cy - 1.4 * 96):int(cy) + 4, 30:1050]
            white = int((band.min(axis=2) > 225).sum())
            yellow = int(((band[..., 0] > 200) & (band[..., 1] > 170) & (band[..., 2] < 90)).sum())
        else:
            white = yellow = -1
        check("MSG 쇼츠 하이텐션형(노래방) 스킨 쇼츠: 말 자막 모양은 흰 글씨 '바로'(노래방 아님) · 미리보기 말 자막 흰색(노란 낱말 없음) · 내보낸 말 자막 줄에 흰 글씨 있고 노란 글씨 없음",
              nm >= 1 and e7["msg"] and "skin" in e7["lay"] and e7["cs"]["effect"] == "none" and e7["cs"]["fill"] == "#FFFFFF" and e7c
              and all(x["color"] == "rgb(255, 255, 255)" and all(sc == "rgb(255, 255, 255)" for sc in x["spans"]) for x in e7c)
              and white > 400 and yellow < 30,
              json.dumps({"n": nm, "cs": {k: e7["cs"].get(k) for k in ("effect", "fill", "highlight")}, "c": e7.get("c"), "pv": e7c, "white": white, "yellow": yellow}, ensure_ascii=False)[:500])

        # ---- 4f. 예전(v2.26.0) 긴 큰 제목 편집본(한 줄 28 · 둘째 줄도 28)을 열면 두 줄 · 둘째 줄도 새 크기 · 바로 저장 · 되돌리기 · 다시 열면 알림 없음 ----
        # (검토: 열 때마다 알림 · 프로젝트 파일은 28 그대로 · 둘째 줄은 28 그대로 남아 큰 두 줄 밑에 아주 작은 줄)
        LONG_O = "패스하고 그 자리에 서 있으면 왜 안될까요 끝까지 보면 알게 돼요"
        sid = qk or q0["id"]
        PJ = WORK / "projects" / (N1[:-4] + ".json")
        ev(f"""(() => {{ const q = P.sequences.find(x => x.id === '{sid}'), b = skinTop(q); b.text = {json.dumps(LONG_O, ensure_ascii=False)}; b.style = skinTitleOne(b.text);
            const g = q.shapes.find(x => x.plan === 'card'); g.y = SKIN.BOX.y; g.h = SKIN.BOX.h; skinBoxFit(b, g);
            for (const k of skinTopics(q, b)) {{ const s = skinTopicStyle(k.text, SKX.top, b.style.size); k.style.size = s.size; k.style.y = s.y; }}
            P.active = '{sid}'; }})()""")
        ev("saveNow()")
        W(800)

        def disk_seq():
            return next(x for x in json.loads(PJ.read_text(encoding="utf-8"))["sequences"] if x["id"] == sid)
        old_q = disk_seq()
        want_q = json.loads(json.dumps(old_q))
        want_gone = editor.skin_title_fix(want_q, info["top"], cover=info["coverTop"])

        def reopen():
            pg.goto(f"http://127.0.0.1:{PORT}/editor?name={urllib.request.quote(N1)}")
            pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=300000)
            seen = []
            for _ in range(16):
                t_ = ev("$('toast').classList.contains('show') ? $('toast').textContent : ''")
                if t_ and t_ not in seen:
                    seen.append(t_)
                W(250)
            W(1500)
            return seen
        seen1 = reopen()
        e8 = ev(f"""(() => {{ const q = P.sequences.find(x => x.id === '{sid}'); return {{ titles: q.titles, shapes: q.shapes, hist: hist.length ? hist[hist.length - 1].label : null, dirty }}; }})()""")
        d8 = disk_seq()
        ob = editor.skin_big(old_q)
        ok_old = ob["style"]["size"] == 28 and all(t_["style"]["size"] == 28 for t_ in old_q["titles"] if t_.get("plan") == "topic")
        nb, nk = editor.skin_big(want_q), [t_ for t_ in want_q["titles"] if t_.get("plan") == "topic"]
        ev("seek(1.0)")
        W(1500)
        pg.locator("#frame").screenshot(path=str(SHOTS / "e6_open_old_long_title.png"))
        check("예전(v2.26.0) 한 줄 28 큰 제목 편집본을 열면: 두 줄 53 · 둘째 줄도 새 크기(48 · 28 아님) = 서버 skin_title_fix · 알림 · 되돌리기 기록 · 프로젝트 파일에 바로 저장",
              ok_old and want_gone == [] and (nb["style"]["size"], nb["style"].get("lines")) == (53, 2) and nk and all(k["style"]["size"] == 48 for k in nk)
              and (e8["titles"], e8["shapes"]) == (want_q["titles"], want_q["shapes"]) and any("긴 큰 제목을 흰 상자 두 줄로 바꿨어요" in x for x in seen1)
              and e8["hist"] == "긴 큰 제목 두 줄로" and (d8["titles"], d8["shapes"]) == (want_q["titles"], want_q["shapes"]),
              json.dumps({"toasts": seen1, "hist": e8["hist"], "old": [ob["style"]["size"]] + [t_["style"]["size"] for t_ in old_q["titles"] if t_.get("plan") == "topic"],
                          "new": [nb["style"]["size"], nb["style"].get("lines")] + [k["style"]["size"] for k in nk],
                          "disk": [editor.skin_big(d8)["style"]["size"], editor.skin_big(d8)["style"].get("lines")]}, ensure_ascii=False)[:500])
        seen2 = reopen()
        d9 = disk_seq()
        check("다시 열면 '두 줄로 바꿨어요' 알림 없음 · 프로젝트 파일 그대로",
              not any("두 줄로 바꿨어요" in x for x in seen2) and (d9["titles"], d9["shapes"]) == (d8["titles"], d8["shapes"]) and ev("hist.length") == 0,
              json.dumps({"toasts": seen2}, ensure_ascii=False))

        # ---- 5. 바꾼 스킨으로 템플릿 (편집실을 다시 열 때 설정을 읽음) ----
        import base64  # noqa: E402
        r = api("/api/style/skin", {"data": "data:image/png;base64," + base64.b64encode(PICK.read_bytes()).decode()})
        info2 = editor.skin_layout(r["file"]) if r.get("ok") else None
        pg.goto(f"http://127.0.0.1:{PORT}/editor?name={urllib.request.quote(N1)}")
        pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=300000)
        W(1500)
        ev(f"switchSeq('{q0['id']}')")
        W(800)
        ev("commit('템플릿'); TEMPLATES['풋살사관학교 스킨'](); changed(false)")
        ev("seek(1.0)")
        W(2500)
        g4 = ev("({lay: S.layout, cap: S.captionStyle.y, src: $('skinL').getAttribute('src'), nw: $('skinL').naturalWidth, skx: SKX})")
        pg.locator("#frame").screenshot(path=str(SHOTS / "e4_new_skin.png"))
        shot = np.asarray(Image.open(SHOTS / "e4_new_skin.png").convert("RGB")).astype(int)
        sh_h = shot.shape[0]
        topc = shot[int(0.05 * sh_h):int(0.08 * sh_h), :20].reshape(-1, 3).mean(axis=0)
        check("바꾼 스킨: 템플릿이 그 스킨·영상 창(0.3~0.7)으로 · 미리보기 위 띠가 그 색(자홍)",
              r.get("ok") and g4["lay"] == info2 and g4["cap"] == editor.skin_cap_y(0.7) and r["file"] in (g4["src"] or "") and g4["nw"] == 1080
              and abs(g4["skx"]["top"] - 0.3) < 0.002 and np.abs(topc - (200, 30, 160)).max() < 30,
              json.dumps({"lay": g4["lay"], "want": info2, "top": topc.round().tolist()}, ensure_ascii=False)[:400])
        check("JS 오류 없음", not errs, "; ".join(errs[:3]))
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(10)
    except Exception:
        srv.kill()

n_ok = sum(1 for _, ok in res if ok)
print(f"{n_ok}/{len(res)} 통과")
sys.exit(0 if n_ok == len(res) else 1)
