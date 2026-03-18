from config import TELEGRAM_SUPER_ADMIN
from database import is_user_allowed


def is_super_admin(user_id: int) -> bool:
    return user_id == TELEGRAM_SUPER_ADMIN


async def check_access(user_id: int) -> bool:
    if is_super_admin(user_id):
        return True
    return await is_user_allowed(user_id)
