# app/match/routes.py
from __future__ import annotations
import os
import json
import re
from flask import Blueprint, request, jsonify
from sqlalchemy import text
from app.db_utilis import engine
from app.services.eligibility_ai import score_many

bp_match = Blueprint("match", __name__, url_prefix="/api/match")

# Optional OpenAI (used only by the non-/ai endpoint below, and guarded)
USE_AI = True
try:
    from openai import OpenAI  # noqa: F401
except Exception:
    USE_AI = False


def romanian_band(score: int) -> str:
    if score >= 80:
        return "verde"
    if score >= 50:
        return "galben"
    return "roșu"


def clamp(n, lo=1, hi=100):
    try:
        n = int(n)
    except Exception:
        n = 1
    return max(lo, min(hi, n))


@bp_match.post("/ai")
def match_ai():
    """
    Request body:
      { "businessId": <int>, "topN": <int optional> }

    Response:
      {
        "businessId": <int>,
        "matches": [
          {
            "subsidyCode": "...",
            "subsidyTitle": "...",
            "summary": "...",  # <-- Added short summary
            "score": 0..100,  # relative to this result set
            "band": "verde|galben|roșu",
            "reasoning_ro": "..."
          },
          ...
        ]
      }
    """
    data = request.get_json(force=True) or {}
    business_id = data.get("businessId")
    topn_raw = data.get("topN", 5)
    try:
        topn = int(topn_raw)
    except Exception:
        topn = 5

    if not business_id:
        return jsonify(error="businessId required"), 400

    with engine.connect() as conn:
        try:
            rows = conn.execute(text("""
                select code, title, coalesce(summary, '') as summary
                from public.subsidy
                where code is not null and title is not null
                order by code
            """)).mappings().all()
        except Exception:
            conn.rollback()
            rows = conn.execute(text("""
                select code, title
                from public.subsidy
                where code is not null and title is not null
                order by code
            """)).mappings().all()

        subsidies = [
            {
                "code": r["code"],
                "title": r["title"],
                "summary": (r.get("summary") or "") if isinstance(r, dict) else ""
            }
            for r in rows
        ]

        scored_list = score_many(conn, int(business_id), subsidies)

    # Find min and max scores for normalization
    raw_scores = [item.get("score", 1) for item in scored_list if isinstance(item, dict)]
    if not raw_scores:
        raw_scores = [1]
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    score_range = max_score - min_score if max_score != min_score else 1

    normalized = []
    for i, item in enumerate(scored_list):
        code = item.get("code") if isinstance(item, dict) else None
        title = item.get("title") if isinstance(item, dict) else None
        summary = item.get("summary") if isinstance(item, dict) else None
        if not code:
            code = subsidies[i].get("code")
        if not title:
            title = subsidies[i].get("title")
        if not summary:
            summary = subsidies[i].get("summary")

        raw_score = item.get("score", 1)
        # Normalize to 0..100 relative to this result set
        rel_score = int(round((raw_score - min_score) * 100 / score_range))
        band = romanian_band(rel_score)
        reasoning = (item.get("reasoning_ro") or "").strip()
        if not reasoning:
            reasoning = "Evaluare pe bază de similitudini text între descriere și profilul fermei."

        normalized.append({
            "subsidyCode": code,
            "subsidyTitle": title,
            "summary": summary,  # <-- Added here
            "score": rel_score,
            "band": band,
            "reasoning_ro": reasoning,
        })

    normalized.sort(key=lambda x: x["score"], reverse=True)
    return jsonify({"businessId": int(business_id), "matches": normalized[:topn]})
    """
    Request body:
      { "businessId": <int>, "topN": <int optional> }

    Response:
      {
        "businessId": <int>,
        "matches": [
          {
            "subsidyCode": "...",
            "subsidyTitle": "...",
            "score": 0..100,  # relative to this result set
            "band": "verde|galben|roșu",
            "reasoning_ro": "..."
          },
          ...
        ]
      }
    """
    data = request.get_json(force=True) or {}
    business_id = data.get("businessId")
    topn_raw = data.get("topN", 5)
    try:
        topn = int(topn_raw)
    except Exception:
        topn = 5

    if not business_id:
        return jsonify(error="businessId required"), 400

    with engine.connect() as conn:
        try:
            rows = conn.execute(text("""
                select code, title, coalesce(summary, '') as summary
                from public.subsidy
                where code is not null and title is not null
                order by code
            """)).mappings().all()
        except Exception:
            conn.rollback()
            rows = conn.execute(text("""
                select code, title
                from public.subsidy
                where code is not null and title is not null
                order by code
            """)).mappings().all()

        subsidies = [
            {
                "code": r["code"],
                "title": r["title"],
                "summary": (r.get("summary") or "") if isinstance(r, dict) else ""
            }
            for r in rows
        ]

        scored_list = score_many(conn, int(business_id), subsidies)

    # Find min and max scores for normalization
    raw_scores = [item.get("score", 1) for item in scored_list if isinstance(item, dict)]
    if not raw_scores:
        raw_scores = [1]
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    score_range = max_score - min_score if max_score != min_score else 1

    normalized = []
    for i, item in enumerate(scored_list):
        code = item.get("code") if isinstance(item, dict) else None
        title = item.get("title") if isinstance(item, dict) else None
        if not code:
            code = subsidies[i].get("code")
        if not title:
            title = subsidies[i].get("title")

        raw_score = item.get("score", 1)
        # Normalize to 0..100 relative to this result set
        rel_score = int(round((raw_score - min_score) * 100 / score_range))
        band = romanian_band(rel_score)
        reasoning = (item.get("reasoning_ro") or "").strip()
        if not reasoning:
            reasoning = "Evaluare pe bază de similitudini text între descriere și profilul fermei."

        normalized.append({
            "subsidyCode": code,
            "subsidyTitle": title,
            "score": rel_score,
            "band": band,
            "reasoning_ro": reasoning,
        })

    normalized.sort(key=lambda x: x["score"], reverse=True)
    return jsonify({"businessId": int(business_id), "matches": normalized[:topn]})