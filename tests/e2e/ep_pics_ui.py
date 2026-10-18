"""은평 스타일 띠 그림·로고 화면 e2e (BR-280~BR-284 · D-470~D-477 · 개발용 · 배포 안 됨 · unittest 가 줍지 않음) — 마지막 줄 'N/N 통과' 면 통과.

실행: EP_SRC=<시험 작업 폴더> [EP_REPO=<저장소>] [EP_PORT=9488] [EP_OUT=<출력 폴더>] python3 tests/e2e/ep_pics_ui.py
- EP_SRC: videos/ 와 analysis/<영상 이름>/ 이 있는 작업 폴더 — MSG 시험 원본 MSGRAW01(퍼스트 터치 레슨) (tests/e2e/topic_ui.py 와 같은 폴더 ·
  리드 개발 환경은 `$SCRATCH/topic_ev2/work` · 영상은 저장소에 넣지 않음)
- 저장소를 EP_OUT/app 으로 복사(config.json 은 시험용)해 띄우고, 작업 폴더 EP_OUT/work 는 매번 새로 · 스크린샷·비교 그림 EP_OUT/shots
확인: 스타일 화면(띠 그림·로고 기본 켬 · 그림 보임 · 끄기/켜기 · 한글 이름 그림 파일로 '그림 바꾸기' · 처음 그림으로) · 가편집 쇼츠 화면 구성 ·
편집실 미리보기(띠 그림·로고) = 내보낸 장면(1080×1920 · 바탕 화소 차이·로고 자리) · 말 자막·강좌 줄이 로고 위 ·
두 줄로 감기는 말 자막(강좌 줄 + 로고)은 글자를 줄여 미리보기·내보내기 모두 영상 창 아래(같은 크기·자리) · 다른 템플릿은 그림을 지움 · '박스 색' 옆 안내(띠 그림이 있을 때만) ·
템플릿 '은평 스타일'은 스타일 화면 설정대로 (로고를 끄면 로고 없음) · JS 오류 0."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(os.environ.get("EP_REPO") or Path(__file__).resolve().parents[2])
SRC = Path(os.environ.get("EP_SRC", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/topic_ev2/work"))
OUT = Path(os.environ.get("EP_OUT", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/ep_e2e"))
PORT = int(os.environ.get("EP_PORT", "9488"))
CHROME = os.environ.get("EP_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
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

# 한글·띄어쓰기 이름의 바꿀 그림 (Windows 의 '내 그림\\은평 띠 새 그림.png' 같은 파일)
from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402
PICK = OUT / "내 그림" / "은평 띠 새 그림.png"
PICK.parent.mkdir(parents=True, exist_ok=True)
g = np.zeros((200, 1000, 3), np.uint8)
g[..., 0] = np.linspace(20, 220, 1000)[None, :]
g[..., 2] = 160
Image.fromarray(g).save(PICK)

try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME)
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("dialog", lambda d: d.accept())
        ev, W = pg.evaluate, pg.wait_for_timeout

        # ---- 1. 스타일 화면: 띠 그림·로고 (기본 켬 · 그림 보임) ----
        pg.goto(f"http://127.0.0.1:{PORT}/")
        W(1500)
        pg.locator('[data-p="style"]').first.click()
        W(1000)
        ev("document.getElementById('autoSet').scrollIntoView()")
        st0 = ev("""(() => ({band: $('cardBand').checked, logo: $('cardLogo').checked, bw: $('cardBandImg').naturalWidth, lw: $('cardLogoImg').naturalWidth,
            reset: [$('cardBandReset').hidden, $('cardLogoReset').hidden], text: $('autoSet').innerText}))()""")
        pg.locator("#autoSet").screenshot(path=str(SHOTS / "s1_style_pics.png"))
        check("스타일 화면: '띠에 그림 깔기'·'아래 띠에 로고 넣기' 기본 켬 · 실은 그림이 보임 · '처음 그림으로'는 숨김",
              st0["band"] and st0["logo"] and st0["bw"] == 1600 and st0["lw"] == 180 and st0["reset"] == [True, True] and "그림 바꾸기" in st0["text"],
              json.dumps({k: v for k, v in st0.items() if k != "text"}))
        pg.select_option("#shortsLayout", "eunpyeong")
        W(800)
        pg.locator("#cardLong").check()
        W(800)
        check("쇼츠 화면 배치 '은평 스타일' · 전체 강좌 줄 켬", api("/api/style/shorts_layout")["layout"] == "eunpyeong" and api("/api/style/card_extras")["longLine"])

        pg.locator("#cardLogo").uncheck()
        W(700)
        off = api("/api/style/list")["cardPics"]
        pg.locator("#cardLogo").check()
        W(700)
        on = api("/api/style/list")["cardPics"]
        check("로고 끄기/켜기가 저장됨 (띠 그림은 그대로)", off["logo"] is False and off["band"] and on["logo"] and on["band"], f"{off} {on}")

        with pg.expect_file_chooser() as fc:
            pg.click("#cardBandPick")
        fc.value.set_files(str(PICK))
        pg.wait_for_function("!document.getElementById('cardBandReset').hidden", timeout=20000)
        W(800)
        picked = api("/api/style/list")["cardPics"]
        nw = ev("$('cardBandImg').naturalWidth")
        copied = WORK / "styles" / "설정" / "은평 그림" / picked["bandFile"]
        pg.locator("#autoSet").screenshot(path=str(SHOTS / "s2_style_picked.png"))
        check("'그림 바꾸기'(한글 이름 그림 파일) → 작업 폴더에 복사 · 그 그림이 보임 · '처음 그림으로' 보임",
              picked["bandFile"].startswith("band_") and copied.is_file() and nw == 1000 and picked["band"], f"{picked} {nw}")
        pg.click("#cardBandReset")
        W(800)
        back = api("/api/style/list")["cardPics"]
        check("'처음 그림으로' → 실은 그림 · 복사한 그림 파일은 남음", back["bandFile"] == "" and ev("$('cardBandReset').hidden") and copied.is_file(), str(back))
        bad = OUT / "내 그림" / "그림 아님.png"
        bad.write_bytes(b"not a picture")
        with pg.expect_file_chooser() as fc:
            pg.click("#cardLogoPick")
        fc.value.set_files(str(bad))
        W(1500)
        toast = ev("$('toast').textContent")
        check("그림이 아닌 파일 → 쉬운 말 안내 · 설정 그대로", "그림 파일을 읽지 못했어요" in toast and api("/api/style/list")["cardPics"]["logoFile"] == "", toast)

        # ---- 2. 편집실: 가편집 쇼츠 = 띠 그림·로고 ----
        pg.goto(f"http://127.0.0.1:{PORT}/editor?name={urllib.request.quote(N1)}")
        pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=300000)
        W(1500)
        sq = ev("P.sequences.filter(q => q.format === 'shorts').map(q => ({id: q.id, lay: q.layout, cap: q.captionStyle.y, "
                "line: (q.titles.find(t => t.plan === 'longline') || {style: {}}).style.y}))")
        check("가편집 쇼츠: 화면 구성에 띠 그림·로고 · 강좌 줄은 로고 바로 위(0.804) · 말 자막은 그 위",
              sq and all(q["lay"].get("band") == "band.png" and q["lay"].get("logo") == "logo.png" and q["line"] == 0.804 and q["cap"] <= 0.768 for q in sq),
              json.dumps(sq, ensure_ascii=False)[:400])
        q0 = sq[0]
        ev(f"switchSeq('{q0['id']}')")
        W(1000)
        # 검토(I-472): 1초에 보이는 말 자막을 두 줄로 감기는 긴 글로 (강좌 줄 + 로고 → 말 자막 아래 끝 0.768)
        LONG = "트래핑할 때는 공을 멈추는 게 아니라 다음 동작을 준비하는 거예요"
        cap_id = ev("""(t => { const c = capsTL().find(x => (x.trk || "C1") === "C1" && x.mid === "main" && x.s <= 1.0 && 1.0 < x.e);
              if (!c) return null; c.cap.text = t; ver++; frameDirty = true; return c.cap.id; })""" + f"({json.dumps(LONG, ensure_ascii=False)})")
        cst = ev("JSON.parse(JSON.stringify(capStyleOf(OTK('C1') || { id: 'C1' })))")
        ev("seek(1.0)")
        W(2500)
        ev("selX = null; if (window.selI) selI.clear(); renderTimeline(); frameDirty = true")
        W(800)
        dom = ev("""(() => { const r = [...document.querySelectorAll('.lay')].find(l => l.style.display !== 'none' && l.querySelector('.lbp') &&
              l.querySelector('.lbp').style.display !== 'none'); if (!r) return null; const p = r.querySelector('.lbp'), i = p.querySelectorAll('i'), im = p.querySelector('img');
              return {top: i[0].style.backgroundImage, bot: i[1].style.backgroundImage, logo: im.naturalWidth, shown: im.style.display, h: im.style.height}; })()""")
        check("미리보기: 위·아래 띠에 띠 그림 · 로고 그림 보임", dom and "card_pic?f=band.png" in dom["top"] and dom["top"] == dom["bot"] and dom["logo"] == 180
              and dom["shown"] == "block", json.dumps(dom))
        pv = SHOTS / "e1_preview_1s.png"
        pg.locator("#frame").screenshot(path=str(pv))
        cdom = ev("""(id => { const el = ovEls["c" + id]; if (!el) return null; const F = $("frame").getBoundingClientRect(), r = el.getBoundingClientRect(), k = F.height / BH();
              return { top: (r.top - F.top) / k, bottom: (r.bottom - F.top) / k, fs: parseFloat(el.style.fontSize) / k, text: el.textContent }; })""" + f"({json.dumps(cap_id)})")

        # ---- 3. 미리보기 = 내보낸 장면 (처음 3초 구간 · 1080×1920) ----
        proj = ev("({...JSON.parse(JSON.stringify(S)), captions: P.captions, info: P.info, source: NAME, media: P.media})")
        before = set(os.listdir(WORK / "out")) if (WORK / "out").exists() else set()
        api("/api/edit/export", {"name": N1, "project": proj, "opts": {"preset": "youtube", "range": [0, 3], "video": True, "srt": False, "xml": False, "hw": False}})
        wait_job(600)
        mp4 = sorted([f for f in os.listdir(WORK / "out") if f.endswith(".mp4") and f not in before])
        ff = os.popen("python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())'").read().strip() or "ffmpeg"
        fr = SHOTS / "e1_export_1s.png"
        subprocess.run([ff, "-v", "error", "-y", "-ss", "1.0", "-i", str(WORK / "out" / mp4[-1]), "-frames:v", "1", str(fr)], check=True)
        import editor  # noqa: E402 — 저장소의 셈 (바탕 그림)
        a = np.asarray(Image.open(fr).convert("RGB")).astype(int)
        Hh, Ww = a.shape[:2]
        pvi = np.asarray(Image.open(pv).convert("RGB").resize((Ww, Hh), Image.LANCZOS)).astype(int)
        fit = editor._fit({"track": "V1", "fit": "auto"}, {"kind": "video", "w": proj["info"]["width"], "h": proj["info"]["height"]},
                          {"format": "shorts", "layout": q0["lay"]}, Ww, Hh)
        ref = np.asarray(editor.ep_bg_image(Ww, Hh, fit["by"], fit["bh"], fit["bar"], REPO / "eunpyeong" / "band.png", REPO / "eunpyeong" / "logo.png")).astype(int)
        refb = np.asarray(editor.ep_bg_image(Ww, Hh, fit["by"], fit["bh"], fit["bar"], REPO / "eunpyeong" / "band.png", None)).astype(int)
        wy0, wy1 = int(fit["by"]) & ~1, (int(fit["by"]) & ~1) + int(fit["bh"])
        bandrows = np.zeros((Hh, Ww), bool)
        bandrows[:wy0 - 3] = True
        bandrows[wy1 + 3:] = True
        bgpix = bandrows & (np.abs(a - ref).max(axis=2) < 25)   # 내보낸 장면에서 글자·도형이 아닌 바탕 화소
        d_bg = np.abs(pvi - a)[bgpix].mean()
        d_ref = np.abs(a - ref)[bgpix].mean()
        tex = a[int(0.2 * Hh):wy0 - 3, :40].std(axis=(0, 1)).max(), a[wy1 + 3:int(0.75 * Hh), :40].std(axis=(0, 1)).max()
        check("내보낸 장면: 위·아래 띠가 질감(한 색 아님) · 바탕 그림 그대로", min(tex) > 4 and d_ref < 6, f"질감 표준편차 {tex[0]:.1f}/{tex[1]:.1f} · 바탕 그림과 차이 {d_ref:.2f}")
        check("미리보기 = 내보낸 장면 (띠 바탕 화소 평균 차이 < 8 / 255)", d_bg < 8, f"평균 차이 {d_bg:.2f} · 화소 {int(bgpix.sum())}개")

        def logo_bbox(img):
            m = np.abs(img - refb).max(axis=2) > 45
            m[:int(0.81 * Hh)] = False
            m[:, :int(0.33 * Ww)] = False
            m[:, int(0.67 * Ww):] = False
            ys, xs = np.nonzero(m.sum(axis=1) >= 3)[0], np.nonzero(m.sum(axis=0) >= 3)[0]   # 띠 무늬 가장자리 한두 화소(미리보기 축소)는 셈하지 않음
            return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(ys) and len(xs) else None
        le, lp = logo_bbox(a), logo_bbox(pvi)
        lx, ly, lw, lh = editor.ep_logo_box(180, 254, Ww, Hh)
        check("로고 자리: 내보내기·미리보기·약속한 자리(높이 0.151 · 가운데 0.892)가 같음 (4화소 안)",
              le and lp and all(abs(x - y) <= 4 for x, y in zip(le, lp)) and abs(le[1] - ly) <= 6 and abs(le[3] - (ly + lh)) <= 8 and abs((le[0] + le[2]) / 2 - Ww / 2) <= 3,
              f"내보내기 {le} · 미리보기 {lp} · 약속 {(round(lx), round(ly), round(lx + lw), round(ly + lh))}")
        side = np.concatenate([a[wy1:, :le[0] - 4], a[wy1:, le[2] + 4:]], axis=1)
        wrows = np.where((side.min(axis=2) > 225).sum(axis=1) > 3)[0] + wy1
        check("말 자막·전체 강좌 줄 흰 글씨는 로고 위 (안 겹침)", len(wrows) > 10 and wrows.max() < le[1],
              f"흰 글씨 줄 {wrows.min() if len(wrows) else None}~{wrows.max() if len(wrows) else None} · 로고 위 끝 {le[1]}")
        Image.fromarray(np.concatenate([pvi, a], axis=1).astype(np.uint8)).resize((Ww, Hh // 2)).save(SHOTS / "e1_preview_vs_export.png")
        # 두 줄 말 자막: 서버 셈(ep_room_fit)대로 줄어든 크기 · 미리보기 글 상자 위 끝이 영상 창 아래 · 흰 글씨 자리가 미리보기 = 내보내기 · 창 안(미리보기는 빈 창)에 흰 글씨 없음
        wb = editor.ep_win_bottom({"format": "shorts", "layout": q0["lay"]}, 1080, 1920)
        kf = min(editor.cap_fit(LONG, cst, 1080), editor.ep_room_fit(LONG, cst, 1080, 1920, wb))
        line_top = int((editor.EP_LINK_LOGO_Y - editor.TXT_LINE * editor.EP_LINK["size"] / 1920) * Hh) - 4

        def ink(img, r0):
            m = img[r0:line_top].min(axis=2) > 225
            return np.where(m.sum(axis=1) > 3)[0] + r0
        ip, ie = ink(pvi, wy0), ink(a, wy1)
        cap_ok = (cap_id and cdom and cdom["text"] == LONG and kf < 1 and abs(cdom["fs"] - cst["size"] * kf) < 0.6 and cdom["top"] >= wb - 1
                  and len(ip) > 20 and ip.min() >= wy1 and len(ie) > 20 and ie.min() > wy1 + 2 and abs(int(ip.min()) - int(ie.min())) <= 6
                  and abs(int(ip.max()) - int(ie.max())) <= 6 and len(np.split(ie, np.where(np.diff(ie) > 2)[0] + 1)) >= 2)
        check("두 줄 말 자막(강좌 줄 + 로고): 글자를 줄여 미리보기·내보내기 모두 영상 창 아래 · 같은 크기·자리", cap_ok,
              f"배율 {kf} · 미리보기 글자 {cdom and round(cdom['fs'], 1)} · 글 상자 위 {cdom and round(cdom['top'], 1)} / 창 아래 {wb:.1f} · "
              f"흰 글씨 줄 미리보기 {ip.min() if len(ip) else None}~{ip.max() if len(ip) else None} · 내보내기 {ie.min() if len(ie) else None}~{ie.max() if len(ie) else None} · 창 {wy0}~{wy1}")

        # ---- 4. 다른 템플릿은 그림을 지움 · 다시 은평 스타일 ----
        ev("commit('템플릿'); TEMPLATES['위아래 검정 박스'](); changed(false)")
        W(1200)
        g1 = ev("({lay: S.layout, shown: [...document.querySelectorAll('.lbp')].some(p => p.style.display !== 'none' && p.closest('.lay').style.display !== 'none')})")
        ev("commit('템플릿'); TEMPLATES['은평 스타일'](); changed(false)")
        W(1200)
        g2 = ev("({lay: S.layout, shown: [...document.querySelectorAll('.lbp')].some(p => p.style.display !== 'none' && p.closest('.lay').style.display !== 'none')})")
        pg.locator("#frame").screenshot(path=str(SHOTS / "e2_template_again.png"))
        check("다른 템플릿 → 띠 그림·로고 없음 · 다시 '은평 스타일' → 스타일 화면 설정대로 다시",
              "band" not in g1["lay"] and "logo" not in g1["lay"] and not g1["shown"] and g2["lay"].get("band") == "band.png" and g2["lay"].get("logo") == "logo.png"
              and g2["shown"], f"{g1} {g2}")
        # 검토: 띠 그림이 있으면 '박스 색'을 바꿔도 띠가 안 바뀌므로 그 옆에 안내 (그림이 없으면 안내 없음)
        hint_on = ev("(() => { renderSeqTab(); const e = document.getElementById('pBarPic'); return !!(e && document.getElementById('pBar') && e.textContent.includes('띠에 그림 깔기')); })()")
        ev("commit('템플릿'); TEMPLATES['위아래 검정 박스'](); changed(false)")
        W(600)
        hint_off = ev("(() => { renderSeqTab(); return !document.getElementById('pBarPic') && !!document.getElementById('pBar'); })()")
        ev("commit('템플릿'); TEMPLATES['은평 스타일'](); changed(false)")
        W(600)
        check("편집실 '박스 색' 옆 안내: 띠 그림이 있을 때만 ('띠에 그림 깔기'를 끄면 보임)", hint_on and hint_off, f"{hint_on} {hint_off}")
        ev("doSave && doSave()")
        W(1500)

        # ---- 5. 로고를 끄면 템플릿도 로고 없이 (편집실을 다시 열 때 설정을 읽음) ----
        api("/api/style/card_pics", {"logo": False})
        pg.goto(f"http://127.0.0.1:{PORT}/editor?name={urllib.request.quote(N1)}")
        pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=300000)
        W(1500)
        ev(f"switchSeq('{q0['id']}')")
        W(800)
        ev("commit('템플릿'); TEMPLATES['은평 스타일'](); changed(false)")
        W(1500)
        g3 = ev("({lay: S.layout, line: (S.titles.find(t => t.plan === 'longline') || {style: {}}).style.y, cap: S.captionStyle.y, "
                "logo: [...document.querySelectorAll('.lbp img')].some(i => i.style.display === 'block' && i.closest('.lay').style.display !== 'none')})")
        pg.locator("#frame").screenshot(path=str(SHOTS / "e3_logo_off.png"))
        check("로고를 끄고 템플릿 '은평 스타일' → 띠 그림만 · 로고 없음 · 강좌 줄은 예전 자리(0.865)",
              g3["lay"].get("band") == "band.png" and "logo" not in g3["lay"] and not g3["logo"] and g3["line"] == 0.865, json.dumps(g3))
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
