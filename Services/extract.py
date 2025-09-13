# Services/extract.py
from __future__ import annotations
import os, re, json, datetime as dt
from typing import Any, Dict, List
from .scrape import openai_client, chunk_text

MEASURE_RE = re.compile(r"\bSP[_\.\s]*(\d{1,2})(?:[_\.\s]*(\d{1,2}))?\b", re.I)
DATE_RE    = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")

def detect_measure(filename: str, preview_text: str) -> str | None:
    txt = f"{filename} {preview_text}"
    m = MEASURE_RE.search(txt)
    if not m: return None
    a, b = m.group(1), m.group(2)
    return f"SP_{a}" + (f".{b}" if b else "")

def detect_language(filename: str) -> str:
    # crude but works for ro/ru based on your sample
    if re.search(r"[\u0400-\u04FF]", filename):  # Cyrillic => ru
        return "ru"
    return "ro"

def parse_date(s: str) -> dt.date | None:
    m = DATE_RE.search(s)
    if not m: return None
    d, mo, y = map(int, m.groups())
    try:
        return dt.date(y, mo, d)
    except Exception:
        return None

def llm_json(prompt: str, *, model_env="OPENAI_MODEL", default_model="gpt-4o-mini") -> dict:
    client = openai_client()
    model  = os.getenv(model_env, default_model)
    try:
        chat = client.chat.completions.create(
            model=model,
            messages=[{"role":"user","content":prompt}],
            response_format={"type":"json_object"},
            temperature=0
        )
        return json.loads(chat.choices[0].message.content)
    except TypeError:
        # SDK without response_format
        chat = client.chat.completions.create(
            model=model,
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )
        txt = chat.choices[0].message.content or "{}"
        jmatch = re.search(r"\{.*\}", txt, flags=re.S)
        return json.loads(jmatch.group(0)) if jmatch else {}
    except Exception as e:
        return {"_error": str(e)}

ELIGIBILITY_SYSTEM_PROMPT = """Extrage reguli de eligibilitate clare din textul de mai jos.
Întoarce STRICT JSON cu cheile:
{
  "title": string,                     // denumirea măsurii sau pe scurt ce acoperă documentul
  "applies_to": [string],              // ex: ["fermieri", "asociații utilizatori apă", "vitivinicol"]
  "deadline": { "start": "YYYY-MM-DD|null", "end": "YYYY-MM-DD|null" },
  "rule_set": {                        // un DSL simplu AND/OR pe câmpuri ușor mapabile la baza de date
    "all": [
      // exemple de reguli:
      // {"field":"users.verified","op":"==","value":true}
      // {"field":"field.cropType","op":"in","value":["grâu","porumb"],"aggregate":"any"}
      // {"field":"cattle.type","op":"in","value":["bovine","ovine"],"aggregate":"any"}
      // {"field":"finance.yearlyIncome","op":">=","value":100000}
    ]
  },
  "required_fields": [                 // câmpuri care apar în cerere/fișe și trebuie pre-completate
    {"key":"users.firstName","label":"Numele solicitantului"},
    {"key":"users.lastName","label":"Prenumele solicitantului"},
    {"key":"users.email","label":"E-mail"},
    {"key":"users.phone","label":"Telefon"}
  ]
}"""

def build_subsidy_index(scrape_result: dict) -> List[Dict[str, Any]]:
    """
    Input = your /api/scraper/run output
    Output = array of subsidy entries with code, rules, docs etc.
    """
    out: Dict[str, Dict[str, Any]] = {}

    for item in scrape_result.get("results", []):
        fn   = item.get("filename","")
        url  = item.get("url","")
        ext  = item.get("ext","")
        summ = item.get("summary",{}) or {}
        about = summ.get("about","")
        preview = item.get("text_preview","")
        text_for_llm = "\n".join(chunk_text(preview or about, 6000)[:1])  # keep prompt light

        code = detect_measure(fn, preview) or "GENERAL"
        lang = detect_language(fn)

        if code not in out:
            out[code] = {
                "code": code,
                "language": lang,
                "title": "", "applies_to": [],
                "deadline": {"start": None, "end": None},
                "rule_set": {"all":[]},
                "required_fields": [],
                "docs": []
            }

        # classify doc type from your summarizer + filename
        doc_type = (summ.get("doc_type") or "").lower()
        if not doc_type or doc_type == "altele":
            if ext == "docx" and "cerere" in fn.lower(): doc_type = "cerere"
            elif ext == "xlsx": doc_type = "fișă de calcul"
            elif ext == "pdf" and ("hotărâre" in fn.lower() or "hg" in fn.lower()): doc_type = "hotărâre"
            elif ext == "pdf" and "ordin" in fn.lower(): doc_type = "ordin"
            else:
                doc_type = ext

        out[code]["docs"].append({
            "type": doc_type, "filename": fn, "url": url, "ext": ext, "about": about
        })

        # Only run the LLM extractor once per code (prioritize ordin/ghid/cerere)
        if not out[code]["title"] and doc_type in ("ordin","ghid","cerere","hotărâre"):
            prompt = (
                ELIGIBILITY_SYSTEM_PROMPT + "\n\n=== TEXT ===\n" + text_for_llm
            )
            data = llm_json(prompt) or {}
            out[code]["title"] = data.get("title") or summ.get("filename") or code
            out[code]["applies_to"] = data.get("applies_to") or []
            out[code]["rule_set"] = data.get("rule_set") or {"all":[]}
            out[code]["required_fields"] = data.get("required_fields") or []
            out[code]["deadline"] = data.get("deadline") or {"start":None,"end":None}

    # Convert dict → list
    return list(out.values())
