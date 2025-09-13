# app/scraper/routes.py
from flask import Blueprint, request, jsonify
from Services.scrape import scrape_and_summarize, discover_file_links, DEFAULT_EXTS

scraper_bp = Blueprint("scraper", __name__, url_prefix="/api/scraper")

@scraper_bp.post("/links")
def list_links():
    data = request.get_json(force=True) or {}
    pages = data.get("pages") or []
    exts  = data.get("ext") or DEFAULT_EXTS
    if not pages:
        return jsonify(error="Provide pages: []"), 400
    all_links = []
    for p in pages:
        all_links.extend(discover_file_links(p, exts))
    # de-dupe
    all_links = list(dict.fromkeys(all_links))
    return jsonify({"pages": pages, "ext": exts, "links": all_links})

@scraper_bp.post("/run")
def run_pipeline():
    data = request.get_json(force=True) or {}
    pages = data.get("pages") or []
    exts  = data.get("ext") or DEFAULT_EXTS
    lang  = data.get("lang", "ro")
    save  = data.get("save_dir")  # optional: path to persist files
    dry   = bool(data.get("dry_run", False))
    if not pages:
        return jsonify(error="Provide pages: []"), 400
    out = scrape_and_summarize(pages, exts=exts, lang=lang, save_dir=save, dry_run=dry)
    return jsonify(out)


bp = Blueprint("main", __name__)

@bp.route("/")
def home():
    return "Hello, World!"

bp.register_blueprint(scraper_bp)