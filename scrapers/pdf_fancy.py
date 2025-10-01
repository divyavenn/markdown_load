#!/usr/bin/env python3
import os, sys, json, hashlib, tempfile, shutil, re
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from PyPDF2 import PdfReader, PdfWriter
import torch

from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered
from marker.config.parser import ConfigParser

from dotenv import load_dotenv
load_dotenv()

PDF_PATH = "./documents/input_med.pdf"
OUT_PATH = "output_pdf.md"

# Models (override via env if you actually have GPT-5):
STRONG_MODEL = os.getenv("OPENAI_STRONG_MODEL", "gpt-5")       # e.g., "gpt-5"
FAST_MODEL   = os.getenv("OPENAI_FAST_MODEL", "gpt-4o-mini")    # e.g., "gpt-5-mini" or leave empty to disable tiering

USE_WHOLE_DOC = False
USE_TIERED    = True
OPENAI_TIMEOUT = 120
OPENAI_MAX_RETRIES = 6

# Internal worker cap on macOS to avoid nested pools (key name varies by build)
PDFTEXT_WORKERS_KEY = "workers"     # some builds prefer "pdftext_workers"
PDFTEXT_WORKERS_VAL = 1

# Escalation control
ESCALATE_MAX_FRACTION = 0.25   # never escalate more than 25% of pages
ESCALATE_MAX_ABS = 25          # …or more than 25 pages total, whichever is smaller

CACHE_DIR = os.getenv("MARKER_CACHE_DIR", ".marker_cache")




# ---------- utilities ----------
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def cache_key(file_hash: str, page_idx: Optional[int], model_id: str, marker_version: str, config_sig: str) -> str:
    pid = "doc" if page_idx is None else f"p{page_idx:05d}"
    raw = f"{file_hash}:{pid}:{model_id}:{marker_version}:{config_sig}"
    return hashlib.sha256(raw.encode()).hexdigest()

def cache_path(key: str) -> str:
    ensure_dir(CACHE_DIR)
    return os.path.join(CACHE_DIR, f"{key}.json")

def load_cached_text(key: str) -> Optional[str]:
    p = cache_path(key)
    if not os.path.exists(p): return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            j = json.load(f)
        return j.get("text")
    except Exception:
        return None

def save_cached_text(key: str, text: str):
    p = cache_path(key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"text": text}, f, ensure_ascii=False)
    os.replace(tmp, p)

def split_pdf_to_temp_pages(pdf_path: str) -> Tuple[str, int, List[str]]:
    temp_dir = tempfile.mkdtemp(prefix="marker_pages_")
    reader = PdfReader(pdf_path)
    page_files = []
    for i, page in enumerate(reader.pages):
        out_path = os.path.join(temp_dir, f"page_{i:05d}.pdf")
        writer = PdfWriter()
        writer.add_page(page)
        with open(out_path, "wb") as f:
            writer.write(f)
        page_files.append(out_path)
    return temp_dir, len(page_files), page_files


# ---------- validator heuristics ----------
_table_row = re.compile(r"^\s*\|.*\|\s*$")        # crude: a line starting/ending with |
_table_sep = re.compile(r"^\s*\|?\s*:?[-]{2,}.*\|?\s*$")  # --- style header sep
_bad_tokens = re.compile(r"(ocr[\s_-]?error|failed\s+ocr|illegible|###\s*table\s*failed)", re.I)

def _extract_tables(md: str) -> List[List[str]]:
    """Return list of tables; each as list of lines (strings)."""
    tables = []
    cur = []
    for line in md.splitlines():
        if _table_row.match(line) or _table_sep.match(line):
            cur.append(line)
        else:
            if cur:
                tables.append(cur)
                cur = []
    if cur: tables.append(cur)
    return tables

def _table_shape_ok(lines: List[str]) -> bool:
    # Count '|' columns per data row and check consistency (allow header sep)
    cols = None
    data_rows = 0
    for ln in lines:
        if _table_sep.match(ln):
            continue
        if _table_row.match(ln):
            # split but ignore leading/trailing bar empties
            parts = [p.strip() for p in ln.strip().strip("|").split("|")]
            if cols is None:
                cols = len(parts)
            elif len(parts) != cols:
                return False
            data_rows += 1
    return (cols or 0) >= 2 and data_rows >= 2

def is_suspect_markdown(md: str) -> bool:
    text = md.strip()
    if not text:
        return True

    # 1) Bad tokens / obvious OCR flags
    if _bad_tokens.search(text):
        return True

    # 2) Replacement chars or weirdness
    replacement_count = text.count("�")
    if replacement_count >= 5:
        return True

    # 3) Alphanumeric density (too symbol-heavy → likely mangled)
    alnum = sum(ch.isalnum() for ch in text)
    density = alnum / max(len(text), 1)
    if density < 0.15 and len(text) < 4000:  # long appendices can be graphs; tolerate length
        return True

    # 4) Tiny outputs on a page (likely missed recognition)
    if len(text) < 80:
        # mention of table with no actual table markup is extra suspicious
        if re.search(r"\btab(le|\.?)\b", text, re.I) and "|" not in text:
            return True
        # otherwise still suspicious
        return True

    # 5) Table structure checks
    tables = _extract_tables(text)
    if tables:
        # if we have many '|' but no valid tables → suspect
        any_ok = any(_table_shape_ok(t) for t in tables)
        if not any_ok:
            return True
    else:
        # A lot of bar characters but no recognized table blocks?
        bar_lines = sum(1 for ln in text.splitlines() if "|" in ln)
        if bar_lines >= 5:  # many pipes but no grouped tables => probably broken structure
            return True

    # Otherwise looks sane
    return False


# ---------- marker runner ----------
@dataclass
class MarkerRunner:
    api_key: str
    base_url: str
    marker_version: str
    config_sig: str

    def build_converter(self, model_id: str) -> PdfConverter:
        cfg: Dict[str, Any] = {
            "output_format": "markdown",
            "use_llm": True,
            "redo_inline_math": True,
            "llm_service": "marker.services.openai.OpenAIService",
            "openai_api_key": self.api_key,
            "openai_model": model_id,
            "openai_base_url": self.base_url,
            PDFTEXT_WORKERS_KEY: PDFTEXT_WORKERS_VAL,
            "openai_timeout": OPENAI_TIMEOUT,
            "openai_max_retries": OPENAI_MAX_RETRIES,
        }
        parser = ConfigParser(cfg)
        return PdfConverter(
            config=parser.generate_config_dict(),
            artifact_dict=create_model_dict(),
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
            llm_service=parser.get_llm_service(),
        )

    def run_markdown(self, converter: PdfConverter, path: str) -> str:
        result = converter(path)
        text, _, _ = text_from_rendered(result)
        return text


# ---------- main ----------
def main():
    api_key = os.getenv("OPENAI_API_KEY")
    assert api_key, "OPENAI_API_KEY missing"
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # keep MPS fallback OFF; we’re not threading through model code
    os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    _ = torch.backends.mps.is_available()

    try:
        import marker as _marker_mod
        marker_version = getattr(_marker_mod, "__version__", "unknown")
    except Exception:
        marker_version = "unknown"

    config_sig = f"redo_inline_math=1;workers={PDFTEXT_WORKERS_VAL}"
    runner = MarkerRunner(api_key, base_url, marker_version, config_sig)
    file_hash = sha256_file(PDF_PATH)

    # WHOLE-DOC route
    if USE_WHOLE_DOC:
        first_model = STRONG_MODEL if not (USE_TIERED and FAST_MODEL) else FAST_MODEL
        strong_needed = False

        k = cache_key(file_hash, None, first_model, marker_version, config_sig)
        cached = load_cached_text(k)
        if cached is None:
            conv = runner.build_converter(first_model)
            try:
                cached = runner.run_markdown(conv, PDF_PATH)
            except Exception:
                if USE_TIERED and first_model == FAST_MODEL:
                    strong_needed = True
                else:
                    raise
            if not strong_needed:
                save_cached_text(k, cached)

        if USE_TIERED and strong_needed:
            k2 = cache_key(file_hash, None, STRONG_MODEL, marker_version, config_sig)
            cached2 = load_cached_text(k2)
            if cached2 is None:
                conv2 = runner.build_converter(STRONG_MODEL)
                cached2 = runner.run_markdown(conv2, PDF_PATH)
                save_cached_text(k2, cached2)
            cached = cached2

        with open(OUT_PATH, "w", encoding="utf-8") as f:
            f.write(cached)
        print(f"Wrote markdown to {OUT_PATH}")
        return

    # PER-PAGE route (caching + selective escalation)
    temp_dir, n_pages, page_files = split_pdf_to_temp_pages(PDF_PATH)
    print(f"[info] split into {n_pages} pages → {temp_dir}")

    converters: Dict[str, Optional[PdfConverter]] = {}
    converters["fast"] = runner.build_converter(FAST_MODEL) if (USE_TIERED and FAST_MODEL) else None
    converters["strong"] = runner.build_converter(STRONG_MODEL)

    results: List[str] = ["" for _ in range(n_pages)]
    escalate_budget = min(int(n_pages * ESCALATE_MAX_FRACTION), ESCALATE_MAX_ABS)

    try:
        for idx, pf in enumerate(page_files):
            # 1) strongest cache
            k_strong = cache_key(file_hash, idx, STRONG_MODEL, marker_version, config_sig)
            t = load_cached_text(k_strong)
            if t is not None:
                results[idx] = t
                continue

            # 2) fast cache (if tiered)
            k_fast = cache_key(file_hash, idx, FAST_MODEL, marker_version, config_sig) if (USE_TIERED and FAST_MODEL) else None
            t_fast = load_cached_text(k_fast) if k_fast else None

            if t_fast is None and converters["fast"] is not None:
                try:
                    t_fast = runner.run_markdown(converters["fast"], pf)
                    save_cached_text(k_fast, t_fast)  # type: ignore[arg-type]
                except Exception as e:
                    # fast failed hard → force escalate if budget allows
                    t_fast = None

            # 3) validate fast output
            need_strong = (t_fast is None)
            if not need_strong:
                if is_suspect_markdown(t_fast):
                    need_strong = (escalate_budget > 0)

            if need_strong:
                try:
                    t_strong = runner.run_markdown(converters["strong"], pf)
                    save_cached_text(k_strong, t_strong)
                    results[idx] = t_strong
                    if t_fast is not None and is_suspect_markdown(t_fast):
                        escalate_budget = max(escalate_budget - 1, 0)
                except Exception as e:
                    # last resort: if fast exists, keep it; otherwise embed an error marker
                    results[idx] = t_fast if t_fast is not None else f"[ERROR page {idx}: {e!r}]"
            else:
                results[idx] = t_fast

            if (idx + 1) % 10 == 0 or idx == n_pages - 1:
                print(f"[progress] {idx+1}/{n_pages}, remaining escalate budget={escalate_budget}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    combined = "\n\n".join(results)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(combined)
    print(f"Wrote markdown to {OUT_PATH}")

if __name__ == "__main__":
    import multiprocessing as mp
    try: 
      mp.set_start_method("spawn", force=True)
    except RuntimeError: 
      pass
    sys.exit(main())