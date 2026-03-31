_langs: dict[int, str] = {}


def get_lang(user_id: int) -> str:
    return _langs.get(user_id, "ru")


def toggle_lang(user_id: int) -> str:
    lang = "en" if get_lang(user_id) == "ru" else "ru"
    _langs[user_id] = lang
    return lang
