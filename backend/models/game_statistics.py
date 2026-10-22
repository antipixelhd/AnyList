"""Private, atomic Games-only statistical generations."""
from datetime import datetime
from sqlalchemy import BigInteger,DateTime,ForeignKey,Integer,String,func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base


class GameStatsState(Base):
    __tablename__='game_stats_state'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    source_revision:Mapped[int]=mapped_column(BigInteger,nullable=False,server_default='0')
    metadata_source_revision:Mapped[int]=mapped_column(BigInteger,nullable=False,server_default='0')
    computed_source_revision:Mapped[int|None]=mapped_column(BigInteger)
    metadata_revision:Mapped[int|None]=mapped_column(BigInteger)
    generation:Mapped[int]=mapped_column(BigInteger,nullable=False,server_default='0')
    contract_version:Mapped[int]=mapped_column(Integer,nullable=False,server_default='0')
    payload:Mapped[dict|None]=mapped_column(JSONB(none_as_null=True))
    computed_at:Mapped[datetime|None]=mapped_column(DateTime)
    next_due_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,index=True,server_default=func.timezone('UTC',func.now()))
    lease_token:Mapped[str|None]=mapped_column(String(36))
    lease_until:Mapped[datetime|None]=mapped_column(DateTime)
    attempts:Mapped[int]=mapped_column(Integer,nullable=False,server_default='0')
    error_code:Mapped[str|None]=mapped_column(String(32))
