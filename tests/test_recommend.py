"""Unit tests for the best-partner recommender (stat-effect aware)."""

from types import SimpleNamespace

from mewgenics_overlay.core.recommend import recommend, effect_stat_net


def cat(name, defects=None):
    return SimpleNamespace(name=name, defects=defects or [],
                           disorders=[], parent_a=None, parent_b=None,
                           visual_mutation_entries=[])


def row(partner, focused, *, risk=2.0, sevens=2.0, exp_avg=5.0, coi=0.0,
        compatible=True):
    return SimpleNamespace(
        partner=partner, compatible=compatible,
        risk_pct=risk, seven_plus_total=sevens, expected_avg=exp_avg,
        coi=coi,
        pair_factors=SimpleNamespace(cat_a=focused, cat_b=partner),
    )


# defect-name -> effect text (what resources.gpak would supply)
EFFECTS = {
    "Arm Birth Defect": "-2 DEX",
    "Leg Birth Defect": "+2 CON",      # positive-stats defect
}


def effect_of(a, b, name):
    return EFFECTS.get(name, "")


def test_sevens_weigh_highest():
    focus = cat("Focus")
    many_sevens = cat("Sevens")     # slightly riskier but far more 7s
    few_sevens = cat("Few")
    rows = [
        row(few_sevens, focus, risk=2.0, sevens=2.0, exp_avg=5.0),
        row(many_sevens, focus, risk=10.0, sevens=6.0, exp_avg=6.0),
    ]
    rec = recommend(rows, focus, effect_of=effect_of)
    assert rec.row.partner is many_sevens  # 6×6 −10 ≫ 6×2 −2


def test_positive_stat_defect_helps_candidate():
    focus = cat("Focus")
    bonus = cat("Bonus", defects=["Leg Birth Defect"])   # +2 CON effect
    clean = cat("Clean")
    rows = [
        row(clean, focus, risk=2.0, sevens=2.0, exp_avg=5.0),
        # identical stats, but this partner passes a +stat defect → wins
        row(bonus, focus, risk=2.0, sevens=2.0, exp_avg=5.0),
    ]
    rec = recommend(rows, focus, effect_of=effect_of)
    assert rec.row.partner is bonus
    assert any("bonus" in b for b in rec.breakdown)


def test_negative_stat_defect_drops_candidate():
    focus = cat("Focus")
    harmed = cat("Harmed", defects=["Arm Birth Defect"])  # -2 DEX effect
    clean = cat("Clean")
    rows = [
        row(clean, focus, risk=2.0, sevens=2.0, exp_avg=5.0),
        # identical stats, but this partner passes a −stat defect → loses
        row(harmed, focus, risk=2.0, sevens=2.0, exp_avg=5.0),
    ]
    assert recommend(rows, focus, effect_of=effect_of).row.partner is clean


def test_stat_parser():
    assert effect_stat_net("+1 CON, -2 INT") == -1
    assert effect_stat_net("-2 SPD") == -2
    assert effect_stat_net("+2 CON") == 2
    assert effect_stat_net("Start each battle with Immobilize") == 0
    assert effect_stat_net("") == 0


def test_incompatible_ignored_and_none_case():
    focus = cat("Focus")
    blocked = cat("Blocked")
    rec = recommend([row(blocked, focus, compatible=False)], focus,
                    effect_of=effect_of)
    assert rec.row is None
    assert recommend([], focus, effect_of=effect_of).row is None
