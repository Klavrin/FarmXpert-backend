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

# Basic ops
def _cmp(val, op, tgt):
    if op == "==":  return val == tgt
    if op == "!=":  return val != tgt
    if op == ">=":  return (val is not None) and (tgt is not None) and (val >= tgt)
    if op == "<=":  return (val is not None) and (tgt is not None) and (val <= tgt)
    if op == ">":   return (val is not None) and (tgt is not None) and (val > tgt)
    if op == "<":   return (val is not None) and (tgt is not None) and (val < tgt)
    if op == "in":  return val in (tgt or [])
    if op == "any_in": return bool(set(val or []) & set(tgt or []))
    return False

def _collect(records: List[Dict[str,Any]], field: str) -> List[Any]:
    # field like "field.cropType"
    parts = field.split(".")
    out = []
    for r in records:
        v = r
        for p in parts:
            v = v.get(p) if isinstance(v, dict) else None
        out.append(v)
    return out

def evaluate_rule_set(rule_set: Dict[str,Any], dataset: Dict[str,List[Dict[str,Any]]]) -> Dict[str,Any]:
    """
    dataset keys expected from Supabase: users, field, cattle, animal, finance, vehicle, vehicleGroup
    Returns {passed:bool, details:[...], score:float}
    """
    details = []
    total, ok = 0, 0

    for rule in rule_set.get("all", []):
        total += 1
        field = rule.get("field")
        op    = rule.get("op")
        tgt   = rule.get("value")
        agg   = rule.get("aggregate","one")  # 'any' over a table, or single value

        # table name is the first part
        table = field.split(".")[0]
        rows  = dataset.get(table, [])

        passed = False
        if agg == "any":
            vals = _collect(rows, field)
            passed = any(_cmp(v, op, tgt) for v in vals)
        elif agg == "count>=":
            # rule.value is minimal count; compare count of non-empty matching rows
            vals = _collect(rows, field)
            cnt  = sum(1 for v in vals if _cmp(v, "in" if isinstance(tgt,list) else "==", tgt))
            passed = cnt >= (rule.get("min", 1))
        else:
            # try to read the first row or 'users' single row
            if table == "users":
                v = rows[0].get(field.split(".",1)[1]) if rows else None
            else:
                v = rows[0].get(field.split(".",1)[1]) if rows else None
            passed = _cmp(v, op, tgt)

        details.append({"rule": rule, "passed": passed})
        if passed: ok += 1

    score = ok / total if total else 0.0
    return {"passed": ok == total, "details": details, "score": round(score, 3)}
