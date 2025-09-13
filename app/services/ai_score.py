# app/services/ai_score.py
import os, json, re
from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # cheap/solid
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def band_from_score(s: int) -> str:
    if s >= 80: return "green"
    if s >= 50: return "yellow"
    return "red"

def infer_code_from_filename(name: str) -> str:
    # e.g. "Cerere SP 2.10 ...", "SP_2.1", etc
    m = re.search(r"SP[\s_]*(\d+\.\d+)", name)
    return f"SP_{m.group(1)}" if m else "GENERAL"

def score_one(farm_profile: dict, doc: dict) -> dict:
    """
    doc: {subsidy_code, about, doc_type, text_excerpt, filename}
    Returns: {"subsidy_code","score","band","reasons":[],"missing":[],"raw":{...}}
    """
    system = (
        "Ești un evaluator pentru eligibilitatea subvențiilor AIPA.\n"
        "Răspuns STRICT JSON (fără alt text) cu cheile: eligible:boolean, score:int(0..100), "
        "reasons:list[str], missing:list[str], risks:list[str]. Limba: română."
    )
    user = {
        "ferma": farm_profile,
        "document": {
            "cod": doc["subsidy_code"],
            "tip": doc.get("doc_type"),
            "descriere": doc.get("about"),
            # tăiem textul ca să nu depășim; sumarul tău are deja ~15k max
            "fragment": (doc.get("text_excerpt") or "")[:14000],
        },
        "scorare": {
            "criterii": [
                "Potrivire sector (vegetal/viticole/zootehnie/irigare/tehnologii)",
                "Scara investiției vs dimensiunea fermei",
                "Utilaj/vehicule relevante",
                "Situația financiară (venituri/cheltuieli disponibile)",
                "Lipsuri critice de date pentru dosar (IDNO, denumire entitate, etc)",
            ],
            "regulă_benzi": {"verde": ">=80", "galben": "50-79", "roșu": "<50"}
        }
    }

    resp = client.responses.create(
        model=MODEL,
        input=[
            {"role":"system","content":system},
            {"role":"user","content":json.dumps(user, ensure_ascii=False)}
        ],
    )
    raw = resp.output_text
    try:
        js = json.loads(raw)
        score = int(js.get("score", 0))
    except Exception:
        js = {"eligible": False, "score": 0, "reasons": ["Eroare de parsare răspuns."], "missing": ["Revizuiți promptul."], "risks":[]}
        score = 0

    return {
        "subsidy_code": doc["subsidy_code"],
        "score": score,
        "band": band_from_score(score),
        "reasons": js.get("reasons", []),
        "missing": js.get("missing", []),
        "raw": js
    }
