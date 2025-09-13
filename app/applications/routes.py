from flask import Blueprint, jsonify
from sqlalchemy import text

applications_bp = Blueprint("applications", __name__, url_prefix="/api/applications")

@applications_bp.get("/<int:app_id>")
def get_application(app_id: int):
    with engine.begin() as conn:
        app_row = conn.execute(text("""
            select a.id, a.business_id, a.user_id, a.subsidy_id, a.status, a.created_at, a.submitted_at,
                   s.code as subsidy_code, s.title as subsidy_title
            from application a
            join subsidy s on s.id = a.subsidy_id
            where a.id = :id
        """), {"id": app_id}).mappings().first()
        if not app_row:
            return jsonify(error="Application not found"), 404

        docs = conn.execute(text("""
            select id, name, file_ext, status, rejection_reason
            from application_document
            where application_id = :id
            order by id
        """), {"id": app_id}).mappings().all()

    return jsonify({
        "id": app_row["id"],
        "businessId": app_row["business_id"],
        "userId": app_row["user_id"],
        "subsidy": {"code": app_row["subsidy_code"], "title": app_row["subsidy_title"]},
        "status": app_row["status"],
        "createdAt": app_row["created_at"].isoformat() if app_row["created_at"] else None,
        "submittedAt": app_row["submitted_at"].isoformat() if app_row["submitted_at"] else None,
        "documents": [dict(d) for d in docs],
    })
