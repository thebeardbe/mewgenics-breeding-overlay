"""Kinship labeling for pairs of cats.

Given two cats (any objects exposing ``parent_a`` / ``parent_b`` and
``generation``), describe how they are related in one short human label plus
the generation gap. Pure functions, no Qt, no save parsing — unit-tested with
stub family graphs in tests/test_kinship.py.

Relationship scope is limited to shared ancestry within ``MAX_DEPTH``
generations; pairs with none in that window are labelled "unrelated" even
though every house cat ultimately descends from strays.

Semantics of the label: it always describes the *second* cat (the partner)
from the *first* cat's (the focused cat's) point of view, e.g. label
"niece/nephew" means the partner is the focused cat's niece/nephew.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from typing import Optional

MAX_DEPTH = 9    # generations of shared ancestry considered for labels
RECENT_DEPTH = 4  # both sides must be within this many gens to 'count' for defects


def depths_of(cat, max_depth: int = MAX_DEPTH) -> dict:
    """cat -> minimum generational distance map (self = 0, parents = 1, ...).

    Public so callers ranking many pairs against ONE focused cat can trace
    that cat's ancestry once and hand it to :func:`relation` as
    ``first_depths`` instead of re-walking it for every partner.
    """
    return _depths(cat, max_depth)


def _depths(cat, max_depth: int = MAX_DEPTH) -> dict:
    """cat -> minimum generational distance (self = 0, parents = 1, ...)."""
    if cat is None:
        return {}
    depths = {cat: 0}
    frontier = deque([(cat, 0)])
    while frontier:
        cur, d = frontier.popleft()
        if d >= max_depth:
            continue
        for parent in (getattr(cur, "parent_a", None), getattr(cur, "parent_b", None)):
            if parent is None:
                continue
            nd = d + 1
            if nd < depths.get(parent, nd + 1):
                depths[parent] = nd
                frontier.append((parent, nd))
    return depths


def _parents(cat) -> set:
    out = set()
    for p in (getattr(cat, "parent_a", None), getattr(cat, "parent_b", None)):
        if p is not None:
            out.add(p)
    return out


def _generation(cat) -> int:
    return int(getattr(cat, "generation", 0) or 0)


@dataclass(frozen=True)
class Relation:
    """How the second cat relates to the first."""

    label: str                 # e.g. "half sibling", "1st cousin once removed"
    shared_ancestors: int      # common ancestors found within the depth scope
    shared_recent: int         # ... that are recent on BOTH sides (<= RECENT_DEPTH)
    gen_gap: int               # generation(first) - generation(second)

    @property
    def is_family(self) -> bool:
        return self.label != "unrelated"


_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}
for _n in range(4, 21):
    _ORDINALS[_n] = f"{_n}th"


def _ordinal(n: int) -> str:
    return _ORDINALS.get(n, f"{n}th")


def _ascending_chain(d: int, noun: str) -> str:
    """Ancestor labels: d=1 parent, d=2 grandparent, d>=3 great-great-..."""
    if d <= 1:
        return noun
    return "great-" * (d - 2) + f"grand{noun}"


def _descending_chain(d: int, noun: str) -> str:
    """Descendant labels: d=1 child, d=2 grandchild, d>=3 great-grand..."""
    return _ascending_chain(d, noun)


def _collateral(da: int, db: int) -> str:
    """Nearest shared ancestor is *da* generations above cat A, *db* above B.

    A is the focused cat, B the partner; the label always describes B from
    A's point of view, so orientation (which side is closer to the shared
    ancestor) matters: the closer side is the older generation.

    ``da == db == 1`` (shared parents) is unreachable here: relation()
    returns the full/half-sibling label before ever calling this.
    """
    if db == 1:
        # B is only one step from the shared ancestor -> B is the elder:
        # B is A's aunt/uncle (A is 2 up), great-aunt/uncle (3 up), …
        if da == 2:
            return "aunt/uncle"
        return "great-" * (da - 2) + "aunt/uncle"
    if da == 1:
        # A is the elder: B is A's niece/nephew (2 up), grandniece/nephew
        # (3 up), great-grandniece/nephew (4 up), …
        if db == 2:
            return "niece/nephew"
        if db == 3:
            return "grandniece/nephew"
        return "great-" * (db - 3) + "grandniece/nephew"
    # classic cousins: (min-1)-th cousins, |da-db| times removed
    k = min(da, db) - 1
    removed = abs(da - db)
    base = f"{_ordinal(k)} cousin"
    if removed == 0:
        return base
    if removed == 1:
        return f"{base} once removed"
    if removed == 2:
        return f"{base} twice removed"
    return f"{base} {removed}× removed"


def relation(first, second,
             first_depths: Optional[dict] = None) -> Relation:
    """Label *second* from *first*'s point of view.

    ``first_depths`` (from :func:`depths_of`) may be passed to reuse a
    precomputed ancestry trace of *first* across many calls.
    """
    da = first_depths if first_depths is not None else _depths(first)
    db = _depths(second)
    common = set(da) & set(db)

    gen_gap = _generation(first) - _generation(second)
    recent = sum(1 for c in common
                 if da[c] <= RECENT_DEPTH and db[c] <= RECENT_DEPTH)

    # direct ancestor / descendant
    d_b = da.get(second)
    if d_b is not None and d_b >= 1:
        # *second* is *first*'s ancestor (parent, grandparent, …)
        return Relation(_ascending_chain(d_b, "parent"), len(common), recent, gen_gap)
    d_a = db.get(first)
    if d_a is not None and d_a >= 1:
        # *second* is *first*'s descendant (child, grandchild, …)
        return Relation(_descending_chain(d_a, "child"), len(common), recent, gen_gap)

    # siblings (shared parents)
    shared_parents = _parents(first) & _parents(second)
    if shared_parents:
        if len(shared_parents) >= 2 and \
                len(_parents(first)) == 2 and len(_parents(second)) == 2:
            label = "full sibling"
        else:
            label = "half sibling"
        return Relation(label, len(common), recent, gen_gap)

    # nearest shared ancestor → collateral degree (keep orientation: which
    # side is closer decides aunt/uncle vs niece/nephew)
    best = None
    best_pair = None
    for c in common:
        cand = (da[c] + db[c], da[c], db[c])
        if best is None or cand < best:
            best = cand
            best_pair = (da[c], db[c])
    if best is None:
        return Relation("unrelated", 0, 0, gen_gap)
    return Relation(_collateral(best_pair[0], best_pair[1]), len(common), recent,
                    gen_gap)
