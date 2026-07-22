from src.i18n import load_locales, t


def test_known_key_formats_kwargs():
    locales = load_locales()
    msg = t(locales, "ru", "add_ok", alias="Бишкек 8", task_id=8017)
    assert "Бишкек 8" in msg and "8017" in msg


def test_missing_language_falls_back_to_en():
    locales = load_locales()
    assert t(locales, "sl", "list_empty") == t(locales, "en", "list_empty")


def test_missing_key_returns_key():
    assert t({"en": {}}, "en", "nope") == "nope"


def test_ru_and_en_have_same_keys():
    locales = load_locales()
    assert set(locales["ru"]) == set(locales["en"])


def test_key_missing_in_lang_falls_back_to_en_value():
    locales = {"ru": {"other": "x"}, "en": {"foo": "Hello {name}"}}
    assert t(locales, "ru", "foo", name="World") == "Hello World"
