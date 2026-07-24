"""Человеческие таймзоны: города/страны сети Dodo International -> IANA.

Партнёр пишет `/time 09:00 белград`, а не `/time 09:00 Europe/Belgrade` — владелец
зафиксировал (скриншот фидбека), что Telegram не отдаёт таймзону юзера, но домен
бота ограничен странами сети, так что конечный список городов/стран известен и
конечен. `resolve_tz` резолвит то, что реально пишут: город/страну на ru или en,
ИЛИ уже готовый IANA-идентификатор.
"""

from zoneinfo import available_timezones

# Множество канонических IANA-идентификаторов считается один раз при импорте
# (available_timezones() перечисляет фактически установленные в tzdata зоны) —
# сравнение по нему НЕ зависит от регистронезависимости файловой системы (на
# macOS/APFS ZoneInfo("europe/belgrade") резолвится, на Linux — нет; членство
# в этом set не читает файлы по пути, а сравнивает строки один в один).
_VALID_ZONES = available_timezones()

# lower-case алиас (ru/en, город или страна) -> канонический IANA-идентификатор.
# Покрыты страны сети Dodo International и их столицы/крупные города.
TZ_ALIASES: dict[str, str] = {
    # Сербия / Белград
    "сербия": "Europe/Belgrade",
    "белград": "Europe/Belgrade",
    "serbia": "Europe/Belgrade",
    "belgrade": "Europe/Belgrade",
    # Черногория / Подгорица
    "черногория": "Europe/Podgorica",
    "подгорица": "Europe/Podgorica",
    "montenegro": "Europe/Podgorica",
    "podgorica": "Europe/Podgorica",
    # Кыргызстан / Бишкек
    "кыргызстан": "Asia/Bishkek",
    "киргизия": "Asia/Bishkek",
    "бишкек": "Asia/Bishkek",
    "kyrgyzstan": "Asia/Bishkek",
    "bishkek": "Asia/Bishkek",
    # Казахстан / Алматы / Астана (страна перешла на единую зону Asia/Almaty)
    "казахстан": "Asia/Almaty",
    "алматы": "Asia/Almaty",
    "астана": "Asia/Almaty",
    "kazakhstan": "Asia/Almaty",
    "almaty": "Asia/Almaty",
    "astana": "Asia/Almaty",
    # Узбекистан / Ташкент
    "узбекистан": "Asia/Tashkent",
    "ташкент": "Asia/Tashkent",
    "uzbekistan": "Asia/Tashkent",
    "tashkent": "Asia/Tashkent",
    # Таджикистан / Душанбе
    "таджикистан": "Asia/Dushanbe",
    "душанбе": "Asia/Dushanbe",
    "tajikistan": "Asia/Dushanbe",
    "dushanbe": "Asia/Dushanbe",
    # Армения / Ереван
    "армения": "Asia/Yerevan",
    "ереван": "Asia/Yerevan",
    "armenia": "Asia/Yerevan",
    "yerevan": "Asia/Yerevan",
    # Грузия / Тбилиси
    "грузия": "Asia/Tbilisi",
    "тбилиси": "Asia/Tbilisi",
    "georgia": "Asia/Tbilisi",
    "tbilisi": "Asia/Tbilisi",
    # Азербайджан / Баку
    "азербайджан": "Asia/Baku",
    "баку": "Asia/Baku",
    "azerbaijan": "Asia/Baku",
    "baku": "Asia/Baku",
    # ОАЭ / Дубай
    "оаэ": "Asia/Dubai",
    "дубай": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "dubai": "Asia/Dubai",
    "united arab emirates": "Asia/Dubai",
    # Турция / Стамбул
    "турция": "Europe/Istanbul",
    "стамбул": "Europe/Istanbul",
    "turkey": "Europe/Istanbul",
    "istanbul": "Europe/Istanbul",
    # Польша / Варшава
    "польша": "Europe/Warsaw",
    "варшава": "Europe/Warsaw",
    "poland": "Europe/Warsaw",
    "warsaw": "Europe/Warsaw",
    # Румыния / Бухарест
    "румыния": "Europe/Bucharest",
    "бухарест": "Europe/Bucharest",
    "romania": "Europe/Bucharest",
    "bucharest": "Europe/Bucharest",
    # Словения / Любляна
    "словения": "Europe/Ljubljana",
    "любляна": "Europe/Ljubljana",
    "slovenia": "Europe/Ljubljana",
    "ljubljana": "Europe/Ljubljana",
    # Хорватия / Загреб
    "хорватия": "Europe/Zagreb",
    "загреб": "Europe/Zagreb",
    "croatia": "Europe/Zagreb",
    "zagreb": "Europe/Zagreb",
    # Литва / Вильнюс
    "литва": "Europe/Vilnius",
    "вильнюс": "Europe/Vilnius",
    "lithuania": "Europe/Vilnius",
    "vilnius": "Europe/Vilnius",
    # Эстония / Таллин
    "эстония": "Europe/Tallinn",
    "таллин": "Europe/Tallinn",
    "таллинн": "Europe/Tallinn",
    "estonia": "Europe/Tallinn",
    "tallinn": "Europe/Tallinn",
    # Финляндия / Хельсинки
    "финляндия": "Europe/Helsinki",
    "хельсинки": "Europe/Helsinki",
    "finland": "Europe/Helsinki",
    "helsinki": "Europe/Helsinki",
    # Кипр / Никосия
    "кипр": "Asia/Nicosia",
    "никосия": "Asia/Nicosia",
    "cyprus": "Asia/Nicosia",
    "nicosia": "Asia/Nicosia",
    # Вьетнам / Ханой / Хошимин
    "вьетнам": "Asia/Ho_Chi_Minh",
    "ханой": "Asia/Ho_Chi_Minh",
    "хошимин": "Asia/Ho_Chi_Minh",
    "vietnam": "Asia/Ho_Chi_Minh",
    "hanoi": "Asia/Ho_Chi_Minh",
    "ho chi minh": "Asia/Ho_Chi_Minh",
    "ho_chi_minh": "Asia/Ho_Chi_Minh",
    # Китай / Ханчжоу
    "китай": "Asia/Shanghai",
    "ханчжоу": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "hangzhou": "Asia/Shanghai",
    # Великобритания / Лондон
    "великобритания": "Europe/London",
    "лондон": "Europe/London",
    "uk": "Europe/London",
    "london": "Europe/London",
    "united kingdom": "Europe/London",
    # Германия / Берлин
    "германия": "Europe/Berlin",
    "берлин": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "berlin": "Europe/Berlin",
    # Молдова / Кишинёв
    "молдова": "Europe/Chisinau",
    "кишинёв": "Europe/Chisinau",
    "кишинев": "Europe/Chisinau",
    "moldova": "Europe/Chisinau",
    "chisinau": "Europe/Chisinau",
    # Беларусь / Минск
    "беларусь": "Europe/Minsk",
    "белоруссия": "Europe/Minsk",
    "минск": "Europe/Minsk",
    "belarus": "Europe/Minsk",
    "minsk": "Europe/Minsk",
    # Россия / Москва
    "россия": "Europe/Moscow",
    "москва": "Europe/Moscow",
    "russia": "Europe/Moscow",
    "moscow": "Europe/Moscow",
}


def resolve_tz(text: str) -> str | None:
    """Строку из /time -> канонический IANA-идентификатор или None (не резолвится).

    Порядок: сначала точный IANA-идентификатор как есть (регистр важен — сверяем
    с `available_timezones()`, а не пробуем `ZoneInfo()` напрямую, потому что
    последний регистронезависим на регистронезависимых ФС вроде macOS/APFS и
    регистрозависим на Linux — набор `available_timezones()` от этого не зависит).
    Если не совпало — словарь алиасов по strip/casefold (казахстан ~ Казахстан ~
    КАЗАХСТАН — casefold агрессивнее lower(), надёжнее для не-ASCII)."""
    stripped = text.strip()
    if not stripped:
        return None
    if stripped in _VALID_ZONES:
        return stripped
    return TZ_ALIASES.get(stripped.casefold())
