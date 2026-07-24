import html

from src.digest.llm import CardDelta
from src.i18n import t


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


def card_message(delta: CardDelta, summary: str | None, task_url: str, locales, lang: str) -> str:
    header = f'🏗 <b><a href="{html.escape(task_url, quote=True)}">{html.escape(delta.alias)}</a></b>'
    lines = [header, _checklist_line(delta, locales, lang)]
    if summary is not None:
        lines.append(html.escape(summary.strip()))
    else:  # деградация без LLM (§7 п.6)
        lines.append(t(locales, lang, "fallback_notice"))
        lines += [html.escape(ln) for ln in delta.task_changes]
        lines += [f"{html.escape(m.author)}: {html.escape(m.text[:200])}" for m in delta.comments]
    for f in delta.files:
        name = html.escape(f.name)
        if f.url:  # кликабельная ссылка — всегда ценность
            lines.append(f'📎 <a href="{html.escape(f.url, quote=True)}">{name}</a>')
        elif summary is None:  # fallback без LLM: в тексте вложения не упомянуты — не терять
            lines.append(f"📎 {name}")
        # файл без ссылки при живой LLM-выжимке не дублируем: он уже упомянут в контексте
    return clip("\n".join(lines))


def no_changes_line(alias: str, task_url: str, locales, lang: str) -> str:
    return (f'🏗 <b><a href="{html.escape(task_url, quote=True)}">{html.escape(alias)}</a></b> — '
            f"{t(locales, lang, 'no_changes')}")


def clip(text: str, limit: int = 4000) -> str:
    """Страховка от 4096 (§14): срез по границе строки, не разрывая теги."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 1)
    return text[: cut if cut > 0 else limit - 1] + "…"
