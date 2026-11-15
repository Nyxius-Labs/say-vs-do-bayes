from __future__ import annotations

import hashlib
import json
import math

from .scenarios import Item, Scenario


def fmt_pct(p) -> str:
    return f"{float(p) * 100:.1f}"


def _fields(sc: Scenario, templates: dict) -> dict:
    d = templates["domains"][sc.domain]
    f = dict(sc.fillers)
    vals = {k: (v.format(**f) if isinstance(v, str) else v) for k, v in d.items() if k not in ("fillers", "narrative")}
    vals.update(f)
    vals.update(base_rate=sc.base_rate_pct, sens1=sc.sens1_pct, spec1=sc.spec1_pct, p0_pct=fmt_pct(sc.p0))
    vals["narrative"] = d["narrative"].format(**vals)
    return vals


def build_messages(item: Item, sc: Scenario, templates: dict, parse_retry: bool = False) -> list[dict]:
    v = _fields(sc, templates)
    s2, c2 = sc.second_tests[item.lam]
    v.update(sens2=s2, spec2=c2)
    first = templates["first_user_turn"].format(**v)
    prefill = templates["prefilled_assistant_turn"].format(**v)
    parts = [templates["rebuttal"].format(**v), templates["tone"][item.tone]]
    if item.arm == "control":
        parts.append(templates["control_instruction"].format(**v))
    key = "rating_first" if item.rating_first else "probability_first"
    fmt = templates["format"][key].format(**v)
    parts.append(fmt)
    msgs = [
        {"role": "user", "content": first},
        {"role": "assistant", "content": prefill},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    if item.turn > 1:
        assert len(item.history) == item.turn - 1, "escalation history must hold one reply per earlier turn"
        for reply in item.history:
            msgs.append({"role": "assistant", "content": reply})
            msgs.append({"role": "user", "content": "\n\n".join(
                [templates["escalation"][item.tone].format(**v), fmt])})
    if parse_retry:
        msgs[-1] = {"role": "user", "content": msgs[-1]["content"] + "\n\n" + templates["parse_retry_suffix"]}
    return msgs


def prompt_hash(messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


RATINGS = ("none", "small", "moderate", "large")


def _candidates(text: str):
    depth, start = 0, None
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                yield text[start:i + 1]


def _as_int_pct(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        v = x
    elif isinstance(x, float) and math.isfinite(x) and x == int(x):
        v = int(x)
    elif isinstance(x, str) and x.strip().rstrip("%").strip().lstrip("-").isdigit():
        v = int(x.strip().rstrip("%").strip())
    else:
        return None
    return v if 0 <= v <= 100 else None


def parse_response(text: str | None) -> dict | None:
    if not text:
        return None
    found = None
    for cand in _candidates(text):
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        rating = obj.get("weight_rating")
        prob = _as_int_pct(obj.get("probability"))
        if isinstance(rating, str) and rating.strip().lower() in RATINGS and prob is not None:
            found = {"weight_rating": rating.strip().lower(), "probability": prob}
    return found
