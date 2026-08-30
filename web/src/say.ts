/**
 * 숫자를 말로 옮기는 **한 곳**.
 *
 * 이 도구를 쓰는 사람이 분위수·적중률·skill 을 공부했을 이유가 없다. 화면이
 * `+0.17% · −3.7%~+4.3% · 오를 확률 52%` 만 내놓으면, 읽는 사람은 그게 좋다는
 * 뜻인지 나쁘다는 뜻인지 알 수가 없다. **의사가 환자에게 말하듯** 먼저 문장으로
 * 말하고, 숫자는 `숫자 보기` 로 접어 둔다.
 *
 * ## 규칙 셋
 *
 * 1. **문구를 화면마다 적지 않는다.** 같은 상황이 추천에서 "거의 안 움직인다",
 *    판단에서 "잠잠하다" 로 읽히면 두 화면이 다른 말을 하는 것처럼 보인다.
 *    여기가 한 벌이고 화면들은 가져다 쓴다.
 * 2. **숫자를 지우지 않는다.** 접을 뿐이다. "믿지 마세요" 만 적고 근거를 감추면
 *    읽는 사람이 얼마나 안 믿어야 하는지 모른다 — 그건 더 나쁜 화면이다.
 * 3. **명령형을 쓰지 않는다.** "사세요" 가 아니라 "이렇게 보입니다" 다. 방향
 *    적중이 동전던지기를 겨우 넘는 모델로 명령을 하면 그 사실을 숨기는 화면이 된다.
 *    `alerts` 가 같은 규칙을 지키고 `test_alerts.py` 가 금지어를 잡는다.
 */

/** 비율을 `열에 여덟` 로. 퍼센트를 안 읽는 사람에게는 이쪽이 훨씬 빨리 들어온다. */
export function outOfTen(ratio: number | null | undefined): string | null {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return null;
  const n = Math.round(ratio * 10);
  const words = ["한 번도 못", "열에 하나", "열에 둘", "열에 셋", "열에 넷", "열에 다섯",
                 "열에 여섯", "열에 일곱", "열에 여덟", "열에 아홉", "거의 다"];
  return words[Math.max(0, Math.min(10, n))];
}

/**
 * 얼마나 움직일까. **이 도구가 실제로 맞히는 쪽**이라 제일 앞에 온다.
 *
 * `confidence` 는 그날 그 시장 안에서 `move_atr` 을 3분위로 가른 라벨이다 —
 * 화면에서 지어낸 문턱이 아니라 서버가 잰 것이다.
 */
export function moveWords(confidence?: string | null, band?: [number, number] | null): string {
  const word = confidence === "high" ? "평소보다 크게"
    : confidence === "low" ? "거의 안"
    : "평소만큼";
  // **얼마나인지 같이 적는다.** "크게" 만으로는 3% 인지 30% 인지 알 수 없다.
  // 밴드 반폭이 "대개 이만큼" 이다.
  const half = band && band.length === 2 ? (band[1] - band[0]) / 2 : null;
  const size = half !== null && Number.isFinite(half) ? ` (대개 ±${half.toFixed(1)}%)` : "";
  return `${word} 움직일 것 같습니다${size}`;
}

/** 어느 쪽으로. **이 도구가 약한 쪽**이라 늘 뒤에 오고 단정하지 않는다.
 *  확률도 같이 적는다 — "기울었다" 만으로는 얼마나 기울었는지 알 수 없다. */
export function directionWords(probUp?: number | null): string {
  if (probUp === null || probUp === undefined || !Number.isFinite(probUp)) {
    return "어느 쪽인지는 말하지 않았습니다";
  }
  const size = ` (오를 확률 ${Math.round(probUp * 100)}%)`;
  if (probUp >= 0.58) return `오르는 쪽으로 기울었습니다${size}`;
  if (probUp >= 0.53) return `오르는 쪽으로 조금 기울었습니다${size}`;
  if (probUp <= 0.42) return `내리는 쪽으로 기울었습니다${size}`;
  if (probUp <= 0.47) return `내리는 쪽으로 조금 기울었습니다${size}`;
  return `오를지 내릴지는 반반에 가깝습니다${size}`;
}

/**
 * 이 묶음 전체에 대한 한 문장. **줄마다 되풀이하지 않으려고 여기 모았다.**
 *
 * 예전에 이 말을 행마다 적었더니 여섯 줄이 "후보들이 거의 붙어 있습니다. 오를지
 * 내릴지는 반반에 가깝습니다." 로 똑같아졌다. 그러면 정작 줄마다 다른 것(얼마나
 * 움직일까)이 안 읽힌다. **묶음 공통은 위에 한 번, 줄에는 그 종목만의 것.**
 *
 * `spread` 는 1위와 꼴찌의 기대 차이(%p), `probs` 는 후보들의 오를 확률이다.
 */
export function groupWords(spread: number | null, probs: (number | null | undefined)[]): string {
  const tight = spread !== null && Number.isFinite(spread) && spread < 1.0;
  const sure = probs.filter((p): p is number =>
    p !== null && p !== undefined && Number.isFinite(p) && (p >= 0.55 || p <= 0.45));
  const rank = tight
    ? "후보들의 기대가 거의 붙어 있어 순위 차이는 크지 않습니다"
    : "후보들 사이에 기대 차이가 제법 있습니다";
  const way = sure.length === 0
    ? "오를지 내릴지는 대체로 반반입니다"
    : `${sure.length}종목만 한쪽으로 기울었습니다`;
  return `${rank}. ${way}.`;
}

/** 사지 말 것 목록의 뜻. **목록 위에 한 번만** 적는다. 공매도 신호로 읽히면 안 된다. */
export const avoidWords = "이 묶음에서 기대가 제일 낮았던 셋입니다. 내린다는 뜻이 아니라 꼴찌라는 뜻입니다.";

/**
 * 되돌려 본 성적을 한 문장으로.
 *
 * `edge` 는 추천 셋의 평균에서 후보 전체 평균을 뺀 값이다. **0 과 견주지 않는다** —
 * 시장이 다 오른 날 추천도 올랐다는 건 아무 말도 아니기 때문이다.
 */
export function edgeWords(edge?: number | null): string {
  if (edge === null || edge === undefined || !Number.isFinite(edge)) {
    return "아직 잴 만큼 쌓이지 않았습니다";
  }
  // **짧게 끝낸다.** "판이 적다" 같은 단서는 묶음마다 같은 말이라 목록 밑에 한 번만
  // 쓴다 — 줄마다 붙이면 세 줄이 통째로 같아져 정작 다른 부분이 안 읽힌다.
  if (edge > 0.3) return "그냥 다 산 것보다 나았습니다";
  if (edge < -0.3) return "그냥 다 산 것보다 못했습니다";
  return "그냥 다 산 것과 별 차이가 없었습니다";
}

/** 판이 모자랄 때 붙이는 단서. **목록 밑에 한 번만.** */
export function fewHandsWords(hands: (number | null | undefined)[]): string | null {
  const most = Math.max(0, ...hands.map((n) => n ?? 0));
  return most < 30 ? "아직 판이 적어 어느 쪽으로도 단정할 수는 없습니다." : null;
}

/** 밴드가 얼마나 맞았나. 이 도구가 잘하는 쪽이라 성적표에서 살려 준다. */
export function bandWords(hit?: number | null): string | null {
  const words = outOfTen(hit);
  if (!words) return null;
  return `"이 범위 안에서 움직인다" 는 ${words} 맞혔습니다`;
}

/**
 * 종합 판단의 신뢰도를 말로. **여기가 제일 헷갈리는 자리다.**
 *
 * `signals/engine.py` 의 주석 그대로 — 관망일 때의 신뢰도는 "관망이라는 판단"의
 * 신뢰도가 아니라 **쏠림의 크기**다. 그래서 `관망 · 신뢰도 5%` 는 "관망을 5%만
 * 확신한다" 가 아니라 "규칙들이 거의 완전히 상쇄됐다" 는 뜻인데, 숫자로만 내놓으면
 * 정반대로 읽힌다. 실제로 그렇게 읽혔다.
 */
export function verdictWords(direction: number, confidence: number): string {
  // 쏠림의 크기를 같이 적는다. 다만 **말이 먼저** 와야 한다 — 숫자만 있으면
  // `관망 · 5%` 가 "관망을 5%만 확신한다" 로 정반대로 읽힌다.
  const size = ` (쏠림 ${Math.round(confidence * 100)}%, 문턱 24%)`;
  if (direction === 0) {
    return (confidence < 0.2
      ? "규칙들이 서로 엇갈려 어느 쪽도 아니라고 봤습니다"
      : "한쪽으로 기울긴 했지만 판단을 내릴 만큼은 아니었습니다") + size;
  }
  const side = direction > 0 ? "사는" : "파는";
  const strength = confidence >= 0.7 ? `규칙 대부분이 ${side} 쪽을 가리켰습니다`
    : confidence >= 0.4 ? `규칙 다수가 ${side} 쪽을 가리켰습니다`
    : `${side} 쪽이 조금 우세했지만 아슬아슬했습니다`;
  return strength + size;
}

/** 아침 추천이 이 종목을 지평별로 어디에 뒀나. `GET /api/recommend/symbol` 이 준다. */
export interface SymbolPlan {
  available: boolean;
  date?: string;
  name?: string | null;
  days?: Record<string, {
    expected: number | null; band: [number, number] | null;
    probUp: number | null; confidence: string | null;
    side: "buy" | "avoid" | "mid";
  }>;
}

const SIDE: Record<string, string> = { buy: "살 만한 셋", avoid: "사지 말 것 셋", mid: "중간" };

/**
 * **"언제" 에 답한다.** 추천과 판단이 어긋나 보이는 가장 흔한 이유가 지평이다.
 *
 * 아침 추천은 일봉으로 1·2·3일을 **각각** 봤고 그 답이 서로 다를 수 있다 —
 * 실제로 삼성바이오로직스가 하루 뒤에는 `사라`, 이틀 뒤에는 `사지 말 것` 이었다.
 * 그걸 안 보여 주면 "추천은 사라는데 판단은 팔라네" 로만 읽힌다.
 *
 * **여기서 사라고 하지 않는다.** 며칠 뒤를 어떻게 봤는지를 적을 뿐이다 — 방향
 * 적중이 반반을 겨우 넘는 모델로 시점을 찍어 주면 그 사실을 숨기는 화면이 된다.
 */
export function whenWords(plan?: SymbolPlan | null): string[] {
  if (!plan?.available || !plan.days) return [];
  const rows = Object.entries(plan.days)
    .map(([key, v]) => ({ day: Number(key), ...v }))
    .filter((r) => Number.isFinite(r.day))
    .sort((a, b) => a.day - b.day);
  if (rows.length === 0) return [];

  const out = [
    `아침 추천은 이 종목을 ${rows.map((r) => `${r.day}일 뒤 ${SIDE[r.side] ?? "중간"}`).join(", ")}에 뒀습니다.`,
  ];

  const sides = new Set(rows.map((r) => r.side));
  const buys = rows.filter((r) => r.side === "buy");

  if (buys.length > 0 && sides.size > 1) {
    // **`살 만한 셋` 에 든 날만 그렇게 부른다.** 셋 다 아닌데 "제일 좋게 봤다" 고
    // 쓰면 없던 매수 신호가 생긴다.
    out.push(
      `**며칠을 보느냐에 따라 답이 다릅니다.** ${buys.map((r) => `${r.day}일 뒤`).join("·")}로 볼 때만 ` +
      "살 만한 쪽에 들었습니다 — 어느 쪽을 따를지는 얼마나 들고 있을 생각인지에 달렸습니다.",
    );
  } else if (sides.size > 1) {
    out.push(
      "**며칠을 보느냐에 따라 답이 다릅니다.** 다만 어느 지평으로도 살 만한 셋에는 " +
      "들지 않았습니다.",
    );
  } else if (rows.every((r) => r.side === "buy")) {
    out.push("1·2·3일 어느 쪽으로 봐도 살 만한 쪽이었습니다.");
  } else if (rows.every((r) => r.side === "avoid")) {
    out.push("1·2·3일 어느 쪽으로 봐도 그 묶음에서 기대가 낮은 쪽이었습니다.");
  }
  return out;
}

/**
 * 이 도구를 얼마나 믿을 것인가 — 한 문장.
 *
 * **숫자를 여기 박지 않는다.** `api/recommend.py: measured()` 가 학습 실행에서
 * 읽어 넘긴 값을 받아 말로 옮길 뿐이다. 박아 두면 학습이 다시 돌 때 화면만 옛말을 한다.
 */
export function trustWords(measured?: { directionHit?: number; bandHit?: number }): string | null {
  if (!measured) return null;
  const band = outOfTen(measured.bandHit);
  const way = measured.directionHit;
  if (!band || way === undefined) return null;
  const direction = way >= 0.6 ? "제법 맞히고"
    : way >= 0.53 ? "반반을 조금 넘는 정도고"
    : "반반에 가깝고";
  return `이 도구는 "얼마나 움직일까" 를 ${band} 맞힙니다. "오를까 내릴까" 는 ${direction}, 그래서 폭을 먼저 읽는 게 맞습니다.`;
}
