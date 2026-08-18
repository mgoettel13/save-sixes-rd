from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class HealthResponse(BaseModel):
    status: str
    database: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PostCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=220)
    excerpt: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    image_url: str | None = None
    status: str = Field(default="draft", pattern="^(draft|published)$")


class PostResponse(PostCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

