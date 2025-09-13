import os, re, json, pathlib, urllib.parse, tempfile
import requests
from bs4 import BeautifulSoup

from pypdf import PdfReader
from docx import Document as DocxDocument
import pandas as pd

USE_OPENAI = True
try:
    from openai import OpenAI
except Exception:
    USE_OPENAI = False

DEFAULT_EXTS = [".pdf", ".docx", ".xlsx"]
MAX_CHARS = 15000

def to_abs(url, base): return urllib.parse.urljoin(base, url)

def safe_filename(name: str) -> str:
    name = urllib.parse.unquote(name)
    name = re.sub(r'[:*?"<>|]', "_", name).strip()
    return name[:200] or "download"

def discover_file_links(page_url: str, allowed_exts=DEFAULT_EXTS):
    r = requests.get(page_url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        absu = to_abs(href, page_url)
        path = urllib.parse.urlparse(absu).path.lower()
        if any(path.endswith(ext) for ext in allowed_exts):
            if absu not in seen:
                seen.add(absu)
                out.append(absu)
    return out

def download(url: str, outdir: pathlib.Path) -> pathlib.Path:
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / safe_filename(url.split("/")[-1])
    if dest.exists(): return dest
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1<<20):
                if chunk: f.write(chunk)
    return dest

def extract_text_from_pdf(path: pathlib.Path) -> str:
    parts = []
    with open(path, "rb") as f:
        reader = PdfReader(f)
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            parts.append(f"\n\n=== [PDF page {i+1}] ===\n{t}")
    return "".join(parts).strip()

def extract_text_from_docx(path: pathlib.Path) -> str:
    doc = DocxDocument(str(path))
    return "\n".join(p.text for p in doc.paragraphs).strip()

def extract_text_from_xlsx(path: pathlib.Path) -> str:
    try:
        xl = pd.ExcelFile(path)
    except Exception as e:
        return f"[Could not open Excel: {e}]"
    parts = []
    for sheet in xl.sheet_names:
        df = xl.parse(sheet).iloc[:200, :30]
        parts.append(f"\n\n=== [Sheet: {sheet}] ===\n{df.to_csv(index=False)}")
    return "".join(parts).strip()

def extract_text(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":  return extract_text_from_pdf(path)
    if ext == ".docx": return extract_text_from_docx(path)
    if ext == ".xlsx": return extract_text_from_xlsx(path)
    return f"[Unsupported extension {ext}]"

def chunk_text(s: str, size: int = MAX_CHARS):
    s = s.strip()
    if len(s) <= size: return [s]
    return [s[i:i+size] for i in range(0, len(s), size)]

def openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY first")
    return OpenAI(api_key=api_key)

def summarize_with_openai(text: str, filename: str, *, lang: str = "ro", model: str | None = None, max_chunks: int = 3) -> dict:
    """
    Short, structured summary:
      - 2–3 sentences (≈ <= 60 words total) that say exactly what the file is about,
        who it's for, and what action it enables.
      - Returns strict JSON: {filename, language, doc_type, about}
    """
    if not USE_OPENAI:
        return {
            "filename": filename,
            "language": lang,
            "doc_type": "altele",
            "about": "[AI dezactivat] Instalează openai și setează OPENAI_API_KEY."
        }

    client = openai_client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # set via env if you prefer another

    # Keep it cheap/fast: limit to the first few chunks
    chunks = chunk_text(text)
    joined = "\n\n".join(chunks[:max_chunks])

    prompt = (
        f"Sarcină: oferă o descriere **foarte concisă** (2–3 propoziții, max 60 de cuvinte) "
        f"care explică exact despre ce este fișierul «{filename}», pentru cine este și ce acțiune permite.\n"
        f"Identifică și tipul documentului (una dintre: cerere, ghid, fișă de calcul, anexă, contract, altele).\n"
        f"Limba răspunsului: {lang}.\n\n"
        "Returnează STRICT JSON (fără text în afara JSON-ului) cu cheile exacte:\n"
        "{\n"
        '  "filename": string,\n'
        '  "language": string,\n'
        '  "doc_type": string,\n'
        '  "about": string  // 2–3 propoziții, fără liste\n'
        "}\n\n"
        "Text:\n"
        f"{joined}"
    )

    resp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": prompt}],
    )
    raw = resp.output_text
    try:
        data = json.loads(raw)
        # Ensure required keys exist (light post-validate)
        return {
            "filename": data.get("filename", filename),
            "language": data.get("language", lang),
            "doc_type": data.get("doc_type", "altele"),
            "about": data.get("about", ""),
        }
    except Exception:
        # If model returned non-JSON, keep the raw text (debug)
        return {"filename": filename, "language": lang, "doc_type": "altele", "about": "", "raw": raw}
    
    
def scrape_and_summarize(pages: list[str], *, exts=DEFAULT_EXTS, lang="ro", save_dir: str | None = None, dry_run=False):
    # Discover links
    all_links = []
    for p in pages:
        all_links.extend(discover_file_links(p, exts))
    # de-dupe
    all_links = list(dict.fromkeys(all_links))
    if dry_run:
        return {"links": all_links}

    # Download + extract + summarize
    results, errors = [], []
    tmp_ctx = tempfile.TemporaryDirectory() if not save_dir else None
    base_out = pathlib.Path(save_dir or tmp_ctx.name)
    files_dir = base_out / "files"
    for url in all_links:
        try:
            fpath = download(url, files_dir)
            text = extract_text(fpath)
            summary = summarize_with_openai(text, fpath.name, lang=lang)
            results.append({"url": url, "filename": fpath.name, "summary": summary})
        except Exception as e:
            errors.append({"url": url, "error": str(e)})
    if tmp_ctx: tmp_ctx.cleanup()
    return {"links": all_links, "results": results, "errors": errors}
