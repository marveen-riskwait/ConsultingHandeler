"""Sanctions watchlist — a local, refreshable copy of public sanctions lists.

Instead of paying a screening vendor for the basics, the platform ingests the
free public lists (OFAC SDN, UN Consolidated, EU) into `SanctionedEntity` and
screens customers against them locally. Each import is recorded in
`WatchlistImport` for provenance (who/when/how many/live-or-sample).
"""
from datetime import datetime

from sqlalchemy import (String, Text, Integer, Boolean, DateTime, JSON,
                        UniqueConstraint, ForeignKey)
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

# Public sanctions sources we ingest.
SANCTION_SOURCES = ("OFAC", "UN", "EU")
SANCTION_ENTITY_TYPES = ("INDIVIDUAL", "ENTITY", "VESSEL", "AIRCRAFT", "OTHER")


class SanctionedEntity(db.Model):
    """One record on a public sanctions list (a person, company or vessel)."""
    __tablename__ = "sanctioned_entity"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_sanctioned_entity_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)      # OFAC / UN / EU
    external_id: Mapped[str] = mapped_column(String(60), nullable=False)  # list-native id
    entity_type: Mapped[str] = mapped_column(String(20), default="ENTITY")

    name: Mapped[str] = mapped_column(String(400), nullable=False)
    # Lowercased/de-punctuated primary name — the column we actually match on.
    name_normalized: Mapped[str] = mapped_column(String(400), index=True, nullable=False)

    aliases: Mapped[list] = mapped_column(JSON, default=list)          # a.k.a names
    aliases_normalized: Mapped[list] = mapped_column(JSON, default=list)
    programs: Mapped[list] = mapped_column(JSON, default=list)         # sanctions programmes
    country: Mapped[str] = mapped_column(String(120), nullable=True)
    remarks: Mapped[str] = mapped_column(Text, nullable=True)

    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "source": self.source,
            "external_id": self.external_id,
            "entity_type": self.entity_type,
            "name": self.name,
            "aliases": self.aliases or [],
            "programs": self.programs or [],
            "country": self.country,
            "remarks": self.remarks,
        }


class WatchlistImport(db.Model):
    """Provenance for one ingestion run of one source."""
    __tablename__ = "watchlist_import"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OK")   # OK / FAILED
    live: Mapped[bool] = mapped_column(Boolean, default=True)       # live fetch vs bundled sample
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(String(300), nullable=True)
    actor_id: Mapped[int] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def serialize(self):
        return {
            "id": self.id,
            "source": self.source,
            "status": self.status,
            "live": self.live,
            "record_count": self.record_count,
            "detail": self.detail,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class SanctionedWallet(db.Model):
    """A blockchain address published on a sanctions list.

    Its own table rather than a field on the entity: screening a wallet is an
    exact lookup on the address, and a designated entity commonly has dozens of
    them across several chains.
    """
    __tablename__ = "sanctioned_wallet"
    __table_args__ = (
        UniqueConstraint("source", "asset", "address_normalized",
                         name="uq_sanctioned_wallet_addr"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    asset: Mapped[str] = mapped_column(String(10), nullable=False)   # XBT, ETH, TRX…
    address: Mapped[str] = mapped_column(String(160), nullable=False)
    # Lowercased for lookup: ETH addresses are case-insensitive hex, and users
    # paste addresses in whatever case their explorer showed them.
    address_normalized: Mapped[str] = mapped_column(
        String(160), index=True, nullable=False)

    entity_id: Mapped[int] = mapped_column(
        ForeignKey("sanctioned_entity.id"), nullable=True)
    entity_name: Mapped[str] = mapped_column(String(400), nullable=True)
    programs: Mapped[list] = mapped_column(JSON, default=list)

    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "source": self.source,
            "asset": self.asset,
            "address": self.address,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "programs": self.programs or [],
        }
