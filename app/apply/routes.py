# app/apply/routes.py
from __future__ import annotations
import os, json, pathlib, tempfile, requests
from flask import Blueprint, request, jsonify, send_file
from sqlalchemy import create_engine, text
from Services.templating import fill_docx, fill_xlsx, package_zip

bp_apply = Blueprint("apply", __name__, url_prefix="/api/apply")
engine = create_engine(os.environ["DATABASE_URL"])

def _download(url: str, dest: pathlib.Path) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1<<20):
                if chunk: f.write(chunk)
    return dest

@bp_apply.post("/generate")
def generate():
    data = request.get_json(force=True) or {}
    business_id = data.get("businessId")
    user_id     = data.get("userId")
    subsidy_id  = data.get("subsidyId")
    if not (business_id and user_id and subsidy_id):
        return jsonify(error="Provide businessId, userId, subsidyId"), 400

    with engine.begin() as conn:
        # dataset (reuse from matcher)
        def q(sql, **p): return [dict(r) for r in conn.execute(text(sql), p)]
        users  = q("select * from users where businessId=:bid and isOwner=true order by createdAt limit 1", bid=business_id)
        dataset = {"users":users, "field":q("select * from field where businessId=:bid", bid=business_id),
                   "cattle":q("select * from cattle where businessId=:bid", bid=business_id),
                   "animal":q("select * from animal"), "finance":q("select * from finance order by updatedAt desc limit 1"),
                   "vehicle":q("select * from vehicle"), "vehicleGroup":q("select * from vehicleGroup where businessId=:bid", bid=business_id)}
        docs = q("select * from subsidy_doc where subsidy_id=:sid", sid=subsidy_id)
        reqs = q("select key,label from required_field where subsidy_id=:sid", sid=subsidy_id)

    # identify missing required fields
    missing = []
    for rf in reqs:
        table, attr = rf["key"].split(".",1)
        rows = dataset.get(table, [])
        val  = (rows[0].get(attr) if rows else None) if table=="users" else (rows[0].get(attr) if rows else None)
        if not val:
            missing.append(rf)

    tmp = pathlib.Path(tempfile.mkdtemp())
    out_files = []
    for d in docs:
        url = d["url"]
        fname = url.split("/")[-1]
        src = _download(url, tmp / "src" / fname)
        tgt = tmp / ("filled_" + fname)
        if d["ext"].lower() == "docx":
            fill_docx(src, dataset, tgt)
            out_files.append(tgt)
        elif d["ext"].lower() == "xlsx":
            fill_xlsx(src, dataset, tgt)
            out_files.append(tgt)
        # PDFs are left for manual review or later overlay step

    zpath = tmp / "application_draft.zip"
    package_zip(out_files, zpath)

    # Persist draft row
    with engine.begin() as conn:
        r = conn.execute(text("""
            insert into application_draft (user_id, business_id, subsidy_id, status, filled_files, missing)
            values (:uid, :bid, :sid, 'draft', :files::jsonb, :missing::jsonb)
            returning id
        """), dict(uid=user_id, bid=business_id, sid=subsidy_id,
                   files=json.dumps([p.name for p in out_files]),
                   missing=json.dumps(missing)))
        draft_id = r.scalar_one()

    # Return JSON with a presigned or app-served download. For dev, send file directly:
    return send_file(str(zpath), as_attachment=True, download_name=f"draft_{draft_id}.zip")
