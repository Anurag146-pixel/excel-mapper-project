import json, csv
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import openpyxl, uvicorn

# =====================================================
# CONFIG
# =====================================================

BASE = Path(r"D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader")
TEMPLATE_DIR = BASE / "TEMPLATE"
SOURCE_DIR   = BASE / "SOURCE"
RESULT_DIR   = BASE / "RESULT"
EXTS = {".xlsx", ".xls", ".csv"}

app = FastAPI(title="Excel Mapper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================
# HELPERS — FILE I/O
# =====================================================

def to_grid(fp: Path):
    """Read any supported file into a 2D list of strings."""
    if fp.suffix.lower() == ".csv":
        return list(csv.reader(open(fp, encoding="utf-8-sig", errors="replace")))
    wb = openpyxl.load_workbook(fp, data_only=True)
    ws = wb.active
    return [[str(c) if c is not None else "" for c in r] for r in ws.iter_rows(values_only=True)]

def find_keyword(grid, kw, exact=False):
    """Find first cell matching keyword; returns (row, col) or (None, None)."""
    kw = kw.strip().lower()
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            s = str(cell).strip().lower()
            if (s == kw) if exact else (kw in s):
                return r, c
    return None, None

# =====================================================
# CORE EXTRACTION — single section
# =====================================================

def extract_section(grid, sec_def):
    """
    Extract data from grid using a section definition dict.
    sec_def shape:
        { section_name, data: {from_keyword, including_spaces1, skip_rows, skip_cols},
          end_section: {extract_upto_rows, extract_upto_columns, until_keyword, including_spaces} }
    """
    d = sec_def.get("data", {})
    e = sec_def.get("end_section", {})

    kw = d.get("from_keyword", "").strip()
    if not kw:
        return []

    exact = d.get("including_spaces1") == "1"
    sr, sc = find_keyword(grid, kw, exact)
    if sr is None:
        return {"warning": f"Keyword '{kw}' not found in sheet"}

    dr = sr + int(d.get("skip_rows", 1))
    dc = sc + int(d.get("skip_cols", 0))

    until_kw = e.get("until_keyword", "").strip()
    urows    = int(e.get("extract_upto_rows", 0))
    ucols    = int(e.get("extract_upto_columns", 0))

    # Determine end row
    if until_kw:
        er = next(
            (r for r in range(dr, len(grid))
             if until_kw.lower() in str(grid[r][sc]).strip().lower()),
            None
        )
        if er is None:
            er = dr + urows if urows else len(grid)
    else:
        er = dr + urows if urows else len(grid)

    ec = dc + ucols if ucols else None

    return [row[dc:ec] for row in grid[dr:er]]

# =====================================================
# RECURSIVE EXTRACTION — handles nested children
# =====================================================

def build_section_map(template):
    """Build a lookup dict: section_name -> section_def from flat sections list."""
    return {s["section_name"]: s for s in template.get("sections", [])}

def build_group_map(template):
    """Build a lookup dict: group_name -> group_def from flat groups list."""
    return {g["group_name"]: g for g in template.get("groups", [])}

def extract_node(node, grid, section_map, group_map):
    """
    Recursively extract a tree node.
    node shape: { type, name, children: [...] }
    types: ref-section, ref-group (and legacy: section_name key)
    """
    node_type = node.get("type", "")
    name      = node.get("name", "")
    children  = node.get("children", [])

    result = {}

    # Extract this node's own data
    if node_type in ("ref-section", "section"):
        sec_def = section_map.get(name)
        if sec_def:
            result["_data"] = extract_section(grid, sec_def)
        else:
            result["_data"] = {"warning": f"Section '{name}' not found in template definitions"}

    elif node_type in ("ref-group", "group"):
        grp_def = group_map.get(name)
        if grp_def:
            # Extract that group's children recursively
            for child in grp_def.get("children", []):
                child_name = child.get("name", "")
                result[child_name] = extract_node(child, grid, section_map, group_map)
        else:
            result["_data"] = {"warning": f"Group '{name}' not found in template definitions"}

    # Extract this node's inline children (nested inside this instance)
    for child in children:
        child_name = child.get("name", "")
        result[child_name] = extract_node(child, grid, section_map, group_map)

    return result

def apply_template(template, grid):
    """
    Apply a full template to a grid.
    Returns a dict with:
      - top-level sections (flat)
      - top-level groups (recursively extracted)
    """
    section_map = build_section_map(template)
    group_map   = build_group_map(template)
    out = {}

    # Extract all flat sections
    for sec in template.get("sections", []):
        out[sec["section_name"]] = extract_section(grid, sec)

    # Extract all groups (recursively)
    for grp in template.get("groups", []):
        grp_result = {}
        for child in grp.get("children", []):
            child_name = child.get("name", "")
            grp_result[child_name] = extract_node(child, grid, section_map, group_map)
        out[grp["group_name"]] = grp_result

    return out

# =====================================================
# MODELS
# =====================================================

class TemplatePayload(BaseModel):
    template_name: str
    created_date: Optional[str] = None
    sections: list = []
    groups: list = []

class ExtractionRequest(BaseModel):
    template_name: str
    source_file: str

# =====================================================
# ENDPOINTS — TEMPLATE
# =====================================================

@app.post("/save-template")
def save_template(payload: TemplatePayload):
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in payload.template_name)
    fp = TEMPLATE_DIR / f"{safe}.json"
    json.dump({
        "template_name": payload.template_name,
        "created_date": payload.created_date or datetime.now().isoformat(),
        "sections": payload.sections,
        "groups": payload.groups
    }, open(fp, "w", encoding="utf-8"), indent=2)
    return {"success": True, "file_path": str(fp)}


@app.get("/get-templates")
def get_templates():
    tmpls = []
    if TEMPLATE_DIR.is_dir():
        for f in sorted(TEMPLATE_DIR.glob("*.json")):
            try:
                data = json.load(open(f, encoding="utf-8"))
                tmpls.append({
                    "template_name": data.get("template_name", f.stem),
                    "file_name": f.name,
                    "section_count": len(data.get("sections", [])),
                    "group_count": len(data.get("groups", []))
                })
            except Exception:
                pass
    return {"success": True, "templates": tmpls}

# =====================================================
# ENDPOINTS — SOURCE FILES
# =====================================================

@app.get("/get-source-files")
def get_source_files():
    """List all Excel/CSV files in the SOURCE folder."""
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        f.name for f in sorted(SOURCE_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in EXTS
    ]
    return {"success": True, "files": files}

# =====================================================
# ENDPOINTS — MANUAL EXTRACTION
# =====================================================

@app.post("/execute-extraction")
def execute_extraction(req: ExtractionRequest):
    """Run extraction for one template + one source file."""
    # Resolve template
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in req.template_name)
    tmpl_path = TEMPLATE_DIR / f"{safe}.json"
    if not tmpl_path.exists():
        # Try finding by template_name field inside any JSON
        found = None
        if TEMPLATE_DIR.is_dir():
            for f in TEMPLATE_DIR.glob("*.json"):
                try:
                    d = json.load(open(f, encoding="utf-8"))
                    if d.get("template_name") == req.template_name:
                        found = f
                        break
                except Exception:
                    pass
        if not found:
            raise HTTPException(404, f"Template '{req.template_name}' not found")
        tmpl_path = found

    template = json.load(open(tmpl_path, encoding="utf-8"))

    # Resolve source file
    src_path = SOURCE_DIR / req.source_file
    if not src_path.exists():
        raise HTTPException(404, f"Source file '{req.source_file}' not found in SOURCE folder")

    try:
        grid   = to_grid(src_path)
        result = apply_template(template, grid)
    except Exception as ex:
        raise HTTPException(500, f"Extraction error: {str(ex)}")

    # Save result
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_filename = f"{src_path.stem}_{safe}_{ts}.json"
    result_path = RESULT_DIR / result_filename

    json.dump({
        "source_file": req.source_file,
        "template": req.template_name,
        "extracted_at": datetime.now().isoformat(),
        "data": result
    }, open(result_path, "w", encoding="utf-8"), indent=2)

    return {
        "success": True,
        "result": result,
        "result_file": str(result_path)
    }

# =====================================================
# ENDPOINTS — BULK EXTRACTION
# =====================================================

@app.post("/bulk-extract")
def bulk_extract():
    """
    Process all source files in SOURCE folder against all templates in TEMPLATE folder.
    Saves results to RESULT folder.
    """
    if not SOURCE_DIR.is_dir():
        raise HTTPException(404, "SOURCE folder not found")
    if not TEMPLATE_DIR.is_dir():
        raise HTTPException(404, "TEMPLATE folder not found")

    source_files = [f for f in SOURCE_DIR.iterdir() if f.is_file() and f.suffix.lower() in EXTS]
    templates    = []
    for f in TEMPLATE_DIR.glob("*.json"):
        try:
            templates.append((f, json.load(open(f, encoding="utf-8"))))
        except Exception:
            pass

    if not source_files:
        return {"success": True, "processed": 0, "failed": 0, "message": "No source files found"}
    if not templates:
        return {"success": True, "processed": 0, "failed": 0, "message": "No templates found"}

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    processed = 0
    failed    = 0

    for src in source_files:
        try:
            grid = to_grid(src)
            for tmpl_path, template in templates:
                result = apply_template(template, grid)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_name = f"{src.stem}_{tmpl_path.stem}_{ts}.json"
                json.dump({
                    "source_file": src.name,
                    "template": template.get("template_name", tmpl_path.stem),
                    "extracted_at": datetime.now().isoformat(),
                    "data": result
                }, open(RESULT_DIR / out_name, "w", encoding="utf-8"), indent=2)
            processed += 1
        except Exception as ex:
            failed += 1
            print(f"[ERROR] {src.name}: {ex}")

    return {
        "success": True,
        "processed": processed,
        "failed": failed,
        "total_source_files": len(source_files),
        "templates_used": len(templates)
    }


@app.get("/extraction-status")
def extraction_status():
    """Return counts of source files, results produced, etc."""
    source_files = list(SOURCE_DIR.glob("*")) if SOURCE_DIR.is_dir() else []
    result_files = list(RESULT_DIR.glob("*.json")) if RESULT_DIR.is_dir() else []
    template_files = list(TEMPLATE_DIR.glob("*.json")) if TEMPLATE_DIR.is_dir() else []

    src_count  = sum(1 for f in source_files if f.is_file() and f.suffix.lower() in EXTS)
    res_count  = sum(1 for f in result_files if f.is_file())
    tmpl_count = sum(1 for f in template_files if f.is_file())

    return {
        "success": True,
        "total": src_count,
        "processed": res_count,
        "pending": max(0, src_count - res_count),
        "failed": 0,
        "templates": tmpl_count
    }

# =====================================================
# HEALTH
# =====================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "base_path": str(BASE),
        "template_dir": str(TEMPLATE_DIR),
        "source_dir": str(SOURCE_DIR),
        "result_dir": str(RESULT_DIR),
    }

# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)