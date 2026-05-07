"""Markify Client"""

import json
import random
import time
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
        db_code = DB_CODE_ALIASES.get(database, database)
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

    def search_all(
        self,
        mark: str,
        classes: str,
        database: str,
        mode: str = "knockout",
        startIndex: int = 0,
        pageLimit: int = 30,
        **kwargs,
    ):
        # 搜索所有页，api(根据api文档写required参数)，返回 Result 对象，包含所有页数据
        while True:
            result = self.search(
                mark=mark,
                classes=classes,
                database=database,
                mode=mode,
                startIndex=startIndex,
                pageLimit=pageLimit,
                **kwargs,
            )
            yield result

            total = int(result["meta"]["totalFound"])
            startIndex += pageLimit
            if startIndex >= total:
                break

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
        response = self._session.get(
            url,
            params=params,
            headers=_NO_CACHE_HEADERS,
            timeout=self.http_timeout,
        )
        self._call_count += 1
        if response.status_code != 200:
            raise MarkifyError(f"HTTP错误 {response.status_code}: {response.text}")

        data = response.json()
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
        msg = data["error"]
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
