from __future__ import annotations
import os, json, math, re
from typing import Any, Dict, List
from sqlalchemy import text

_WORD = re.compile(r"[A-Za-zĂÂÎȘȚăâîșț]+", re.UNICODE)

def _tokenize(s: str) -> List[str]:
    return [w.lower() for w in _WORD.findall(s or "")]

def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb: return 0.0
    return len(sa & sb) / len(sa | sb)

def _band(score: float) -> str:
    return "verde" if score >= 80 else ("galben" if score >= 50 else "roșu")

def _safe_openai_client():
    try:
        from openai import OpenAI
    except Exception:
        return None, None
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key: 
        return None, None
    try:
        return OpenAI(api_key=api_key), model
    except Exception:
        return None, None

def farm_profile(conn, business_id: int) -> Dict[str, Any]:
    prof: Dict[str, Any] = {"businessId": int(business_id)}

    # fields
    rows = conn.execute(
        text('select "cropType" as cropType, size from public.field where "businessId"=:b'),
        {"b": business_id}
    ).mappings().all()
    fields = [{"cropType": r["cropType"], "size": float(r["size"])} for r in rows]
    prof["fields"] = fields
    prof["totalHa"] = sum(f["size"] for f in fields)

    # quick flags inferred from cropType text
    crop_text = " ".join([f.get("cropType", "") or "" for f in fields]).lower()
    prof["hasVines"] = any(k in crop_text for k in ["viță", "vie", "viticol"])
    prof["hasProtected"] = any(k in crop_text for k in ["solar", "solarii", "seră", "sere", "teren protejat"])

    # livestock (from cattle.amount; animals array can be null)
    cattle = conn.execute(
        text('select type, coalesce(amount,0) as amount from public.cattle where "businessId"=:b'),
        {"b": business_id}
    ).mappings().all()
    prof["livestock"] = [{"type": r["type"], "amount": int(r["amount"])} for r in cattle]
    prof["totalAnimals"] = sum(x["amount"] for x in prof["livestock"])

    # machinery
    machines = conn.execute(text("""
        select v."vehicleType" as vehicleType, count(*) as n
        from public.vehicle v
        join public.vehicleGroup g on v."vehicleGroupId" = g.id
        where g."businessId" = :b
        group by v."vehicleType"
    """), {"b": business_id}).mappings().all()
    prof["machinery"] = [{"type": r["vehicleType"], "count": int(r["n"])} for r in machines]

    # finance (latest)
    fin = conn.execute(text("""
        select "yearlyIncome" as inc, "yearlyExpenses" as exp
        from public.finance
        order by "updatedAt" desc
        limit 1
    """)).mappings().first()
    if fin:
        prof["finance"] = {"income": float(fin["inc"]), "expenses": float(fin["exp"])}

    return prof

def _fallback_score(profile: Dict[str, Any], title: str, summary: str) -> Dict[str, Any]:
    """
    No-LLM fallback: compute similarity between subsidy text and farm profile cues.
    """
    text_sub = " ".join([title or "", summary or ""]).lower()
    toks_sub = _tokenize(text_sub)

    cues = []
    # cues from profile
    if profile.get("hasVines"): cues += ["viță", "vie", "viticol", "vitivinicol", "vin"]
    if profile.get("hasProtected"): cues += ["seră", "solarii", "teren protejat"]
    if (profile.get("totalAnimals", 0) > 0): cues += ["zootehnic", "zootehnie", "bovine", "ovine", "porcine", "lapte", "carne", "animal"]
    if (profile.get("totalHa", 0) > 0): cues += ["sector vegetal", "cultură", "culturi", "cerealiere", "legume", "irigare", "bazine", "tehnologii", "infrastructură"]

    toks_cues = _tokenize(" ".join(cues))
    jac = _jaccard(toks_sub, toks_cues)

    # weights for scale; add a size/livestock factor
    size_bonus = min(20.0, profile.get("totalHa", 0) * 0.5)          # +0.5 per ha up to +20
    herd_bonus = min(20.0, profile.get("totalAnimals", 0) * 0.1)     # +0.1 per head up to +20

    score = int(round(min(100.0, 100.0 * (0.55 * jac + 0.15) + size_bonus * 0.15 + herd_bonus * 0.15)))
    score = max(1, min(100, score))
    band = _band(score)
    reason = (
        f"Scor estimat pe bază de potrivire textuală între descrierea subvenției și profilul fermei. "
        f"Semnale detectate: {', '.join(sorted(set(cues)))}. "
        f"Suprafață: {profile.get('totalHa', 0)} ha; efective: {profile.get('totalAnimals', 0)} capete."
    )
    return {"score": score, "band": band, "reasoning_ro": reason}

def score_one(conn, business_id: int, subsidy: Dict[str, str]) -> Dict[str, Any]:
    """
    subsidy = {"code": "...", "title": "...", "summary": "..."}
    """
    profile = farm_profile(conn, business_id)
    client, model = _safe_openai_client()

    title = subsidy.get("title") or ""
    summary = subsidy.get("summary") or ""

    if client and model:
        # LLM path (Romanian, strict JSON)
        prompt = (
            "Ești un evaluator de eligibilitate pentru subvenții agricole AIPA.\n"
            "Primești 1) profilul fermei (date agregate) și 2) un rezumat scurt al subvenției (titlu + 2–3 fraze).\n"
            "Returnează STRICT JSON cu cheile: score (1-100), band ('verde'/'galben'/'roșu'), reasoning_ro (scurt, în română).\n"
            "Reguli pentru band: 80-100=verde; 50-79=galben; 1-49=roșu.\n"
            "Score trebuie să reflecte compatibilitatea dintre profil și criteriile implicite din rezumat (nu inventa reguli externe).\n"
        )
        user = {
            "profil_ferma": profile,
            "subventie": {"titlu": title, "rezumat": summary}
        }
        try:
            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
                ]
            )
            raw = resp.output_text
            data = json.loads(raw)
            score = int(data.get("score", 0))
            band = data.get("band") or _band(score)
            reasoning = data.get("reasoning_ro") or "Evaluare AI."
            score = max(1, min(100, score))
            return {
                "code": subsidy.get("code"),
                "title": title,
                "score": score,
                "band": band,
                "reasoning_ro": reasoning
            }
        except Exception:
            pass  # fall back

    fb = _fallback_score(profile, title, summary)
    fb.update({"code": subsidy.get("code"), "title": title})
    return fb

def score_many(conn, business_id: int, subsidies: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out = []
    for s in subsidies:
        try:
            out.append(score_one(conn, business_id, s))
        except Exception as e:
            out.append({
                "code": s.get("code"),
                "title": s.get("title"),
                "score": 1,
                "band": "roșu",
                "reasoning_ro": f"Eroare internă la evaluare: {e}"
            })
    return out
