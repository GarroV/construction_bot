import json
from pathlib import Path
from unittest.mock import AsyncMock

from src.bitrix import methods

_LIVE_CHECKLIST = json.loads(
    Path("tests/fixtures/live/checklist.json").read_text()
)


def _item(id_, parent_id, title, sort_index, complete):
    return {
        "ID": id_, "PARENT_ID": parent_id, "TITLE": title,
        "SORT_INDEX": sort_index, "IS_COMPLETE": complete,
    }


async def test_checklist_summary_finds_first_unfinished_stage():
    """Иерархия из трёх этапов: первый закрыт, второй (по SORT_INDEX) не закрыт —
    сводка должна вернуть именно его, а не первый по списку/ID."""
    items = [
        _item("1", 0, "01 Stage one", "0", "Y"),
        _item("11", "1", "child 1.1", "0", "Y"),
        _item("2", 0, "02 Stage two", "1", "N"),
        _item("21", "2", "child 2.1", "0", "N"),
        _item("22", "2", "child 2.2", "0", "Y"),
        _item("3", 0, "03 Stage three", "2", "N"),
        _item("31", "3", "child 3.1", "0", "N"),
    ]
    bx = AsyncMock()
    bx.call = AsyncMock(return_value=items)

    summary = await methods.get_checklist_summary(bx, 8017)

    assert summary.has_stages is True
    assert summary.stage_title == "02 Stage two"
    assert (summary.stage_done, summary.stage_total) == (1, 2)
    assert (summary.done, summary.total) == (3, 7)  # по ВСЕМ пунктам, включая корни


async def test_checklist_summary_all_stages_closed_returns_none_title():
    """Дети всех корней выполнены -> stage_title=None при has_stages=True
    («все этапы закрыты»), даже если сами корневые галочки не проставлены (люди их не тыкают)."""
    items = [
        _item("1", 0, "01 Stage one", "0", "N"),  # корневая галочка не тронута
        _item("11", "1", "child 1.1", "0", "Y"),
        _item("2", 0, "02 Stage two", "1", "N"),
        _item("21", "2", "child 2.1", "0", "Y"),
    ]
    bx = AsyncMock()
    bx.call = AsyncMock(return_value=items)

    summary = await methods.get_checklist_summary(bx, 8017)

    assert summary.has_stages is True
    assert summary.stage_title is None
    assert (summary.stage_done, summary.stage_total) == (0, 0)
    assert (summary.done, summary.total) == (2, 4)


async def test_checklist_summary_flat_list_has_no_stages():
    """Плоский чек-лист без иерархии (PARENT_ID отсутствует/0 у всех, детей нет) ->
    has_stages False, счётчики по-прежнему верны."""
    items = [
        _item("1", 0, "Пункт 1", "0", "Y"),
        _item("2", 0, "Пункт 2", "1", "N"),
        _item("3", None, "Пункт 3", "2", "Y"),
    ]
    bx = AsyncMock()
    bx.call = AsyncMock(return_value=items)

    summary = await methods.get_checklist_summary(bx, 8017)

    assert summary.has_stages is False
    assert summary.stage_title is None
    assert (summary.done, summary.total) == (2, 3)


async def test_checklist_summary_garbage_sort_index_does_not_crash():
    """SORT_INDEX мусорный (не число) -> трактуется как 0, не роняет сборку; при прочих
    равных мусорный SORT_INDEX сортируется как самый первый этап."""
    items = [
        _item("1", 0, "01 Stage one", "garbage", "N"),
        _item("11", "1", "child 1.1", "0", "N"),
        _item("2", 0, "02 Stage two", "1", "N"),
        _item("21", "2", "child 2.1", "0", "N"),
    ]
    bx = AsyncMock()
    bx.call = AsyncMock(return_value=items)

    summary = await methods.get_checklist_summary(bx, 8017)

    assert summary.has_stages is True
    assert summary.stage_title == "01 Stage one"  # garbage -> 0, сортируется первым


async def test_checklist_summary_on_live_fixture():
    """Живой пример (71 пункт, 7 этапов «01 …»–«07 …»): все 7 корней имеют детей и у
    каждого корня все дети выполнены (сами корневые галочки при этом не проставлены у
    6 из 7) -> «все этапы закрыты», done/total считаются по всем 71 пункту."""
    bx = AsyncMock()
    bx.call = AsyncMock(return_value=_LIVE_CHECKLIST)

    summary = await methods.get_checklist_summary(bx, 42103)

    assert summary.has_stages is True
    assert summary.stage_title is None
    assert (summary.done, summary.total) == (65, 71)
