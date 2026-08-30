"""토큰 한 개로 문을 잠근다.

이 서버는 지금 이 PC 에서만 뜨고 라우트 스물다섯 개가 전부 열려 있다. 집 밖으로
내보내는 순간(공개 주소·LAN·터널) 그건 **누구나 쓰는 서버**가 된다 — 시세만 나가는 게
아니라 학습을 돌리는 `POST /api/train` 도, 토스 자격증명으로 부르는 국내주식도 같이
열린다. 그때 켜라고 있는 잠금이다.

## 규칙

- `MARKET_LENS_TOKEN` 이 **없으면 잠그지 않는다.** 집에서 개발할 때 매번 토큰을 넣게
  하면 그 불편이 결국 토큰을 지우게 만든다. 밖으로 낼 때만 켠다.
- 있으면 `/api/*` 와 `/ws` 전부에 요구한다.
- **`/api/health` 만 연다.** 밖에서 살아있는지 볼 수 있어야 하고, 여기서 나가는
  건 "떠 있다"와 지표 개수뿐이다.

## 토큰을 어디에 실어 보내나

브라우저의 WebSocket 은 **헤더를 못 붙인다.** 그래서 헤더와 쿼리 둘 다 받는다.
쿼리로 받는 건 주소창에 토큰이 남는다는 뜻이라 좋지 않지만, 웹소켓을 쓰려면 다른
방법이 없다 — 대신 **로그에 토큰이 찍히지 않게** 값을 비교만 하고 어디에도 안 적는다.

비교는 `secrets.compare_digest` 로 한다. `==` 는 앞에서부터 다르면 바로 끝나서
응답 시간으로 토큰을 한 글자씩 맞춰 볼 수 있다.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

# 이 경로만 토큰 없이 열린다.
OPEN_PATHS = frozenset({"/api/health"})
# 잠글 접두사. 나머지(정적 파일·SPA)는 토큰 없이 받아야 폰에서 화면이 뜬다 —
# 화면은 떠도 데이터가 안 나오면 토큰을 넣으라고 안내할 수 있다.
GUARDED = ("/api/", "/ws")


def configured() -> str | None:
    """설정된 토큰. 빈 문자열은 '설정 안 함' 으로 본다."""
    value = (os.environ.get("MARKET_LENS_TOKEN") or "").strip()
    return value or None


def presented(request: Request) -> str | None:
    """요청이 들고 온 토큰. 헤더 우선, 없으면 쿼리."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return (request.query_params.get("token") or "").strip() or None


def allowed(request: Request) -> bool:
    path = request.url.path
    if path in OPEN_PATHS or not path.startswith(GUARDED):
        return True
    expected = configured()
    if expected is None:
        return True                       # 집에서 개발 중 — 안 잠근다
    given = presented(request)
    return given is not None and secrets.compare_digest(given, expected)


async def guard(request: Request, call_next):
    """미들웨어. 막을 때는 **왜 막혔는지** 알려 준다.

    401 만 던지면 폰에서 화면이 비어 있는 이유를 알 수 없다.
    """
    if allowed(request):
        return await call_next(request)
    return JSONResponse(
        {"detail": "토큰이 필요하다. 앱 설정에서 넣거나 `Authorization: Bearer …` 로 보낼 것"},
        status_code=401,
    )
