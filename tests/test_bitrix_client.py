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
    respx.get(BASE + "tasks.task.get.json").respond(json={"result": {"task": {"id": "8017"}}})
    bx = BitrixClient(BASE, http, min_interval=0)

    result = await bx.call("tasks.task.get", {"taskId": 8017})

    assert result["task"]["id"] == "8017"


@respx.mock
async def test_retries_on_503_then_succeeds(http):
    route = respx.get(BASE + "tasks.task.get.json")
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
    respx.get(BASE + "tasks.task.get.json").respond(
        json={"error": "TASK_NOT_FOUND", "error_description": "нет задачи"}
    )
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError) as e:
        await bx.call("tasks.task.get", {"taskId": 1})
    assert e.value.code == "TASK_NOT_FOUND"


def test_webhook_user_id_parsed_from_url(http):
    bx = BitrixClient(BASE, http)
    assert bx.webhook_user_id == 123


def test_webhook_url_returns_full_base(http):
    """§8 фича 2: коллектору нужен полный base вебхука для links.comment_url."""
    bx = BitrixClient(BASE, http)
    assert bx.webhook_url == BASE


@respx.mock
async def test_non_503_http_status_wrapped_in_bitrix_error(http):
    """500 не должен улетать наружу голым HTTPStatusError (§ фикс №4): вызывающий код
    (handle_add/resolve_files) ловит только BitrixError и иначе молчит перед партнёром."""
    respx.get(BASE + "tasks.task.get.json").respond(500, text="internal error")
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError) as e:
        await bx.call("tasks.task.get", {"taskId": 1})
    assert e.value.code == "HTTP_500"


@respx.mock
async def test_transport_error_wrapped_in_bitrix_error(http):
    """Сетевая ошибка httpx (обрыв/таймаут) должна оборачиваться в BitrixError, а не
    пробрасываться как httpx.ConnectError мимо except BitrixError у вызывающих."""
    respx.get(BASE + "tasks.task.get.json").mock(side_effect=httpx.ConnectError("connection refused"))
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError) as e:
        await bx.call("tasks.task.get", {"taskId": 1})
    assert e.value.code == "TRANSPORT_ERROR"


@respx.mock
async def test_sends_browser_like_user_agent(http):
    """WAF коробочного портала режет python-httpx UA — клиент обязан слать браузерный."""
    route = respx.get(BASE + "tasks.task.get.json").respond(json={"result": {"ok": True}})
    bx = BitrixClient(BASE, http, min_interval=0)

    await bx.call("tasks.task.get")

    ua = route.calls[0].request.headers["User-Agent"]
    assert ua.startswith("Mozilla/5.0") and "construction-bot" in ua


@respx.mock
async def test_get_encodes_nested_params_bitrix_style(http):
    """GET-переход из-за антибота: списки -> key[], словари -> key[sub]."""
    route = respx.get(BASE + "tasks.task.get.json").respond(json={"result": {"ok": True}})
    bx = BitrixClient(BASE, http, min_interval=0)

    await bx.call("tasks.task.get", {"taskId": 42103, "select": ["ID", "TITLE"], "filter": {"FIELD": "COMMENT"}})

    q = str(route.calls[0].request.url)
    assert "taskId=42103" in q
    assert "select%5B%5D=ID" in q and "select%5B%5D=TITLE" in q
    assert "filter%5BFIELD%5D=COMMENT" in q


@respx.mock
async def test_follows_same_origin_redirect_of_antibot_cookie_challenge(http):
    """Антибот Servicepipe перед коробочным порталом периодически отвечает на REST-запрос
    307-ым с телом 'blank', Set-Cookie и Location на ТОТ ЖЕ url: «возьми куку и повтори».
    Проверено живьём 23.09.2026 — без прохода по редиректу прогон дайджеста получал ложную
    «HTTP_307: blank» и пропускал дискавери подзадач, хотя подзадачи доступны."""
    route = respx.get(BASE + "tasks.task.list.json")
    route.side_effect = [
        httpx.Response(
            307,
            text="blank",
            headers={"Location": BASE + "tasks.task.list.json?filter%5BPARENT_ID%5D=62241",
                     "Set-Cookie": "spid=1790144700181_x; Path=/"},
        ),
        httpx.Response(200, json={"result": {"tasks": [{"id": "83061"}]}}),
    ]
    bx = BitrixClient(BASE, http, min_interval=0)

    result = await bx.call("tasks.task.list", {"filter": {"PARENT_ID": 62241}})

    assert result["tasks"][0]["id"] == "83061"
    assert route.call_count == 2


@respx.mock
async def test_does_not_follow_redirect_to_foreign_host(http):
    """В url вебхука лежит токен доступа к порталу, поэтому редирект наружу не проходим:
    подменённый Location увёл бы секрет на чужой хост. Такой ответ — ошибка, и сам токен
    в её текст не попадает."""
    respx.get(BASE + "tasks.task.get.json").respond(
        307, headers={"Location": "https://evil.example.com/rest/123/abc/tasks.task.get.json"}
    )
    foreign = respx.get("https://evil.example.com/rest/123/abc/tasks.task.get.json").respond(json={"result": 1})
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError) as e:
        await bx.call("tasks.task.get", {"taskId": 1})

    assert e.value.code == "HTTP_307"
    assert not foreign.called
    assert "abc" not in e.value.description


@respx.mock
async def test_redirect_loop_ends_with_error_not_hang(http):
    """Бесконечный challenge не должен вешать прогон: попытки ограничены, наружу — ошибка."""
    respx.get(BASE + "tasks.task.get.json").respond(
        307, text="blank", headers={"Location": BASE + "tasks.task.get.json"}
    )
    bx = BitrixClient(BASE, http, min_interval=0)

    with pytest.raises(BitrixError):
        await bx.call("tasks.task.get", {"taskId": 1})
