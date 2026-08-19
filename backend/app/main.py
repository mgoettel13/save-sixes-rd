from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import Base, engine, get_db
from .models import AdminUser, Post
from .schemas import AdminSetupRequest, HealthResponse, LoginRequest, PostCreate, PostResponse, TokenResponse
from .security import create_access_token, hash_password, verify_password
from .seed import INITIAL_POSTS

settings = get_settings()
bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@app.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(AdminUser).where(AdminUser.email == payload.email.lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return {"access_token": create_access_token(str(user.id))}


@app.post("/auth/setup", response_model=TokenResponse, status_code=201)
async def setup_admin(payload: AdminSetupRequest, db: AsyncSession = Depends(get_db)):
    if not settings.admin_setup_token or payload.setup_token != settings.admin_setup_token:
        raise HTTPException(status_code=404, detail="Setup unavailable")
    existing = await db.scalar(select(AdminUser).limit(1))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Admin setup has already been completed")
    admin = AdminUser(email=str(settings.admin_email).lower(), password_hash=hash_password(payload.password))
    db.add(admin)
    await db.flush()
    for item in INITIAL_POSTS:
        post_data = {key: value for key, value in item.items() if key != "date"}
        db.add(Post(**post_data, status="published", published_at=datetime.fromisoformat(f"{item['date']}T12:00:00+00:00"), author_id=admin.id))
    await db.commit()
    return {"access_token": create_access_token(str(admin.id))}


async def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid authentication token") from exc
    user = await db.get(AdminUser, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Admin account unavailable")
    return user


@app.get("/posts", response_model=list[PostResponse])
async def list_published_posts(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(
        select(Post).where(Post.status == "published").order_by(Post.published_at.desc(), Post.created_at.desc())
    )
    return list(result)


@app.post("/admin/posts", response_model=PostResponse, status_code=201)
async def create_post(
    payload: PostCreate,
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.scalar(select(Post).where(Post.slug == payload.slug))
    if existing:
        raise HTTPException(status_code=409, detail="A post with this slug already exists")
    post = Post(**payload.model_dump(), author_id=admin.id)
    if post.status == "published":
        post.published_at = datetime.now(timezone.utc)
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return post
