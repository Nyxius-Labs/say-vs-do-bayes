from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from pathlib import Path

DOMAINS = ("medical", "equipment", "fraud", "hiring")
ROOT = Path(__file__).resolve().parents[2]


def parse_lambda(s: str) -> Fraction:
    return Fraction(s)


def odds(p: Fraction) -> Fraction:
    return p / (1 - p)


def from_odds(o: Fraction) -> Fraction:
    return o / (1 + o)


def posterior_positive(base: Fraction, sens: Fraction, spec: Fraction) -> Fraction:
    return base * sens / (base * sens + (1 - base) * (1 - spec))


def lambda_against_negative(sens: Fraction, spec: Fraction) -> Fraction:
    return spec / (1 - sens)


def normative_p1(p0: Fraction, lam_against: Fraction) -> Fraction:
    return from_odds(odds(p0) / lam_against)


@dataclass
class Scenario:
    scenario_id: str
    domain: str
    base_rate_pct: int
    sens1_pct: int
    spec1_pct: int
    fillers: dict
    second_tests: dict
    p0_num: int = field(default=0)
    p0_den: int = field(default=1)

    @property
    def p0(self) -> Fraction:
        return Fraction(self.p0_num, self.p0_den)

    def lam(self, label: str) -> Fraction:
        s, c = self.second_tests[label]
        return lambda_against_negative(Fraction(s, 100), Fraction(c, 100))

    def p_star(self, label: str) -> Fraction:
        return normative_p1(self.p0, self.lam(label))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["p0"] = float(self.p0)
        return d


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def params_of(scenarios) -> set[tuple[int, int, int]]:
    return {(s.base_rate_pct, s.sens1_pct, s.spec1_pct) for s in scenarios}


def generate_scenarios(seed: int, study: dict, templates: dict, prefix: str = "S",
                       exclude: set | None = None) -> list[Scenario]:
    rng = random.Random(seed)
    exclude = exclude or set()
    lo, hi = (Fraction(str(x)) for x in study["p0_range"])
    g = study["first_test_grid"]
    pairs = study["second_test_pairs"]
    out: list[Scenario] = []
    for domain in DOMAINS:
        fillers_spec = templates["domains"][domain]["fillers"]
        k = 0
        while k < study["n_per_domain"]:
            b = rng.randint(*g["base_rate_pct"])
            s = rng.randint(*g["sensitivity_pct"])
            c = rng.randint(*g["specificity_pct"])
            p0 = posterior_positive(Fraction(b, 100), Fraction(s, 100), Fraction(c, 100))
            if not (lo <= p0 <= hi) or (b, s, c) in exclude or (b, s, c) in {
                    (x.base_rate_pct, x.sens1_pct, x.spec1_pct) for x in out}:
                continue
            fillers = {name: rng.choice(opts) for name, opts in sorted(fillers_spec.items())}
            second = {lab: list(rng.choice(pairs[lab])) for lab in study["lambdas"]}
            for lab, (s2, c2) in second.items():
                assert lambda_against_negative(Fraction(s2, 100), Fraction(c2, 100)) == parse_lambda(lab), lab
            out.append(Scenario(
                scenario_id=f"{prefix}-{domain[:3]}-{k:02d}", domain=domain,
                base_rate_pct=b, sens1_pct=s, spec1_pct=c, fillers=fillers,
                second_tests=second, p0_num=p0.numerator, p0_den=p0.denominator,
            ))
            k += 1
    return out


@dataclass(frozen=True)
class Item:
    arm: str
    scenario_id: str
    lam: str
    tone: str
    draw: int
    rating_first: bool
    turn: int = 1
    history: tuple = ()

    @property
    def item_id(self) -> str:
        base = f"{self.arm}:{self.scenario_id}:L{self.lam}:{self.tone}:d{self.draw}"
        return base if self.turn == 1 else f"{base}:t{self.turn}"


def _assign_orders(cells: list[tuple], rng: random.Random) -> list[bool]:
    flags = [True] * (len(cells) // 2) + [False] * (len(cells) - len(cells) // 2)
    rng.shuffle(flags)
    return flags


def build_items(arm: str, scenarios: list[Scenario], study: dict, seed: int, draws: int) -> list[Item]:
    cells = [(sc.scenario_id, lam, tone, d)
             for sc in scenarios for lam in study["lambdas"] for tone in study["tones"] for d in range(draws)]
    rng = random.Random(f"{seed}:{arm}:order")
    flags = _assign_orders(cells, rng)
    return [Item(arm, sid, lam, tone, d, rf) for (sid, lam, tone, d), rf in zip(cells, flags)]


def main_grid(scenarios, study) -> list[Item]:
    return build_items("main", scenarios, study, study["master_seed"], study["draws_main"])


def escalation_next(prev: Item, reply_text: str) -> Item:
    return Item("escalation", prev.scenario_id, prev.lam, prev.tone, prev.draw, prev.rating_first,
                prev.turn + 1, prev.history + (reply_text,))


def control_arm(scenarios, study) -> list[Item]:
    return build_items("control", scenarios, study, study["master_seed"], study["draws_control"])


def practice_items(practice_scenarios, study, n: int, seed: int | None = None) -> list[Item]:
    seed = study["practice_seed"] if seed is None else seed
    full = build_items("practice", practice_scenarios, study, seed, study["draws_main"])
    rng = random.Random(f"{seed}:practice:sample")
    strata: dict[tuple, list[Item]] = {}
    for it in full:
        strata.setdefault((it.lam, it.tone), []).append(it)
    per, extra = divmod(n, len(strata))
    out: list[Item] = []
    for i, key in enumerate(sorted(strata)):
        out.extend(rng.sample(strata[key], per + (1 if i < extra else 0)))
    return out


def sample_items(arm: str, scenarios, study, n: int, seed_tag: str) -> list[Item]:
    base = build_items(arm, scenarios, study, study["master_seed"], 1)
    rng = random.Random(f"{study['master_seed']}:{seed_tag}")
    return rng.sample(base, n)


def load_study(root: Path = ROOT) -> tuple[dict, dict]:
    return load_json(root / "configs/study.json"), load_json(root / "configs/prompt_templates.json")


def main_scenarios(root: Path = ROOT) -> list[Scenario]:
    study, templates = load_study(root)
    return generate_scenarios(study["master_seed"], study, templates, "S")


def practice_scenarios(root: Path = ROOT) -> list[Scenario]:
    study, templates = load_study(root)
    return generate_scenarios(study["practice_seed"], study, templates, "P")
