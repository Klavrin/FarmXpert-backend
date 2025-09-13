# app/match/routes.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from sqlalchemy import create_engine, text
import os, json
from Services.matcher import evaluate_rule_set

bp_match = Blueprint("match", __name__, url_prefix="/api/match")
engine = create_engine(os.environ["DATABASE_URL"])

def load_dataset(conn, business_id: str, user_id: str):
    # NOTE: adjust queries to your exact schema & access pattern
    q = lambda sql, **p: [dict(r) for r in conn.execute(text(sql), p)]
    users  = q("select * from users where businessId=:bid and isOwner=true order by createdAt limit 1", bid=business_id)
    field  = q("select * from field where businessId=:bid", bid=business_id)
    cattle = q("select * from cattle where businessId=:bid", bid=business_id)
    animal = q("select * from animal", )  # if linked via cattleId join as needed
    finance= q("select * from finance order by updatedAt desc limit 1")
    vehicle= q("select * from vehicle", )
    vgrp   = q("select * from vehicleGroup where businessId=:bid", bid=business_id)
    return {"users":users, "field":field, "cattle":cattle, "animal":animal,
            "finance":finance, "vehicle":vehicle, "vehicleGroup":vgrp}

@bp_match.post("/")
def match_all():
    data = request.get_json(force=True) or {}
    business_id = data.get("businessId")
    user_id     = data.get("userId")
    if not (business_id and user_id):
        return jsonify(error="Provide businessId and userId"), 400

    with engine.begin() as conn:
        subs = [dict(r) for r in conn.execute(text("select * from subsidy"))]
        rules = { r["subsidy_id"]: dict(r) for r in conn.execute(text("select subsidy_id, rule_json from eligibility_rule")) }
        dataset = load_dataset(conn, business_id, user_id)

        matches = []
        for s in subs:
            rs = rules.get(s["id"], {})
            evald = evaluate_rule_set(rs.get("rule_json", {}), dataset)
            score = evald["score"]
            matches.append({
                "subsidyId": s["id"],
                "code": s["code"],
                "title": s["title"],
                "status": s["status"],
                "score": score,
                "explanation": evald["details"]
            })

        # rank best first
        matches.sort(key=lambda x: (x["status"]=="open", x["score"]), reverse=True)

        # persist optional
        for m in matches:
            conn.execute(text("""
                insert into user_subsidy_match (user_id, business_id, subsidy_id, score, explanation)
                values (:uid, :bid, :sid, :sc, :exp::jsonb)
            """), dict(uid=user_id, bid=business_id, sid=m["subsidyId"], sc=m["score"], exp=json.dumps(m["explanation"])))

    # Recommend top 1–3
    return jsonify({
        "recommendations": matches[:3],
        "all": matches
    })
