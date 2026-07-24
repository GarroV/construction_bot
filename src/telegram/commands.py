import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src import repo
from src.bitrix import methods
from src.bitrix.client import BitrixError
from src.i18n import t
from src.telegram.capture import chat_title_of, thread_id_of

log = logging.getLogger(__name__)
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_LANG_RE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.IGNORECASE)
# карточек в Битрикс24 миллион — поиск по названию не годится (владелец зафиксировал
# дизайн). Партнёр либо шлёт голый ID, либо копирует ссылку из браузера вида
# .../company/personal/user/1650/tasks/task/view/42103/ (бывает и .../workgroups/
# group/25/tasks/task/view/42103/) — извлекаем ID регэкспом, префикс пути не важен.
_TASK_URL_RE = re.compile(r"/tasks/task/view/(\d+)")


def _parse_task_ref(text: str) -> int | None:
    """Голое число ИЛИ ссылка на карточку -> ID; иначе None (мусор). \\d+ в регэкспе
    сам останавливается на первом не-цифровом символе — хвост вида ?commentId=1#com1
    или закрывающий /  не мешают. Ссылка может быть где угодно внутри фразы —
    ищем по всему тексту, а не только по началу."""
    stripped = text.strip()
    if stripped.isdigit():
        return int(stripped)
    m = _TASK_URL_RE.search(text)
    return int(m.group(1)) if m else None


_EMPTY_ARGS_FLOWS = {"add": "add", "time": "time", "lang": "lang", "remove": "remove"}


def resolve_empty_args_flow(cmd: str, args: str) -> str | None:
    """Владелец зафиксировал: голая команда (без аргументов) — это диалог, а не
    «Использование: …» (та подсказка — только на непустые НЕвалидные аргументы,
    например /time 9:99 или /add фигня). Возвращает имя диалогового флоу из menu.py
    для add/time/lang/remove, если аргументы пустые/из пробелов; иначе None — ядро
    команды вызывается как обычно (list/start/help диалога не имеют вовсе)."""
    if args.strip():
        return None
    return _EMPTY_ARGS_FLOWS.get(cmd)


async def handle_add(deps, chat, args: str, user_id: int) -> str:
    lang = chat.digest_language
    task_id = _parse_task_ref(args)
    if task_id is None:
        return t(deps.locales, lang, "add_usage")
    try:
        task = await methods.get_task(deps.bx, task_id)
    except BitrixError:
        return t(deps.locales, lang, "add_not_found", task_id=task_id)

    alias = str(task.get("title") or f"#{task_id}")
    bitrix_chat_id = task.get("chatId") or (task.get("chat") or {}).get("id")
    last_history_id = await methods.get_latest_history_id(deps.bx, task_id)
    last_message_id = await methods.get_latest_chat_message_id(
        deps.bx, int(bitrix_chat_id) if bitrix_chat_id else None
    )
    # курсор комментариев «с этого момента» (§5): иначе первый дайджест старой карточки
    # вываливает всю историю task.commentitem.getlist (сотни шт., см. §13 fallback).
    # task.commentitem.getlist — deprecated: на карточке нового типа может ответить ошибкой;
    # /add не должен из-за этого падать целиком (паттерн как в links.resolve_files).
    try:
        last_comment_id = await methods.get_latest_comment_id(deps.bx, task_id)
    except BitrixError as e:
        log.warning("get_latest_comment_id(%s) не удался: %s", task_id, e)
        last_comment_id = 0
    outcome = await repo.add_card(
        deps.pool, chat.id, task_id, alias, user_id,
        last_history_id, last_message_id, last_comment_id,
    )
    key = {"added": "add_ok", "exists": "add_exists", "reactivated": "add_reactivated"}[outcome]
    return t(deps.locales, lang, key, alias=alias, task_id=task_id)


async def handle_remove(deps, chat, args: str) -> str:
    lang = chat.digest_language
    if not args.strip().isdigit():
        return t(deps.locales, lang, "remove_usage")
    task_id = int(args.strip())
    removed = await repo.deactivate_card(deps.pool, chat.id, task_id)
    key = "remove_ok" if removed else "remove_not_tracked"
    return t(deps.locales, lang, key, task_id=task_id)


def _addressed_to_me(message, bot_username: str) -> bool:
    """Строгая адресация в группах: без @username команду игнорируем (в чате могут
    жить другие боты со своей /add). В личке адресация не нужна. Если username
    неизвестен (bot_username пуст) — ведём себя как раньше, не молчим."""
    if not bot_username:
        return True
    chat_type = getattr(getattr(message, "chat", None), "type", "private")
    if chat_type == "private":
        return True
    first_word = ((message.text or "").split() or [""])[0]
    return first_word.lower().endswith(f"@{bot_username.lower()}")


async def handle_start(deps, chat) -> str:
    """Приветствие/справка — партнёры жмут Start первым делом."""
    return t(deps.locales, chat.digest_language, "start_help")


async def handle_list(deps, chat) -> str:
    lang = chat.digest_language
    cards = await repo.list_active_cards(deps.pool, chat.id)
    if not cards:
        return t(deps.locales, lang, "list_empty")
    lines = [t(deps.locales, lang, "list_header")]
    # «↳» — авто-подхваченная подзадача (auto_from не None, §7 фича 1), «•» — ручная /add.
    lines += [
        f"{'↳' if c.auto_from is not None else '•'} {c.alias or '#'} (#{c.bitrix_task_id})"
        for c in cards
    ]
    return "\n".join(lines)


async def handle_lang(deps, chat, args: str) -> str:
    code = args.strip().lower()
    if not _LANG_RE.match(code):
        return t(deps.locales, chat.digest_language, "lang_usage")
    await repo.set_chat_language(deps.pool, chat.id, code)
    return t(deps.locales, code, "lang_ok", code=code)


async def handle_time(deps, chat, args: str) -> str:
    lang = chat.digest_language
    parts = args.split()
    if not parts or not _TIME_RE.match(parts[0]):
        return t(deps.locales, lang, "time_usage")
    hh, mm = parts[0].split(":")
    new_time = dt.time(int(hh), int(mm))

    tz: str | None = None
    if len(parts) > 1:
        try:
            ZoneInfo(parts[1])
        except Exception:
            return t(deps.locales, lang, "time_usage")
        tz = parts[1]
    elif chat.timezone == "UTC":  # §5: дефолтная UTC без явной tz — просим указать
        return t(deps.locales, lang, "time_need_tz", time=parts[0])

    await repo.set_chat_time(deps.pool, chat.id, new_time, tz)
    return t(deps.locales, lang, "time_ok", time=parts[0], tz=tz or chat.timezone)


async def handle_membership(deps, telegram_chat_id: int, new_status: str) -> None:
    """§11: бот выкинут из чата -> деактивировать записи, планировщик чат пропускает."""
    if new_status not in {"kicked", "left"}:
        return
    rows = await deps.pool.fetch(
        "SELECT id FROM chats WHERE telegram_chat_id = $1 AND active", telegram_chat_id
    )
    for r in rows:
        await repo.deactivate_chat(deps.pool, r["id"])


async def _ensure_chat_core(deps, chat_id: int, thread_id, title, user_id: int | None):
    chat = await repo.upsert_chat(deps.pool, chat_id, thread_id, title, deps.settings.default_language)
    if not chat.restricted:
        return chat
    admins = {
        r["telegram_user_id"]
        for r in await deps.pool.fetch(
            "SELECT telegram_user_id FROM chat_admins WHERE chat_id = $1", chat.id
        )
    }
    if user_id is not None and user_id in admins:
        return chat
    return None  # §6: restricted и не в вайтлисте


async def ensure_chat(deps, message: Message):
    """Команды и reply: адресат права — автор сообщения (message.from_user)."""
    return await _ensure_chat_core(
        deps, message.chat.id, thread_id_of(message), chat_title_of(message),
        message.from_user.id if message.from_user else None,
    )


async def ensure_chat_for_callback(deps, callback):
    """Callback-путь инлайн-меню (правка ревью, Critical): callback.message — это
    сообщение БОТА (автор кнопки), не нажавшего. Проверять права по
    callback.message.from_user означало бы сверять id бота с вайтлистом — restricted-чат
    отваливался бы даже для админа в вайтлисте. Чат/тред/title всё равно берём из
    callback.message (там же, где висит кнопка), а user_id — из callback.from_user
    (реально нажавший)."""
    message = callback.message
    return await _ensure_chat_core(
        deps, message.chat.id, thread_id_of(message), chat_title_of(message),
        callback.from_user.id if callback.from_user else None,
    )


def _args_of(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


def build_router(deps) -> Router:
    router = Router()

    def register(cmd: str, core, *, attach_menu: bool = False):
        @router.message(Command(cmd))
        async def _handler(message: Message, _core=core, _attach_menu=attach_menu, _cmd=cmd):
            if not _addressed_to_me(message, getattr(deps, "bot_username", "")):
                return  # голая команда в группе — молчим (адресность при нескольких ботах)
            chat = await ensure_chat(deps, message)
            if chat is None:
                await message.reply(
                    t(deps.locales, deps.settings.default_language, "restricted_denied")
                )
                return
            args = _args_of(message)
            flow = resolve_empty_args_flow(_cmd, args)
            if flow is not None:
                # локальный импорт (не на уровне модуля): menu.py импортирует ядра
                # отсюда же на уровне модуля — импорт на уровне модуля дал бы цикл.
                from src.telegram.menu import (
                    send_add_prompt,
                    send_lang_keyboard,
                    send_rm_keyboard,
                    send_time_prompt,
                )
                flow_fn = {
                    "add": send_add_prompt, "time": send_time_prompt,
                    "lang": send_lang_keyboard, "remove": send_rm_keyboard,
                }[flow]
                await flow_fn(deps, message, chat)
                return
            if _core is handle_add:
                text = await _core(deps, chat, args,
                                   message.from_user.id if message.from_user else 0)
            elif _core in (handle_list, handle_start):
                text = await _core(deps, chat)
            else:
                text = await _core(deps, chat, args)
            if _attach_menu:
                # локальный импорт (не на уровне модуля): menu.py импортирует ядра
                # отсюда же на уровне модуля — импорт на уровне модуля дал бы цикл.
                from src.telegram.menu import build_panel_keyboard
                await message.reply(
                    text, reply_markup=build_panel_keyboard(deps.locales, chat.digest_language)
                )
            else:
                await message.reply(text)

    register("start", handle_start, attach_menu=True)
    register("help", handle_start)
    register("add", handle_add)
    register("remove", handle_remove)
    register("list", handle_list)
    register("lang", handle_lang)
    register("time", handle_time)

    @router.my_chat_member()
    async def _membership(update):  # §11: kicked/left -> active=false
        await handle_membership(deps, update.chat.id, update.new_chat_member.status)

    return router
