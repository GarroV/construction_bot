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
    assert '🏗 <b><a href="https://p/task/8017/">Бишкек &lt;8&gt;</a></b>' in msg
    assert "Сводка &lt;дня&gt; &amp; выводы" in msg          # LLM-текст экранирован
    assert '<a href="https://p/disk/1">план&lt;1&gt;.pdf</a>' in msg
    # summary не упоминает "без ссылки" дословно -> 📎-страховка обязана его показать
    # (§7: дельта не теряется молча), иначе имя файла пропало бы без следа
    assert "📎 без ссылки" in msg
    assert 'href=""' not in msg


def test_card_message_fallback_lists_raw_changes():
    msg = render.card_message(DELTA, None, "https://p/task/8017/", LOCALES, "ru")
    assert "Краткая версия" in msg
    assert "статус: 2 → 5" in msg and "Иван" in msg


def test_card_message_checklist_line_plain_when_no_stages():
    """DELTA без иерархии (has_stages=False по умолчанию) -> плоская строка чек-листа,
    и в LLM-режиме, и в fallback."""
    msg_llm = render.card_message(DELTA, "Сводка", "https://p/task/8017/", LOCALES, "ru")
    msg_fallback = render.card_message(DELTA, None, "https://p/task/8017/", LOCALES, "ru")
    assert "📋 Чек-лист: 3/10" in msg_llm
    assert "📋 Чек-лист: 3/10" in msg_fallback


def test_card_message_checklist_line_shows_open_stage():
    delta = CardDelta(
        task_id=8017, alias="Бишкек 8", task_changes=[], comments=[],
        checklist_done=40, checklist_total=71, files=[],
        new_history_id=0, new_message_id=0,
        has_stages=True, stage_title="02 Store design", stage_done=3, stage_total=17,
    )
    msg = render.card_message(delta, "Сводка", "https://p/task/8017/", LOCALES, "ru")
    assert "📋 Этап: 02 Store design (3/17) · чек-лист 40/71" in msg


def test_card_message_checklist_line_all_stages_closed():
    delta = CardDelta(
        task_id=8017, alias="Бишкек 8", task_changes=[], comments=[],
        checklist_done=71, checklist_total=71, files=[],
        new_history_id=0, new_message_id=0,
        has_stages=True, stage_title=None, stage_done=0, stage_total=0,
    )
    msg = render.card_message(delta, "Сводка", "https://p/task/8017/", LOCALES, "ru")
    assert "📋 Все этапы закрыты (71/71)" in msg


def test_card_message_escapes_stage_title():
    delta = CardDelta(
        task_id=8017, alias="Бишкек 8", task_changes=[], comments=[],
        checklist_done=1, checklist_total=2, files=[],
        new_history_id=0, new_message_id=0,
        has_stages=True, stage_title="<script>", stage_done=0, stage_total=1,
    )
    msg = render.card_message(delta, "Сводка", "https://p/task/8017/", LOCALES, "ru")
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg


def test_no_changes_line():
    line = render.no_changes_line("Бишкек 8", "https://p/task/8017/", LOCALES, "ru")
    assert "изменений нет" in line and "Бишкек 8" in line
    assert line.startswith("🏗 <b>")


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


def test_card_message_hides_linkless_file_mentioned_verbatim_in_summary():
    """LLM упомянула имя файла дословно в тексте выжимки -> 📎-страховка не дублирует
    (владелец: без дублей — файл уже в контексте)."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[FileLink(name="план.pdf", url=None)],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(
        delta, "Обсудили план.pdf, согласовали сроки", "https://p/task/1/", LOCALES, "ru"
    )
    assert "📎 план.pdf" not in msg
    assert msg.count("план.pdf") == 1  # только в тексте выжимки, без второго упоминания


def test_card_message_shows_linkless_file_not_mentioned_in_summary():
    """LLM НЕ упомянула файл (например, приоритизация в насыщенный день по новому
    промпту) -> 📎-строка обязана подстраховать: имя не должно пропасть молча (§7)."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[FileLink(name="план.pdf", url=None)],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(
        delta, "Обсудили общий прогресс, всё по графику", "https://p/task/1/", LOCALES, "ru"
    )
    assert "📎 план.pdf" in msg


# --- Кликабельные файлы: инлайн-ссылка в тексте выжимки (§8 фича 2) ---


def test_card_message_inlines_link_when_file_mentioned_in_summary():
    """Файл с url упомянут дословно в тексте выжимки -> имя внутри текста становится
    ссылкой на комментарий-источник, а в 📎-блоке внизу НЕ дублируется."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[FileLink(name="план.pdf", url="https://p/task/1/?commentId=5#com5")],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(
        delta, "Обсудили план.pdf, согласовали сроки", "https://p/task/1/", LOCALES, "ru"
    )
    assert '<a href="https://p/task/1/?commentId=5#com5">план.pdf</a>' in msg
    assert "📎" not in msg  # ссылка уже в тексте — дубль в 📎-блоке не нужен


def test_card_message_shows_file_with_url_in_footer_when_not_mentioned():
    """Файл с url НЕ упомянут дословно в тексте выжимки -> страховка обязана показать
    ссылку в 📎-блоке (та же логика, что у url-less файлов)."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[FileLink(name="план.pdf", url="https://p/task/1/?commentId=5#com5")],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(
        delta, "Обсудили общий прогресс, всё по графику", "https://p/task/1/", LOCALES, "ru"
    )
    assert '📎 <a href="https://p/task/1/?commentId=5#com5">план.pdf</a>' in msg
    assert msg.count("план.pdf") == 1  # только в 📎-блоке, в тексте выжимки имени не было


def test_card_message_linkless_branch_still_works_alongside_inline_linking():
    """Смешанный случай: один файл со ссылкой упомянут (инлайнится в текст), другой без
    ссылки не упомянут (уходит в 📎 как раньше) — ветки не мешают друг другу."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[
            FileLink(name="план.pdf", url="https://p/task/1/?commentId=5#com5"),
            FileLink(name="смета.xlsx", url=None),
        ],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(
        delta, "Обсудили план.pdf, вопросы по бюджету открыты", "https://p/task/1/", LOCALES, "ru"
    )
    assert '<a href="https://p/task/1/?commentId=5#com5">план.pdf</a>' in msg
    assert "📎 смета.xlsx" in msg
    assert "📎 план.pdf" not in msg


def test_fallback_mode_shows_footer_links_without_inlining_names():
    """Fallback (нет LLM-выжимки) — 📎-строки со ссылками для всех файлов с url;
    имена файлов в fallback-тексте (списке сырых изменений) не линкуются."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=["план.pdf: добавлен"], comments=[],
        checklist_done=0, checklist_total=0,
        files=[FileLink(name="план.pdf", url="https://p/task/1/?commentId=5#com5")],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(delta, None, "https://p/task/1/", LOCALES, "ru")
    assert '📎 <a href="https://p/task/1/?commentId=5#com5">план.pdf</a>' in msg
    # в самом fallback-тексте (сырые task_changes) имя НЕ обёрнуто в <a> — только в 📎-блоке
    assert "план.pdf: добавлен" in msg
    assert msg.count("<a ") == 2  # заголовок карточки + одна ссылка в 📎-блоке


# --- Ревью-фикс (Critical): одно-проходная инлайн-линковка — дубликаты/подстроки имён ---


def test_card_message_duplicate_file_names_get_one_anchor_each_no_nesting():
    """Регрессия: два файла с ОДИНАКОВЫМ именем ("IMG.jpg" дважды в тексте) — цепочка
    последовательных .replace() заворачивала уже вставленный <a> повторно (вложенные
    <a><a>...</a></a>), Telegram такое молча ронял (TelegramBadRequest -> карточка не
    отправлялась). Один re.sub-проход обязан обернуть каждое вхождение РОВНО одним <a>,
    без вложенности; дубль имени в mapping — первый url побеждает."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[
            FileLink(name="IMG.jpg", url="https://p/task/1/?commentId=5#com5"),
            FileLink(name="IMG.jpg", url="https://p/task/1/?commentId=9#com9"),
        ],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(
        delta, "Сначала прислали IMG.jpg, потом ещё раз IMG.jpg для сравнения",
        "https://p/task/1/", LOCALES, "ru",
    )
    assert "</a></a>" not in msg and "<a><a" not in msg  # никакой вложенности
    assert msg.count('<a href="https://p/task/1/?commentId=5#com5">IMG.jpg</a>') == 2
    assert "commentId=9" not in msg  # дубль имени — второй url в mapping не используется
    assert "📎" not in msg  # оба вхождения инлайн — footer пуст


def test_card_message_substring_file_names_link_independently():
    """Регрессия: "план.pdf" — подстрока "план.pdf.bak". Последовательный .replace() по
    короткому имени порвал бы длинное пополам. Сортировка альтернации по убыванию длины
    обязана залинковать каждое своим url без пересечений."""
    delta = CardDelta(
        task_id=1, alias="X", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0,
        files=[
            FileLink(name="план.pdf", url="https://p/task/1/?commentId=5#com5"),
            FileLink(name="план.pdf.bak", url="https://p/task/1/?commentId=9#com9"),
        ],
        new_history_id=0, new_message_id=0,
    )
    msg = render.card_message(
        delta, "Актуальный план.pdf и старая копия план.pdf.bak для сверки",
        "https://p/task/1/", LOCALES, "ru",
    )
    assert '<a href="https://p/task/1/?commentId=5#com5">план.pdf</a>' in msg
    assert '<a href="https://p/task/1/?commentId=9#com9">план.pdf.bak</a>' in msg
    assert "</a></a>" not in msg and "<a><a" not in msg
    # план.pdf не должен был "откусить" префикс план.pdf.bak и оставить хвост ".bak" снаружи тега
    assert "</a>.bak" not in msg
    assert "📎" not in msg
