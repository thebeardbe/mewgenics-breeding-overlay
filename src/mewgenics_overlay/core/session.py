"""Parsed-save session: cats, relationship maps, partner ranking.

`Session` wraps the vendored parser and mirrors the small amount of
post-processing the Mewgenics Breeding Manager does in its UI layer after
`parse_save` (per-cat inbreeding coefficient from the parents' kinship), then
exposes focused queries for the overlay:

  * pick a cat (by name substring / key)
  * "who can this cat breed with right now, and how good is each pair?"

Pair evaluation uses the vendored `score_pair` (game compatibility formula,
birth-defect risk via coefficient of inbreeding, expected-offspring stat
projection, lover/haters/family blocking) - identical math to MBM.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from mewgenics_overlay.core.kinship import Relation, depths_of, relation as relation_of
from mewgenics_overlay.vendor.save_parser import (
    Cat,
    SaveData,
    parse_save,
    _kinship,
    kinship_coi,
)
from mewgenics_overlay.vendor.breeding import (
    PairFactors,
    game_compatibility,
    is_direct_family_pair,
    score_pair,
)

log = logging.getLogger("mewgenics_overlay.session")

ALIVE_STATUSES = ("In House", "Adventure")

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

RISK_SAFE_TIER = 8.0   # partners at/below this risk % sort above riskier ones
                      # (see the "risk" order in rank_partners)

# The game rejects a mating attempt when its compatibility score falls below
# this line. The vendored pair evaluation uses the same value as its default
# ``compat_threshold``; naming it here keeps the overlay's own wording and
# the vendor math agreeing on one value.
GAME_COMPATIBILITY_MIN = 0.05

# Save property values are decoded for display/detection only; cap each one
# so a large blob can never be retained in memory.
SAVE_PROPERTY_MAX_CHARS = 64

# Overlay label for a parent-child / sibling block (one source of truth).
FAMILY_BLOCK_REASON = "Direct family pair"


def display_location(cat) -> str:
    """Human-friendly location label.

    Cats with status 'In House' but no assigned room/adventure box are simply
    standing on screen - they are NOT inside a room, so label them
    'Outside house' rather than implying they can breed from a room.
    """
    status = getattr(cat, "status", "") or ""
    room = (getattr(cat, "room", "") or "").strip()
    if status == "Adventure":
        return "Adventure"
    if status == "In House":
        return room if room else "Outside house"
    return status or "Gone"


def blocked_reason(cat: Cat, partner: Cat, vendored_reason: str,
                   direct_family: bool = False) -> str:
    """Overlay wording for why a partner row is blocked (pure).

    The vendored ``can_breed`` rejects every same-gender pair with a single
    message before the game's compatibility gate is considered, so a
    straight same-sex pair is described as "mates but produces no kitten"
    even when its compatibility is below the line and the game would never
    attempt it. Direct family pairs keep the overlay label; same-gender pairs
    are re-described from their real compatibility value; every other pair
    keeps the vendored reason unchanged. All returned strings are em-dash
    free.
    """
    if direct_family:
        return FAMILY_BLOCK_REASON
    gender_a = (getattr(cat, "gender", "?") or "?").strip().lower()
    gender_b = (getattr(partner, "gender", "?") or "?").strip().lower()
    # Mirror the vendored can_breed: neutral ("?") pairs are never same-sex.
    if gender_a == gender_b and gender_a != "?":
        compat = game_compatibility(cat, partner)
        if compat < GAME_COMPATIBILITY_MIN:
            return (f"This pair will not mate, compatibility {compat:.3f} "
                    f"is below the game's {GAME_COMPATIBILITY_MIN:.2f} line")
        return (f"Same-gender pair, compatibility {compat:.3f} is at or "
                f"above the game's {GAME_COMPATIBILITY_MIN:.2f} line: they "
                f"mate but produce no kitten and raise the Gay Stray chance")
    return vendored_reason


def _build_key_maps(
    cats: list[Cat],
) -> tuple[dict[int, set[int]], dict[int, set[int]], dict[int, set[int]]]:
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
    defect_rows: list = field(repr=False, default=None)
    # inheritance rows for the defects the parents carry; pure function of
    # (cat_a, cat_b, coi, stimulation) so the UI worker computes it ONCE per
    # pair and table/tooltip/best-match rendering reuse it instead of
    # re-deriving it 3-4x per row.
    defect_rows_ok: bool = True   # False = worker failed to compute (≠ no defects)


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
        self.npc_progress_flags: set = set()
        self.save_properties: dict = {}
        self._parent_map: dict[int, set[int]] = {}
        self._lover_map: dict[int, set[int]] = {}
        self._hater_map: dict[int, set[int]] = {}
        self.load()

    # ── loading ────────────────────────────────────────────────────────────
    def load(self) -> None:
        self.data = parse_save(self.save_path)
        self.cats = list(self.data.cats)
        self._finish_cats(self.cats)
        self._parent_map, self._lover_map, self._hater_map = _build_key_maps(self.cats)
        # current_day + npc_progress + the properties table all live in the
        # same sqlite file the parser just opened, so read them in ONE extra
        # read-only connection.
        (self.current_day, self.npc_progress_flags,
         self.save_properties) = _read_aux_save_data(self.save_path)

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
    def dead_cats(self) -> list[Cat]:
        """Cats that have died (is_dead flag) - candidates for the Organ
        Grinder, who takes the dead."""
        return [c for c in self.cats if getattr(c, "is_dead", False)]

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
        show_blocked: Optional[int] = None,
        quality_floor: Optional[float] = None,
        order: str = "risk",
        stimulation: float = 50.0,
    ) -> list[PartnerRow]:
        """Rank breeding partners for *cat* using MBM's exact pair math.

        Compatible, non-family pairs come first, then optionally up to
        *show_blocked* rejected pairs with their reasons (direct family,
        hater conflict, hard sexuality block, …).

        ``order`` controls how compatible pairs are sorted:

          * ``"risk"`` (default) - safe partners first (risk <= 8 %), then
            higher-risk ones, each tier by quality. Best for the overlay's
            "which pair should I actually use now" question.
          * ``"quality"`` - MBM's full quality score (expected stats minus
            risk/variance penalties, plus lover bonuses).

        ``stimulation`` is the breeding room's furniture Stimulation value
        (default 50) and feeds the inheritance math via ``score_pair``.
        """
        pool = self.alive if include_adventure else self.in_house
        lover_map, hater_map = self._lover_map, self._hater_map
        parent_map = self._parent_map

        # One shared kinship memo for the whole loop: partner ancestries
        # overlap heavily, so a shared memo turns each fresh deep-lineage
        # walk into dict lookups (the vendored engine documents this use).
        kinship_memo: dict = {}
        # The focused cat's ancestry is identical for every partner - trace
        # it once and reuse it for every relation label.
        focus_depths = depths_of(cat)

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
                kinship_memo=kinship_memo,
                stimulation=stimulation,
            )
            family = is_direct_family_pair(cat, b, parent_map)
            # The vendored can_breed rejects same-gender pairs before the
            # game's compatibility gate runs, so blocked_reason re-describes
            # those rows with their real compatibility value.
            ok = bool(factors.compatible and not family)
            reason = "" if ok else blocked_reason(cat, b, factors.reason, family)
            proj = factors.projection
            is_lover = b.db_key in lover_map.get(cat.db_key, set())
            row = PartnerRow(
                partner=b,
                compatible=ok,
                reason=reason,
                risk_pct=factors.risk,
                game_compat=factors.game_compat,
                expected_avg=proj.avg_expected,
                stat_sum_range=proj.sum_range,
                seven_plus_total=proj.seven_plus_total,
                direct_family=family,
                relation=relation_of(cat, b, first_depths=focus_depths),
                coi=kinship_coi(cat, b, kinship_memo),
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
            good.sort(key=lambda r: (0 if r.risk_pct <= RISK_SAFE_TIER else 1,
                                     -r.quality, r.risk_pct))
        else:
            good.sort(key=lambda r: (-r.quality, r.risk_pct))
        blocked.sort(key=lambda r: (r.direct_family, r.is_hater, r.reason))

        rows = good[:max_partners]
        if show_blocked is None:
            rows = rows + blocked            # show every blocked cat
        elif show_blocked > 0:
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

def _read_aux_save_data(save_path: str) -> tuple[Optional[int], set, dict]:
    """Best-effort extra save fields: (day, npc_progress flags, properties).

    All three live in the same read-only sqlite database the parser already
    opened (``properties/current_day``, the ``files/npc_progress`` blob, and
    the ``properties`` key/value table), so they are read together in ONE
    connection. Property values are decoded as UTF-8 with errors replaced and
    truncated to ``SAVE_PROPERTY_MAX_CHARS`` so no large blob is retained. On
    any problem returns ``(None, empty set, empty dict)`` - the overlay
    degrades gracefully without them.
    """
    try:
        conn = sqlite3.connect(f"file:{save_path}?mode=ro", uri=True)
        try:
            day: Optional[int] = None
            row = conn.execute(
                "SELECT data FROM properties WHERE key='current_day'"
            ).fetchone()
            if row is not None:
                day = int(row[0])
            flags: set = set()
            row = conn.execute(
                "SELECT data FROM files WHERE key='npc_progress'").fetchone()
            if row:
                names = re.findall(rb"[A-Za-z_][A-Za-z0-9_]{2,}", row[0])
                flags = {n.decode(errors="replace") for n in names}
            properties: dict = {}
            for key, value in conn.execute("SELECT key, data FROM properties"):
                if isinstance(value, (bytes, bytearray)):
                    text = bytes(value).decode("utf-8", errors="replace")
                elif value is None:
                    text = ""
                else:
                    text = str(value)
                properties[str(key)] = text[:SAVE_PROPERTY_MAX_CHARS]
        finally:
            conn.close()
        return day, flags, properties
    except sqlite3.Error as exc:
        # corrupt/locked save: degrade gracefully but stay discoverable
        log.warning("aux save data unreadable for %s: %s", save_path, exc)
        return None, set(), {}
    except Exception:
        log.exception("unexpected error reading aux save data for %s",
                      save_path)
        return None, set(), {}

