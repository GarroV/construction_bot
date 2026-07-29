"""Загрузчик вложений комментариев (§8, per-country `chats.attach_files`).

`DOWNLOAD_URL` из `ATTACHED_OBJECTS` несёт незатухающий токен вебхука — партнёру
наружу он не отдаётся никогда (см. `src.bitrix.parse.AttachedFile`, `collector`).
Вместо этого бот сам качает файл СЕРВЕРНО (токен не покидает сервер) и пересылает
уже готовое содержимое через Telegram `sendDocument` (`src.telegram.send.send_document`).

Прямой httpx GET по `DOWNLOAD_URL` живьём режется антиботом Servicepipe перед
коробочным порталом — тот отдаёт JS-challenge HTML вместо файла (пока не оформлено
WAF-исключение по маркеру construction-bot); из браузера файл при этом качается
нормально. Пока challenge не снят — `download_attachment` возвращает `None`, и
вызывающий (`scheduler.process_chat`) деградирует на текущее поведение (ссылка на
комментарий в дайджесте, а не файл), с `warning` в лог.

Headless-браузер как fallback на JS-challenge — ОТДЕЛЬНАЯ будущая задача, здесь
сознательно не реализован.
"""
import logging
from urllib.parse import urlsplit

import httpx

from src.bitrix.client import _USER_AGENT

log = logging.getLogger(__name__)

TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MiB — лимит Telegram Bot API на sendDocument
_CHALLENGE_PREFIXES = (b"<!doctype", b"<html")


def _looks_like_antibot_challenge(content_type: str, body: bytes) -> bool:
    if "html" in content_type.lower():
        return True
    prefix = body[:32].lstrip().lower()
    return any(prefix.startswith(p) for p in _CHALLENGE_PREFIXES)


def _redact_for_log(url: str) -> str:
    """DOWNLOAD_URL несёт токен вебхука в query (§8) — в логи он попадать не должен
    (иначе секрет утекает в лог-файлы). Для диагностики оставляем только host+path."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


async def download_attachment(http: httpx.AsyncClient, download_url: str) -> bytes | None:
    """Качает вложение по сырому `DOWNLOAD_URL` (несёт токен вебхука — не публикуется,
    качается только сервером). Браузерный User-Agent — тот же, что у `BitrixClient`
    (WAF Servicepipe режет не-браузерные UA JS-challenge'ем, проверено живьём).

    `None` — деградация, не ошибка:
    - антибот-challenge (content-type содержит html ИЛИ тело начинается с
      `<!DOCTYPE`/`<html`, регистронезависимо) — WAF-исключение по маркеру
      construction-bot ещё не оформлено, headless-fallback — отдельная будущая задача;
    - файл больше `TELEGRAM_FILE_LIMIT` (50 МБ) — Telegram Bot API его не примет.

    Сетевые ошибки (`httpx.HTTPError`) намеренно НЕ глушатся здесь — это уже сбой, а
    не деградация; вызывающий (`scheduler.process_chat`) ловит per-file try/except и
    добавляет в errors, не роняя прогон остальных карточек/файлов."""
    resp = await http.get(download_url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True)
    safe_url = _redact_for_log(download_url)  # без query — токен вебхука не должен попасть в лог
    if not (200 <= resp.status_code < 300):
        log.warning("download_attachment: HTTP %s на %s", resp.status_code, safe_url)
        return None
    body = resp.content
    if _looks_like_antibot_challenge(resp.headers.get("content-type", ""), body):
        log.warning("download_attachment: похоже на antibot-challenge (не файл): %s", safe_url)
        return None
    if len(body) > TELEGRAM_FILE_LIMIT:
        log.warning("download_attachment: файл больше лимита Telegram (%d байт): %s",
                    len(body), safe_url)
        return None
    return body
