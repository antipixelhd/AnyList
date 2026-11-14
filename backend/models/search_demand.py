"""Short-lived subscribers to shared public search verification."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SearchDemand(Base):
    __tablename__ = 'search_demand'
    __table_args__ = (Index('ix_search_demand_expiry', 'expires_at'),
                      Index('ix_search_demand_targets', 'targets', postgresql_using='gin'))
    subscriber: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query: Mapped[str] = mapped_column(String(64), nullable=False)
    targets: Mapped[list] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
