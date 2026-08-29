/** 화면을 실제로 띄워 PNG 로 남긴다.
 *  차트는 캔버스라 DOM 스냅샷으로는 아무것도 못 본다 - 눈으로 볼 그림이 필요하다.
 *  쓰기 전에 uvicorn(8000) 과 vite(5173) 가 떠 있어야 한다. */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const URL = process.env.SHOT_URL ?? "http://localhost:5173/";
const OUT = "../screenshots";

const SHOTS = [
  // 확대해도 세 패널이 같은 구간을 보는지. 이건 그림으로만 확인된다.
  { name: "zoom", width: 1600, height: 950, indicators: [], timeframe: "1d", zoom: 6 },
  { name: "wide", width: 1600, height: 950, indicators: [] },
  { name: "narrow", width: 1280, height: 860, indicators: [] },
  // 일목은 선행스팬이 마지막 봉보다 26봉 앞에 찍혀야 한다. 그건 그림으로만 확인된다.
  { name: "ichimoku", width: 1600, height: 950, indicators: ["일목균형표"], solo: true },
  { name: "levels", width: 1600, height: 950, indicators: ["피보나치 되돌림", "피벗 포인트"], solo: true },
  // 질문 → 예측 경로가 차트에 실제로 그려지는지. 이건 그림으로만 확인된다.
  { name: "predict", width: 1600, height: 950, indicators: [], solo: true,
    ask: "급락 나온 뒤 3일 동안 어떻게 움직였어?" },
  { name: "research", width: 1600, height: 950, indicators: [], tab: "근거" },
  // 판단 탭도 폭이 먼저 오는지. 순서는 그림으로만 확인된다.
  { name: "verdict", width: 1600, height: 950, indicators: [], tab: "판단",
    timeframe: "1d", waitFor: "text=예상 변동 폭" },
  // 학습 성적표는 "이 도구가 자기 한계를 말하는가" 를 보는 화면이다.
  { name: "learn", width: 1600, height: 950, indicators: [], tab: "학습",
    timeframe: "1d", waitFor: "text=봉 뒤 변동 폭" },
  // 추천 목록은 "순위를 오를 순서로 읽게 만드는가" 를 보는 화면이다. 문구가
  // 순위 바로 위에 붙어 있는지는 그림으로만 확인된다.
  { name: "screen", width: 1600, height: 950, indicators: [], tab: "추천",
    timeframe: "1d", waitFor: "text=사라 ·" },
  // 종목 고르기. 4,300종목을 그리면 브라우저가 멎으므로 잘라 놓았는데, 실제로
  // 잘렸는지와 한글 종목명이 읽히는지는 그림으로만 확인된다.
  { name: "symbols", width: 1600, height: 950, indicators: [], picker: "삼성" },
];

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();

for (const shot of SHOTS) {
  const page = await browser.newPage({
    viewport: { width: shot.width, height: shot.height },
    deviceScaleFactor: 1,
  });
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".pane.main canvas", { timeout: 20000 });
  if (shot.timeframe) {
    await page.locator(".topbar button", { hasText: new RegExp(`^${shot.timeframe}$`) })
      .first().click();
    await page.waitForTimeout(2500);
  }
  // 첫 스냅샷이 와서 값이 찍힐 때까지. 실시간이라 고정 대기로는 못 맞춘다.
  // 탭에 따라 사이드 내용이 달라지므로 항상 있는 상단 가격을 본다.
  await page.waitForFunction(
    () => (document.querySelectorAll(".topbar b").length > 0),
    { timeout: 30000 },
  );

  if (shot.tab) {
    await page.locator(".tabs button", { hasText: shot.tab }).first().click();
  }
  if (shot.indicators.length || shot.solo) {
    await page.locator(".tabs button", { hasText: "지표" }).first().click();
  }
  if (shot.solo) {
    // 기본으로 켜져 있는 것들을 끄고 볼 것만 남긴다.
    const on = page.locator(".indicator-row input:checked");
    for (let i = (await on.count()) - 1; i >= 0; i -= 1) await on.nth(i).uncheck();
  }
  for (const label of shot.indicators) {
    await page.locator(".indicator-row", { hasText: label }).first().locator("input").check();
  }
  if (shot.ask) {
    await page.locator(".tabs button", { hasText: "예측" }).first().click();
    await page.fill(".ask-form input", shot.ask);
    await page.locator(".ask-form button").click();
    // 유사구간 검색은 몇 초 걸린다. 답이 뜰 때까지 기다린다.
    await page.waitForSelector(".answer", { timeout: 90000 });
    await page.waitForTimeout(1200);
  }

  if (shot.waitFor) {
    // 학습 예측은 모델을 읽고 피처를 만드느라 10초 넘게 걸린다. 안 기다리면
    // 카드가 없는 그림을 찍어 놓고 "안 나온다"고 오해하게 된다.
    await page.locator(shot.waitFor).first()
      .waitFor({ timeout: 60000 }).catch(() => {});
  }

  if (shot.zoom) {
    // 메인 차트를 확대한다. RSI·MACD 가 같은 구간으로 따라오는지는 그림으로만 보인다.
    const box = await page.locator(".pane.main").boundingBox();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height / 2);
    for (let i = 0; i < shot.zoom; i += 1) {
      await page.mouse.wheel(0, -240);
      await page.waitForTimeout(120);
    }
    await page.waitForTimeout(1200);
    // 세 패널이 실제로 같은 구간을 보고 있는지 숫자로도 남긴다.
    const axes = await page.$$eval(".pane", (panes) =>
      panes.map((p) => {
        const label = p.querySelector(".pane-label")?.textContent ?? "가격";
        const ticks = Array.from(p.querySelectorAll("td, .tv-lightweight-charts *"))
          .map((n) => n.textContent ?? "").filter((t) => /\d/.test(t));
        return { label, first: ticks[0] ?? "", last: ticks.at(-1) ?? "" };
      }),
    );
    console.log("   축:", JSON.stringify(axes));
  }

  if (shot.picker) {
    // 헤더 종목 버튼 → 전체화면 목록. 국내주식 목록은 첫 적재가 오래 걸린다.
    await page.locator(".topbar button", { hasText: "찾기" }).first().click();
    await page.waitForSelector(".picker", { timeout: 10000 });
    await page.locator(".picker-markets .picker-row", { hasText: "국내주식" })
      .first().click();
    await page.waitForTimeout(9000);
    await page.fill(".picker-search input", shot.picker);
    await page.waitForTimeout(3500);
  }

  await page.waitForTimeout(1800);

  await page.screenshot({ path: `${OUT}/chart-${shot.name}.png` });
  console.log(
    `${OUT}/chart-${shot.name}.png`,
    errors.length ? `errors: ${errors.join(" | ")}` : "ok",
  );
  await page.close();
}

await browser.close();
