import httpx
import pytest
import respx
from src.bitrix.client import BitrixClient
from src.bitrix import links

BASE = "https://portal.bitrix24.ru/rest/123/abc/"


def test_task_url_built_from_webhook_host():
    url = links.task_url(BASE, 123, 8017)
    assert url == "https://portal.bitrix24.ru/company/personal/user/123/tasks/task/view/8017/"


@respx.mock
async def test_resolve_files_returns_detail_url_and_survives_errors():
    route = respx.post(BASE + "disk.file.get")
    route.side_effect = [
        httpx.Response(200, json={"result": {
            "NAME": "план.pdf",
            "DETAIL_URL": "https://portal.bitrix24.ru/disk/1",
            "DOWNLOAD_URL": "https://portal.bitrix24.ru/secret-token-url",
        }}),
        httpx.Response(200, json={"error": "ACCESS_DENIED", "error_description": ""}),
    ]
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        files = await links.resolve_files(bx, [1, 2])

    assert files[0].name == "план.pdf"
    assert files[0].url == "https://portal.bitrix24.ru/disk/1"
    assert "secret" not in (files[0].url or "")     # DOWNLOAD_URL не течёт (§8)
    assert files[1].url is None                     # ошибка -> имя-заглушка без падения
