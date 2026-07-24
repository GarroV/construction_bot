import html
import re

from src.digest.llm import CardDelta
from src.i18n import t

MESSAGE_LIMIT = 4000  # §14: запас под 4096 (after entities parsing) — общий лимит clip/chunk_blocks


def _checklist_line(delta: CardDelta, locales, lang: str) -> str:
    """Системная строка (не LLM) — этап чек-листа сейчас. Идёт и в LLM-режиме, и в
    fallback (§ дизайн владельца): первый незакрытый этап, «все этапы закрыты» либо
    плоский счётчик, если у чек-листа нет иерархии этапов вообще."""
    if delta.stage_title is not None:
        return t(
            locales, lang, "checklist_stage_line",
            stage=html.escape(delta.stage_title),
            sd=delta.stage_done, st=delta.stage_total,
            done=delta.checklist_done, total=delta.checklist_total,
        )
    if delta.has_stages:
        return t(locales, lang, "checklist_all_closed",
                  done=delta.checklist_done, total=delta.checklist_total)
    return t(locales, lang, "checklist_plain",
             done=delta.checklist_done, total=delta.checklist_total)


def _linkify_mentioned_files(escaped_summary: str, files, summary: str) -> str:
    """Инлайн-ссылка (§8 фича 2): имя файла упомянуто LLM дословно в тексте выжимки —
    превращаем его прямо там в кликабельную ссылку на комментарий-источник, вместо дубля
    в 📎-блоке ниже.

    Ревью-фикс (Critical): раньше это была цепочка последовательных `.replace()` — при
    ДВУХ файлах с одинаковым именем второй `.replace()` заворачивал уже вставленный первым
    `<a>` повторно (`<a><a>...</a></a>`), Telegram такое молча ронял (TelegramBadRequest,
    #send_html), и дайджест карточки не отправлялся. Теперь — один проход `re.sub` по
    альтернации экранированных имён:
    - дедуп по имени: несколько файлов с одинаковым именем -> первый url побеждает, второй
      якорь не нужен (оба всё равно "упомянуты" одним и тем же именем в тексте);
    - альтернация отсортирована по длине имени УБЫВАЮЩЕ, чтобы длинное имя
      ("план.pdf.bak") матчилось раньше своей же подстроки ("план.pdf") и не рвалось пополам;
    - один проход re.sub физически не может вложить `<a>` друг в друга — replacement
      подставляется в результирующую строку, а не сканируется повторно.
    """
    name_to_url: dict[str, str] = {}
    for f in files:
        if f.url and f.name in summary and f.name not in name_to_url:
            name_to_url[f.name] = f.url

    if not name_to_url:
        return escaped_summary

    anchors = {
        html.escape(name): f'<a href="{html.escape(url, quote=True)}">{html.escape(name)}</a>'
        for name, url in name_to_url.items()
    }
    pattern = re.compile("|".join(re.escape(n) for n in sorted(anchors, key=len, reverse=True)))
    return pattern.sub(lambda m: anchors[m.group(0)], escaped_summary)


def card_message(delta: CardDelta, summary: str | None, task_url: str, locales, lang: str) -> str:
    header = f'🏗 <b><a href="{html.escape(task_url, quote=True)}">{html.escape(delta.alias)}</a></b>'
    lines = [header, _checklist_line(delta, locales, lang)]
    if summary is not None:
        escaped_summary = html.escape(summary.strip())
        escaped_summary = _linkify_mentioned_files(escaped_summary, delta.files, summary)
        lines.append(escaped_summary)
    else:  # деградация без LLM (§7 п.6)
        lines.append(t(locales, lang, "fallback_notice"))
        lines += [html.escape(ln) for ln in delta.task_changes]
        lines += [f"{html.escape(m.author)}: {html.escape(m.text[:200])}" for m in delta.comments]
    for f in delta.files:
        name = html.escape(f.name)
        # Страховка от молчаливой потери (§7): файл добавляем в 📎-блок, если LLM не
        # упомянула его имя дословно в тексте выжимки — либо выжимки вообще нет (fallback,
        # там имена никогда не считаются «упомянутыми» — footer-ссылка нужна всем файлам
        # с url). `f.name not in summary` — по сырому (неэкранированному) summary: LLM
        # инструктирована упоминать имена дословно.
        mentioned = summary is not None and f.name in summary
        if f.url:
            if not mentioned:  # иначе уже заинлайнена в тексте выжимки — дубль не нужен
                lines.append(f'📎 <a href="{html.escape(f.url, quote=True)}">{name}</a>')
        elif not mentioned:
            lines.append(f"📎 {name}")
    return clip("\n".join(lines))


def no_changes_line(alias: str, task_url: str, locales, lang: str) -> str:
    return (f'🏗 <b><a href="{html.escape(task_url, quote=True)}">{html.escape(alias)}</a></b> — '
            f"{t(locales, lang, 'no_changes')}")


def clip(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """Страховка от 4096 (§14): срез по границе строки, не разрывая теги."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 1)
    return text[: cut if cut > 0 else limit - 1] + "…"


def chunk_blocks(blocks: list[str], limit: int = MESSAGE_LIMIT) -> list[list[int]]:
    """Дайджест чата одним сообщением (§7 п.7): группирует индексы блоков карточек в
    минимальное число чанков так, чтобы блок ("\\n\\n".join внутри чанка) никогда не
    рвался пополам. Возвращает индексы блоков по чанкам — курсорам нужен маппинг
    чанк -> карточки (§7 п.8: granularity сдвига курсора укрупняется с карточки до чанка).

    Один блок сам по себе длиннее limit получает собственный чанк без дальнейшей резки —
    это уже подстраховано `clip` на уровне рендера отдельного блока (card_message/
    no_changes_line), здесь его резать вторично незачем."""
    chunks: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    for i, block in enumerate(blocks):
        added_len = len(block) + (2 if current else 0)  # + "\n\n" separator, кроме первого в чанке
        if current and current_len + added_len > limit:
            chunks.append(current)
            current = []
            current_len = 0
            added_len = len(block)
        current.append(i)
        current_len += added_len
    if current:
        chunks.append(current)
    return chunks
