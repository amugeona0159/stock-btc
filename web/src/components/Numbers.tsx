import type { ReactNode } from "react";

/**
 * `▸ 숫자 보기` — 접어 둔 숫자.
 *
 * 화면은 **말로 먼저** 설명하고 숫자는 여기 넣는다. 지우지 않는 이유는
 * `say.ts` 머리말에 적은 그대로다 — "믿지 마세요" 만 적고 근거를 감추면 읽는
 * 사람이 얼마나 안 믿어야 하는지 알 수가 없다.
 *
 * **한 벌로 둔다.** 화면마다 `<details>` 를 따로 쓰면 여는 글자도 여백도 조금씩
 * 달라지고, 그게 쌓이면 같은 앱이 아닌 것처럼 보인다.
 */
export function Numbers({ children, label = "숫자 보기" }: {
  children: ReactNode;
  label?: string;
}) {
  return (
    <details className="numbers">
      <summary>{label}</summary>
      <div>{children}</div>
    </details>
  );
}
