# app/apply/routes.py
from __future__ import annotations
import os, json, pathlib, tempfile, urllib.parse, requests
from flask import Blueprint, request, jsonify, send_file
from sqlalchemy import create_engine, text
from app.services.doc_fill import prefill_docx, prefill_xlsx, ensure_dir

USE_OPENAI = True
try:
    from openai import OpenAI
except Exception:
    USE_OPENAI = False

bp_apply = Blueprint("apply", __name__, url_prefix="/api/apply")

# Local engine (avoid circular import)
engine = create_engine(os.environ["DATABASE_URL"])
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", "./data")).resolve()

def openai_client():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY")
    return OpenAI(api_key=key)

def safe_name(s: str) -> str:
    s = urllib.parse.unquote(s or "").strip().replace("\\","_").replace("/","_")
    return (s[:180] or "fisier")

def guess_ext(url_or_name: str) -> str:
    p = urllib.parse.urlparse(url_or_name).path.lower()
    for ext in (".docx", ".xlsx", ".pdf"):
        if p.endswith(ext): return ext
    return pathlib.Path(url_or_name).suffix.lower() or ".docx"

def download_template(url: str, out_path: pathlib.Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1<<20):
                if chunk: f.write(chunk)

def build_suggestions(conn, business_id: int, user_id: int, lang="ro") -> dict:
    """Collect DB facts + (optional) ask AI to propose field values in Romanian."""
    user = conn.execute(text("""
        select userId, firstName, lastName, phone, email, businessId
        from users where userId = :u
    """), {"u": user_id}).mappings().first()

    fields = conn.execute(text("""
        select cropType, size, soilType, fertiliser, herbicide
        from field where businessId = :b
    """), {"b": business_id}).mappings().all()

    cattle = conn.execute(text("""
        select type, amount from cattle where businessId = :b
    """), {"b": business_id}).mappings().all()

    finance = conn.execute(text("""
        select yearlyIncome, yearlyExpenses from finance
        order by updatedAt desc nulls last limit 1
    """)).mappings().first()

    total_area = sum(float(f.get("size") or 0) for f in fields)
    total_animals = sum(int(c.get("amount") or 0) for c in cattle)
    facts = {
        "utilizator": dict(user) if user else {},
        "campuri": [dict(f) for f in fields[:10]],
        "efective_animale": [dict(c) for c in cattle[:10]],
        "finante": dict(finance) if finance else {},
        "total_suprafata_ha": total_area,
        "numar_total_animale": total_animals
    }

    base = {
        "Denumirea solicitantului": f"{(user or {}).get('firstName','')} {(user or {}).get('lastName','')}".strip() or "[Nume solicitant]",
        "IDNO/IDNP solicitant": str((user or {}).get("userId") or "") or "[ID solicitant]",
        "Telefon": (user or {}).get("phone") or "",
        "E-mail": (user or {}).get("email") or "",
        "Suprafața totală exploatată (ha)": f"{total_area:.2f}",
        "Efective animale (total)": str(total_animals),
        "Venit anual (lei)": str((finance or {}).get("yearlyIncome") or ""),
        "Cheltuieli anuale (lei)": str((finance or {}).get("yearlyExpenses") or ""),
    }

    if USE_OPENAI and os.getenv("OPENAI_API_KEY"):
        try:
            client = openai_client()
            prompt = (
                "Generează câmpuri propuse pentru completarea documentelor AIPA.\n"
                "Răspunde STRICT JSON (fără text în afara JSON-ului), chei simple în română, valori concise.\n"
                "Dacă nu ai o valoare credibilă, pune un placeholder în paranteze pătrate (ex. \"[completați]\").\n\n"
                f"Fapte despre fermă:\n{facts}\n\n"
                "Exemple de chei: \"Denumirea solicitantului\", \"Adresa exploatației\", "
                "\"Suma subvenției solicitate (lei)\", \"Descriere scurtă investiție\", "
                "\"Suprafața vizată (ha)\", \"Cod IBAN\", \"Banca\".\n"
            )
            resp = client.responses.create(
                model=os.getenv("OPENAI_MODEL","gpt-4o-mini"),
                input=[{"role": "user", "content": prompt}],
            )
            import json as _json
            ai = _json.loads(resp.output_text)
            base.update({k: str(v) for k,v in ai.items()})
        except Exception:
            # keep base only
            pass
    return base

@bp_apply.post("/prepare")
def apply_prepare():
    data = request.get_json(force=True) or {}
    subsidy_code = data.get("subsidyCode")
    business_id  = data.get("businessId")
    user_id      = data.get("userId")
    if not subsidy_code or not business_id or not user_id:
        return jsonify(error="subsidyCode, businessId, userId required"), 400

    with engine.begin() as conn:
        sub = conn.execute(
            text("select id, code, title from subsidy where code = :c"),
            {"c": subsidy_code}
        ).mappings().first()
        if not sub:
            return jsonify(error="Unknown subsidyCode"), 404

        app_row = conn.execute(text("""
            insert into application (business_id, user_id, subsidy_id, status)
            values (:b, :u, :s, 'draft')
            returning id, business_id, user_id, subsidy_id, status, created_at
        """), {"b": business_id, "u": user_id, "s": sub["id"]}).mappings().first()

        # templates optional table; else provide defaults
        try:
            templates = conn.execute(text("""
                select id, name, file_ext, source_url
                from doc_template
                where subsidy_id = :sid
                order by id
            """), {"sid": sub["id"]}).mappings().all()
        except Exception:
            templates = []

        if not templates:
            # You can pass real docUrl later to /fill
            templates = [
                {"id": None, "name": "Cerere",         "file_ext": "docx", "source_url": None},
                {"id": None, "name": "Fișa de calcul", "file_ext": "xlsx", "source_url": None},
            ]

        docs = []
        for t in templates:
            d = conn.execute(text("""
                insert into application_document (application_id, template_id, name, file_ext, status, ai_filled_payload)
                values (:aid, :tid, :name, :ext, 'pending', '{}'::jsonb)
                returning id, name, file_ext, status
            """), {
                "aid": app_row["id"],
                "tid": t.get("id"),
                "name": t["name"],
                "ext": t["file_ext"]
            }).mappings().first()
            docs.append(d)

    return jsonify({
        "applicationId": app_row["id"],
        "subsidy": {"code": sub["code"], "title": sub["title"]},
        "status": "draft",
        "documents": [
            {"id": d["id"], "name": d["name"], "ext": d["file_ext"], "status": d["status"]}
            for d in docs
        ],
        "missingFields": []
    })

@bp_apply.post("/fill")
def apply_fill():
    """
    Body:
    {
      "applicationId": 123,
      "docId": 456,
      "docUrl": "https://aipa.gov.md/...docx",   // optional if you have doc_template.source_url
      "lang": "ro"
    }
    """
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    doc_url = data.get("docUrl")
    lang = data.get("lang","ro")
    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId required"), 400

    with engine.begin() as conn:
        row = conn.execute(text("""
            select ad.id, ad.name, ad.file_ext, a.business_id, a.user_id, a.subsidy_id, dt.source_url
            from application_document ad
            join application a on a.id = ad.application_id
            left join doc_template dt on dt.id = ad.template_id
            where ad.id = :d and ad.application_id = :a
        """), {"d": doc_id, "a": app_id}).mappings().first()
        if not row:
            return jsonify(error="Document not found"), 404

        src_url = doc_url or row.get("source_url")
        if not src_url:
            return jsonify(error="docUrl missing and no template source_url found"), 400

        base_dir = DATA_DIR / "applications" / str(app_id)
        tpl_dir  = base_dir / "templates"
        out_dir  = base_dir / "filled"
        ensure_dir(tpl_dir); ensure_dir(out_dir)

        ext = row["file_ext"] or guess_ext(src_url)
        tpl_name = f"{safe_name(row['name'])}{ext}"
        tpl_path = tpl_dir / tpl_name
        if not tpl_path.exists():
            download_template(src_url, tpl_path)

        # Build suggestions (DB + AI)
        suggestions = build_suggestions(conn, row["business_id"], row["user_id"], lang=lang)

        # Fill
        out_path = out_dir / f"{row['id']}_filled{ext}"
        if ext == ".docx":
            prefill_docx(tpl_path, out_path, suggestions)
        elif ext == ".xlsx":
            prefill_xlsx(tpl_path, out_path, suggestions)
        else:
            return jsonify(error=f"Unsupported template type {ext}"), 400

        conn.execute(text("""
            update application_document
            set status = 'generated', ai_filled_payload = :p
            where id = :doc and application_id = :app
        """), {"doc": doc_id, "app": app_id, "p": json.dumps({"suggestions": suggestions, "file": str(out_path)})})

    return jsonify({
        "ok": True,
        "applicationId": app_id,
        "docId": doc_id,
        "status": "generated",
        "download": f"/api/apply/download/{app_id}/{doc_id}"
    })

@bp_apply.get("/download/<int:app_id>/<int:doc_id>")
def apply_download(app_id: int, doc_id: int):
    base_dir = DATA_DIR / "applications" / str(app_id) / "filled"
    # find file by doc_id prefix
    candidates = list(base_dir.glob(f"{doc_id}_filled.*"))
    if not candidates:
        return jsonify(error="Filled file not found"), 404
    return send_file(str(candidates[0]), as_attachment=True, download_name=candidates[0].name)

@bp_apply.post("/change")
def apply_change():
    """
    Allows user to propose manual edits to AI-filled suggestions (stored in ai_filled_payload).
    Body:
    {
      "applicationId": 123,
      "docId": 456,
      "patch": { "Telefon": "0690 00000", "Suma subvenției solicitate (lei)": "40000" }
    }
    """
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    patch  = data.get("patch") or {}
    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId required"), 400

    with engine.begin() as conn:
        # read existing payload
        payload = conn.execute(text("""
            select ai_filled_payload
            from application_document
            where id = :doc and application_id = :app
        """), {"doc": doc_id, "app": app_id}).scalar()

        suggestions = {}
        try:
            if payload: suggestions = (payload or {}).get("suggestions") or {}
        except Exception:
            suggestions = {}

        suggestions.update({k: str(v) for k, v in patch.items()})

        # mark back as 'generated' (needs re-download)
        conn.execute(text("""
            update application_document
            set ai_filled_payload = :p, status = 'generated'
            where id = :doc and application_id = :app
        """), {"doc": doc_id, "app": app_id, "p": json.dumps({"suggestions": suggestions})})

    return jsonify({"ok": True, "document": {"id": doc_id, "status": "generated"}, "suggestions": suggestions})

@bp_apply.post("/approve")
def apply_approve():
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId required"), 400

    with engine.begin() as conn:
        upd = conn.execute(text("""
            update application_document
            set status = 'approved'
            where id = :doc and application_id = :app
            returning id, application_id, status
        """), {"doc": doc_id, "app": app_id}).mappings().first()
        if not upd:
            return jsonify(error="Document not found for application"), 404

    return jsonify({"ok": True, "document": dict(upd)})

@bp_apply.post("/reject")
def apply_reject():
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    doc_id = data.get("docId")
    reason = data.get("reason", "")
    if not app_id or not doc_id:
        return jsonify(error="applicationId and docId required"), 400

    with engine.begin() as conn:
        upd = conn.execute(text("""
            update application_document
            set status = 'rejected', rejection_reason = :r
            where id = :doc and application_id = :app
            returning id, application_id, status, rejection_reason
        """), {"doc": doc_id, "app": app_id, "r": reason}).mappings().first()
        if not upd:
            return jsonify(error="Document not found for application"), 404

    return jsonify({"ok": True, "document": dict(upd)})

@bp_apply.post("/submit")
def apply_submit():
    data = request.get_json(force=True) or {}
    app_id = data.get("applicationId")
    if not app_id:
        return jsonify(error="applicationId required"), 400

    with engine.begin() as conn:
        counts = conn.execute(text("""
            select
              count(*) filter (where status = 'approved') as approved,
              count(*) as total
            from application_document
            where application_id = :app
        """), {"app": app_id}).first()

        if not counts:
            return jsonify(error="No documents for application"), 400

        approved, total = counts
        if approved != total:
            return jsonify(error=f"All documents must be approved before submitting (approved {approved}/{total})."), 400

        app_upd = conn.execute(text("""
            update application
            set status = 'submitted', submitted_at = now()
            where id = :app
            returning id, status, submitted_at
        """), {"app": app_id}).mappings().first()

    return jsonify({"ok": True, "application": dict(app_upd)})
