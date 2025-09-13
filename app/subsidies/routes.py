# app/subsidies/routes.py
from __future__ import annotations
import os, pathlib, tempfile, json
from flask import Blueprint, request, jsonify
from sqlalchemy import create_engine, text
from Services.extract import build_subsidy_index

bp_subsidy = Blueprint("subsidies", __name__, url_prefix="/api/subsidies")
engine = create_engine(os.environ["DATABASE_URL"])

@bp_subsidy.post("/ingest")
def ingest():
    data = request.get_json(force=True) or {}
    scrape_out = data.get("scrape_result")
    if not scrape_out:
        return jsonify(error="Provide scrape_result from /api/scraper/run"), 400

    items = build_subsidy_index(scrape_out)

    # persist
    with engine.begin() as conn:
        for s in items:
            r = conn.execute(text("""
                insert into subsidy (code, title, language, applies_to, deadline)
                values (:code, :title, :language, :applies_to::jsonb, :deadline::jsonb)
                on conflict (code) do update
                  set title=excluded.title,
                      language=excluded.language,
                      applies_to=excluded.applies_to,
                      deadline=excluded.deadline
                returning id
            """), dict(code=s["code"], title=s["title"] or s["code"], language=s["language"],
                       applies_to=json.dumps(s["applies_to"]), deadline=json.dumps(s["deadline"]))
            )
            sid = r.scalar_one()
            # docs
            conn.execute(text("delete from subsidy_doc where subsidy_id=:sid"), dict(sid=sid))
            for d in s["docs"]:
                conn.execute(text("""
                    insert into subsidy_doc (subsidy_id, doc_type, filename, url, ext, about)
                    values (:sid, :doc_type, :filename, :url, :ext, :about)
                """), dict(sid=sid, **d))
            # rules + required fields
            conn.execute(text("delete from eligibility_rule where subsidy_id=:sid"), dict(sid=sid))
            conn.execute(text("""
                insert into eligibility_rule (subsidy_id, rule_json, description)
                values (:sid, :rule_json::jsonb, :desc)
            """), dict(sid=sid, rule_json=json.dumps(s["rule_set"]), desc=s["title"]))
            conn.execute(text("delete from required_field where subsidy_id=:sid"), dict(sid=sid))
            for rf in s["required_fields"]:
                conn.execute(text("""
                  insert into required_field (subsidy_id, key, label) values (:sid, :k, :l)
                """), dict(sid=sid, k=rf["key"], l=rf["label"]))
    return jsonify({"ingested": len(items), "items": items})
