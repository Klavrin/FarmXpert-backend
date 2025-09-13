# app/match/routes.py
from __future__ import annotations
import os, json, re
from flask import Blueprint, request, jsonify
from sqlalchemy import text
from app.services.eligibility_ai import score_one 
from app.db_utilis import engine

bp_match = Blueprint("match", __name__, url_prefix="/api/match")

# ---- Optional OpenAI (primary path) ----
USE_AI = True
try:
    from openai import OpenAI
except Exception:
    USE_AI = False

def ai_client():
    k = os.getenv("OPENAI_API_KEY")
    if not k:
        raise RuntimeError("OPENAI_API_KEY missing")
    return OpenAI(api_key=k)

def romanian_verdict(score: int) -> str:
    if score >= 80: return "verde"
    if score >= 50: return "galben"
    return "roșu"

def clamp(n, lo=1, hi=100):
    try:
        n = int(n)
    except Exception:
        n = 1
    return max(lo, min(hi, n))

# ---- Fallback: summary-driven keyword scoring (no hard-coded measure map) ----
KW_GROUPS = {
    "zootehnie": [r"\bzootehn", r"\banimal", r"\bferm[ăa]\s+zootehn", r"origine animal"],
    "viticol":   [r"\bvitivin", r"\bviticol", r"\bvie", r"\bstrugur"],
    "vegetal":   [r"\bsector(ul)?\s+vegetal", r"\bplanta(ț|t)ii", r"\bculturi\s+(arabile|horti)"],
    "irigare":   [r"\birig", r"\bap[ăa]\s+p[ie]ntru?\s+irig"],
    "bazine":    [r"\bbazin(e)?\s+de\s+acumul", r"\bacumulare\s+a\s+apei"],
    "teren_protejat": [r"\bteren\s+protejat", r"\bsere|\bsolarii|\btuneluri"],
    "utilaje/tehnologii": [r"\butilaj", r"\btehnologii\b", r"\bma[șs]ini\s+agricole", r"\bdrone?"],
    "infrastructură": [r"\binfrastructur", r"\bdepozit", r"\blogistic", r"\bprocesare"],
    "agroturism": [r"\bagroturism", r"\bturism\s+vitivin"],
}

def summary_score(summary: str, profile: dict) -> int:
    """Heuristic only if AI not available/breaks. Uses the *summary text only* plus coarse profile signals."""
    s = 40  # neutral base
    text_l = (summary or "").lower()

    # Extract coarse profile signals (without assuming schema beyond what we gather)
    area = float(profile.get("total_suprafata_ha") or 0)
    animals = int(profile.get("numar_total_animale") or 0)
    has_fields = area > 0.1
    has_animals = animals > 0

    def any_kw(group):
        return any(re.search(p, text_l) for p in KW_GROUPS[group])

    if any_kw("zootehnie") and has_animals: s += 20
    if any_kw("viticol") and "vit" in (" ".join(profile.get("culturi_cuvinte", []))): s += 18
    if any_kw("vegetal") and has_fields: s += 15
    if any_kw("teren_protejat") and has_fields: s += 12
    if any_kw("irigare") and has_fields: s += 12
    if any_kw("bazine") and has_fields: s += 10
    if any_kw("utilaje/tehnologii"): s += 8
    if any_kw("infrastructură"): s += 8
    if any_kw("agroturism"): s += 3

    # Penalize if summary clearly mismatches (e.g., zootehnie but no animals)
    if any_kw("zootehnie") and not has_animals: s -= 12
    if (any_kw("viticol") or any_kw("agroturism")) and area <= 0: s -= 8

    return clamp(s)

@bp_match.post("/")
def match_subsidies():
    """
    Body:
    {
      "businessId": 1,
      "userId": 1,
      "subsidyCodes": ["SP_2.5","SP_2.6"],   # optional filter
      "summaries": { "SP_2.6": "text summary ..." }  # optional: let the client pass summaries directly
    }
    """
    data = request.get_json(force=True) or {}
    business_id = data.get("businessId")
    user_id     = data.get("userId")
    codes_filter = set([c.strip() for c in data.get("subsidyCodes", [])]) if data.get("subsidyCodes") else None
    provided_summaries = data.get("summaries") or {}

    if not business_id or not user_id:
        return jsonify(error="businessId and userId required"), 400

    with engine.connect() as conn:
        # Load all subsidies (no assumptions about columns beyond id, code, title; we read the whole row to probe for possible summary fields)
        subsidies = [dict(r) for r in conn.execute(text("select * from subsidy order by id")).mappings().all()]
        if codes_filter:
            subsidies = [s for s in subsidies if (s.get("code") or "").strip() in codes_filter]

        # Farm profile snapshot (using only your existing tables; read-only)
        user = conn.execute(text("""
            select userId, firstName, lastName, phone, email, businessId
            from users where userId = :u
        """), {"u": user_id}).mappings().first()

        fields = [dict(r) for r in conn.execute(text("""
            select cropType, size, soilType, fertiliser, herbicide
            from field where businessId = :b
        """), {"b": business_id}).mappings().all()]

        cattle = [dict(r) for r in conn.execute(text("""
            select type, amount from cattle where businessId = :b
        """), {"b": business_id}).mappings().all()]

        finance = conn.execute(text("""
            select yearlyIncome, yearlyExpenses
            from finance order by updatedAt desc nulls last limit 1
        """)).mappings().first()

    total_area = sum(float(f.get("size") or 0) for f in fields)
    total_animals = sum(int(c.get("amount") or 0) for c in cattle)

    # A compact profile the model / fallback will consume
    profile = {
        "utilizator": dict(user) if user else {},
        "campuri": fields[:15],
        "efective_animale": cattle[:15],
        "finante": dict(finance) if finance else {},
        "total_suprafata_ha": total_area,
        "numar_total_animale": total_animals,
        "culturi_cuvinte": sorted(set([str((f.get("cropType") or "")).lower() for f in fields if f.get("cropType")])),
    }

    out = []
    client = None
    if USE_AI and os.getenv("OPENAI_API_KEY"):
        try:
            client = ai_client()
        except Exception:
            client = None

    for s in subsidies:
        code = s.get("code") or ""
        title = s.get("title") or ""
        # Prefer summary passed by client; else try common column names if present; else empty.
        summary_text = (
            provided_summaries.get(code) or
            s.get("summary") or s.get("about") or s.get("descriere") or s.get("description") or s.get("notes") or ""
        )

        score = None
        reasons = []
        suggestions = []
        verdict = None
        confidence = 2

        if client:
            try:
                prompt = (
                    "Ești expert AIPA. Primești *rezumatul* unei măsuri de subvenționare și *profilul fermei*. "
                    "Calculează o potrivire 1–100 pe baza cerințelor implicite din rezumat și a datelor fermei. "
                    "Dacă lipsesc date critice, nu presupune; scade scorul moderat și notează ce lipsește. "
                    "Răspunde STRICT JSON, fără text în afara JSON-ului, cu schema:\n"
                    "{"
                    "\"score\": int 1..100, "
                    "\"verdict\": \"verde\"|\"galben\"|\"roșu\", "
                    "\"reasons\": [max 4 strings], "
                    "\"missing\": [max 5 strings], "
                    "\"suggestions\": [2-4 strings], "
                    "\"confidence\": int 1..5"
                    "}\n\n"
                    f"Cod măsură: {code}\nTitlu: {title}\n\n"
                    f"Rezumat:\n{summary_text}\n\n"
                    f"Profil fermă (JSON):\n{json.dumps(profile, ensure_ascii=False)}\n"
                )
                # Chat Completions (stable)
                resp = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL","gpt-4o-mini"),
                    temperature=0.2,
                    messages=[
                        {"role":"system","content":"Răspunde exclusiv în limba română."},
                        {"role":"user","content": prompt}
                    ]
                )
                content = resp.choices[0].message.content
                parsed = json.loads(content)
                score = clamp(parsed.get("score", 0))
                verdict = parsed.get("verdict") or romanian_verdict(score)
                reasons = parsed.get("reasons") or []
                missing = parsed.get("missing") or []
                suggestions = parsed.get("suggestions") or []
                confidence = clamp(parsed.get("confidence", 2), 1, 5)
                # Ensure Romanian verdict bands if model returned something odd
                verdict = romanian_verdict(score) if verdict not in ("verde","galben","roșu") else verdict
            except Exception:
                pass

        # Fallback if AI failed / disabled
        if score is None:
            score = summary_score(summary_text, profile)
            verdict = romanian_verdict(score)
            reasons = [
                "Evaluare pe baza cuvintelor-cheie din rezumat și a datelor minime ale fermei.",
                "Scor redus dacă există neconcordanțe evidente (ex.: măsură pentru animale dar ferma nu are animale)."
            ]
            suggestions = ["Completați profilul cu date mai detaliate (terenuri/animale/finanțe)."]
            missing = []

        out.append({
            "subsidy": {"code": code, "title": title},
            "summaryUsed": bool(summary_text.strip()),
            "compatibilityScore": score,
            "verdict": verdict,
            "reasons": reasons,
            "suggestions": suggestions,
            "confidence": confidence
        })

    out.sort(key=lambda x: x["compatibilityScore"], reverse=True)
    return jsonify({"businessId": business_id, "userId": user_id, "matches": out})

@bp_match.post("/ai")
def match_ai():
    data = request.get_json(force=True) or {}
    business_id = data.get("businessId")
    topn = int(data.get("topN", 5))
    if not business_id:
        return jsonify(error="businessId required"), 400

    with engine.begin() as conn:
        # Try to pull a 'summary' column if you added it; fall back to "", not an error.
        try:
            rows = conn.execute(text("""
                select code, title, coalesce(summary, '') as summary
                from public.subsidy
                order by code
            """)).mappings().all()
        except Exception:
            rows = conn.execute(text("""
                select code, title
                from public.subsidy
                order by code
            """)).mappings().all()
        subsidies = [{"code": r["code"], "title": r["title"], "summary": r.get("summary", "")} for r in rows]

        results = score_one(conn, int(business_id), subsidies)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return jsonify({"businessId": int(business_id), "results": results[:topn]})