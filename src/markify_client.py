"""Markify Client"""

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

DB_CODE_ALIASES = {"EU": "EUIPO", "US": "USPTO"}

# no-cache headers 是 _cb 的备份保险，确保中间缓存层不会返回旧结果
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

# Exceptions

class MarkifyError(Exception):
    """所有 Markify 错误的基类"""

class QuotaExhaustedError(MarkifyError):
    """当 Markify 配额耗尽时引发的错误"""

class InvalidAPIKeyError(MarkifyError):
    """当提供的 API 密钥无效时引发的错误"""

class ExpiredAPIKeyError(MarkifyError):
    """当提供的 API 密钥过期时引发的错误"""

class CallCapExceededError(MarkifyError):
    """主动设置Markify调用上限"""

class BadDBCodeError(MarkifyError):
    """数据库代码无效"""


# Retain the public name used by the experiment runners.
CallCapReachedError = CallCapExceededError


@dataclass
class CallResult:
    status: str
    data: Optional[dict] = None
    error: Optional[str] = None
    elapsed: float = 0.0
    http_code: Optional[int] = None
    pages_fetched: int = 0

# 主类

class MarkifyClient:
    # --- 状态（__init__里面初始化）： client 在"开始工作之前"必须准备好的所有"档案"。
    # api_key, call_cap, rate_limit_seconds, http_timeout, base_url
    # _last_call_time, _quota_exhausted, _call_count, _session
    # _bad_codes_file, _known_bad_db_codes

    #----公开方法（用户需求）-----
    def __init__(
        self,
        api_key: str,
        call_cap: Optional[int] = 700,
        rate_limit_seconds: float = 2.0,
        http_timeout: float = 18.0,
        base_url: str = "https://www.markify.com/api",
        bad_codes_file: Optional[str] = None,
    ):
        # 公开/可配置属性
        self.api_key = api_key
        self.call_cap = call_cap
        self.rate_limit_seconds = rate_limit_seconds
        self.http_timeout = http_timeout
        self.base_url = base_url
        # 内部状态属性
        self._session = requests.Session()
        self._last_call_time = 0.0
        self._call_count = 0
        self._quota_exhausted = False
        self._bad_codes_file = Path(bad_codes_file) if bad_codes_file else None
        self._known_bad_db_codes = self._load_bad_codes()
        self._last_http_code = None

    @property
    def call_count(self):
        return self._call_count

    @classmethod
    def from_env(cls, **overrides):
        # 从 config 读取所有默认值；overrides 允许测试时覆盖
        try:
            from . import config
        except ImportError:
            import config  # 直接当脚本跑时的回退
        if not config.MARKIFY_API_KEY:
            raise ValueError("环境变量 MARKIFY_API_KEY 未设置")
        kwargs = dict(
            api_key=config.MARKIFY_API_KEY,
            base_url=config.MARKIFY_BASE_URL,
            rate_limit_seconds=config.RATE_LIMIT_SECONDS,
            http_timeout=float(config.HTTP_TIMEOUT),
            bad_codes_file=str(config.KNOWN_BAD_CODES_FILE),
        )
        kwargs.update(overrides)
        return cls(**kwargs)

    def check_quota(self):
        # 查 usage_rules
        return self._make_request(
            f"{self.base_url}/usage_rules.json",
            {"api_key": self.api_key},
        )

#类比：你 → 秘书 → 客户
#
#你（search_all）：跟秘书说"帮我打 5 个电话给客户问问情况"
#秘书（search）：每打一个电话，要先准备好礼貌用语、自报家门、说事
#客户（API）：接电话的人
#
#你给秘书的指令很简单："打电话给客户，问 Apple 商标的事"。
#秘书真打电话时要说的内容："您好我是某某公司，工号 xxx，我想咨询..."
#这两段话长得像（都关于 Apple 商标），但不是同一段话。秘书要加上"工号"（= api_key），你不用管。

    def search(
        self,
        mark: str,
        classes: str,
        database: str,
        mode: str = "knockout",
        startIndex: int = 0,
        pageLimit: int = 30,
        gs: int = 1,
        minScore: float = 0.0,
        **kwargs,
    ) -> dict:
        # 搜索一页，api(根据api文档写required参数)，用户需要自己传的，返回 Result 对象
        db_code = ",".join(DB_CODE_ALIASES.get(db.strip(), db.strip())
                           for db in database.split(","))
        # 发请求前先拦：已知坏的 DB code 直接抛，不浪费 quota
        if db_code in self._known_bad_db_codes:
            raise BadDBCodeError(f"已知无效的 DB code: {db_code}")
        params = {
            "api_key": self.api_key,
            "mark": mark,
            "class": classes,
            "database": db_code,
            "mode": mode,
            "startIndex": startIndex,
            "pageLimit": pageLimit,
            "gs": gs,
            "minScore": minScore,
        }
        try:
            return self._call_with_retry(
                f"{self.base_url}/search.json",
                params,
            )
        except BadDBCodeError:
            # 第一次发现这个 db_code 坏，记下来下次不再试
            self._known_bad_db_codes.add(db_code)
            self._save_bad_codes()
            raise

    def _result(self, **kwargs):
        """Adapt raw responses to the stable contract used by the runners."""
        started = time.monotonic()
        self._last_http_code = None
        try:
            data = self.search(**kwargs)
            return CallResult("ok", data=data, elapsed=time.monotonic() - started,
                              http_code=self._last_http_code, pages_fetched=1)
        except (QuotaExhaustedError, CallCapExceededError, InvalidAPIKeyError, ExpiredAPIKeyError):
            raise
        except requests.exceptions.Timeout:
            status, message = "timeout", "Request timed out"
        except requests.exceptions.RequestException:
            status, message = "http_error", "Request failed"
        except (MarkifyError, ValueError):
            # Transport exceptions and response bodies may contain the API key.
            status, message = "http_error", "API returned an unsuccessful or invalid response"
        return CallResult(status, error=message, elapsed=time.monotonic() - started,
                          http_code=self._last_http_code)

    def search_all(self, mark, classes, database=None, mode="knockout",
                   startIndex=0, pageLimit=30, **kwargs):
        database = kwargs.pop("databases", database)
        page_limit = kwargs.pop("page_limit", pageLimit)
        max_pages = kwargs.pop("max_pages", 100)
        min_score = kwargs.pop("min_score", kwargs.pop("minScore", 0.0))
        if page_limit <= 0 or max_pages <= 0 or startIndex < 0 or not database:
            raise ValueError("database and positive page limits are required")
        started = time.monotonic()
        items, pages, meta = [], 0, {}
        for _ in range(max_pages):
            result = self._result(mark=mark, classes=classes, database=database,
                                  mode=mode, startIndex=startIndex,
                                  pageLimit=page_limit, minScore=min_score, **kwargs)
            if result.status != "ok":
                result.elapsed = time.monotonic() - started
                result.pages_fetched = pages
                return result
            data = result.data
            page = data.get("result", [])
            if not isinstance(page, list) or not isinstance(data.get("meta", {}), dict):
                return CallResult("http_error", error="Invalid page structure",
                                  elapsed=time.monotonic() - started, pages_fetched=pages)
            pages += 1
            items.extend(page)
            meta = data.get("meta", {})
            total = meta.get("totalFound")
            try:
                complete = (startIndex + len(page) >= int(total)) if total is not None else len(page) < page_limit
            except (TypeError, ValueError):
                return CallResult("http_error", error="Invalid totalFound",
                                  elapsed=time.monotonic() - started, pages_fetched=pages)
            if not page or complete:
                return CallResult("ok", data={"meta": meta, "result": items},
                                  elapsed=time.monotonic() - started,
                                  http_code=result.http_code, pages_fetched=pages)
            startIndex += len(page)
        return CallResult("ok_truncated", data={"meta": meta, "result": items},
                          elapsed=time.monotonic() - started,
                          http_code=result.http_code, pages_fetched=pages)

    #----内部方法（实现细节）-----
    def _load_bad_codes(self):
        if not self._bad_codes_file or not self._bad_codes_file.exists():
            return set()
        try:
            return set(json.loads(self._bad_codes_file.read_text()))
        except Exception:
            return set()

    def _save_bad_codes(self):
        if not self._bad_codes_file:
            return
        self._bad_codes_file.write_text(
            json.dumps(sorted(self._known_bad_db_codes))
        )

    def _wait_for_rate_limit(self):
        # 根据 rate_limit_seconds 等待，确保不超过速率限制
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_call_time = time.monotonic()

    def _make_request(self, url, params):
        # 发送 HTTP 请求，处理响应，更新状态（如配额耗尽），拼好 URL、参数,按一下"发送"
        if self._quota_exhausted:
            raise QuotaExhaustedError("配额已耗尽")
        if self.call_cap is not None and self._call_count >= self.call_cap:
            raise CallCapExceededError("已达到调用上限")
        self._wait_for_rate_limit()
        params = {**params, "_cb": random.randint(1, 10**9)}
        # cache busting：让每次的 URL 都长得不一样，缓存层就不会命中旧结果
        # Count attempts before I/O: timeouts may still consume server quota.
        self._call_count += 1
        response = self._session.get(
            url,
            params=params,
            headers=_NO_CACHE_HEADERS,
            timeout=self.http_timeout,
        )
        self._last_http_code = response.status_code
        if response.status_code != 200:
            raise MarkifyError(f"HTTP错误 {response.status_code}")

        data = response.json()
        if not isinstance(data, dict):
            raise MarkifyError("API response must be an object")
        if "error" in data:
            self._classify_error(data)
        return data

    def _call_with_retry(self, url, params, max_retries=3):
        # 在遇到临时网络错误时重试，区分超时和其他网络错以便日志/监控分开统计
        for attempt in range(max_retries + 1):
            err_type = None
            try:
                return self._make_request(url, params)
            except requests.exceptions.Timeout:
                # Timeout 是 RequestException 的子类，必须先捕获
                err_type = "超时"
                if attempt == max_retries:
                    raise
            except requests.exceptions.RequestException:
                err_type = "网络"
                if attempt == max_retries:
                    raise
            wait = 2 ** attempt
            print(f"{err_type}错误，{wait}秒后重试 ({attempt+1}/{max_retries})")
            time.sleep(wait)

    def _classify_error(self, data):
        # 根据 API 返回的 error 文本分类抛对应异常
        msg = str(data["error"])
        if self.api_key:
            msg = msg.replace(self.api_key, "[REDACTED]")
        if "Invalid" in msg or "Inactive" in msg:
            raise InvalidAPIKeyError(msg)
        elif "Quota" in msg and "exceeded" in msg:
            self._quota_exhausted = True
            raise QuotaExhaustedError(msg)
        elif "Expired" in msg:
            raise ExpiredAPIKeyError(msg)
        elif "disallowed parameter" in msg and "database" in msg:
            raise BadDBCodeError(msg)
        else:
            raise MarkifyError(f"未知错误: {msg}")


_default_client = None
_default_cap = 700


def _get_client():
    global _default_client
    if _default_client is None:
        _default_client = MarkifyClient.from_env(call_cap=_default_cap)
    return _default_client


def set_call_cap(cap):
    global _default_cap
    if cap is not None and cap < 0:
        raise ValueError("call cap must be non-negative")
    _default_cap = cap
    if _default_client is not None:
        _default_client.call_cap = cap


def get_call_count():
    return _default_client.call_count if _default_client is not None else 0


def search(mark, classes, databases=None, *, database=None, min_score=0.0,
           page_limit=30, **kwargs):
    """One-page compatibility API used by timing and smoke-test scripts."""
    database = databases or database
    if not database or page_limit <= 0:
        raise ValueError("database and a positive page_limit are required")
    return _get_client()._result(mark=mark, classes=classes, database=database,
                                 minScore=min_score, pageLimit=page_limit, **kwargs)
