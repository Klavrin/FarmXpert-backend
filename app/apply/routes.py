# app/apply/routes.py
from __future__ import annotations
import os, json, pathlib, urllib.parse
from flask import Blueprint, request, jsonify, send_file
from sqlalchemy import text
from app.db_utilis import engine
from app.services.doc_fill import ensure_dir, prefill_docx, prefill_xlsx, safe_filename

bp_apply = Blueprint("apply", __name__, url_prefix="/api/apply")
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", "./data")).resolve()

# ---------------------------- helpers ----------------------------

def _safe_name(s: str) -> str:
    s = urllib.parse.unquote(str(s or "")).strip().replace("\\", "_").replace("/", "_")
    return (s[:180] or "fisier")

def _guess_ext(url_or_name: str) -> str:
    p = urllib.parse.urlparse(str(url_or_name)).path.lower()
    for ext in (".docx", ".xlsx", ".pdf"):
        if p.endswith(ext):
            return ext
    ext = pathlib.Path(url_or_name).suffix.lower()
    return ext if ext else ".docx"

def _business_profile(conn, business_id: int) -> dict:
    user = conn.execute(text(
        'select "userId", "firstName", "lastName", phone, email, "businessId" '
        'from "users" where "businessId" = :b order by "userId" limit 1'
    ), {"b": business_id}).mappings().first()

    fields = conn.execute(text(
        'select "cropType" as crop_type, coalesce(size,0) as size '
        'from "field" where "businessId" = :b'
    ), {"b": business_id}).mappings().all()

    cattle = conn.execute(text(
        'select coalesce(type, \'\') as type, coalesce(amount,0) as amount '
        'from "cattle" where "businessId" = :b'
    ), {"b": business_id}).mappings().all()

    # vehicles grouped (tolerant to missing table/columns)
    try:
        veh = conn.execute(text(
            'select v."vehicleType" as vehicle_type, count(*)::int as n '
            'from "vehicle" v join "vehicleGroup" g on v."vehicleGroupId" = g.id '
            'where g."businessId" = :b group by v."vehicleType"'
        ), {"b": business_id}).mappings().all()
    except Exception:
        veh = []

    total_area = sum(float((r.get("size") or 0)) for r in fields)
    total_animals = sum(int((r.get("amount") or 0)) for r in cattle)

    return {
        "utilizator": dict(user) if user else {},
        "campuri": [{"cropType": (r.get("crop_type") or r.get("cropType") or r.get("croptype") or ""), "size": float(r.get("size") or 0)} for r in fields],
        "efective_animale": [{"type": r.get("type", ""), "amount": int(r.get("amount") or 0)} for r in cattle],
        "machines": [{"vehicleType": (m.get("vehicle_type") or m.get("vehicleType") or m.get("vehicletype") or ""), "n": int(m.get("n") or 0)} for m in veh],
        "finante": {},
        "total_suprafata_ha": total_area,
        "numar_total_animale": total_animals,
        "culturi_cuvinte": sorted(set([(r.get("crop_type") or r.get("cropType") or "").lower() for r in fields if (r.get("crop_type") or r.get("cropType"))])),
    }

# ---------------------------- endpoints ----------------------------

@bp_apply.post("/prepare")
def apply_prepare():
    """
    Creates application (status 'draft') and pending documents (from doc_template or defaults).
    Body: { "businessId": <int>, "userId": <int>, "subsidyCode": "<code>" }
    """
    data = request.get_json(force=True) or {}
    business_id = data.get("businessId")
    user_id = data.get("userId")
    subsidy_code = (data.get("subsidyCode") or "").strip()

    if not business_id or not user_id or not subsidy_code:
        return jsonify(error="businessId, userId and subsidyCode are required"), 400

    with engine.begin() as conn:
        sub = conn.execute(text(
            'select id, code, title from "subsidy" where code = :c'
        ), {"c": subsidy_code}).mappings().first()
        if not sub:
            return jsonify(error=f"Subsidy '{subsidy_code}' not found (seed code/title)."), 404

        app_row = conn.execute(text(
            'insert into "application" (business_id, user_id, subsidy_id, status, created_at) '
            "values (:b, :u, :s, 'draft', now()) "
            "returning id, status, created_at"
        ), {"b": int(business_id), "u": int(user_id), "s": sub["id"]}).mappings().first()
        app_id = app_row["id"]

        # try templates; if table not present, fall back
        try:
            tmpls = conn.execute(text(
                'select id, name, file_ext, coalesce(source_url, \'\') as source_url '
                'from "doc_template" where subsidy_id = :sid'
            ), {"sid": sub["id"]}).mappings().all()
        except Exception:
            tmpls = []

        docs = []
        if tmpls:
            for t in tmpls:
                docs.append({"name": t["name"], "file_ext": t["file_ext"], "template_id": t["id"]})
        else:
            docs = [
                {"name": "Cerere", "file_ext": "docx", "template_id": None},
                {"name": "Fisa de calcul", "file_ext": "xlsx", "template_id": None},
            ]

        created_docs = []
        for d in docs:
            row = conn.execute(text(
                'insert into "application_document" '
                "(application_id, template_id, name, file_ext, status, ai_filled_payload, rejection_reason, storage_path, created_at) "
                "values (:app, :tid, :name, :ext, 'pending', '{}'::jsonb, null, null, now()) "
                "returning id, name, file_ext, status, ai_filled_payload, storage_path"
            ), {
                "app": app_id,
                "tid": d["template_id"],
                "name": d["name"],
                "ext": d["file_ext"],
            }).mappings().first()
            created_docs.append(dict(row))

    return jsonify({
        "application": {
            "id": app_id,
            "status": app_row["status"],
            "created_at": app_row["created_at"].isoformat() if app_row["created_at"] else None,
            "subsidyCode": sub["code"],
            "subsidyTitle": sub["title"],
        },
        "documents": created_docs
    })


@bp_apply.post("/fill")
def apply_fill():
    """
    Prefill a single doc using an external template URL (or doc_template.source_url if present).
    Body: { "applicationId": <int>, "docId": <int>, "docUrl": "<url optional>" }
    """
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    doc_url = (data.get("docUrl") or "").strip()

    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId are required"), 400

    with engine.begin() as conn:
        row = conn.execute(text(
            'select ad.id, ad.name, ad.file_ext, a.business_id, a.user_id, '
            'a.subsidy_id, dt.source_url '
            'from "application_document" ad '
            'join "application" a on a.id = ad.application_id '
            'left join "doc_template" dt on dt.id = ad.template_id '
            "where ad.id = :d and ad.application_id = :a"
        ), {"d": int(doc_id), "a": int(app_id)}).mappings().first()
        if not row:
            return jsonify(error="Document not found"), 404

        src_url = doc_url or (row.get("source_url") or "")
        if not src_url:
            return jsonify(error="docUrl missing and no template source_url found"), 400

        base_dir = DATA_DIR / "applications" / str(app_id)
        tpl_dir = base_dir / "templates"
        out_dir = base_dir / "filled"
        ensure_dir(tpl_dir)
        ensure_dir(out_dir)

        ext = row["file_ext"] or _guess_ext(src_url)
        tpl_name = f"{_safe_name(row['name'])}{ext}"
        tpl_path = tpl_dir / tpl_name

        # Download template (lazy import requests and handle error)
        try:
            import requests  # lazy
            if not tpl_path.exists():
                with requests.get(src_url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(tpl_path, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            if chunk:
                                f.write(chunk)
        except Exception as e:
            return jsonify(error=f"Failed to download template: {e}"), 400

        # Build a minimal profile for suggestions
        profile = _business_profile(conn, int(row["business_id"]))
        out_path = out_dir / f"{row['id']}_filled{ext}"

        if ext.lower() == ".docx":
            prefill_docx(tpl_path, out_path, suggestions=_suggestions_from_profile(profile, row))
        elif ext.lower() == ".xlsx":
            prefill_xlsx(tpl_path, out_path, suggestions=_suggestions_from_profile(profile, row))
        else:
            return jsonify(error=f"Unsupported template type {ext}"), 400

        conn.execute(text(
            'update "application_document" '
            "set status='generated', ai_filled_payload=:p, storage_path=:spath "
            "where id=:doc and application_id=:app"
        ), {
            "doc": int(doc_id),
            "app": int(app_id),
            "p": json.dumps({"suggestions": _suggestions_from_profile(profile, row), "file": str(out_path)}),
            "spath": str(out_path),
        })

    return jsonify({
        "ok": True,
        "applicationId": int(app_id),
        "docId": int(doc_id),
        "status": "generated",
        "download": f"/api/apply/download/{app_id}/{doc_id}"
    })


def _suggestions_from_profile(profile: dict, row: dict) -> dict:
    """Very small deterministic set; you can enrich later or call LLM upstream."""
    total_ha = profile.get("total_suprafata_ha") or 0
    animals = profile.get("numar_total_animale") or 0
    crops = ", ".join(sorted(set([str(x).lower() for x in profile.get("culturi_cuvinte", []) if x])))
    user = profile.get("utilizator") or {}
    return {
        "Denumirea solicitantului": f"{user.get('firstName','')} {user.get('lastName','')}".strip() or "[Nume solicitant]",
        "Telefon": user.get("phone") or "",
        "Email": user.get("email") or "",
        "Profil fermă (rezumat)": f"Culturi: {crops or '-'}; Suprafață: {total_ha} ha; Animale: {animals}.",
        "Suprafață totală (ha)": f"{total_ha}",
    }


@bp_apply.get("/download/<int:app_id>/<int:doc_id>")
def apply_download(app_id: int, doc_id: int):
    base_dir = DATA_DIR / "applications" / str(app_id) / "filled"
    if not base_dir.exists():
        return jsonify(error="No generated files"), 404
    # Find file by doc_id prefix
    candidates = list(base_dir.glob(f"{doc_id}_filled.*"))
    if not candidates:
        # try database path
        with engine.connect() as conn:
            rec = conn.execute(text(
                'select storage_path from "application_document" '
                "where id=:d and application_id=:a"
            ), {"d": int(doc_id), "a": int(app_id)}).mappings().first()
        if not rec or not rec.get("storage_path"):
            return jsonify(error="Filled file not found"), 404
        file_path = pathlib.Path(rec["storage_path"])
    else:
        file_path = candidates[0]

    if not file_path.exists():
        return jsonify(error="Stored file not found on disk"), 404
    return send_file(str(file_path), as_attachment=True, download_name=file_path.name)


@bp_apply.post("/change")
def apply_change():
    """
    Let user patch suggestions in ai_filled_payload.
    Body: { "applicationId": <int>, "docId": <int>, "patch": { "Telefon": "0690 00000", ... } }
    """
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    patch = data.get("patch") or {}
    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId are required"), 400

    with engine.begin() as conn:
        payload = conn.execute(text(
            'select ai_filled_payload from "application_document" '
            "where id=:doc and application_id=:app"
        ), {"doc": int(doc_id), "app": int(app_id)}).scalar()

        suggestions = {}
        try:
            if payload:
                suggestions = (payload or {}).get("suggestions") or {}
        except Exception:
            suggestions = {}

        # apply user patch (as strings)
        suggestions.update({str(k): str(v) for k, v in patch.items()})

        conn.execute(text(
            'update "application_document" '
            "set ai_filled_payload=:p, status='generated' "
            "where id=:doc and application_id=:app"
        ), {"doc": int(doc_id), "app": int(app_id), "p": json.dumps({"suggestions": suggestions})})

    return jsonify({"ok": True, "document": {"id": int(doc_id), "status": "generated"}, "suggestions": suggestions})


@bp_apply.post("/approve")
def apply_approve():
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId are required"), 400

    with engine.begin() as conn:
        upd = conn.execute(text(
            'update "application_document" set status = \'approved\' '
            "where id=:doc and application_id=:app "
            "returning id, application_id, status"
        ), {"doc": int(doc_id), "app": int(app_id)}).mappings().first()
        if not upd:
            return jsonify(error="Document not found for application"), 404

    return jsonify({"ok": True, "document": dict(upd)})


@bp_apply.post("/reject")
def apply_reject():
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    reason = (data.get("reason") or "").strip()
    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId are required"), 400

    with engine.begin() as conn:
        upd = conn.execute(text(
            'update "application_document" set status = \'rejected\', rejection_reason = :r '
            "where id=:doc and application_id=:app "
            "returning id, application_id, status, rejection_reason"
        ), {"doc": int(doc_id), "app": int(app_id), "r": reason}).mappings().first()
        if not upd:
            return jsonify(error="Document not found for application"), 404

    return jsonify({"ok": True, "document": dict(upd)})


@bp_apply.post("/submit")
def apply_submit():
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    if not app_id:
        return jsonify(error="applicationId is required"), 400

    with engine.begin() as conn:
        counts = conn.execute(text(
            'select '
            "sum(case when status='approved' then 1 else 0 end) as approved, "
            "count(*) as total "
            'from "application_document" where application_id = :a'
        ), {"a": int(app_id)}).mappings().first()

        approved = (counts or {}).get("approved", 0) or 0
        total = (counts or {}).get("total", 0) or 0
        if total == 0:
            return jsonify(error="No documents for application"), 400
        if approved != total:
            return jsonify(error=f"All documents must be approved before submitting (approved {approved}/{total})."), 400

        app_upd = conn.execute(text(
            'update "application" set status = \'submitted\', submitted_at = now() '
            "where id=:a returning id, status, submitted_at"
        ), {"a": int(app_id)}).mappings().first()

    return jsonify({"ok": True, "application": dict(app_upd)})
