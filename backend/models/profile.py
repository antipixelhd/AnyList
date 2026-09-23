from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, PrivacyLevel


class UserProfileData(Base):
    __tablename__ = "user_profiles"

    id                  : Mapped[int]                  = mapped_column(Integer, primary_key=True)
    user_id             : Mapped[int]                  = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    display_name        : Mapped[Optional[str]]        = mapped_column(String(64))
    bio                 : Mapped[Optional[str]]        = mapped_column(String(5000))
    country             : Mapped[Optional[str]]        = mapped_column(String(2))
    movie_genres        : Mapped[Optional[list[str]]]  = mapped_column(JSONB)
    show_genres         : Mapped[Optional[list[str]]]  = mapped_column(JSONB)
    disliked_genres     : Mapped[Optional[list[str]]]  = mapped_column(JSONB)
    streaming_services  : Mapped[Optional[list[str]]]  = mapped_column(JSONB)
    content_language    : Mapped[Optional[str]]        = mapped_column(String(10))
    metadata_language   : Mapped[Optional[str]]        = mapped_column(String(10))
    privacy_level       : Mapped[PrivacyLevel]         = mapped_column(SQLEnum(PrivacyLevel), default=PrivacyLevel.private, nullable=False, server_default=PrivacyLevel.private.value)
    avatar_path         : Mapped[Optional[str]]        = mapped_column(String(255))
    profile_color       : Mapped[str]                  = mapped_column(String(7), default="#3db4f2", server_default="#3db4f2", nullable=False)
    apply_site_wide     : Mapped[bool]                 = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    user: Mapped["User"] = relationship(back_populates="profile")
