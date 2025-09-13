# app/scraper/routes.py
import os, json, pathlib, re
from flask import Blueprint, request, jsonify
from sqlalchemy import create_engine, text

# Keep your original import path casing
from Services.scrape import scrape_and_summarize, discover_file_links, DEFAULT_EXTS

engine = create_engine(os.environ["DATABASE_URL"])

scraper_bp = Blueprint("scraper", __name__, url_prefix="/api/scraper")
bp = Blueprint("main", __name__)

URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

def _normalize_pages(pages_raw):
    """
    Accepts a single string or a list of strings. Returns (pages, errors)
    where pages is a cleaned list of http(s) URLs.
    """
    errors = []
    if isinstance(pages_raw, str):
        pages = [pages_raw]
    elif isinstance(pages_raw, (list, tuple)):
        pages = [p for p in pages_raw if isinstance(p, str)]
    else:
        return [], ["'pages' must be a string or an array of strings"]

    # trim empties
    pages = [p.strip() for p in pages if p and p.strip()]

    # auto-prepend https:// for bare domains (optional; comment out if you prefer strict 400)
    fixed = []
    for p in pages:
        if URL_SCHEME_RE.match(p):
            fixed.append(p)
        elif re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", p, re.I):
            fixed.append("https://" + p)
        else:
            errors.append(f"Invalid URL (must start with http:// or https://): {p}")
    return fixed, errors

@bp.route("/")
def home():
    return "Hello, World!"

@scraper_bp.post("/links")
def list_links():
    data = request.get_json(force=True) or {}
    pages_raw = data.get("pages")
    # accept both "ext" and "exts"
    exts = data.get("ext") or data.get("exts") or DEFAULT_EXTS

    pages, errs = _normalize_pages(pages_raw)
    if errs:
        return jsonify(error="Bad 'pages' input", details=errs), 400
    if not pages:
        return jsonify(error="Provide pages: []"), 400

    links, errors = [], []
    for p in pages:
        try:
            links.extend(discover_file_links(p, exts))
        except Exception as e:
            errors.append({"page": p, "error": str(e)})

    links = list(dict.fromkeys(links))
    return jsonify({"pages": pages, "ext": exts, "links": links, "errors": errors})

@scraper_bp.post("/run")
def run_pipeline():
    data = request.get_json(force=True) or {}
    pages_raw = data.get("pages")
    exts = data.get("ext") or data.get("exts") or DEFAULT_EXTS
    lang = data.get("lang", "ro")
    save = data.get("save_dir")
    dry  = bool(data.get("dry_run", False))

    pages, errs = _normalize_pages(pages_raw)
    if errs:
        return jsonify(error="Bad 'pages' input", details=errs), 400
    if not pages:
        return jsonify(error="Provide pages: []"), 400

    out = scrape_and_summarize(pages, exts=exts, lang=lang, save_dir=save, dry_run=dry)
    return jsonify(out)

# mount /api/scraper/*
bp.register_blueprint(scraper_bp)
