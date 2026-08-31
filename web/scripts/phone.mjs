/** 폰에서 가로로 넘치지 않는지. **그림이 아니라 숫자로 답한다.**
 *
 *  넘침은 눈으로 잘 안 잡힌다 — 페이지가 조금 밀린 것과 원래 그런 것을 구별할 수가
 *  없어서, 스크린샷만으로는 몇 달을 지나친다. 실제로 보유 탭이 390px 화면에서 87px
 *  밀려 있었고 아무도 못 봤다(격자 칸의 기본값 `min-width: auto` 가 캔버스의 고유
 *  폭만큼 칸을 늘린 것이었다).
 *
 *  **한 폭만 보면 안 된다.** 어디로 틀어졌는지는 폭마다 다르게 나타나고, 가로 모드는
 *  아예 다른 배치다. 탭마다도 따로 본다 — 차트 폭이 탭에 따라 달라지므로 한 탭이
 *  멀쩡해도 다른 탭이 밀릴 수 있다.
 *
 *      cd web; npm run phone      # uvicorn(8000) 과 vite(5173) 가 떠 있어야 한다
 */
import { chromium } from "playwright";
const URL = "http://localhost:5173/";
const SIZES = [
  { name: "SE", w: 375, h: 667 },
  { name: "12", w: 390, h: 664 },
  { name: "12Pro", w: 390, h: 664 },
  { name: "12ProMax", w: 428, h: 746 },
  { name: "landscape", w: 844, h: 390 },
];
const TABS = ["추천", "알림", "학습", "근거", "보유"];
const browser = await chromium.launch();
for (const s of SIZES) {
  const page = await browser.newPage({ viewport: { width: s.w, height: s.h } });
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelectorAll(".topbar b").length > 0,
                             { timeout: 30000 }).catch(() => {});
  for (const tab of TABS) {
    await page.locator(".tabs button", { hasText: new RegExp(`^${tab}$`) }).first().click();
    await page.waitForTimeout(1200);
    const over = await page.evaluate(() => {
      const d = document.documentElement;
      const wide = [...document.querySelectorAll(".side *")]
        .filter((el) => el.scrollWidth > el.clientWidth + 1 &&
                        getComputedStyle(el).overflowX === "visible")
        .map((el) => el.className + " " + el.scrollWidth + ">" + el.clientWidth);
      return { page: d.scrollWidth - d.clientWidth, wide: wide.slice(0, 3) };
    });
    console.log(`${s.name} ${s.w}x${s.h} ${tab}: 넘침 ${over.page}px`,
                over.wide.length ? JSON.stringify(over.wide) : "");
  }
  await page.close();
}
await browser.close();
