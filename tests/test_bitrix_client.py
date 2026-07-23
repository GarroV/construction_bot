import httpx
import pytest
import respx
from src.bitrix.client import BitrixClient, BitrixError

BASE = "https://portal.bitrix24.ru/rest/123/abc/"


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


@respx.mock
async def test_call_returns_result_field(http):
    respx.post(BASE + "tasks.task.get").respond(json={"result": {"task": {"id": "8017"}}})
    bx = BitrixClient(BASE, http, min_interval=0)

    result = await bx.call("tasks.task.get", {"taskId": 8017})

    assert result["task"]["id"] == "8017"


@respx.mock
async def test_retries_on_503_then_succeeds(http):
    route = respx.post(BASE + "tasks.task.get")
    route.side_effect = [
        httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED"}),
        httpx.Response(200, json={"result": {"ok": True}}),
    ]
    bx = BitrixClient(BASE, http, min_interval=0)

    result = await bx.call("tasks.task.get")

    assert result == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_raises_bitrix_error_on_api_error(http):
    respx.post(BASE + "tasks.task.get").respond(
        json={"error": "TASK_NOT_FOUND", "error_description": "нет задачи"}
    )
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError) as e:
        await bx.call("tasks.task.get", {"taskId": 1})
    assert e.value.code == "TASK_NOT_FOUND"


def test_webhook_user_id_parsed_from_url(http):
    bx = BitrixClient(BASE, http)
    assert bx.webhook_user_id == 123


@respx.mock
async def test_non_503_http_status_wrapped_in_bitrix_error(http):
    """500 не должен улетать наружу голым HTTPStatusError (§ фикс №4): вызывающий код
    (handle_add/resolve_files) ловит только BitrixError и иначе молчит перед партнёром."""
    respx.post(BASE + "tasks.task.get").respond(500, text="internal error")
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError) as e:
        await bx.call("tasks.task.get", {"taskId": 1})
    assert e.value.code == "HTTP_500"


@respx.mock
async def test_transport_error_wrapped_in_bitrix_error(http):
    """Сетевая ошибка httpx (обрыв/таймаут) должна оборачиваться в BitrixError, а не
    пробрасываться как httpx.ConnectError мимо except BitrixError у вызывающих."""
    respx.post(BASE + "tasks.task.get").mock(side_effect=httpx.ConnectError("connection refused"))
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError) as e:
        await bx.call("tasks.task.get", {"taskId": 1})
    assert e.value.code == "TRANSPORT_ERROR"


@respx.mock
async def test_sends_browser_like_user_agent(http):
    """WAF коробочного портала режет python-httpx UA — клиент обязан слать браузерный."""
    route = respx.post(BASE + "tasks.task.get").respond(json={"result": {"ok": True}})
    bx = BitrixClient(BASE, http, min_interval=0)

    await bx.call("tasks.task.get")

    ua = route.calls[0].request.headers["User-Agent"]
    assert ua.startswith("Mozilla/5.0") and "construction-bot" in ua
