# app/subsidies/routes.py
import os, json, re, pathlib, shutil
from flask import Blueprint, request, jsonify, current_app, send_from_directory
from sqlalchemy import text
from datetime import datetime
from app import engine

from app.services.farm_profile import load_farm_profile
from app.services.ai_score import score_one, infer_code_from_filename
from app.services.doc_fill import prefill_docx, prefill_xlsx, ensure_dir
from app.services.suggest_fields import suggest_field_values
from Services.matcher import evaluate_rule_set

bp_sub = Blueprint("subsidies", __name__, url_prefix="/api")

GENERATED_BASE = pathlib.Path(os.getenv("GENERATED_DIR", "generated")).resolve()

# Serve generated files (download links)
@bp_sub.get("/files/<path:relpath>")
def get_file(relpath):
    fp = GENERATED_BASE / relpath
    if not fp.exists():
        return jsonify({"error":"not found"}), 404
    return send_from_directory(GENERATED_BASE, relpath, as_attachment=True)

def parse_code(row):
    # prefer what you extracted earlier; fallback parse from filename
    return row["subsidy_code"] or infer_code_from_filename(row["filename"])

@bp_sub.post("/match")
def match_subsidies():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify(error="user_id required"), 400

    with engine.connect() as conn:
        # build farm profile from existing tables
        farm = load_farm_profile(conn, int(user_id))
        bid = farm["user"]["businessId"]

        # read docs we already scraped/summarized
        rows = conn.execute(text("""
            select id, subsidy_code, url, filename, ext, doc_type, about, text_excerpt
            from subsidy_doc
            order by id
        """)).mappings().all()
        if not rows:
            return jsonify(error="Nicio subvenție în catalog (rulează ingest)"), 400

        # create run
        run_id = conn.execute(text("""
            insert into match_run (user_id, business_id) values (:uid, :bid)
            returning id
        """), {"uid": user_id, "bid": bid}).scalar_one()

        results = []
        for r in rows:
            code = parse_code(r)
            doc = {
                "subsidy_code": code,
                "about": r.get("about"),
                "doc_type": r.get("doc_type"),
                "text_excerpt": r.get("text_excerpt"),
                "filename": r.get("filename"),
            }
            scored = score_one(farm, doc)
            conn.execute(text("""
                insert into match_item (run_id, subsidy_code, score, band, reasons, missing, raw)
                values (:rid, :code, :score, :band, :reasons, :missing, :raw)
                on conflict (run_id, subsidy_code) do update set
                  score=excluded.score, band=excluded.band, reasons=excluded.reasons, missing=excluded.missing, raw=excluded.raw
            """), {
                "rid": run_id,
                "code": scored["subsidy_code"],
                "score": scored["score"],
                "band": scored["band"],
                "reasons": json.dumps(scored["reasons"], ensure_ascii=False),
                "missing": json.dumps(scored["missing"], ensure_ascii=False),
                "raw": json.dumps(scored["raw"], ensure_ascii=False),
            })
            results.append(scored)

        # rank and return (top 1–3 as recommendations)
        ranked = sorted(results, key=lambda x: x["score"], reverse=True)
        recs = ranked[:3]
        return jsonify({
            "run_id": run_id,
            "ranked": ranked,
            "recommendations": recs
        })

@bp_sub.post("/apply/prepare")
def apply_prepare():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id")
    subsidy_code = data.get("subsidy_code")
    if not user_id or not subsidy_code:
        return jsonify(error="user_id și subsidy_code sunt obligatorii"), 400

    with engine.connect() as conn:
        farm = load_farm_profile(conn, int(user_id))
        bid = farm["user"]["businessId"]

        # pick relevant docs for this subsidy (prefer cereri/xlsx)
        docs = conn.execute(text("""
            select id, url, filename, ext
            from subsidy_doc
            where subsidy_code = :code
               or filename ilike '%' || :code || '%'
               or filename ilike '%' || replace(:code,'_',' ') || '%'
            order by filename
        """), {"code": subsidy_code}).mappings().all()

        if not docs:
            return jsonify(error=f"Niciun fișier pentru {subsidy_code}"), 404

        suggestions = suggest_field_values(farm, subsidy_code)

        draft_id = conn.execute(text("""
           insert into application_draft (user_id, business_id, subsidy_code, suggestions)
           values (:uid, :bid, :code, :sug)
           returning id
        """), {"uid": user_id, "bid": bid, "code": subsidy_code, "sug": json.dumps(suggestions, ensure_ascii=False)}).scalar_one()

        # create output dir
        outdir = GENERATED_BASE / "drafts" / str(draft_id)
        ensure_dir(outdir)

        files_out = []
        for d in docs:
            src_name = d["filename"]
            # download source again? you already have a download pipeline; here we rely on remote URL
            # For simplicity, fetch now. In production reuse cached local file.
            import requests, tempfile
            resp = requests.get(d["url"], timeout=60)
            resp.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(resp.content); tmp.close()

            src_ext = (d["ext"] or "").lower()
            if src_ext == "docx":
                out_name = pathlib.Path(src_name).with_suffix("").name + " (prefilled).docx"
                dest = outdir / out_name
                prefill_docx(pathlib.Path(tmp.name), dest, suggestions)
                rel = f"drafts/{draft_id}/{out_name}"
            elif src_ext == "xlsx":
                out_name = pathlib.Path(src_name).with_suffix("").name + " (PROPUNERI).xlsx"
                dest = outdir / out_name
                prefill_xlsx(pathlib.Path(tmp.name), dest, suggestions)
                rel = f"drafts/{draft_id}/{out_name}"
            else:
                # PDFs not editable: just copy as-is
                out_name = src_name
                dest = outdir / out_name
                shutil.copyfile(tmp.name, dest)
                rel = f"drafts/{draft_id}/{out_name}"

            files_out.append({"src_url": d["url"], "out_path": rel, "ext": src_ext})
            conn.execute(text("""
               insert into application_file (draft_id, src_url, out_path, ext)
               values (:did, :u, :p, :e)
            """), {"did": draft_id, "u": d["url"], "p": rel, "e": src_ext})

        return jsonify({
            "draft_id": draft_id,
            "status": "prepared",
            "subsidy_code": subsidy_code,
            "suggestions": suggestions,
            "files": [
                {"download": f"/api/files/{f['out_path']}", "ext": f["ext"], "source": f["src_url"]}
                for f in files_out
            ]
        })

@bp_sub.post("/apply/mark")
def apply_mark():
    data = request.get_json(force=True) or {}
    draft_id = data.get("draft_id")
    action = data.get("action")  # 'agree' | 'disagree' | 'change'
    edits = data.get("edits")    # optional dict camp->valoare
    if not draft_id or action not in ("agree","disagree","change"):
        return jsonify(error="draft_id și action(agree|disagree|change) sunt obligatorii"), 400

    with engine.connect() as conn:
        # load current suggestions
        row = conn.execute(text("select suggestions from application_draft where id=:id"), {"id": draft_id}).mappings().first()
        if not row:
            return jsonify(error="draft inexistent"), 404
        sug = row["suggestions"] or {}
        if action == "change" and isinstance(edits, dict):
            # merge edits
            sug.update(edits)
            status = "amended"
        elif action == "agree":
            status = "approved"
        else:
            status = "rejected"

        conn.execute(text("""
            update application_draft set suggestions=:sug, status=:st, updated_at=now() where id=:id
        """), {"sug": json.dumps(sug, ensure_ascii=False), "st": status, "id": draft_id})

        return jsonify({"draft_id": draft_id, "status": status, "suggestions": sug})

@bp_sub.post("/apply/submit")
def apply_submit():
    data = request.get_json(force=True) or {}
    draft_id = data.get("draft_id")
    if not draft_id:
        return jsonify(error="draft_id obligatoriu"), 400

    with engine.connect() as conn:
        conn.execute(text("""
            update application_draft set status='submitted', updated_at=now() where id=:id
        """), {"id": draft_id})
        # In real life: we’d guide user to încărcare pe portalul AIPA.
        return jsonify({
            "draft_id": draft_id,
            "status": "submitted",
            "mesaj": "Dosarul a fost pregătit. Descărcați fișierele și încărcați-le pe portalul AIPA."
        })

@bp.post("/eligibility")
def eligibility_check():
    """
    POST JSON:
    {
      "rule_set": { "all": [ {field, op, value, aggregate, weight, threshold, required, ...}, ... ] },
      "dataset": { "users": [...], "field": [...], "finance": [...], ... }
    }
    Returns: evaluate_rule_set(...) result
    """
    data = request.get_json(force=True, silent=True) or {}
    rule_set = data.get("rule_set") or {}
    dataset = data.get("dataset") or {}
    try:
        res = evaluate_rule_set(rule_set, dataset)
        return jsonify(res)
    except Exception as e:
        return jsonify(error="evaluation_failed", details=str(e)), 500