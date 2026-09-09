from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from src.config import (
    HTTP_CONNECT_TIMEOUT,
    HTTP_READ_TIMEOUT,
    HTTP_WRITE_TIMEOUT,
    HTTP_MAX_RETRIES,
    HTTP_CIRCUIT_BREAKER_THRESHOLD,
    HTTP_CIRCUIT_BREAKER_COOLDOWN,
    HTTP_BACKOFF_BASE,
    HTTP_429_BACKOFF_BASE,
)

logger = logging.getLogger(__name__)


class CircuitBreakerOpenError(Exception):
    """断路器开启：目标主机暂时不可用"""


class CircuitBreaker:
    """基于滑动窗口的简单断路器

    按主机追踪连续失败次数：
    - 连续失败 ≥ threshold → 开启 (open)，拒绝所有请求 cooldown 秒
    - cooldown 过后第一个请求即探测 (half-open)
    - 探测成功 → 计数清零，关闭 (closed)
    - 探测失败 → 重新开启（新的 cooldown）
    """

    def __init__(self, threshold: int = 5, cooldown: float = 60.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        # 每个主机的失败原因分布 (用于断路器开启时区分 429/超时/连接失败)
        self._failure_reasons: dict[str, dict[str, int]] = {}

    def allow(self, host: str) -> bool:
        now = time.monotonic()
        open_until = self._open_until.get(host, 0)
        return now >= open_until

    def success(self, host: str):
        self._failures[host] = 0
        self._failure_reasons.pop(host, None)
        self._open_until.pop(host, None)

    def failure(self, host: str, reason: str = ""):
        count = self._failures.get(host, 0) + 1
        self._failures[host] = count
        if reason:
            reasons = self._failure_reasons.setdefault(host, {})
            reasons[reason] = reasons.get(reason, 0) + 1
        now = time.monotonic()
        was_open = self._open_until.get(host, 0) > now
        if count >= self.threshold:
            self._open_until[host] = now + self.cooldown
            if not was_open:
                dist = self._failure_reasons.get(host, {})
                dist_str = ", ".join(f"{k}:{v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])) if dist else reason or "未知"
                logger.warning(
                    f"断路器开启: {host} (连续失败 {count} 次, 原因: {dist_str}, 冷却 {self.cooldown}s)"
                )

    def is_open(self, host: str) -> bool:
        """断路器是否处于开启状态（冷却期内）"""
        return not self.allow(host)


def is_circuit_open(url: str) -> bool:
    """检查 URL 对应主机是否被断路器阻断"""
    return _breaker.is_open(_extract_host(url))


def circuit_remaining_cooldown(url: str) -> float:
    """返回断路器剩余冷却秒数; 未开启时返回 0"""
    host = _extract_host(url)
    if not _breaker.is_open(host):
        return 0.0
    return max(0.0, _breaker._open_until.get(host, 0) - time.monotonic())


_breaker = CircuitBreaker(
    threshold=HTTP_CIRCUIT_BREAKER_THRESHOLD,
    cooldown=HTTP_CIRCUIT_BREAKER_COOLDOWN,
)

_client: Optional[httpx.Client] = None
_client_lock = __import__("threading").Lock()


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        with _client_lock:
            if _client is None or _client.is_closed:
                limits = httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                )
                _client = httpx.Client(
                    limits=limits,
                    timeout=httpx.Timeout(
                        connect=HTTP_CONNECT_TIMEOUT,
                        read=HTTP_READ_TIMEOUT,
                        write=HTTP_WRITE_TIMEOUT,
                        pool=HTTP_CONNECT_TIMEOUT,
                    ),
                )
    return _client


def _extract_host(url: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(url)
    return p.hostname or url


def _classify_network_error(e: Exception) -> str:
    """把网络异常归类为可读的原因标签 (用于断路器开启时的诊断)。"""
    if isinstance(e, httpx.TimeoutException):
        return "超时"
    if isinstance(e, httpx.ConnectError):
        return "连接失败"
    if isinstance(e, httpx.ReadError):
        return "读取中断"
    if isinstance(e, httpx.RemoteProtocolError):
        return "协议错误"
    if isinstance(e, (ConnectionError, OSError)):
        return "连接失败"
    return "网络错误"


def get_with_retry(
    url: str,
    *,
    connect_timeout: float = None,
    read_timeout: float = None,
    write_timeout: float = None,
    max_retries: int = None,
    raise_on_status: bool = True,
    **kwargs,
) -> httpx.Response:
    """httpx.get 的增强版：连接池复用 + 自动重试 + 断路器 + 429 退避

    直接替换 httpx.get(url, **kwargs)，所有现有 headers/params/follow_redirects
    等参数直接透传。

    Extra kwargs (not passed to httpx):
        connect_timeout:  连接超时 (默认 HTTP_CONNECT_TIMEOUT)
        read_timeout:     读取超时 (默认 HTTP_READ_TIMEOUT)
        write_timeout:    写入超时 (默认 HTTP_WRITE_TIMEOUT)
        max_retries:      最大重试次数 (默认 HTTP_MAX_RETRIES)
        raise_on_status:  是否对 4xx/5xx 抛 HTTPStatusError (默认 True)。
                          设为 False 时可手动检查 status_code (如 404=DOI不存在)。
    """
    connect = connect_timeout if connect_timeout is not None else HTTP_CONNECT_TIMEOUT
    read = read_timeout if read_timeout is not None else HTTP_READ_TIMEOUT
    write = write_timeout if write_timeout is not None else HTTP_WRITE_TIMEOUT
    retries = max_retries if max_retries is not None else HTTP_MAX_RETRIES

    host = _extract_host(url)

    for attempt in range(retries):
        if not _breaker.allow(host):
            raise CircuitBreakerOpenError(
                f"断路器开启: {host}, {_breaker._open_until.get(host, 0) - time.monotonic():.0f}s 后恢复"
            )

        try:
            client = _get_client()
            resp = client.get(
                url,
                timeout=httpx.Timeout(connect, read=read, write=write, pool=connect),
                **kwargs,
            )
            if resp.status_code == 429:
                wait = HTTP_429_BACKOFF_BASE * (2 ** attempt)
                logger.debug(f"{host} 429, 第{attempt+1}/{retries}次退避 {wait:.0f}s")
                time.sleep(wait)
                _breaker.failure(host, "429限流")
                continue
            if raise_on_status:
                resp.raise_for_status()
            _breaker.success(host)
            return resp
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            # 429: 限流, 退避重试并计入断路器
            if status == 429 and attempt < retries - 1:
                wait = HTTP_429_BACKOFF_BASE * (2 ** attempt)
                time.sleep(wait)
                _breaker.failure(host, "429限流")
                continue
            # 4xx 客户端错误 (404=DOI 不存在等): 是确定性响应, 并非主机故障,
            # 不重试也不计入断路器 — 否则几个未收录的 DOI 就会误触发断路器
            if 400 <= status < 500:
                raise
            # 5xx 服务端错误: 计入断路器并重试
            _breaker.failure(host, f"5xx({status})")
            if attempt < retries - 1:
                wait = HTTP_BACKOFF_BASE * (2 ** attempt)
                time.sleep(wait)
                continue
            raise
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException,
                httpx.RemoteProtocolError, ConnectionError, OSError) as e:
            _breaker.failure(host, _classify_network_error(e))
            if attempt < retries - 1:
                wait = HTTP_BACKOFF_BASE * (2 ** attempt)
                logger.debug(f"{host} 网络错误第{attempt+1}/{retries}次: {e}, {wait:.0f}s后重试")
                time.sleep(wait)
                continue
            raise
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            _breaker.failure(host, "其他错误")
            if attempt < retries - 1:
                wait = HTTP_BACKOFF_BASE * (2 ** attempt)
                time.sleep(wait)
                continue
            raise

    raise RuntimeError(f"get_with_retry exhausted retries for {url}")


def head_with_retry(
    url: str,
    *,
    connect_timeout: float = 10.0,
    read_timeout: float = 15.0,
    max_retries: int = 2,
    **kwargs,
) -> httpx.Response:
    """httpx.head 的增强版"""
    return get_with_retry(
        url,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_retries=max_retries,
        method="HEAD",
        **kwargs,
    )


def reset_circuit_breaker():
    """重置断路器状态（用于测试）"""
    _breaker._failures.clear()
    _breaker._open_until.clear()


def close_client():
    global _client
    if _client is not None and not _client.is_closed:
        _client.close()
        _client = None
