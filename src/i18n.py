import json
from pathlib import Path

FALLBACK = "en"


def load_locales(dir: str = "locales") -> dict[str, dict[str, str]]:
    return {p.stem: json.loads(p.read_text()) for p in Path(dir).glob("*.json")}


def t(locales: dict, lang: str, key: str, **kwargs) -> str:
    table = locales.get(lang) or locales.get(FALLBACK) or {}
    template = table.get(key) or (locales.get(FALLBACK) or {}).get(key) or key
    return template.format(**kwargs) if kwargs else template
