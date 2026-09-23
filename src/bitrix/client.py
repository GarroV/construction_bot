import asyncio
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

_MAX_ATTEMPTS = 4  # 503-ретраи: паузы 1s, 2s, 4s

# WAF коробочного портала (b24.dodoteam.ru) отдаёт HTML "Forbidden" на не-браузерные
# User-Agent (python-httpx/*, пустой) — проверено вживую 2026-07-23. Шлём браузерный.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 construction-bot/0.1"
)


class BitrixError(Exception):
    def __init__(self, code: str, description: str = ""):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def _same_origin(a: str, b: str) -> bool:
    """Один и тот же портал: схема, хост и порт. Путь и query не сравниваем — антибот
    редиректит на тот же url, а Битрикс может дописать хвост."""
    x, y = urlsplit(a), urlsplit(b)
    return (x.scheme, x.hostname, x.port) == (y.scheme, y.hostname, y.port)


def _encode_params(params: dict | None) -> list[tuple[str, str]]:
    """Bitrix-стиль query-параметров: списки -> key[], словари -> key[sub]."""
    out: list[tuple[str, str]] = []

    def walk(key: str, val) -> None:
        if isinstance(val, dict):
            for k, v in val.items():
                walk(f"{key}[{k}]", v)
        elif isinstance(val, (list, tuple)):
            for v in val:
                walk(f"{key}[]", v)
        elif val is not None:
            out.append((key, str(val)))

    for k, v in (params or {}).items():
        walk(k, v)
    return out


class BitrixClient:
    """Вебхук-клиент: троттлинг ≤2 rps (leaky bucket облака), retry на 503 (§13).

    Вызовы идут GET-ом с параметрами в query: антибот Servicepipe перед коробочным
    порталом отдаёт JS-challenge на POST, но пропускает GET (проверено 2026-07-23).
    """

    def __init__(self, webhook_url: str, http: httpx.AsyncClient, min_interval: float = 0.5):
        self._base = webhook_url.rstrip("/") + "/"
        self._http = http
        self._min_interval = min_interval
        self._throttle = asyncio.Lock()
        self._last_call = 0.0

    @property
    def webhook_user_id(self) -> int:
        # https://portal/rest/<user_id>/<token>/ -> <user_id>
        return int(self._base.rstrip("/").split("/")[-2])

    @property
    def webhook_url(self) -> str:
        """Полный base вебхука (с завершающим /) — нужен коллектору для ссылок на
        комментарий-источник файла (§8 фича 2, links.comment_url)."""
        return self._base

    async def call(self, method: str, params: dict | None = None) -> Any:
        url = self._base + method + ".json"
        query: list[tuple[str, str]] | None = _encode_params(params)
        for attempt in range(_MAX_ATTEMPTS):
            await self._wait_slot()
            try:
                resp = await self._http.get(
                    url,
                    params=query,
                    headers={"User-Agent": _USER_AGENT},
                )
            except httpx.HTTPError as e:  # сеть/таймаут — честный контракт (§ фикс №4):
                raise BitrixError("TRANSPORT_ERROR", str(e)) from e  # вызывающие ловят только BitrixError
            if resp.status_code == 503:  # QUERY_LIMIT_EXCEEDED
                await asyncio.sleep(2**attempt)
                continue
            if resp.is_redirect:  # cookie-challenge антибота Servicepipe (проверено 23.09.2026):
                # 307 + тело "blank" + Set-Cookie + Location на тот же url = «возьми куку и
                # повтори». Проходим его сами, а не httpx follow_redirects: в url лежит токен
                # вебхука, и на чужой хост его уносить нельзя даже по валидному Location.
                location = urljoin(str(resp.url), resp.headers.get("location", ""))
                if not _same_origin(location, self._base):
                    host = urlsplit(location).netloc or "?"  # без пути: он мог бы унести токен в текст ошибки
                    raise BitrixError(f"HTTP_{resp.status_code}", f"редирект на чужой хост {host}")
                url, query = location, None  # параметры уже внутри Location
                continue
            if not (200 <= resp.status_code < 300):
                raise BitrixError(f"HTTP_{resp.status_code}", resp.text[:200])
            data = resp.json()
            if "error" in data:
                raise BitrixError(str(data["error"]), str(data.get("error_description", "")))
            return data["result"]
        raise BitrixError("QUERY_LIMIT_EXCEEDED", f"после {_MAX_ATTEMPTS} попыток")

    async def _wait_slot(self) -> None:
        async with self._throttle:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
