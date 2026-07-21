"""Team chat: rooms (1-1 DMs and groups), memberships and messages.

Distinct from the AI Copilot's Conversation/Message — this is human-to-human,
org-internal messaging with media (voice notes, video, images, files via
Cloudinary or local storage) and call events (WebRTC calls signalled over
Socket.IO). Rooms are organization-scoped; membership is the access gate.
"""
from datetime import datetime

from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

# Message kinds. CALL/SYSTEM are machine-generated rows in the thread
# ("Call started", "Alice added Bob") — they render as separators, not bubbles.
CHAT_MESSAGE_KINDS = ("TEXT", "AUDIO", "VIDEO", "IMAGE", "FILE", "CALL", "SYSTEM")


class ChatRoom(db.Model):
    __tablename__ = "chat_room"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organization.id"), nullable=False)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(String(120), nullable=True)  # groups only
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    members: Mapped[list["ChatMember"]] = relationship(
        back_populates="room", cascade="all, delete-orphan", lazy="selectin")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="room", cascade="all, delete-orphan", lazy="dynamic")

    def member_ids(self):
        return {m.user_id for m in self.members}

    def serialize(self, for_user_id=None, last_message=None, unread=0):
        data = {
            "id": self.id,
            "is_group": self.is_group,
            "name": self.name,
            "created_by": self.created_by,
            "members": [m.serialize() for m in self.members],
            "unread": unread,
            "last_message": last_message.serialize() if last_message else None,
        }
        if not self.is_group and for_user_id is not None:
            other = next((m for m in self.members if m.user_id != for_user_id), None)
            data["display_name"] = (other.user.full_name or other.user.email) if other else "Me"
        else:
            data["display_name"] = self.name or "Group"
        return data


class ChatMember(db.Model):
    __tablename__ = "chat_member"
    __table_args__ = (
        UniqueConstraint("room_id", "user_id", name="uq_chat_member_room_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("chat_room.id"), nullable=False)
    room: Mapped["ChatRoom"] = relationship(back_populates="members")
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    user: Mapped["User"] = relationship(lazy="selectin")

    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_read_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def serialize(self):
        return {
            "user_id": self.user_id,
            "full_name": self.user.full_name if self.user else None,
            "email": self.user.email if self.user else None,
            "last_read_at": self.last_read_at.isoformat() if self.last_read_at else None,
        }


class ChatMessage(db.Model):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("chat_room.id"), nullable=False)
    room: Mapped["ChatRoom"] = relationship(back_populates="messages")
    sender_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    sender: Mapped["User"] = relationship(lazy="selectin")

    kind: Mapped[str] = mapped_column(String(12), default="TEXT")
    body: Mapped[str] = mapped_column(Text, nullable=True)
    media_url: Mapped[str] = mapped_column(String(500), nullable=True)
    media_type: Mapped[str] = mapped_column(String(100), nullable=True)  # MIME
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # duration, size…

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "room_id": self.room_id,
            "sender_id": self.sender_id,
            "sender_name": (self.sender.full_name or self.sender.email)
                           if self.sender else "System",
            "kind": self.kind,
            "body": self.body,
            "media_url": self.media_url,
            "media_type": self.media_type,
            "meta": self.meta or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
