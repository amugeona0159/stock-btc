/** 화면을 실제로 띄워 PNG 로 남긴다.
 *  차트는 캔버스라 DOM 스냅샷으로는 아무것도 못 본다 - 눈으로 볼 그림이 필요하다.
 *  쓰기 전에 uvicorn(8000) 과 vite(5173) 가 떠 있어야 한다. */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const URL = process.env.SHOT_URL ?? "http://localhost:5173/";
const OUT = "../screenshots";

const SHOTS = [
  { name: "wide", width: 1600, height: 950, indicators: [] },
  { name: "narrow", width: 1280, height: 860, indicators: [] },
  // 일목은 선행스팬이 마지막 봉보다 26봉 앞에 찍혀야 한다. 그건 그림으로만 확인된다.
  { name: "ichimoku", width: 1600, height: 950, indicators: ["일목균형표"], solo: true },
  { name: "levels", width: 1600, height: 950, indicators: ["피보나치 되돌림", "피벗 포인트"], solo: true },
  // 질문 → 예측 경로가 차트에 실제로 그려지는지. 이건 그림으로만 확인된다.
  { name: "predict", width: 1600, height: 950, indicators: [], solo: true,
    ask: "급락 나온 뒤 3일 동안 어떻게 움직였어?" },
  { name: "research", width: 1600, height: 950, indicators: [], tab: "근거" },
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

  await page.waitForTimeout(1800);

  await page.screenshot({ path: `${OUT}/chart-${shot.name}.png` });
  console.log(
    `${OUT}/chart-${shot.name}.png`,
    errors.length ? `errors: ${errors.join(" | ")}` : "ok",
  );
  await page.close();
}

await browser.close();
