"""
Unified API Server — REST + SOAP
Supports both REST (JSON) and SOAP (XML) protocols for Excel data extraction.
Does NOT modify ext.py in any way.
"""

import json
import logging
import traceback
import uuid
from pathlib import Path

import pymysql
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from ext import (
    get_source_file,
    get_template_file,
    to_grid,
    apply_template,
    DB_CONFIG,
    SOURCE_DIR,
    TEMPLATE_DIR,
    RESULT_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ───────── DB HELPERS ───────── #

def db_update_status(token_id: str, status: str, source: str = None, result: str = None):
    """
    Pure UPDATE — never inserts. Finds the record first, then updates.
    """
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            # Resolve numeric id
            numeric_id = token_id
            if isinstance(token_id, str) and token_id.startswith("token_"):
                try:
                    numeric_id = int(token_id.split("_")[1])
                except Exception:
                    logger.error(f"Cannot parse token_id: {token_id}")
                    return

            update_parts = ["status = %s"]
            params       = [status]

            if source:
                update_parts.append("source_file_name = %s")
                params.append(source)
            if result:
                update_parts.append("result_file_name = %s")
                params.append(result)

            params.append(numeric_id)
            query = f"UPDATE token_details SET {', '.join(update_parts)} WHERE token_id = %s"

            cur.execute(query, params)
            rows = cur.rowcount

        conn.commit()

        if rows == 1:
            logger.info(f"✅ DB UPDATE OK  token={token_id}  status={status}")
        else:
            logger.warning(f"⚠️  DB UPDATE rows_affected={rows} for token={token_id}")

    except Exception as e:
        logger.error(f"❌ DB Update Error: {e}")
        traceback.print_exc()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_create_token():
    """
    Find the first 'pending' record and return it as token_N.
    NEVER inserts — reuses existing pending rows.
    """
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT token_id FROM token_details WHERE status = 'pending' ORDER BY token_id ASC LIMIT 1"
            )
            row = cur.fetchone()

        if row:
            # DictCursor returns dict, normal cursor returns tuple
            tid = row["token_id"] if isinstance(row, dict) else row[0]
            formatted = f"token_{tid}"
            logger.info(f"✅ Found pending token: {formatted}")
            return formatted

        logger.error("❌ No pending records in token_details. Add rows with status='pending' first.")
        return None

    except Exception as e:
        logger.error(f"❌ db_create_token error: {e}")
        traceback.print_exc()
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_source_files():
    if SOURCE_DIR.exists():
        return sorted([f.name for f in SOURCE_DIR.iterdir() if f.is_file()])
    return []


def get_template_files():
    if TEMPLATE_DIR.exists():
        return sorted([f.name for f in TEMPLATE_DIR.iterdir() if f.is_file()])
    return []


# ───────── EXTRACTION PIPELINE ───────── #

def run_extraction_pipeline(token_id: str, source_file_name: str, template_name: str, protocol: str = "REST"):
    """
    1. Mark DB → processing
    2. Validate files
    3. to_grid → apply_template
    4. Write result JSON
    5. Rename source → _done_<uid>
    6. Mark DB → done
    """
    try:
        logger.info(f"[{protocol}] ══════════════════════════════════════")
        logger.info(f"[{protocol}] Pipeline START  token={token_id}")

        # 1. processing
        db_update_status(token_id, "processing")

        # 2. source file
        src = get_source_file(source_file_name)
        if not src:
            raise FileNotFoundError(f"Source file '{source_file_name}' not found in SOURCE directory")
        logger.info(f"[{protocol}] Source  : {src}")

        # 3. template file
        tmpl = get_template_file(template_name)
        if not tmpl:
            raise FileNotFoundError(f"Template '{template_name}' not found in TEMPLATE directory")
        logger.info(f"[{protocol}] Template: {tmpl}")

        # 4. load + extract
        grid          = to_grid(src)
        template_json = json.load(open(tmpl, encoding="utf-8"))
        logger.info(f"[{protocol}] Grid loaded: {len(grid)} rows")

        extraction = apply_template(template_json, grid)
        logger.info(f"[{protocol}] Extraction done — sections={list(extraction.get('sections', {}).keys())}")

        # Debug: log what was extracted
        for sec_name, sec_data in extraction.get("sections", {}).items():
            if isinstance(sec_data, dict):
                count = sec_data.get("count", "?")
                logger.info(f"[{protocol}]   Section {sec_name}: {count} records")
            else:
                logger.info(f"[{protocol}]   Section {sec_name}: {sec_data}")

        # 5. write result
        RESULT_DIR.mkdir(exist_ok=True)
        unique_id   = uuid.uuid4().hex[:8]
        result_name = f"{src.stem}_result_{unique_id}.json"
        result_path = RESULT_DIR / result_name

        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(extraction, f, indent=2)
        logger.info(f"[{protocol}] Result written: {result_path}")

        # 6. rename source
        import shutil
        done_name    = f"{src.stem}_done_{unique_id}{src.suffix}"
        new_src_path = SOURCE_DIR / done_name
        shutil.move(str(src), str(new_src_path))
        logger.info(f"[{protocol}] Source renamed: {src.name} → {done_name}")

        # 7. done
        db_update_status(token_id, "done", done_name, result_name)

        logger.info(f"[{protocol}] Pipeline DONE ✅  token={token_id}")
        logger.info(f"[{protocol}] ══════════════════════════════════════")

        return {
            "token_id":         token_id,
            "result_file_name": result_name,
            "done_source_name": done_name,
            "data":             extraction,
            "sections_count":   len(extraction.get("sections", {})),
            "groups_count":     len(extraction.get("groups",   {})),
        }

    except Exception as e:
        logger.error(f"[{protocol}] ❌ Pipeline ERROR: {e}")
        traceback.print_exc()
        db_update_status(token_id, "failed")
        raise


# ───────── FASTAPI APP ───────── #

app = FastAPI(title="Excel Extraction API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>FastAPI - Excel Extraction API</title>
        <link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500&display=swap" rel="stylesheet">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Roboto', sans-serif; background-color: #fafafa; color: #3b4151; }
            .topbar { background-color: #fff; border-bottom: 1px solid #f0f0f0; padding: 12px 20px; display: flex; align-items: center; }
            .topbar h1 { font-size: 20px; font-weight: 500; color: #222; }
            .topbar .info { margin-left: auto; font-size: 12px; color: #999; }
            .layout { display: flex; min-height: calc(100vh - 60px); }
            .sidebar { width: 250px; background: #fff; border-right: 1px solid #f0f0f0; overflow-y: auto; padding: 20px; }
            .main { flex: 1; padding: 20px; overflow-y: auto; }
            .endpoint-group { margin-bottom: 16px; }
            .endpoint-group h3 { font-size: 12px; text-transform: uppercase; color: #999; margin-bottom: 8px; font-weight: 600; letter-spacing: 0.5px; }
            .endpoint-item { padding: 8px 12px; margin-bottom: 4px; border-radius: 4px; cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 8px; transition: all 0.2s; user-select: none; }
            .endpoint-item:hover { background: #f0f0f0; }
            .endpoint-item.active { background: #e8f5e9; color: #2e7d32; font-weight: 600; }
            .method-badge { font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; color: white; }
            .method-post { background: #4caf50; }
            .content-area { max-width: 900px; }
            .api-operation { background: #fff; border: 1px solid #e0e0e0; border-radius: 4px; margin-bottom: 20px; overflow: hidden; }
            .api-op-header { background: #f5f5f5; padding: 16px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; gap: 12px; }
            .api-op-header .method { font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 4px; color: white; }
            .api-op-header .path { font-family: monospace; font-size: 13px; color: #333; font-weight: 500; flex: 1; }
            .api-op-body { padding: 20px; }
            .form-section { margin-bottom: 20px; }
            .form-section label { display: block; font-size: 13px; font-weight: 600; color: #222; margin-bottom: 12px; }
            .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px; }
            .input-group { display: flex; flex-direction: column; }
            .input-group label { font-size: 12px; font-weight: 500; color: #666; margin-bottom: 6px; }
            .input-group input { padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; font-family: monospace; }
            .input-group input:focus { outline: none; border-color: #2196f3; }
            .file-entry { background: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 4px; padding: 15px; margin-bottom: 12px; }
            .file-entry .entry-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
            .file-entry .entry-num { font-size: 12px; font-weight: 600; color: #999; background: #f0f0f0; padding: 2px 8px; border-radius: 3px; }
            .file-entry .btn-remove { background: #fff; border: 1px solid #f44336; color: #f44336; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: 600; }
            .buttons { display: flex; gap: 12px; margin-top: 24px; }
            .btn { padding: 10px 16px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.2s; }
            .btn-add { background: #e8f5e9; border-color: #4caf50; color: #2e7d32; }
            .btn-add:disabled { opacity: 0.6; cursor: not-allowed; }
            .btn-submit { background: #2196f3; border-color: #2196f3; color: white; width: 100%; margin-top: 20px; }
            .btn-submit:hover:not(:disabled) { background: #1976d2; }
            .btn-submit:disabled { opacity: 0.6; cursor: not-allowed; }
            .message-box { padding: 12px 16px; border-radius: 4px; margin-top: 12px; font-size: 13px; display: none; font-family: monospace; white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow-y: auto; }
            .message-success { background: #e8f5e9; border: 1px solid #4caf50; color: #2e7d32; }
            .message-error { background: #ffebee; border: 1px solid #f44336; color: #c62828; }
            .message-processing { background: #e3f2fd; border: 1px solid #2196f3; color: #1565c0; }
        </style>
    </head>
    <body>
        <div class="topbar">
            <h1>FastAPI - Excel Extraction Engine</h1>
            <div class="info">v1.0.0 | REST + SOAP</div>
        </div>
        <div class="layout">
            <div class="sidebar">
                <div class="endpoint-group">
                    <h3>Extraction APIs</h3>
                    <div class="endpoint-item" data-section="rest">
                        <span class="method-badge method-post">POST</span><span>REST Extract</span>
                    </div>
                    <div class="endpoint-item" data-section="soap">
                        <span class="method-badge method-post">POST</span><span>SOAP Extract</span>
                    </div>
                </div>
            </div>
            <div class="main">
                <div class="content-area">
                    <div id="rest-section" class="api-section" style="display:none;">
                        <div class="api-operation">
                            <div class="api-op-header">
                                <span class="method" style="background:#4caf50;">POST</span>
                                <span class="path">/api/rest/extract</span>
                            </div>
                            <div class="api-op-body">
                                <h3 style="margin-bottom:8px;">REST API Extraction</h3>
                                <p style="font-size:13px;color:#666;margin-bottom:20px;">Extract data from Excel files using REST (JSON)</p>
                                <div class="form-section">
                                    <label>Files <span id="rest-file-count" style="font-size:11px;color:#999;">(loading...)</span></label>
                                    <div id="rest-files-container"></div>
                                    <div class="buttons">
                                        <button class="btn btn-add" id="rest-add-btn" disabled>+ Add File</button>
                                    </div>
                                </div>
                                <button class="btn btn-submit" id="rest-submit-btn" disabled>Execute Extraction</button>
                                <div id="rest-msg" class="message-box"></div>
                            </div>
                        </div>
                    </div>
                    <div id="soap-section" class="api-section" style="display:none;">
                        <div class="api-operation">
                            <div class="api-op-header">
                                <span class="method" style="background:#4caf50;">POST</span>
                                <span class="path">/api/soap/extract</span>
                            </div>
                            <div class="api-op-body">
                                <h3 style="margin-bottom:8px;">SOAP API Extraction</h3>
                                <p style="font-size:13px;color:#666;margin-bottom:20px;">Extract data from Excel files using SOAP (XML)</p>
                                <div class="form-section">
                                    <label>Files <span id="soap-file-count" style="font-size:11px;color:#999;">(loading...)</span></label>
                                    <div id="soap-files-container"></div>
                                    <div class="buttons">
                                        <button class="btn btn-add" id="soap-add-btn" disabled>+ Add File</button>
                                    </div>
                                </div>
                                <button class="btn btn-submit" id="soap-submit-btn" disabled>Execute Extraction</button>
                                <div id="soap-msg" class="message-box"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <script>
            const state = { sourceFiles: [], templateFiles: [], restCounter: 0, soapCounter: 0, processing: false };

            async function init() {
                const [sources, templates] = await Promise.all([
                    fetch('/api/list-sources').then(r => r.json()).catch(() => []),
                    fetch('/api/list-templates').then(r => r.json()).catch(() => [])
                ]);
                state.sourceFiles   = sources   || [];
                state.templateFiles = templates || [];

                document.getElementById('rest-file-count').textContent = `(${state.sourceFiles.length} sources, ${state.templateFiles.length} templates)`;
                document.getElementById('soap-file-count').textContent = `(${state.sourceFiles.length} sources, ${state.templateFiles.length} templates)`;

                ['rest','soap'].forEach(p => {
                    document.getElementById(`${p}-add-btn`).disabled    = false;
                    document.getElementById(`${p}-submit-btn`).disabled = false;
                });

                document.querySelectorAll('.endpoint-item').forEach(el =>
                    el.addEventListener('click', () => showSection(el.dataset.section))
                );
                document.getElementById('rest-add-btn').addEventListener('click', () => addRow('rest'));
                document.getElementById('soap-add-btn').addEventListener('click', () => addRow('soap'));
                document.getElementById('rest-submit-btn').addEventListener('click', () => submit('rest'));
                document.getElementById('soap-submit-btn').addEventListener('click', () => submit('soap'));

                showSection('rest');
            }

            function showSection(s) {
                document.querySelectorAll('.api-section').forEach(el => el.style.display = 'none');
                document.getElementById(`${s}-section`).style.display = 'block';
                document.querySelectorAll('.endpoint-item').forEach(el => el.classList.remove('active'));
                document.querySelector(`[data-section="${s}"]`).classList.add('active');
            }

            function addRow(p) {
                const container = document.getElementById(`${p}-files-container`);
                const n = p === 'rest' ? state.restCounter++ : state.soapCounter++;
                const row = document.createElement('div');
                row.className = 'file-entry';
                row.innerHTML = `
                    <div class="entry-header">
                        <span class="entry-num">File #${n+1}</span>
                        <button type="button" class="btn-remove">Remove</button>
                    </div>
                    <div class="form-row">
                        <div class="input-group">
                            <label>Source File</label>
                            <input type="text" class="source-input" list="src-${p}" placeholder="e.g. students.xlsx" autocomplete="off">
                        </div>
                        <div class="input-group">
                            <label>Template File</label>
                            <input type="text" class="template-input" list="tpl-${p}" placeholder="e.g. TEMPSTU.JSON" autocomplete="off">
                        </div>
                    </div>`;
                row.querySelector('.btn-remove').addEventListener('click', () => row.remove());
                container.appendChild(row);
                updateDatalist(`src-${p}`, state.sourceFiles);
                updateDatalist(`tpl-${p}`, state.templateFiles);
            }

            function updateDatalist(id, files) {
                let dl = document.getElementById(id);
                if (dl) dl.remove();
                dl = document.createElement('datalist');
                dl.id = id;
                files.forEach(f => { const o = document.createElement('option'); o.value = f; dl.appendChild(o); });
                document.body.appendChild(dl);
            }

            async function submit(p) {
                if (state.processing) return;
                const container = document.getElementById(`${p}-files-container`);
                const msgBox    = document.getElementById(`${p}-msg`);
                const rows      = container.querySelectorAll('.file-entry');

                if (!rows.length) { showMsg(msgBox, '❌ Add at least one file.', 'error'); return; }

                const files = [];
                for (const row of rows) {
                    const src = row.querySelector('.source-input').value.trim();
                    const tpl = row.querySelector('.template-input').value.trim();
                    if (!src || !tpl) { showMsg(msgBox, '❌ Fill in all source and template fields.', 'error'); return; }
                    files.push({ source_file_name: src, template_name: tpl });
                }

                state.processing = true;

                for (let i = 0; i < files.length; i++) {
                    const f = files[i];
                    try {
                        showMsg(msgBox, `⏳ Processing file ${i+1}/${files.length}...\\n📁 ${f.source_file_name}\\n🔷 ${f.template_name}`, 'processing');

                        const tokRes  = await fetch('/api/create-token', { method: 'POST' });
                        const tokData = await tokRes.json();
                        if (!tokData.token_id) throw new Error(`Token error: ${JSON.stringify(tokData)}`);

                        const endpoint = p === 'rest' ? '/api/rest/extract' : '/api/soap/extract';
                        const extRes   = await fetch(endpoint, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ token_id: tokData.token_id, source_file_name: f.source_file_name, template_name: f.template_name })
                        });
                        const result = await extRes.json();

                        if (result.status !== 'success') throw new Error(result.error_message || 'Extraction failed');

                        const summary = `✅ File ${i+1}/${files.length} Done!

🔐 Token    : ${result.token_id}
📊 Sections : ${result.sections_count}
📦 Groups   : ${result.groups_count}
📁 Result   : ${result.result_file_name}
🔄 Renamed  : ${result.done_source_name}
📌 Protocol : ${result.protocol}`;

                        const isLast = i === files.length - 1;
                        showMsg(msgBox, summary + (isLast ? '' : '\\n\\n⏳ Next file...'), isLast ? 'success' : 'processing');
                        if (!isLast) await new Promise(r => setTimeout(r, 800));

                    } catch (err) {
                        showMsg(msgBox, `❌ File ${i+1}/${files.length} Failed\\n\\n${err.message}`, 'error');
                        break;
                    }
                }

                state.processing = false;
            }

            function showMsg(el, text, type) {
                el.className = `message-box message-${type}`;
                el.textContent = text;
                el.style.display = 'block';
                el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }

            document.readyState === 'loading'
                ? document.addEventListener('DOMContentLoaded', init)
                : init();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/api/list-sources")
async def list_sources():
    return JSONResponse(content=get_source_files())


@app.get("/api/list-templates")
async def list_templates():
    return JSONResponse(content=get_template_files())


@app.post("/api/create-token")
async def create_token():
    token_id = db_create_token()
    if token_id:
        return JSONResponse(content={"token_id": token_id})
    return JSONResponse(status_code=500, content={"error": "No pending records available. Add rows with status='pending' to token_details."})


@app.post("/api/rest/extract")
async def rest_extract(request: Request):
    try:
        req              = json.loads((await request.body()).decode("utf-8"))
        token_id         = req.get("token_id")
        source_file_name = req.get("source_file_name")
        template_name    = req.get("template_name")

        logger.info(f"REST │ token={token_id}  src={source_file_name}  tmpl={template_name}")

        missing = [k for k, v in {"token_id": token_id, "source_file_name": source_file_name, "template_name": template_name}.items() if not v]
        if missing:
            return JSONResponse(status_code=400, content={"status": "error", "error_message": f"Missing: {', '.join(missing)}"})

        result = run_extraction_pipeline(token_id, source_file_name, template_name, "REST")
        return JSONResponse(content={
            "status":           "success",
            "protocol":         "REST",
            "token_id":         result["token_id"],
            "result_file_name": result["result_file_name"],
            "done_source_name": result["done_source_name"],
            "sections_count":   result["sections_count"],
            "groups_count":     result["groups_count"],
            "data":             result["data"],
            "message":          f"Extraction complete. {result['sections_count']} section(s) processed.",
        })

    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={"status": "error", "error_message": f"Invalid JSON: {e}"})
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"status": "error", "error_message": str(e)})
    except Exception as e:
        logger.error(f"REST │ {e}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "error_message": str(e)})


@app.post("/api/soap/extract")
async def soap_extract(request: Request):
    import xml.etree.ElementTree as ET

    raw = await request.body()
    logger.info(f"SOAP │ bytes={len(raw)}")

    token_id = source_file_name = template_name = None

    try:
        data             = json.loads(raw.decode("utf-8"))
        token_id         = data.get("token_id")
        source_file_name = data.get("source_file_name")
        template_name    = data.get("template_name")
    except Exception:
        try:
            root = ET.fromstring(raw)
            body = next((c for c in root if "Body" in c.tag), None)
            if body is None:
                return JSONResponse(status_code=400, content={"error": "No soap:Body"})
            req_elem = next(iter(body), None)
            if req_elem is None:
                return JSONResponse(status_code=400, content={"error": "Empty SOAP Body"})

            def get_text(parent, tag):
                for child in parent:
                    local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if local.lower() == tag.lower():
                        return (child.text or "").strip()
                return ""

            token_id         = get_text(req_elem, "token_id")
            source_file_name = get_text(req_elem, "source_file_name")
            template_name    = get_text(req_elem, "template_name")
        except ET.ParseError as e:
            return JSONResponse(status_code=400, content={"error": f"Invalid request: {e}"})

    if not all([token_id, source_file_name, template_name]):
        return JSONResponse(status_code=400, content={"error": "Missing required fields"})

    logger.info(f"SOAP │ token={token_id}  src={source_file_name}  tmpl={template_name}")

    try:
        result = run_extraction_pipeline(token_id, source_file_name, template_name, "SOAP")
        return JSONResponse(content={
            "status":           "success",
            "protocol":         "SOAP",
            "token_id":         result["token_id"],
            "result_file_name": result["result_file_name"],
            "done_source_name": result["done_source_name"],
            "sections_count":   result["sections_count"],
            "groups_count":     result["groups_count"],
            "data":             result["data"],
            "message":          f"Extraction complete. {result['sections_count']} section(s) processed.",
        })
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"status": "error", "error_message": str(e)})
    except Exception as e:
        logger.error(f"SOAP │ {e}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "error_message": str(e)})


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("  Excel Extraction API Server")
    logger.info("  Web UI   → http://127.0.0.1:8001/")
    logger.info("  REST API → POST /api/rest/extract")
    logger.info("  SOAP API → POST /api/soap/extract")
    logger.info("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")