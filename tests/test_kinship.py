"""Unit tests for the kinship classifier (no save file required)."""

from dataclasses import dataclass, field

import pytest

from mewgenics_overlay.core.kinship import relation


@dataclass(eq=False)
class Node:
    """Identity-based family-graph node (parent pointers, generation)."""
    name: str
    generation: int = 0
    parent_a: "Node | None" = field(default=None, repr=False)
    parent_b: "Node | None" = field(default=None, repr=False)


def breed(name: str, a: "Node | None", b: "Node | None") -> Node:
    generation = max((a.generation if a else -1), (b.generation if b else -1)) + 1
    return Node(name=name, generation=generation, parent_a=a, parent_b=b)


@pytest.fixture()
def fam():
    """Two founder couples, sibling pairs, cousins and one level deeper."""
    p1, p2, p3, p4 = (Node(f"p{i}", 0) for i in range(1, 5))
    # couple P (p1 x p2) -> full siblings A1, A2
    a1 = breed("A1", p1, p2)
    a2 = breed("A2", p1, p2)
    # couple Q (p3 x p4) -> full siblings B1, B2
    b1 = breed("B1", p3, p4)
    b2 = breed("B2", p3, p4)
    # children of the two couples -> first cousins to each other
    r1 = breed("r1", a1, b1)      # gen 2
    r2 = breed("r2", a1, b1)      # gen 2, full sibling of r1
    c1 = breed("c1", a2, b2)      # gen 2, full-sib cousin pair
    # one more generation: d1 = c1's child by an unrelated founder. Because
    # c1 is r1's first cousin, d1 is r1's first cousin once removed.
    d1 = breed("d1", c1, Node("u0"))     # gen 3
    # a fully unrelated line
    x1 = breed("x1", Node("u1"), Node("u2"))
    return dict(p1=p1, a1=a1, a2=a2, b1=b1, b2=b2, r1=r1, r2=r2,
                c1=c1, d1=d1, x1=x1)


def test_direct(fam):
    assert relation(fam["r1"], fam["p1"]).label == "grandparent"
    assert relation(fam["p1"], fam["r1"]).label == "grandchild"
    assert relation(fam["r1"], fam["a1"]).label == "parent"
    assert relation(fam["a1"], fam["r1"]).label == "child"
    assert relation(fam["a1"], fam["d1"]).label == "grandniece/nephew"


def test_siblings(fam):
    assert relation(fam["a1"], fam["a2"]).label == "full sibling"
    assert relation(fam["a2"], fam["a1"]).label == "full sibling"
    assert relation(fam["r1"], fam["r2"]).label == "full sibling"
    # a1 and b1 share no parents
    assert relation(fam["a1"], fam["b1"]).label == "unrelated"


def test_avuncular(fam):
    # c1's parent A2 is a sibling of A1 -> c1 is A1's niece/nephew,
    # and A1 is c1's aunt/uncle
    assert relation(fam["a1"], fam["c1"]).label == "niece/nephew"
    assert relation(fam["c1"], fam["a1"]).label == "aunt/uncle"


def test_cousins(fam):
    assert relation(fam["r1"], fam["c1"]).label == "1st cousin"
    assert relation(fam["c1"], fam["r1"]).label == "1st cousin"
    # d1 = c1's child; shared ancestor with r1 is one gen further out
    assert relation(fam["r1"], fam["d1"]).label == "1st cousin once removed"


def test_unrelated(fam):
    r = relation(fam["x1"], fam["r1"])
    assert r.label == "unrelated"
    assert r.shared_ancestors == 0
    assert r.shared_recent == 0
    assert not r.is_family
    # same-line cats do report shared ancestry, recent ancestors only counted
    # when close on both sides
    c = relation(fam["r1"], fam["c1"])
    assert c.shared_ancestors > 0
    assert c.shared_recent == 4   # p1..p4 are grandparents on both sides
    assert c.shared_recent <= c.shared_ancestors


def test_generation_gap(fam):
    assert relation(fam["p1"], fam["d1"]).gen_gap == -3
    assert relation(fam["d1"], fam["p1"]).gen_gap == 3
    assert relation(fam["r1"], fam["c1"]).gen_gap == 0
    assert relation(fam["x1"], fam["r1"]).gen_gap == -1
