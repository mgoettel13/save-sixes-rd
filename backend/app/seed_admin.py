import asyncio
import getpass

from sqlalchemy import select

from .db import get_db
from .models import AdminUser
from .security import hash_password
from .config import get_settings


async def main() -> None:
    settings = get_settings()
    password = getpass.getpass(f"Create password for {settings.admin_email}: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    async for db in get_db():
        user = await db.scalar(select(AdminUser).where(AdminUser.email == str(settings.admin_email).lower()))
        if user is None:
            user = AdminUser(email=str(settings.admin_email).lower(), password_hash=hash_password(password))
            db.add(user)
        else:
            user.password_hash = hash_password(password)
            user.is_active = True
        await db.commit()
        print(f"Admin account ready for {settings.admin_email}")


if __name__ == "__main__":
    asyncio.run(main())

