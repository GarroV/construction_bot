from src.bitrix.links import FileLink
from src.bitrix.parse import ChatMessage
from src.digest.llm import CardDelta
from src.digest import render
from src.i18n import load_locales

LOCALES = load_locales()
DELTA = CardDelta(
    task_id=8017, alias="Бишкек <8>",
    task_changes=["статус: 2 → 5"],
    comments=[ChatMessage(id=1, author="Иван", text="<b>ок</b>", file_ids=[])],
    checklist_done=3, checklist_total=10,
    files=[FileLink(name="план<1>.pdf", url="https://p/disk/1"), FileLink(name="без ссылки", url=None)],
    new_history_id=31, new_message_id=202,
)


def test_card_message_with_summary_escapes_and_links():
    msg = render.card_message(DELTA, "Сводка <дня> & выводы", "https://p/task/8017/", LOCALES, "ru")
    assert '<a href="https://p/task/8017/">Бишкек &lt;8&gt;</a>' in msg
    assert "Сводка &lt;дня&gt; &amp; выводы" in msg          # LLM-текст экранирован
    assert '<a href="https://p/disk/1">план&lt;1&gt;.pdf</a>' in msg
    assert "без ссылки" not in msg  # файл без url при живой выжимке не дублируем (он в контексте)
    assert 'href=""' not in msg


def test_card_message_fallback_lists_raw_changes():
    msg = render.card_message(DELTA, None, "https://p/task/8017/", LOCALES, "ru")
    assert "Краткая версия" in msg
    assert "статус: 2 → 5" in msg and "Иван" in msg


def test_no_changes_line():
    line = render.no_changes_line("Бишкек 8", "https://p/task/8017/", LOCALES, "ru")
    assert "изменений нет" in line and "Бишкек 8" in line


def test_clip_cuts_on_line_boundary():
    text = "\n".join(f"строка {i} " + "x" * 100 for i in range(100))
    clipped = render.clip(text, limit=1000)
    body = clipped.removesuffix("…")
    assert len(clipped) <= 1000 and clipped.endswith("…")
    assert text.startswith(body)
    assert text[len(body)] == "\n"  # срез ровно по границе строки, тег/строка не разорваны


def test_urls_are_attribute_escaped():
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[FileLink(name="f.pdf", url='https://p/1?a=1&b="x"')],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(delta, "s", 'https://p/task?x=1&y="2"', LOCALES, "ru")
    assert 'href="https://p/task?x=1&amp;y=&quot;2&quot;"' in msg
    assert 'href="https://p/1?a=1&amp;b=&quot;x&quot;"' in msg


def test_fallback_keeps_linkless_files_visible():
    """Fallback без LLM: вложения не упомянуты в тексте — 📎-список обязан их сохранить."""
    msg = render.card_message(DELTA, None, "https://p/task/8017/", LOCALES, "ru")
    assert "📎 без ссылки" in msg
