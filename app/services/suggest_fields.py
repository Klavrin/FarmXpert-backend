# app/services/suggest_fields.py
import os, json
from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def suggest_field_values(farm_profile: dict, subsidy_code: str) -> dict:
    """
    Returnează un dict {camp: valoare} în română (nu inventăm IDNO real).
    Când lipsesc date critice: prefixăm cu «NECESAR: ...».
    """
    system = (
        "Ești un asistent care pregătește câmpuri pentru formulare AIPA."
        " Produce STRICT JSON obiect simplu cheie->valoare în limba română."
        " Nu inventa ID-uri reale; dacă lipsesc, folosește «NECESAR: <nume_câmp>»."
    )
    user = {
        "subsidy_code": subsidy_code,
        "ferma": farm_profile,
        "campuri_tipice": [
            "Denumirea solicitantului (entitate sau nume complet)",
            "IDNO (dacă e persoană juridică) / IDNP (dacă e persoană fizică)",
            "Telefon",
            "Email",
            "Adresa (dacă o ai; altfel NECESAR)",
            "Suma subvenției solicitate (lei) – dacă lipsește, pune «NECESAR: sumă»",
            "Descriere scurtă investiție",
        ]
    }
    resp = client.responses.create(
        model=MODEL,
        input=[{"role":"system","content":system},{"role":"user","content":json.dumps(user, ensure_ascii=False)}],
    )
    raw = resp.output_text
    try:
        return json.loads(raw)
    except Exception:
        return {"Observație": "Nu am putut genera câmpurile. Completați manual în UI."}
