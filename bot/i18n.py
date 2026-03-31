from database import get_user_lang, set_user_lang


async def get_lang(user_id: int) -> str:
    lang = await get_user_lang(user_id)
    return lang or "ru"


async def set_lang(user_id: int, lang: str):
    await set_user_lang(user_id, lang)


async def toggle_lang(user_id: int) -> str:
    lang = "en" if await get_lang(user_id) == "ru" else "ru"
    await set_lang(user_id, lang)
    return lang
