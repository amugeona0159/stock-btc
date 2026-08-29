"""웹푸시. 앱이 닫혀 있어도 폰에 뜬다.

iOS 는 **홈 화면에 추가한 경우에만** 웹푸시를 준다(16.4+). 사파리 탭으로 열어 두면
권한 요청 자체가 안 뜬다 — 그래서 화면에 안내가 필요하다.

`pywebpush` 가 없으면 **앱은 그대로 뜨고 알림만 기록에 남는다.** 알림 하나 때문에
서버가 안 뜨면 나머지 기능까지 같이 죽는다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from . import store

log = logging.getLogger("marketlens.alerts")


def keys() -> tuple[str, str, str] | None:
    """(공개키, 개인키, 연락처). 하나라도 없으면 푸시를 끈다."""
    public = (os.environ.get("VAPID_PUBLIC_KEY") or "").strip()
    private = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
    contact = (os.environ.get("VAPID_CONTACT") or "mailto:owner@example.com").strip()
    return (public, private, contact) if public and private else None


def available() -> bool:
    if keys() is None:
        return False
    try:
        import pywebpush                                   # noqa: F401
    except ImportError:
        return False
    return True


def _send_one(subscription: dict, payload: str, private: str, contact: str) -> bool:
    from pywebpush import WebPushException, webpush

    try:
        webpush(subscription_info=subscription, data=payload,
                vapid_private_key=private, vapid_claims={"sub": contact}, timeout=10)
        return True
    except WebPushException as exc:
        code = getattr(exc.response, "status_code", None)
        # 404·410 은 그 구독이 죽었다는 뜻이다. 지워야 다음부터 안 시도한다.
        if code in (404, 410):
            store.unsubscribe(subscription.get("endpoint", ""))
            log.info("죽은 구독을 지웠다 (%s)", code)
        else:
            log.warning("푸시 실패 (%s) %s", code, str(exc)[:120])
        return False


async def send(entry: dict) -> int:
    """모든 구독에 보낸다. 돌려주는 것은 성공한 개수.

    구독마다 네트워크를 타므로 스레드로 뺀다 — 하나가 느려도 감시 루프가 안 멈춘다.
    """
    found = keys()
    if found is None or not available():
        return 0
    _public, private, contact = found

    targets = store.subscriptions()
    if not targets:
        return 0

    payload = json.dumps({
        "title": entry.get("title", "market-lens"),
        "body": entry.get("body", ""),
        "id": entry.get("id"),
        "symbol": entry.get("symbol"),
    }, ensure_ascii=False)

    results = await asyncio.gather(*[
        asyncio.to_thread(_send_one, target, payload, private, contact)
        for target in targets
    ], return_exceptions=True)
    return sum(1 for r in results if r is True)


def public_key() -> str:
    found = keys()
    return found[0] if found else ""
