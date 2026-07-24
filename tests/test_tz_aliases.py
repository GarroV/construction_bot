"""resolve_tz: город/страна (ru/en) ИЛИ готовый IANA-идентификатор -> канонический
IANA или None. Без сети и БД — чистые юнит-тесты."""
from src.telegram.tz_aliases import TZ_ALIASES, resolve_tz


def test_resolve_tz_exact_iana_returned_canonically():
    assert resolve_tz("Europe/Belgrade") == "Europe/Belgrade"
    assert resolve_tz("Asia/Bishkek") == "Asia/Bishkek"


def test_resolve_tz_russian_city_name():
    assert resolve_tz("белград") == "Europe/Belgrade"
    assert resolve_tz("бишкек") == "Asia/Bishkek"


def test_resolve_tz_english_city_name_is_case_insensitive():
    assert resolve_tz("Belgrade") == "Europe/Belgrade"
    assert resolve_tz("BELGRADE") == "Europe/Belgrade"
    assert resolve_tz("belgrade") == "Europe/Belgrade"


def test_resolve_tz_russian_country_name_any_case():
    assert resolve_tz("СЕРБИЯ") == "Europe/Belgrade"
    assert resolve_tz("Сербия") == "Europe/Belgrade"


def test_resolve_tz_strips_surrounding_whitespace():
    assert resolve_tz("  белград  ") == "Europe/Belgrade"


def test_resolve_tz_garbage_returns_none():
    assert resolve_tz("фигня") is None
    assert resolve_tz("Mars/Olympus") is None
    assert resolve_tz("") is None
    assert resolve_tz("   ") is None


def test_resolve_tz_multi_word_alias():
    """Алиасы из нескольких слов (ОАЭ, Вьетнам, UK) — handle_time склеивает
    parts[1:] пробелом перед вызовом resolve_tz."""
    assert resolve_tz("united arab emirates") == "Asia/Dubai"
    assert resolve_tz("ho chi minh") == "Asia/Ho_Chi_Minh"
    assert resolve_tz("United Kingdom") == "Europe/London"


def test_resolve_tz_covers_dodo_international_network_countries():
    """Каждая страна сети из тикета — города/столицы резолвятся в ожидаемый IANA,
    и на ru, и на en."""
    expected = {
        "сербия": "Europe/Belgrade", "serbia": "Europe/Belgrade",
        "белград": "Europe/Belgrade", "belgrade": "Europe/Belgrade",
        "черногория": "Europe/Podgorica", "montenegro": "Europe/Podgorica",
        "подгорица": "Europe/Podgorica", "podgorica": "Europe/Podgorica",
        "кыргызстан": "Asia/Bishkek", "kyrgyzstan": "Asia/Bishkek",
        "бишкек": "Asia/Bishkek", "bishkek": "Asia/Bishkek",
        "казахстан": "Asia/Almaty", "kazakhstan": "Asia/Almaty",
        "алматы": "Asia/Almaty", "almaty": "Asia/Almaty",
        "астана": "Asia/Almaty", "astana": "Asia/Almaty",
        "узбекистан": "Asia/Tashkent", "uzbekistan": "Asia/Tashkent",
        "ташкент": "Asia/Tashkent", "tashkent": "Asia/Tashkent",
        "таджикистан": "Asia/Dushanbe", "tajikistan": "Asia/Dushanbe",
        "душанбе": "Asia/Dushanbe", "dushanbe": "Asia/Dushanbe",
        "армения": "Asia/Yerevan", "armenia": "Asia/Yerevan",
        "ереван": "Asia/Yerevan", "yerevan": "Asia/Yerevan",
        "грузия": "Asia/Tbilisi", "georgia": "Asia/Tbilisi",
        "тбилиси": "Asia/Tbilisi", "tbilisi": "Asia/Tbilisi",
        "азербайджан": "Asia/Baku", "azerbaijan": "Asia/Baku",
        "баку": "Asia/Baku", "baku": "Asia/Baku",
        "оаэ": "Asia/Dubai", "uae": "Asia/Dubai",
        "дубай": "Asia/Dubai", "dubai": "Asia/Dubai",
        "турция": "Europe/Istanbul", "turkey": "Europe/Istanbul",
        "стамбул": "Europe/Istanbul", "istanbul": "Europe/Istanbul",
        "польша": "Europe/Warsaw", "poland": "Europe/Warsaw",
        "варшава": "Europe/Warsaw", "warsaw": "Europe/Warsaw",
        "румыния": "Europe/Bucharest", "romania": "Europe/Bucharest",
        "бухарест": "Europe/Bucharest", "bucharest": "Europe/Bucharest",
        "словения": "Europe/Ljubljana", "slovenia": "Europe/Ljubljana",
        "любляна": "Europe/Ljubljana", "ljubljana": "Europe/Ljubljana",
        "хорватия": "Europe/Zagreb", "croatia": "Europe/Zagreb",
        "загреб": "Europe/Zagreb", "zagreb": "Europe/Zagreb",
        "литва": "Europe/Vilnius", "lithuania": "Europe/Vilnius",
        "вильнюс": "Europe/Vilnius", "vilnius": "Europe/Vilnius",
        "эстония": "Europe/Tallinn", "estonia": "Europe/Tallinn",
        "таллин": "Europe/Tallinn", "tallinn": "Europe/Tallinn",
        "финляндия": "Europe/Helsinki", "finland": "Europe/Helsinki",
        "хельсинки": "Europe/Helsinki", "helsinki": "Europe/Helsinki",
        "кипр": "Asia/Nicosia", "cyprus": "Asia/Nicosia",
        "никосия": "Asia/Nicosia", "nicosia": "Asia/Nicosia",
        "вьетнам": "Asia/Ho_Chi_Minh", "vietnam": "Asia/Ho_Chi_Minh",
        "ханой": "Asia/Ho_Chi_Minh", "hanoi": "Asia/Ho_Chi_Minh",
        "хошимин": "Asia/Ho_Chi_Minh",
        "китай": "Asia/Shanghai", "china": "Asia/Shanghai",
        "ханчжоу": "Asia/Shanghai", "hangzhou": "Asia/Shanghai",
        "великобритания": "Europe/London", "uk": "Europe/London",
        "лондон": "Europe/London", "london": "Europe/London",
        "германия": "Europe/Berlin", "germany": "Europe/Berlin",
        "берлин": "Europe/Berlin", "berlin": "Europe/Berlin",
        "молдова": "Europe/Chisinau", "moldova": "Europe/Chisinau",
        "кишинёв": "Europe/Chisinau", "chisinau": "Europe/Chisinau",
        "беларусь": "Europe/Minsk", "belarus": "Europe/Minsk",
        "минск": "Europe/Minsk", "minsk": "Europe/Minsk",
        "россия": "Europe/Moscow", "russia": "Europe/Moscow",
        "москва": "Europe/Moscow", "moscow": "Europe/Moscow",
    }
    for alias, iana in expected.items():
        assert resolve_tz(alias) == iana, f"{alias!r} -> {iana!r}"
        assert TZ_ALIASES[alias] == iana


def test_lowercase_utc_resolves():
    assert resolve_tz("utc") == "UTC"
    assert resolve_tz("gmt") == "UTC"
