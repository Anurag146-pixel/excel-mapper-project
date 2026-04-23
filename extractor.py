import json, csv
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import openpyxl, uvicorn
import mysql.connector

BASE      = Path(r"D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader")
DOC_TYPES = ["BILLS", "RECEIPTS"]
EXTS      = {".xlsx", ".xls", ".csv"}

app = FastAPI(title="Excel Mapper API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ───────────────────────── DB CONFIG ───────────────────────── #

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "database": "excel_reader"
}


# 🔹 Insert Pending (Register) — returns token_id
def insert_pending_token(source_file, template_name):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        query = """
        INSERT INTO Token_details (source_file_name, template_name, result_file_name, status)
        VALUES (%s, %s, %s, %s)
        """

        cursor.execute(query, (source_file, template_name, "", "pending"))
        conn.commit()

        token_id = cursor.lastrowid

        cursor.close()
        conn.close()

        return token_id

    except Exception as e:
        print("DB Insert Error:", str(e))
        return None


# 🔹 Update to DONE after processing
def insert_token(source_file, template_name, result_file):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        query = """
        UPDATE Token_details
        SET result_file_name = %s, status = 'done'
        WHERE source_file_name = %s 
        AND template_name = %s 
        AND status = 'pending'
        LIMIT 1
        """

        cursor.execute(query, (result_file, source_file, template_name))
        conn.commit()

        cursor.close()
        conn.close()

    except Exception as e:
        print("DB Update Error:", str(e))


# 🔹 Get Status
def get_status(token_id):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        query = "SELECT status FROM Token_details WHERE token_id = %s"
        cursor.execute(query, (token_id,))
        result = cursor.fetchone()

        cursor.close()
        conn.close()

        return result[0] if result else "Not Found"

    except Exception as e:
        return str(e)


# ───────────────────────── HELPERS ───────────────────────── #

def resolve_folder(parent: Path, name: str) -> Optional[Path]:
    if not parent.is_dir(): return None
    for entry in parent.iterdir():
        if entry.is_dir() and entry.name.lower() == name.lower():
            return entry
    return None

def resolve_file(folder: Path, name: str) -> Optional[Path]:
    if not folder.is_dir(): return None
    for entry in folder.iterdir():
        if entry.is_file() and entry.name.lower() == name.lower():
            return entry
    return None

def parse(key):
    p = key.rsplit("-", 1)
    return (p[0], p[1].upper()) if len(p) == 2 and p[1].upper() in DOC_TYPES else (None, None)

def paths(company: str, doc: str):
    c_folder = resolve_folder(BASE, company)
    if not c_folder:
        raise HTTPException(404, f"Company folder not found: {company}")

    d_folder = resolve_folder(c_folder, doc)
    if not d_folder:
        raise HTTPException(404, f"Doc type folder not found: {doc}")

    return d_folder / "SOURCE", d_folder / "TEMPLATE", d_folder / "RESULT"


# ───────────────────────── CORE LOGIC ───────────────────────── #

def to_grid(fp: Path):
    if fp.suffix.lower() == ".csv":
        return list(csv.reader(open(fp, encoding="utf-8-sig", errors="replace")))
    wb = openpyxl.load_workbook(fp, data_only=True)
    ws = wb.active
    return [[str(c) if c is not None else "" for c in r] for r in ws.iter_rows(values_only=True)]

def find(g, kw, exact=False):
    kw = kw.strip().lower()
    for r, row in enumerate(g):
        for c, cell in enumerate(row):
            s = str(cell).strip().lower()
            if (s == kw) if exact else (kw in s):
                return r, c
    return None, None

def extract(g, sec):
    d, e = sec.get("data", {}), sec.get("end_section", {})

    kw = d.get("from_keyword", "").strip()
    if not kw:
        return []

    sr, sc = find(g, kw, d.get("including_spaces1") == "1")
    if sr is None:
        return []

    dr = sr + int(d.get("skip_rows", 1))
    dc = sc + int(d.get("skip_cols", 0))

    until = e.get("until_keyword", "").strip()
    urows = int(e.get("extract_upto_rows", 0))
    ucols = int(e.get("extract_upto_columns", 0))

    er = next(
        (r for r in range(dr, len(g)) if until and until.lower() in str(g[r][sc]).strip().lower()),
        None
    )

    if er is None:
        er = dr + urows if urows else len(g)

    ec = dc + ucols if ucols else None

    return [row[dc:ec] for row in g[dr:er]]

def apply(tmpl, g):
    out = {s["section_name"]: extract(g, s) for s in tmpl.get("sections", [])}

    for grp in tmpl.get("groups", []):
        out[grp["group_name"]] = {
            s["section_name"]: extract(
                g,
                next((x for x in tmpl.get("sections", []) if x["section_name"] == s["section_name"]), {})
            )
            for s in grp.get("sections", [])
        }

    return out


# 🔥 PROCESS FUNCTION
def process(src: Path, tdir: Path, rdir: Path):
    if src.stem.endswith("_done"):
        return "skipped"

    tmpls = list(tdir.glob("*.json")) if tdir.is_dir() else []
    if not tmpls:
        raise HTTPException(404, "No templates found")

    g = to_grid(src)
    rdir.mkdir(parents=True, exist_ok=True)

    template_names = []

    for tp in tmpls:
        template_name = tp.stem
        template_names.append(template_name)

        data = apply(json.load(open(tp, encoding="utf-8")), g)

        result_file_name = f"{src.stem}-RESULT.json"
        out_file = rdir / result_file_name

        out_file.write_text(
            json.dumps({
                "source_file": src.name,
                "extracted_at": datetime.now().isoformat(),
                "data": data
            }, indent=2),
            encoding="utf-8"
        )

    insert_token(
        source_file=src.name,
        template_name=",".join(template_names),
        result_file=result_file_name
    )

    done = src.parent / (src.stem + "_done" + src.suffix)
    src.rename(done)

    return "success"


# ───────────────────────── PYDANTIC MODELS ───────────────────────── #

class RegisterRequest(BaseModel):
    source_file: str
    template_name: str


# ───────────────────────── SHARED STYLE ───────────────────────── #

STYLE = """
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #1a1a2e, #16213e, #0f3460, #533483);
            background-size: 400% 400%;
            animation: gradientShift 10s ease infinite;
        }

        @keyframes gradientShift {
            0%   { background-position: 0% 50%; }
            50%  { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .card {
            background: rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 20px;
            padding: 40px 50px;
            width: 420px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
            color: white;
        }

        h2 {
            font-size: 26px;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(90deg, #e96c6c, #f9c74f, #43e97b);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .note {
            font-size: 13px;
            color: #a0c4ff;
            margin-bottom: 24px;
            font-style: italic;
        }

        label {
            display: block;
            font-size: 13px;
            color: #ccc;
            margin-bottom: 6px;
            margin-top: 16px;
        }

        input[type="text"],
        input[type="number"] {
            width: 100%;
            padding: 10px 14px;
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.2);
            background: rgba(255,255,255,0.1);
            color: white;
            font-size: 14px;
            outline: none;
            transition: border 0.3s;
        }

        input[type="text"]:focus,
        input[type="number"]:focus {
            border-color: #a78bfa;
        }

        input[type="submit"] {
            margin-top: 28px;
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 10px;
            background: linear-gradient(90deg, #7c3aed, #3b82f6);
            color: white;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: opacity 0.3s;
        }

        input[type="submit"]:hover {
            opacity: 0.85;
        }

        .result-box {
            margin-top: 20px;
            padding: 16px 20px;
            background: rgba(255,255,255,0.07);
            border-radius: 12px;
            border-left: 4px solid #43e97b;
        }

        .result-box p {
            font-size: 15px;
            margin: 6px 0;
            color: #e2e8f0;
        }

        .result-box span {
            color: #f9c74f;
            font-weight: 600;
        }
    </style>
"""


# ───────────────────────── HTML APIs ───────────────────────── #

# 🔹 REGISTER PAGE
@app.get("/extract/register", response_class=HTMLResponse)
def register_form():
    return f"""
    <html>
        <head>{STYLE}</head>
        <body>
            <div class="card">
                <h2>📋 Register Extraction</h2>
                <p class="note">Kindly enter the source name and template name.</p>
                <form action="/extract/register" method="post">
                    <label>Source File</label>
                    <input type="text" name="source_file" placeholder="e.g. invoice.xlsx">
                    <label>Template Name</label>
                    <input type="text" name="template_name" placeholder="e.g. bills_template">
                    <input type="submit" value="Submit">
                </form>
            </div>
        </body>
    </html>
    """


@app.post("/extract/register", response_class=HTMLResponse)
def register(source_file: str = Form(...), template_name: str = Form(...)):
    insert_pending_token(source_file, template_name)

    return f"""
    <html>
        <head>{STYLE}</head>
        <body>
            <div class="card">
                <h2>✅ Registered Successfully</h2>
                <div class="result-box">
                    <p>Source File: <span>{source_file}</span></p>
                    <p>Template: <span>{template_name}</span></p>
                </div>
            </div>
        </body>
    </html>
    """


# 🔹 REGISTER — JSON API
@app.post("/extract/register/json")
def register_json(req: RegisterRequest):
    token_id = insert_pending_token(req.source_file, req.template_name)

    if token_id is None:
        raise HTTPException(status_code=500, detail="Failed to insert into database")

    return {
        "success": True,
        "message": "Successfully registered",
        "token_id": token_id
    }


# 🔹 RESULT PAGE
@app.post("/extract/register", response_class=HTMLResponse)
def register(source_file: str = Form(...), template_name: str = Form(...)):
    token_id = insert_pending_token(source_file, template_name)

    return f"""
    <html>
        <head>{STYLE}</head>
        <body>
            <div class="card">
                <h2>✅ Registered Successfully</h2>
                <div class="result-box">
                    <p>Source File: <span>{source_file}</span></p>
                    <p>Template: <span>{template_name}</span></p>
                </div>
                <p style="margin-top: 20px; font-size: 15px; color: #43e97b; font-weight: 600;">
                    🎉 Your registration is successful with Token ID: <strong>{token_id}</strong>
                </p>
            </div>
        </body>
    </html>
    """

@app.get("/extract/result", response_class=HTMLResponse)
def result_form():
    return f"""
    <html>
        <head>{STYLE}</head>
        <body>
            <div class="card">
                <h2>📊 Check Status</h2>
                <p class="note">Enter your Token ID to check extraction status.</p>
                <form action="/extract/result" method="post">
                    <label>Token ID</label>
                    <input type="number" name="token_id" placeholder="e.g. 42">
                    <input type="submit" value="Check Status">
                </form>
            </div>
        </body>
    </html>
    """
@app.post("/extract/result", response_class=HTMLResponse)
def result(token_id: int = Form(...)):
    status = get_status(token_id)

    return f"""
    <html>
        <head>{STYLE}</head>
        <body>
            <div class="card">
                <h2>📊 Status Result</h2>
                <div class="result-box">
                    <p>Token ID: <span>{token_id}</span></p>
                    <p>Status: <span>{status}</span></p>
                </div>
            </div>
        </body>
    </html>
    """


# ───────────────────────── EXTRACTION APIs ───────────────────────── #

@app.get("/extract/{company}/{doc_type}")
def extract_all(company: str, doc_type: str):
    sdir, tdir, rdir = paths(company, doc_type)

    pending = [
        f for f in sdir.iterdir()
        if f.suffix.lower() in EXTS and not f.stem.endswith("_done")
    ]

    success, failed = 0, 0

    for src in pending:
        try:
            result = process(src, tdir, rdir)
            if result == "success":
                success += 1
        except:
            failed += 1

    return {"success": True, "message": f"Done ({success} success, {failed} failed)"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ───────────────────────── RUN ───────────────────────── #

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7777, reload=False)