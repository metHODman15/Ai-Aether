"""In-memory state tracking for the current conversation topic."""
from __future__ import annotations

from dataclasses import dataclass, field

from .entities import Entities


@dataclass
class TopicState:
    """Tracks the active topic, its rolling summary, and merged entities."""

    label: str = ""
    summary: str = ""
    started_at: float = 0.0
    entities: Entities = field(
        default_factory=lambda: Entities(
            customer_name=None,
            contact_name=None,
            deal_amount=None,
            deal_stage=None,
            keywords=[],
        )
    )

    def reset(self, label: str, summary: str, started_at: float) -> None:
        self.label = label
        self.summary = summary
        self.started_at = started_at
        self.entities = Entities(
            customer_name=None,
            contact_name=None,
            deal_amount=None,
            deal_stage=None,
            keywords=[],
        )

    def merge_entities(self, new: Entities) -> bool:
        """Merge new entities into the topic. Returns True if anything changed."""
        changed = False

        for key in ("customer_name", "contact_name", "deal_stage"):
            value = new.get(key)
            if value and value != self.entities.get(key):
                self.entities[key] = value
                changed = True

        amt = new.get("deal_amount")
        if amt is not None and amt != self.entities.get("deal_amount"):
            self.entities["deal_amount"] = amt
            changed = True

        existing_keywords = set(self.entities.get("keywords") or [])
        merged_keywords = list(existing_keywords)
        for kw in new.get("keywords") or []:
            if kw and kw not in existing_keywords:
                merged_keywords.append(kw)
                existing_keywords.add(kw)
                changed = True
        if changed:
            self.entities["keywords"] = merged_keywords[:8]

        return changed
