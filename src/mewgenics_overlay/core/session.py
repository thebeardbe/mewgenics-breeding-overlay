"""Parsed-save session: cats, relationship maps, partner ranking.

`Session` wraps the vendored parser and mirrors the small amount of
post-processing the Mewgenics Breeding Manager does in its UI layer after
`parse_save` (per-cat inbreeding coefficient from the parents' kinship), then
exposes focused queries for the overlay:

  * pick a cat (by name substring / key)
  * "who can this cat breed with right now, and how good is each pair?"

Pair evaluation uses the vendored `score_pair` (game compatibility formula,
birth-defect risk via coefficient of inbreeding, expected-offspring stat
projection, lover/haters/family blocking) — identical math to MBM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from mewgenics_overlay.core.kinship import Relation, relation as relation_of
from mewgenics_overlay.vendor.save_parser import (
    Cat,
    SaveData,
    parse_save,
    _kinship,
    kinship_coi,
)
from mewgenics_overlay.vendor.breeding import (
    PairFactors,
    is_direct_family_pair,
    is_mutual_lover_pair,
    score_pair,
)

log = logging.getLogger("mewgenics_overlay.session")

ALIVE_STATUSES = ("In House", "Adventure")


def display_location(cat) -> str:
    """Human-friendly location label.

    Cats with status 'In House' but no assigned room/adventure box are simply
    standing on screen — they are NOT inside a room, so label them
    'Outside house' rather than implying they can breed from a room.
    """
    status = getattr(cat, "status", "") or ""
    room = (getattr(cat, "room", "") or "").strip()
    if status == "Adventure":
        return "Adventure"
    if status == "In House":
        return room if room else "Outside house"
    return status or "Gone"


def _build_key_maps(cats: list[Cat]) -> tuple[dict, dict, dict]:
    parent_map: dict[int, set[int]] = {}
    lover_map: dict[int, set[int]] = {}
    hater_map: dict[int, set[int]] = {}
    for c in cats:
        parent_map[c.db_key] = {p.db_key for p in (c.parent_a, c.parent_b) if p is not None}
        lover_map[c.db_key] = {l.db_key for l in getattr(c, "lovers", [])}
        hater_map[c.db_key] = {h.db_key for h in getattr(c, "haters", [])}
    return parent_map, lover_map, hater_map


@dataclass(slots=True)
class PartnerRow:
    """One candidate partner with the overlay's headline numbers."""

    partner: Cat
    compatible: bool
    reason: str
    risk_pct: float              # combined birth-defect chance, 0..100
    game_compat: float           # the game's own compatibility value (>0.05 passes)
    expected_avg: float          # expected offspring stat average (0..7 scale)
    stat_sum_range: tuple[int, int]
    seven_plus_total: float      # expected # of offspring stats >= 7
    direct_family: bool          # parent-child / sibling (only when shown)
    relation: Relation           # how the partner relates to the focused cat
    coi: float                   # inbreeding coefficient of the pair (0..1)
    mutual_lover: bool
    is_lover: bool
    is_hater: bool
    generation: int
    kitty_total: int = 0        # kittens this pair has already produced
    kitty_available: int = 0    # ... still in house/on adventures (not dead/gone)
    quality: float = 0.0
    pair_factors: PairFactors = field(repr=False, default=None)


@dataclass(slots=True)
class CatSummary:
    """Small display-ready projection of a cat (no raw blob references)."""

    db_key: int
    name: str
    gender: str
    age: Optional[int]
    room: str
    status: str
    generation: int
    base_stats: dict
    total_stats: dict
    lover_names: list[str]
    inbredness: float
    breed_id: int
    unique_id: str

    @property
    def stat_sum(self) -> int:
        return sum(self.base_stats.values())

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.age is not None:
            parts.append(f"{self.age}d")
        parts.append(self.room or self.status)
        return " · ".join(parts)


class Session:
    """A parsed save plus derived lookup structures."""

    def __init__(self, save_path: str):
        self.save_path = str(save_path)
        self.data: SaveData | None = None
        self.cats: list[Cat] = []
        self._parent_map: dict = {}
        self._lover_map: dict = {}
        self._hater_map: dict = {}
        self.load()

    # ── loading ────────────────────────────────────────────────────────────
    def load(self) -> None:
        self.data = parse_save(self.save_path)
        self.cats = list(self.data.cats)
        self._finish_cats(self.cats)
        self._parent_map, self._lover_map, self._hater_map = _build_key_maps(self.cats)

    @staticmethod
    def _finish_cats(cats: list[Cat]) -> None:
        """Mirror MBM's per-cat enrichment (COI from parents' kinship)."""
        memo: dict = {}
        for c in cats:
            pa, pb = c.parent_a, c.parent_b
            if pa is not None and pb is not None and pa is not pb:
                c.inbredness = _kinship(pa, pb, memo)
            else:
                c.inbredness = 0.0

    # ── indexes ────────────────────────────────────────────────────────────
    @property
    def by_key(self) -> dict[int, Cat]:
        return {c.db_key: c for c in self.cats}

    @property
    def alive(self) -> list[Cat]:
        return [c for c in self.cats if c.status in ALIVE_STATUSES]

    @property
    def in_house(self) -> list[Cat]:
        return [c for c in self.cats if c.status == "In House"]

    def search(self, text: str, limit: int = 40) -> list[Cat]:
        """Find alive cats whose name contains *text* (case-insensitive)."""
        t = text.strip().lower()
        if not t:
            return []
        hits = [c for c in self.alive if t in c.name.lower()]
        hits.sort(key=lambda c: (c.name.lower(), c.db_key))
        return hits[:limit]

    # ── partner ranking ────────────────────────────────────────────────────
    def rank_partners(
        self,
        cat: Cat,
        max_partners: int = 30,
        include_adventure: bool = True,
        show_blocked: int = 0,
        quality_floor: Optional[float] = None,
        order: str = "risk",
        stimulation: float = 50.0,
    ) -> list[PartnerRow]:
        """Rank breeding partners for *cat* using MBM's exact pair math.

        Compatible, non-family pairs come first, then optionally up to
        *show_blocked* rejected pairs with their reasons (direct family,
        hater conflict, hard sexuality block, …).

        ``order`` controls how compatible pairs are sorted:

          * ``"risk"`` (default) — safe partners first (risk <= 8 %), then
            higher-risk ones, each tier by quality. Best for the overlay's
            "which pair should I actually use now" question.
          * ``"quality"`` — MBM's full quality score (expected stats minus
            risk/variance penalties, plus lover bonuses).

        ``stimulation`` is the breeding room's furniture Stimulation value
        (default 50) and feeds the inheritance math via ``score_pair``.
        """
        pool = self.alive if include_adventure else self.in_house
        lover_map, hater_map = self._lover_map, self._hater_map
        parent_map = self._parent_map

        good: list[PartnerRow] = []
        blocked: list[PartnerRow] = []
        for b in pool:
            if b.db_key == cat.db_key:
                continue
            factors = score_pair(
                cat,
                b,
                hater_key_map=hater_map,
                lover_key_map=lover_map,
                avoid_lovers=False,   # lover exclusivity is not a hard block in-game
                parent_key_map=parent_map,
                stimulation=stimulation,
            )
            family = is_direct_family_pair(cat, b, parent_map)
            proj = factors.projection
            is_lover = b.db_key in lover_map.get(cat.db_key, set())
            row = PartnerRow(
                partner=b,
                compatible=factors.compatible and not family,
                reason=factors.reason,
                risk_pct=factors.risk,
                game_compat=factors.game_compat,
                expected_avg=proj.avg_expected,
                stat_sum_range=proj.sum_range,
                seven_plus_total=proj.seven_plus_total,
                direct_family=family,
                relation=relation_of(cat, b),
                coi=kinship_coi(cat, b),
                mutual_lover=bool(is_lover and cat.db_key in lover_map.get(b.db_key, set())),
                is_lover=is_lover,
                is_hater=cat.db_key in hater_map.get(b.db_key, set()),
                generation=b.generation,
                quality=factors.quality,
                pair_factors=factors,
            )
            (good if row.compatible else blocked).append(row)

        if quality_floor is not None:
            good = [r for r in good if r.quality >= quality_floor]

        if order == "risk":
            good.sort(key=lambda r: (0 if r.risk_pct <= 8.0 else 1,
                                     -r.quality, r.risk_pct))
        else:
            good.sort(key=lambda r: (-r.quality, r.risk_pct))
        blocked.sort(key=lambda r: (r.direct_family, r.is_hater, r.reason))

        rows = good[:max_partners]
        if show_blocked > 0:
            rows = rows + blocked[:show_blocked]
        return rows

    def summary(self, cat: Cat) -> CatSummary:
        return CatSummary(
            db_key=cat.db_key,
            name=cat.name,
            gender=getattr(cat, "gender", "?"),
            age=cat.age,
            room=cat.room or "",
            status=cat.status,
            generation=cat.generation,
            base_stats=dict(cat.base_stats),
            total_stats=dict(cat.total_stats),
            lover_names=[l.name for l in getattr(cat, "lovers", [])],
            inbredness=cat.inbredness,
            breed_id=cat.breed_id,
            unique_id=cat.unique_id,
        )
