from __future__ import annotations
from typing import Any, Dict, List
import re
from datetime import datetime
from difflib import SequenceMatcher

#helpers

def _norm_str(x):
    return (x or "").strip().lower() if x is not None else None

def _parse_number(x):
    try:
        if x is None: return None
        if isinstance(x, (int, float)): return x
        s = str(x).replace(",", ".").strip()
        return float(re.sub(r"[^\d\.\-]", "", s)) if s else None
    except Exception:
        return None

def _parse_date(x):
    if not x: return None
    if isinstance(x, datetime): return x
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(x).strip(), fmt)
        except Exception:
            pass
    return None

def _fuzzy_score(a, b):
    a = _norm_str(a) or ""
    b = _norm_str(b) or ""
    return SequenceMatcher(None, a, b).ratio()

def _cmp(val, op, tgt, rule=None):
    # normalize
    rule = rule or {}
    if op == "exists":
        return val is not None and val != ""
    if op == "==":
        return val == tgt
    if op == "!=":
        return val != tgt
    if op in (">=", "<=", ">", "<"):
        v = _parse_number(val)
        t = _parse_number(tgt)
        if v is None or t is None: return False
        if op == ">=": return v >= t
        if op == "<=": return v <= t
        if op == ">": return v > t
        if op == "<": return v < t
    if op == "in":
        return val in (tgt or [])
    if op == "any_in":
        return bool(set(val or []) & set(tgt or []))
    if op == "contains":
        return (_norm_str(tgt) or "") in (_norm_str(val) or "")
    if op == "matches":
        try:
            return bool(re.search(tgt, str(val or ""), flags=re.IGNORECASE))
        except Exception:
            return False
    if op == "fuzzy":
        # rule may carry threshold (0..1)
        thr = float(rule.get("threshold", 0.7))
        return _fuzzy_score(val, tgt) >= thr
    return False

def _collect(records: List[Dict[str,Any]], field: str) -> List[Any]:
    parts = field.split(".")
    out = []
    for r in records:
        v = r
        for p in parts[1:]:  # skip table prefix
            if not isinstance(v, dict):
                v = None; break
            v = v.get(p)
        out.append(v)
    return out


def evaluate_rule_set(rule_set: Dict[str,Any], dataset: Dict[str,List[Dict[str,Any]]]) -> Dict[str,Any]:
    """
    Enhanced evaluator with weights, fuzzy, regex and explainability.
    rule format (example):
      { "field": "field.cropType", "op": "fuzzy", "value": "pom", "weight": 2, "aggregate":"any", "threshold": 0.6 }
    Returns:
      { passed:bool, score:float (0..1), details:[{rule,passed,weight,contribution,found_values}] }
    """
    details = []
    total_weight = 0.0
    score_acc = 0.0
    # default rules live under "all" (keep existing shape)
    rules = rule_set.get("all", [])

    if not rules:
        return {"passed": True, "score": 1.0, "details": []}

    for rule in rules:
        weight = float(rule.get("weight", 1.0))
        total_weight += weight

        field = rule.get("field")
        op    = rule.get("op")
        tgt   = rule.get("value")
        agg   = rule.get("aggregate", "one")  # one|any|count>= etc.

        table = field.split(".")[0] if field else None
        # rows may be missing; default to []
        rows = dataset.get(table, []) if table else []
        passed = False
        found = []

        if agg == "any":
            vals = _collect(rows, field)
            for v in vals:
                found.append(v)
                if _cmp(v, op, tgt, rule):
                    passed = True
                    break
        elif agg == "count>=":
            vals = _collect(rows, field)
            cnt = sum(1 for v in vals if _cmp(v, "in" if isinstance(tgt, list) else "==", tgt))
            required = int(rule.get("min", 1))
            passed = cnt >= required
            found = vals
        else:  # single-value check (first row)
            if rows:
                v = _collect(rows, field)[0]
            else:
                v = None
            found = [v]
            passed = _cmp(v, op, tgt, rule)

        contrib = weight if passed else 0.0
        score_acc += contrib

        details.append({
            "rule": rule,
            "passed": bool(passed),
            "weight": weight,
            "contribution": contrib,
            "found_values": found,
        })

    overall_score = (score_acc / total_weight) if total_weight else 0.0
    overall_passed = all(d["passed"] for d in details if d["rule"].get("required", True))
    return {"passed": bool(overall_passed), "score": round(overall_score, 3), "details": details}
