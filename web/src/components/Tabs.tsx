import { useEffect, useState } from "react";

/**
 * 카드 묶음을 고르는 **안쪽 탭**.
 *
 * 오른쪽 띠는 320px 한 칸이라, 나란히 놓을 수 없는 것은 전부 세로로 쌓인다.
 * 추천 탭이 국내주식·해외주식·코인을 한 번에 내면서 열여덟 줄이 됐고, 셋째 묶음을
 * 보려면 스크롤을 한참 내려야 했다.
 *
 * 셋을 다 내는 데는 뜻이 있었다 — 하나만 내면 나머지 둘이 **있다는 것조차** 모른다
 * (예전에 차트가 선 시장 하나만 보여줬고, 기본이 BTCUSDT 라 열면 늘 코인만 나왔다).
 * 그런데 그 뜻은 **이름이 늘 보이는 것**에 있지 목록이 다 펼쳐져 있는 데 있지 않다.
 * 그래서 이름은 셋 다 두고 목록만 고른 하나를 낸다.
 *
 * **빠진 것도 자리를 지킨다.** 못 돈 시장은 흐리게 두되 지우지 않고, 누르면 왜
 * 없는지가 나온다. 목록에서 빼 버리면 "국내주식은 살 게 없다"로 읽힌다.
 *
 * 생김새는 이미 쓰던 `.chips`/`.chip` 그대로다 — 지평 칩(하루 뒤·이틀 뒤)과 보유의
 * 들고 있는 것/닫은 판이 같은 모양이었으므로, 새 모양을 만들면 한 화면에 비슷하지만
 * 다른 것이 둘 생긴다.
 */
export interface TabItem {
  key: string;
  label: string;
  /** 지금은 볼 게 없는 칸. 흐리게 두되 **지우지 않는다.** */
  muted?: boolean;
}

export function CardTabs({ items, active, onPick, label }: {
  items: TabItem[];
  active: string;
  onPick: (key: string) => void;
  /** 스크린리더가 읽는 이름. "무엇을 고르는 탭인가". */
  label: string;
}) {
  // 칸이 하나면 고를 것이 없다. 그때 탭을 그리면 안 눌리는 버튼 하나가 남는다.
  if (items.length < 2) return null;
  return (
    <div className="chips" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          className="chip"
          data-active={active === item.key}
          data-muted={item.muted ? "true" : undefined}
          aria-selected={active === item.key}
          onClick={() => onPick(item.key)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

/**
 * 고른 칸을 들고 있는다. **목록이 바뀌면 고른 것이 사라질 수 있다** — 자료가 늦게
 * 와서 칸이 나중에 생기거나, 지평을 바꿔 없던 묶음이 생기는 경우다. 그때 그대로 두면
 * 아무 카드도 안 나오는 빈 화면이 되므로 첫 칸으로 되돌린다.
 */
export function useCardTab(keys: string[], preferred?: string): [string, (key: string) => void] {
  const [active, setActive] = useState(preferred ?? keys[0] ?? "");
  const fingerprint = keys.join("|");
  useEffect(() => {
    if (keys.length && !keys.includes(active)) setActive(preferred ?? keys[0]);
    // 목록이 실제로 바뀔 때만. 배열을 그대로 두면 렌더마다 새 배열이라 매번 돈다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fingerprint, preferred]);
  return [active, setActive];
}
