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
    # A customer room belongs to the file, not to a person: the organization
    # talks to the client, and membership follows the team on the case.
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id"), nullable=True, index=True)
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
            "is_customer_room": self.customer_id is not None,
            "customer_id": self.customer_id,
            "name": self.name,
            "created_by": self.created_by,
            "members": [m.serialize() for m in self.members],
            "unread": unread,
            "last_message": last_message.serialize() if last_message else None,
        }
        if self.customer_id is not None:
            data["display_name"] = self.name or "Customer"
        elif not self.is_group and for_user_id is not None:
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


def _sign_media(url):
    if not url:
        return url
    from api.integrations import media
    return media.sign_url(url)


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
        # Both labels travel with the message because one payload is broadcast
        # to everyone in the room: staff need to know which colleague wrote it
        # (accountability, audit), while the client is answered by the
        # organization, not by a named individual. The client picks the label.
        staff_author = bool(self.sender and not self.sender.is_portal_user())
        org = self.sender.organization if self.sender else None
        return {
            "id": self.id,
            "room_id": self.room_id,
            "sender_id": self.sender_id,
            "sender_name": (self.sender.full_name or self.sender.email)
                           if self.sender else "System",
            "sender_avatar": (_sign_media(self.sender.avatar_url)
                              if self.sender and self.sender.avatar_url else None),
            "from_staff": staff_author,
            "organization_name": org.name if org is not None else None,
            "kind": self.kind,
            "body": self.body,
            "media_url": _sign_media(self.media_url),
            "media_type": self.media_type,
            "meta": self.meta or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
