"""Загрузчик вложений (§8): DOWNLOAD_URL несёт токен вебхука, качается сервером
httpx-ом; антибот Servicepipe перед JS-challenge отдаёт HTML вместо файла — это
регистрируется как деградация (None), не исключение."""
import httpx
import respx

from src.bitrix.client import _USER_AGENT
from src.bitrix.files import TELEGRAM_FILE_LIMIT, download_attachment

URL = "https://portal.example/bitrix/tools/disk/uf.php?action=download&webhook_token=SECRET"


@respx.mock
async def test_download_attachment_returns_bytes_for_real_file():
    route = respx.get(URL).respond(
        200, content=b"%PDF-1.4 fake pdf bytes", headers={"content-type": "application/pdf"}
    )
    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data == b"%PDF-1.4 fake pdf bytes"
    assert route.calls[0].request.headers["User-Agent"] == _USER_AGENT  # WAF режет не-браузерные UA


@respx.mock
async def test_download_attachment_html_content_type_is_antibot_challenge():
    respx.get(URL).respond(
        200, content=b"<html>challenge</html>", headers={"content-type": "text/html; charset=utf-8"}
    )
    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data is None


@respx.mock
async def test_download_attachment_doctype_body_without_html_content_type_is_antibot_challenge():
    """content-type подделан/отсутствует, но тело — явно HTML-страница challenge'а."""
    respx.get(URL).respond(
        200, content=b"<!DOCTYPE html><html>challenge</html>",
        headers={"content-type": "application/octet-stream"},
    )
    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data is None


@respx.mock
async def test_download_attachment_uppercase_doctype_is_also_detected():
    respx.get(URL).respond(
        200, content=b"<!DOCTYPE HTML><HTML>challenge</HTML>",
        headers={"content-type": "application/octet-stream"},
    )
    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data is None


@respx.mock
async def test_download_attachment_too_large_returns_none():
    respx.get(URL).respond(
        200, content=b"x" * (TELEGRAM_FILE_LIMIT + 1), headers={"content-type": "application/octet-stream"}
    )
    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data is None


@respx.mock
async def test_download_attachment_exactly_at_limit_is_allowed():
    respx.get(URL).respond(
        200, content=b"x" * TELEGRAM_FILE_LIMIT, headers={"content-type": "application/octet-stream"}
    )
    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data is not None and len(data) == TELEGRAM_FILE_LIMIT


@respx.mock
async def test_download_attachment_http_error_status_returns_none():
    respx.get(URL).respond(403, content=b"forbidden")
    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data is None


@respx.mock
async def test_download_attachment_never_logs_the_raw_webhook_token(caplog):
    """§8: DOWNLOAD_URL несёт токен вебхука в query — ни на challenge, ни на HTTP-ошибку
    warning не должен утечь в лог-файлы (регрессия ревью)."""
    respx.get(URL).respond(200, content=b"<html>challenge</html>",
                           headers={"content-type": "text/html"})
    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING", logger="src.bitrix.files"):
            await download_attachment(http, URL)

    assert caplog.records  # warning реально залогирован
    assert "SECRET" not in caplog.text


@respx.mock
async def test_download_attachment_follows_redirects():
    real_url = "https://portal.example/disk/final.pdf"
    respx.get(URL).respond(302, headers={"location": real_url})
    respx.get(real_url).respond(200, content=b"real bytes", headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient() as http:
        data = await download_attachment(http, URL)

    assert data == b"real bytes"
