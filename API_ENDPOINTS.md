# FarmXpert API Reference

This file lists the most important API endpoints and how to call them. See handler implementations for details and behavior.

---

## Scraper (document discovery / download / docgen)
- GET /api/scraper/autocomplete  
  - Query: `q`, `code`, `lang`, `ext`, `limit`  
  - Returns: list of matching scraped documents.  
  - Handler: [`autocomplete_docs`](app/routes.py) — [app/routes.py](app/routes.py)

- POST /api/scraper/links  
  - Body: `{ "pages": string | [string], "ext": [".pdf", ".docx", ...] }`  
  - Returns: discovered links per page.  
  - Handler: [`list_links`](app/routes.py) — [app/routes.py](app/routes.py)

- POST /api/scraper/run  
  - Body: `{ "pages": [...], "exts": [...], "lang":"ro", "persist": true, "dry_run": false, "save_dir": "<path>" }`  
  - Returns: `{ links:[], results:[], errors:[], persist?:{...} }`  
  - Use `dry_run:true` to only discover links.  
  - Handler: [`run_pipeline`](app/routes.py) — [app/routes.py](app/routes.py)  
  - Scraper implementation: [Services/scrape.py](Services/scrape.py)

- POST /api/scraper/complete-docx  
  - Body: one of `{ "url": "<docx url>" }` or `{ "code":"SP_2.10", "doc_type":"cerere" }` plus optional `"profile"`, `"instructions"`, `"language"`, `"filename"`.  
  - Returns: generated .docx with suggestions inserted.  
  - Handler: [`complete_docx`](app/routes.py) — [app/routes.py](app/routes.py)  
  - Doc extraction/fill helpers: [app/services/doc_fill.py](app/services/doc_fill.py)

- POST /api/scraper/docgen  
  - Body: `{ "url": "<docx url>" }` or `{ "code": "...", "doc_type":"..." }`, optional `instructions`, `lang`, `filename`  
  - Returns: generated .docx built from AI-transformed text.  
  - Handler: [`docgen`](app/routes.py) — [app/routes.py](app/routes.py)

---

## Matching & Eligibility
- POST /api/match  
  - Body: `{ "user_id": <int> }`  
  - Runs subsidy matching for a user profile, stores run and items, returns ranked matches.  
  - Handler: [`match_subsidies`](app/subsidies/routes.py) — [app/subsidies/routes.py](app/subsidies/routes.py)  
  - Scoring logic / AI helper: [app/services/ai_score.py](app/services/ai_score.py)  
  - Rule evaluator: [Services/matcher.py](Services/matcher.py)

- POST /api/eligibility  
  - Body: `{ "rule_set": {...}, "dataset": {...} }`  
  - Evaluates rule set against provided dataset and returns explainable score.  
  - Handler: [`eligibility_check`](app/services/routes.py) — [app/services/routes.py](app/services/routes.py)  
  - Evaluator: [Services/matcher.py](Services/matcher.py)

---

## Application / Document filling
- POST /api/apply/prepare  
  - Body: `{ "user_id": <int>, "subsidy_code": "SP_2.10" }` (or businessId/userId variant in other route)  
  - Picks relevant templates/docs, runs prefill (docx/xlsx), stores draft and returns download links.  
  - Handler: [`apply_prepare`](app/services/routes.py) — [app/services/routes.py](app/services/routes.py)  
  - Prefill functions: [app/services/doc_fill.py](app/services/doc_fill.py)

- POST /api/apply/fill  
  - Body: `{ "applicationId": <int>, "docId": <int>, "docUrl": "<optional url>" }`  
  - Prefill a single document.  
  - Handler: [`apply_fill`](app/apply/routes.py) — [app/apply/routes.py](app/apply/routes.py)

---

## Utilities / Admin
- GET /db  
  - Returns DB server time (tests DB connectivity).  
  - Handler: [`db_now`](app/__init__.py) — [app/__init__.py](app/__init__.py)

- Files download route used by generated files:  
  - GET /api/files/<path:relpath>  
  - Handler: [`get_file`](app/services/routes.py) — [app/services/routes.py](app/services/routes.py)

---

## Notes & example

- Scraper run (Postman JSON body):
  ```
  {
    "pages": ["https://aipa.gov.md/"],
    "lang": "ro",
    "persist": true,
    "exts": [".pdf", ".docx", ".xlsx", ".doc", ".xls"]
  }
  ```
  
---

Files to inspect for implementation details:
- Scraper code: [Services/scrape.py](Services/scrape.py)  
- Doc filling + suggestions: [app/services/doc_fill.py](app/services/doc_fill.py)  
- Matching / scoring: [Services/matcher.py](Services/matcher.py) and [app/services/ai_score.py](app/services/ai_score.py)  
- Main HTTP routes: [app/routes.py](app/routes.py), [app/services/routes.py](app/services/routes.py), [app/apply/routes.py](app/apply/routes.py), [app/subsidies/routes.py](app/subsidies/routes.py)
