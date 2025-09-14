import re, hashlib, json
from sqlalchemy import text

MEASURE_RE = re.compile(r'\bSP[ _.-]?(\d+)\.(\d+)\b', re.IGNORECASE)

def infer_doc_type(filename: str, fallback: str | None) -> str | None:
    name = (filename or "").lower()
    if "cerere" in name: return "cerere"
    if "fișa de calcul" in name or "fisa de calcul" in name or "fișa" in name: return "fișă de calcul"
    if "act " in name or name.startswith("act "): return "act"
    if "anexa" in name or "anexă" in name: return "anexă"
    if "angajament" in name: return "contract"
    if "ordin" in name: return "ordin"
    if "hotărâre" in name or "hotarare" in name or name.startswith("hg "): return "hotărâre"
    if "ghid" in name: return "ghid"
    return fallback

def infer_measure(s: str | None) -> str | None:
    if not s: return None
    m = MEASURE_RE.search(s)
    if not m: return None
    # Normalize: SP_2.10
    return f"SP_{m.group(1)}.{m.group(2)}"

def to_text_summary(summary_field) -> tuple[str | None, str | None, str | None, dict | None]:
    """
    Returns (summary_text, language, doc_type, summary_json)
    - Fixes 'dict has no attribute strip' by handling dicts and strings.
    """
    if isinstance(summary_field, dict):
        summary_text = summary_field.get("about") or None
        language = summary_field.get("language")
        doc_type = summary_field.get("doc_type")
        return summary_text, language, doc_type, summary_field
    if isinstance(summary_field, str):
        return summary_field or None, None, None, None
    return None, None, None, None

def persist_scraped_assets(db_session, items: list[dict]) -> dict:
    """
    Upserts everything into public.scraped_asset.
    URL is the natural unique key.
    """
    inserted = 0
    updated = 0
    skipped = 0
    sql = text("""
        insert into public.scraped_asset
          (url, filename, ext, doc_type, language, summary, summary_json, text_preview, measure_code, updated_at)
        values
          (:url, :filename, :ext, :doc_type, :language, :summary, :summary_json, :text_preview, :measure_code, now())
        on conflict (url) do update set
          filename = excluded.filename,
          ext = excluded.ext,
          doc_type = excluded.doc_type,
          language = excluded.language,
          summary = excluded.summary,
          summary_json = excluded.summary_json,
          text_preview = excluded.text_preview,
          measure_code = excluded.measure_code,
          updated_at = now()
        returning (xmax = 0) as inserted
    """)
    for it in items:
        try:
            url = it.get("url") or it.get("link")
            if not url:
                skipped += 1
                continue
            filename = it.get("filename")
            ext = it.get("ext")
            summary_text, lang_from_summary, doc_type_from_summary, summary_json = to_text_summary(it.get("summary"))
            # Prefer explicit doc_type from summary, else infer from filename
            doc_type = doc_type_from_summary or infer_doc_type(filename, None)
            # Language: prefer summary.lang, else heuristic from filename/text_preview
            language = lang_from_summary
            if not language:
                # crude, but works: Cyrillic => ru
                tp = (it.get("text_preview") or "") + " " + (filename or "")
                language = "ru" if re.search(r"[А-Яа-яЁё]", tp) else "ro"
            # Measure: search in filename, then preview
            measure = infer_measure(filename) or infer_measure(it.get("text_preview")) or None

            res = db_session.execute(sql, {
                "url": url,
                "filename": filename,
                "ext": ext,
                "doc_type": doc_type,
                "language": language,
                "summary": summary_text,
                "summary_json": json.dumps(summary_json, ensure_ascii=False) if summary_json else None,
                "text_preview": it.get("text_preview"),
                "measure_code": measure,
            })
            row_inserted = list(res)[0][0]  # True if inserted new row, False if updated
            if row_inserted:
                inserted += 1
            else:
                updated += 1
        except Exception:
            # Don't blow up the whole run on one bad item
            skipped += 1
    db_session.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}
