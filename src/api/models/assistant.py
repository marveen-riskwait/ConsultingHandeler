"""Compliance Copilot — persisted AI conversations.

The assistant is an *advisory* surface: it drafts, explains and summarises, but
never makes a compliance decision. Conversations are organization-scoped and
tied to the user who opened them; a conversation may optionally be anchored to a
customer so the model can reason about that file (risk, matches, requirements).

Every message is stored so a conversation can be replayed to the model (the API
is stateless) and, just as importantly, so the interaction is auditable.
"""
from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

# Who is speaking. "system" is never surfaced to the user — it carries the
# persona/guardrails and any injected customer context.
MESSAGE_ROLES = ("user", "assistant", "system")


class Conversation(db.Model):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organization.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    # Optional anchor: the customer this conversation is about.
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id"), nullable=True)

    title: Mapped[str] = mapped_column(String(200), default="New conversation")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
        lazy="selectin",
    )

    def serialize(self, with_messages=False):
        data = {
            "id": self.id,
            "title": self.title,
            "customer_id": self.customer_id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "message_count": sum(1 for m in self.messages if m.role != "system"),
        }
        if with_messages:
            data["messages"] = [m.serialize() for m in self.messages
                                if m.role != "system"]
        return data


class Message(db.Model):
    __tablename__ = "assistant_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversation.id"), nullable=False)
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional bookkeeping: model id + token usage for the assistant turn.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "meta": self.meta or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
