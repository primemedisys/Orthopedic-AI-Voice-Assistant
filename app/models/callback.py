from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Callback(Base):
    __tablename__ = "callbacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str] = mapped_column(String(20))
    date_of_birth: Mapped[str | None] = mapped_column(String(20), nullable=True)
    issue: Mapped[str] = mapped_column(Text)
    urgency: Mapped[str] = mapped_column(String(20))                 # routine / urgent / emergency
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())