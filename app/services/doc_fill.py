# app/services/doc_fill.py
from __future__ import annotations
import os, re, shutil, pathlib, json, base64, tempfile, urllib.parse
import requests
from docx import Document
import openpyxl

UNDERSCORES = re.compile(r"_{4,}")  # replace long blanks with a value

def ensure_dir(p: pathlib.Path):
    p.mkdir(parents=True, exist_ok=True)

def safe_filename(name: str) -> str:
    name = urllib.parse.unquote(name)
    name = re.sub(r'[:*?"<>|]', "_", name).strip()
    return name[:200] or "download"

def download_url(url: str, dest: pathlib.Path) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1<<20):
                if chunk:
                    f.write(chunk)
    return dest

def prefill_docx(src_path: pathlib.Path, dest_path: pathlib.Path, suggestions: dict):
    """
    Naiv dar eficient: înlocuiește secvențe lungi de underscore cu valori propuse.
    Dacă într-un paragraf există mai multe câmpuri, punem valorile în ordine.
    """
    doc = Document(str(src_path))
    values = list(suggestions.values()) or ["[completați]"]
    for para in doc.paragraphs:
        if "____" in para.text:
            idx = 0
            def rep(m):
                nonlocal idx
                v = values[min(idx, len(values)-1)]
                idx += 1
                return v
            new_text = UNDERSCORES.sub(rep, para.text)
            for run in list(para.runs):  # replace text safely
                run.text = ""
            para.add_run(new_text)
    doc.save(str(dest_path))

def prefill_xlsx(src_path: pathlib.Path, dest_path: pathlib.Path, suggestions: dict):
    """
    Simplu: scriem un sheet numit 'PROPUNERI' cu valorile cheie->valoare.
    (Nu stricăm fișele AIPA; utilizatorul le poate copia.)
    """
    wb = openpyxl.load_workbook(str(src_path))
    try:
        sheet = wb.create_sheet("PROPUNERI")
        sheet["A1"] = "Câmp"
        sheet["B1"] = "Valoare propusă"
        r = 2
        for k, v in suggestions.items():
            sheet.cell(row=r, column=1).value = k
            sheet.cell(row=r, column=2).value = v
            r += 1
        wb.save(str(dest_path))
    finally:
        # Important on Windows so temp files aren't locked
        wb.close()

# ----------------------------- NEW STUFF BELOW -----------------------------

def _first(d: dict, keys: list[str], default=None):
    """Safely fetch the first non-empty key from possibly nested dicts."""
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return default

def _join_nonempty(*vals, sep=" "):
    return sep.join(str(v) for v in vals if v not in (None, "", []))

def _build_suggestions(profile: dict, subsidy: dict | None, lang: str = "ro") -> dict:
    """
    Produce chei uzuale pentru formularele AIPA. Robuste la lipsa unor câmpuri.
    profile: rezultat din app.services.farm_profile.load_farm_profile(conn, business_id)
    subsidy: {"code": "...", "title": "..."} (opțional)
    """
    user = profile.get("user") or profile.get("primary_user") or {}
    business = profile.get("business") or {}
    finance = profile.get("finance") or {}
    fields = profile.get("fields") or []
    animals = profile.get("animals") or []
    vehicles = profile.get("vehicles") or []
    cattle = profile.get("cattle") or []

    first_name = _first(user, ["firstName", "first_name", "firstname", "nume"])
    last_name  = _first(user, ["lastName", "last_name", "lastname", "prenume"])
    solicitant = _join_nonempty(first_name, last_name) or "[Nume solicitant]"
    idno       = _first(business, ["idno", "id", "businessId", "business_id"], _first(user, ["businessId", "business_id"]))
    phone      = _first(user, ["phone", "telefon"])
    email      = _first(user, ["email"])
    denumire   = _first(business, ["name", "denumire", "title"], solicitant)

    # agregate terenuri
    total_ha = 0.0
    crop_types = []
    for f in fields:
        try:
            if f.get("size") is not None:
                total_ha += float(f["size"])
        except Exception:
            pass
        ct = f.get("cropType") or f.get("crop_type")
        if ct:
            crop_types.append(str(ct))
    crop_types_txt = ", ".join(sorted(set(crop_types))) if crop_types else "—"

    # agregate animale
    animal_counts = {}
    for a in animals:
        sp = (a.get("species") or "").strip().lower() or "necunoscut"
        animal_counts[sp] = animal_counts.get(sp, 0) + 1
    animale_txt = ", ".join(f"{k}: {v}" for k, v in animal_counts.items()) if animal_counts else "—"

    # vehicule
    nr_veh = len(vehicles) if isinstance(vehicles, list) else 0

    # bovine/capete din tabela cattle (dacă există)
    bovine = 0
    try:
        for c in cattle:
            amt = c.get("amount")
            if amt is not None:
                bovine += int(amt)
    except Exception:
        pass

    venit_anual = finance.get("yearlyIncome")
    chelt_anual = finance.get("yearlyExpenses")

    su_code = (subsidy or {}).get("code") or ""
    su_title = (subsidy or {}).get("title") or ""

    sugestii = {
        "Denumirea solicitantului": denumire or solicitant,
        "Numele și prenumele": solicitant,
        "IDNO/Cod fiscal": idno or "[IDNO]",
        "Telefon": phone or "[Telefon]",
        "Email": email or "[Email]",
        "Măsura/Program": _join_nonempty(su_code, su_title, sep=" – ") if su_code or su_title else "—",
        "Suprafața totală exploatată (ha)": f"{total_ha:.2f}",
        "Culturile principale": crop_types_txt,
        "Efective de animale": animale_txt,
        "Număr vehicule agricole": str(nr_veh),
        "Efectiv bovine (capete)": str(bovine),
    }
    if venit_anual is not None:
        sugestii["Venit anual (lei)"] = str(venit_anual)
    if chelt_anual is not None:
        sugestii["Cheltuieli anuale (lei)"] = str(chelt_anual)

    # câteva câmpuri comune des întâlnite
    sugestii.update({
        "Localitate": business.get("localitate") or business.get("address") or "[Localitate]",
        "Data completării": "[Auto-completare la tipărire]",
    })
    return sugestii

def _build_plan_5_ani(profile: dict, subsidy: dict | None, lang: str = "ro") -> str:
    user = profile.get("user") or {}
    business = profile.get("business") or {}
    name = _join_nonempty(_first(user, ["firstName", "nume"]), _first(user, ["lastName", "prenume"])) or (business.get("name") or "Solicitant")
    su = (subsidy or {})
    titlu = su.get("title") or su.get("code") or "Investiție agricolă"

    return (
        f"Plan investițional pe 5 ani – {name}\n"
        f"Măsura: {su.get('code','—')} – {su.get('title','—')}\n\n"
        "Anul 1: Achiziție/implementare echipamente și inițiere lucrări. "
        "Întărirea capacității de producție, instruire personal, setarea evidenței costurilor.\n"
        "Anul 2: Optimizarea fluxurilor, creșterea productivității, extinderea suprafețelor/echipamentelor după necesar.\n"
        "Anul 3: Diversificare produse/servicii, îmbunătățire calitate, certificări dacă e cazul.\n"
        "Anul 4: Scalare și integrare verticală (prelucrare/comercializare), parteneriate noi.\n"
        "Anul 5: Consolidare poziție pe piață, evaluare rezultate, plan pentru reinvestirea profitului.\n\n"
        "Indicatori urmăriți: productivitate/ha, randament utilaje, venit operațional, reducerea cheltuielilor, calitatea produselor.\n"
        "Riscuri și măsuri: variație climatică (irigare/adaptarea tehnologiilor), volatilitate prețuri (contracte forward), "
        "riscuri operaționale (mentenanță preventivă, asigurări).\n"
    )

def prepare_documents(profile: dict, docs: list[dict], subsidy: dict | None = None, lang: str = "ro") -> dict:
    """
    docs: [{ "url": "https://.../Cerere_SP_2.10.docx" }, ...]
    Returnează: {
       "files": [{"sourceUrl","filename","ext","b64"}],
       "generated": {"plan_5_ani": "..."}
    }
    """
    suggestions = _build_suggestions(profile, subsidy, lang=lang)
    out_files = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        for item in docs:
            url = item.get("url")
            if not url:
                continue
            src_name = safe_filename(url.split("/")[-1])
            ext = pathlib.Path(src_name).suffix.lower().lstrip(".")
            src = tmp / src_name
            download_url(url, src)

            dest_name = src.stem + "_precompletat" + src.suffix
            dest = tmp / dest_name

            if ext == "docx":
                prefill_docx(src, dest, suggestions)
            elif ext == "xlsx":
                prefill_xlsx(src, dest, suggestions)
            else:
                # Skip unknown ext, but still include as original base64
                dest = src

            b = dest.read_bytes()
            b64 = base64.b64encode(b).decode("ascii")
            out_files.append({
                "sourceUrl": url,
                "filename": dest_name,
                "ext": ext,
                "b64": b64,
            })

    plan = _build_plan_5_ani(profile, subsidy, lang=lang)
    return {
        "files": out_files,
        "generated": {
            "plan_5_ani": plan
        }
    }
