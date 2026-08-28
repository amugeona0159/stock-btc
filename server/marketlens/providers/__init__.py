"""프로바이더 등록 지점.

import 하는 순서가 곧 화면에 뜨는 순서다. 키 없이 바로 도는 것부터 올린다 --
처음 여는 사람이 아무 설정 없이 실시간 차트를 보게 하려는 것이다.
"""
from __future__ import annotations

from .base import (Provider, ProviderError, ProviderInfo, ProviderUnavailable,
                   all_providers, describe, get, register)

from . import binance as _binance  # noqa: F401
from . import upbit as _upbit  # noqa: F401
from . import composite as _composite  # noqa: F401  (미국주식 = Stooq + Finnhub)
from . import kis as _kis  # noqa: F401
from . import csv_file as _csv  # noqa: F401

__all__ = ["Provider", "ProviderInfo", "ProviderError", "ProviderUnavailable",
           "all_providers", "describe", "get", "register"]
