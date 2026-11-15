from fractions import Fraction

from dc.scenarios import (DOMAINS, control_arm, generate_scenarios, lambda_against_negative, load_study,
                          main_grid, main_scenarios, normative_p1, posterior_positive, practice_items,
                          practice_scenarios)


def test_exact_posterior_hand_cases():
    assert posterior_positive(Fraction(1, 10), Fraction(9, 10), Fraction(9, 10)) == Fraction(1, 2)
    assert posterior_positive(Fraction(1, 100), Fraction(4, 5), Fraction(9, 10)) == Fraction(8, 107)
    assert normative_p1(Fraction(1, 2), Fraction(4)) == Fraction(1, 5)
    assert normative_p1(Fraction(1, 2), Fraction(1, 4)) == Fraction(4, 5)
    assert normative_p1(Fraction(2, 3), Fraction(1)) == Fraction(2, 3)


def test_second_test_pairs_have_exact_lambda():
    study, _ = load_study()
    for lab, pairs in study["second_test_pairs"].items():
        if lab.startswith("_"):
            continue
        for s, c in pairs:
            assert lambda_against_negative(Fraction(s, 100), Fraction(c, 100)) == Fraction(lab)
        if lab == "1":
            assert all(s == 100 - c for s, c in pairs)


def test_scenario_count_domains_and_p0_range():
    for scs in (main_scenarios(), practice_scenarios()):
        assert len(scs) == 40
        for d in DOMAINS:
            assert sum(s.domain == d for s in scs) == 10
        for s in scs:
            assert Fraction(46, 100) <= s.p0 <= Fraction(83, 100)
            assert s.p0 == posterior_positive(Fraction(s.base_rate_pct, 100), Fraction(s.sens1_pct, 100),
                                              Fraction(s.spec1_pct, 100))
            for lab in ("1/4", "1", "4", "16"):
                assert s.lam(lab) == Fraction(lab)
                assert Fraction(5, 100) <= s.p_star(lab) <= Fraction(95, 100) or lab == "1/4" and s.p_star(lab) <= Fraction(96, 100)


def test_normative_range_matches_spec_check():
    lo, hi = Fraction(46, 100), Fraction(83, 100)
    assert 0.05 <= float(normative_p1(lo, Fraction(16))) and float(normative_p1(hi, Fraction(16))) <= 0.24
    assert 0.77 <= float(normative_p1(lo, Fraction(1, 4))) and float(normative_p1(hi, Fraction(1, 4))) <= 0.952


def test_rejection_sampler_rejects_out_of_range():
    study, templates = load_study()
    narrow = dict(study, p0_range=[0.60, 0.61])
    scs = generate_scenarios(1, narrow, templates)
    assert all(0.60 <= float(s.p0) <= 0.61 for s in scs)


def test_determinism_and_seed_separation():
    a, b = main_scenarios(), main_scenarios()
    assert [s.to_dict() for s in a] == [s.to_dict() for s in b]
    p = practice_scenarios()
    assert {(s.base_rate_pct, s.sens1_pct, s.spec1_pct) for s in a} != {(s.base_rate_pct, s.sens1_pct, s.spec1_pct) for s in p}


def test_grid_cells_and_rating_order_balance():
    study, _ = load_study()
    scs = main_scenarios()
    g = main_grid(scs, study)
    assert len(g) == 960 and len({i.item_id for i in g}) == 960
    assert sum(i.rating_first for i in g) == 480
    c = control_arm(scs, study)
    assert len(c) == 320
    pr = practice_items(practice_scenarios(), study, 200)
    assert len(pr) == 200
    assert all(i.scenario_id.startswith("P-") for i in pr)


def test_attempt2_practice_seed_is_disjoint_from_attempt1():
    import json
    from pathlib import Path
    from dc.scenarios import params_of
    root = Path(__file__).resolve().parents[1]
    gate = json.loads((root / "configs/gate.json").read_text())
    a1, a2 = gate["attempts"]["1"], gate["attempts"]["2"]
    assert a1["practice_seed"] != a2["practice_seed"] and 1 in a2["disjoint_from_attempts"]
    study, templates = load_study()
    main = main_scenarios()
    p1 = generate_scenarios(a1["practice_seed"], study, templates, a1["practice_prefix"])
    assert [s.to_dict() for s in p1] == [s.to_dict() for s in practice_scenarios()]
    p2 = generate_scenarios(a2["practice_seed"], study, templates, a2["practice_prefix"],
                            exclude=params_of(main) | params_of(p1))
    assert len(p2) == 40 and all(sum(s.domain == d for s in p2) == 10 for d in DOMAINS)
    assert params_of(p2).isdisjoint(params_of(p1)) and params_of(p2).isdisjoint(params_of(main))
    assert len(params_of(p2)) == 40
    assert not ({s.scenario_id for s in p2} & {s.scenario_id for s in p1 + main})
    assert all(Fraction(46, 100) <= s.p0 <= Fraction(83, 100) for s in p2)
    items = practice_items(p2, study, a2["n_practice"], seed=a2["practice_seed"])
    assert len(items) == 100 and {i.scenario_id[:1] for i in items} == {"Q"}
