"""HTTP 客户端断路器诊断测试：错误类型分类 + 原因统计 (离线测试)

运行:
    python tests/test_http_client.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from src.utils.http_client import CircuitBreaker, _classify_network_error


def test_classify_network_error():
    assert _classify_network_error(httpx.ConnectTimeout("x")) == "超时"
    assert _classify_network_error(httpx.ReadTimeout("x")) == "超时"
    assert _classify_network_error(httpx.ConnectError("x")) == "连接失败"
    assert _classify_network_error(httpx.ReadError("x")) == "读取中断"
    assert _classify_network_error(ConnectionError("x")) == "连接失败"
    assert _classify_network_error(httpx.RemoteProtocolError("x")) == "协议错误"


def test_circuit_breaker_tracks_failure_reasons():
    cb = CircuitBreaker(threshold=3, cooldown=60)
    cb.failure("h1", "429限流")
    cb.failure("h1", "429限流")
    assert not cb.is_open("h1")
    cb.failure("h1", "超时")
    assert cb.is_open("h1")
    # 原因分布被记录 (用于断路器开启时的诊断日志)
    assert cb._failure_reasons["h1"] == {"429限流": 2, "超时": 1}


def test_circuit_breaker_reset_clears_reasons():
    cb = CircuitBreaker(threshold=3, cooldown=60)
    cb.failure("h1", "超时")
    cb.failure("h1", "超时")
    cb.success("h1")
    assert "h1" not in cb._failure_reasons
    assert cb._failures.get("h1", 0) == 0


if __name__ == "__main__":
    tests = [
        test_classify_network_error,
        test_circuit_breaker_tracks_failure_reasons,
        test_circuit_breaker_reset_clears_reasons,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
