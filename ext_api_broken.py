"""
Unified API Server — REST + SOAP
Supports both REST (JSON) and SOAP (XML) protocols for Excel data extraction.
Maintains same folder structure, naming conventions, DB activity as ext.py extract().
Does NOT modify ext.py in any way.
"""

import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

import pymysql
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, HTMLResponse
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

def db_update_status(token_id: str, status: str, source: str = None, result: str = None):
    """
    Mirror of ext.update_status — keeps DB in sync regardless of protocol.
    Updates status, source_file_name (renamed to _done), result_file_name.
    """
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE token_details
                SET    status           = %s,
                       source_file_name = COALESCE(%s, source_file_name),
                       result_file_name = COALESCE(%s, result_file_name),
                       updated_at       = NOW()
                WHERE  token_id = %s
                """,
                (status, source, result, token_id),
            )
        conn.commit()
        logger.info(f"DB  │ token={token_id}  status={status}  source={source}  result={result}")
    except Exception as e:
        logger.error(f"DB Update Error for token {token_id}: {str(e)}")
    finally:
        try:
            conn.close()
        except:
            pass


def db_fetch_token(token_id: str):
    """
    Fetch token row — lets us derive source/template names from DB
    when the caller only supplies token_id.
    """
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM token_details WHERE token_id = %s",
                (token_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()

def db_create_token():
    """
    Create a new token and return the token_id.
    """
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO token_details (status, created_at, updated_at)
                VALUES ('pending', NOW(), NOW())
                """
            )
            conn.commit()
            last_id = cur.lastrowid
            token_id = f"token_{last_id}"
            logger.info(f"Token created: {token_id}")
            return token_id
    except Exception as e:
        logger.error(f"Error creating token: {str(e)}")
        return None
    finally:
        try:
            conn.close()
        except:
            pass

def get_source_files():
    """
    List all files in the SOURCE directory.
    """
    if SOURCE_DIR.exists():
        return sorted([f.name for f in SOURCE_DIR.iterdir() if f.is_file()])
    return []

def get_template_files():
    """
    List all files in the TEMPLATE directory.
    """
    if TEMPLATE_DIR.exists():
        return sorted([f.name for f in TEMPLATE_DIR.iterdir() if f.is_file()])
    return []

def run_extraction_pipeline(token_id: str, source_file_name: str, template_name: str, protocol: str = "REST"):
    """
    Full extraction pipeline — identical discipline to ext.extract():
      1. Mark DB → 'processing'
      2. Validate files
      3. to_grid  →  apply_template
      4. Write result JSON  →  RESULT_DIR/<stem>_result.json
      5. Rename source      →  SOURCE_DIR/<stem>_done<ext>
      6. Mark DB → 'done'  with new filenames

    Returns a result dict on success, raises on failure (caller handles DB 'failed').
    """
    try:
        logger.info(f"[{protocol}] Pipeline START  token={token_id}")

        # ── 1. Mark processing ────────────────────────────────────────────────────
        db_update_status(token_id, "processing")

        # ── 2. Validate source file ───────────────────────────────────────────────
        src = get_source_file(source_file_name)
        if not src:
            raise FileNotFoundError(f"Source file '{source_file_name}' not found in SOURCE directory")

        # ── 3. Validate template file ─────────────────────────────────────────────
        tmpl = get_template_file(template_name)
        if not tmpl:
            raise FileNotFoundError(f"Template file '{template_name}' not found in TEMPLATE directory")

        logger.info(f"[{protocol}] Files OK  src={src.name}  tmpl={tmpl.name}")

        # ── 4. Load grid + template, run extraction ───────────────────────────────
        grid          = to_grid(src)
        template_json = json.load(open(tmpl, encoding="utf-8"))
        extraction    = apply_template(template_json, grid)

        # ── 5. Write result JSON ──────────────────────────────────────────────────
        RESULT_DIR.mkdir(exist_ok=True)
        result_name = f"{src.stem}_result.json"
        result_path = RESULT_DIR / result_name
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(extraction, f, indent=2)
        logger.info(f"[{protocol}] Result written → {result_path}")

        # ── 6. Rename source file (_done convention) ──────────────────────────────
        done_name = f"{src.stem}_done{src.suffix}"
        src.rename(SOURCE_DIR / done_name)
        logger.info(f"[{protocol}] Source renamed: {src.name} → {done_name}")

        # ── 7. Mark DB done ───────────────────────────────────────────────────────
        db_update_status(token_id, "done", done_name, result_name)

        logger.info(f"[{protocol}] Pipeline DONE  token={token_id}")

        return {
            "token_id":         token_id,
            "result_file_name": result_name,
            "done_source_name": done_name,
            "data":             extraction,
            "sections_count":   len(extraction.get("sections", {})),
            "groups_count":     len(extraction.get("groups",   {})),
        }
    except Exception as e:
        logger.error(f"[{protocol}] Pipeline ERROR: {str(e)}")
        traceback.print_exc()
        db_update_status(token_id, "failed")
        raise

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    """
    Serve the HTML UI for file extraction with Swagger-like design.
    """
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
            .endpoint-item { padding: 8px 12px; margin-bottom: 4px; border-radius: 4px; cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 8px; transition: all 0.2s; }
            .endpoint-item:hover { background: #f0f0f0; }
            .endpoint-item.active { background: #e8f5e9; color: #2e7d32; font-weight: 600; }
            .method-badge { font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; color: white; }
            .method-post { background: #4caf50; }
            .method-get { background: #2196f3; }
            .content-area { max-width: 900px; }
            .section { background: #fff; border: 1px solid #e0e0e0; border-radius: 4px; margin-bottom: 20px; padding: 24px; }
            .section h2 { font-size: 28px; margin-bottom: 8px; font-weight: 500; }
            .section .desc { font-size: 14px; color: #666; margin-bottom: 20px; }
            .api-operation { background: #fff; border: 1px solid #e0e0e0; border-radius: 4px; margin-bottom: 20px; overflow: hidden; }
            .api-op-header { background: #f5f5f5; padding: 16px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; gap: 12px; }
            .api-op-header .method { font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 4px; color: white; }
            .api-op-header .path { font-family: monospace; font-size: 13px; color: #333; font-weight: 500; flex: 1; }
            .api-op-body { padding: 20px; }
            .form-section { margin-bottom: 20px; }
            .form-section label { display: block; font-size: 13px; font-weight: 600; color: #222; margin-bottom: 12px; }
            .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px; }
            .form-row.full { grid-template-columns: 1fr; }
            .input-group { display: flex; flex-direction: column; }
            .input-group label { font-size: 12px; font-weight: 500; color: #666; margin-bottom: 6px; }
            .input-group input { padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; font-family: 'Roboto Mono', monospace; }
            .input-group input:focus { outline: none; border-color: #2196f3; box-shadow: 0 0 0 2px rgba(33, 150, 243, 0.1); }
            .file-entry { background: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 4px; padding: 15px; margin-bottom: 12px; }
            .file-entry .entry-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
            .file-entry .entry-num { font-size: 12px; font-weight: 600; color: #999; background: #f0f0f0; padding: 2px 8px; border-radius: 3px; }
            .file-entry .btn-remove { background: #fff; border: 1px solid #f44336; color: #f44336; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: 600; }
            .file-entry .btn-remove:hover { background: #ffebee; }
            .buttons { display: flex; gap: 12px; margin-top: 24px; }
            .btn { padding: 10px 16px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.2s; }
            .btn-add { background: #e8f5e9; border-color: #4caf50; color: #2e7d32; }
            .btn-add:hover { background: #c8e6c9; }
            .btn-submit { background: #2196f3; border-color: #2196f3; color: white; }
            .btn-submit:hover { background: #1976d2; }
            .message-box { padding: 12px 16px; border-radius: 4px; margin-top: 12px; font-size: 13px; display: none; font-family: 'Roboto Mono', monospace; white-space: pre-wrap; word-break: break-word; }
            .message-success { background: #e8f5e9; border: 1px solid #4caf50; color: #2e7d32; }
            .message-error { background: #ffebee; border: 1px solid #f44336; color: #c62828; }
            .message-processing { background: #e3f2fd; border: 1px solid #2196f3; color: #1565c0; }
        </style>
    </head>
    <body>
        <div class="topbar">
            <h1>FastAPI</h1>
            <div class="info">
                <div>0.1.0</div>
                <div>OAS 3.1</div>
            </div>
        </div>

        <div class="layout">
            <!-- SIDEBAR -->
            <div class="sidebar">
                <div class="endpoint-group">
                    <h3>Extraction APIs</h3>
                    <div class="endpoint-item" onclick="showSection('rest')">
                        <span class="method-badge method-post">POST</span>
                        <span>REST Extract</span>
                    </div>
                    <div class="endpoint-item" onclick="showSection('soap')">
                        <span class="method-badge method-post">POST</span>
                        <span>SOAP Extract</span>
                    </div>
                </div>
            </div>

            <!-- MAIN CONTENT -->
            <div class="main">
                <div class="content-area">
                    <!-- REST API Section -->
                    <div id="rest-section" style="display: none;">
                        <div class="api-operation">
                            <div class="api-op-header">
                                <span class="method" style="background: #4caf50;">POST</span>
                                <span class="path">/api/rest/extract</span>
                            </div>
                            <div class="api-op-body">
                                <h3 style="margin-bottom: 8px; font-size: 18px;">REST API Extraction</h3>
                                <p style="font-size: 13px; color: #666; margin-bottom: 20px;">Extract data from Excel files using REST API with source and template files</p>
                                
                                <div class="form-section">
                                    <label>Add Files to Process</label>
                                    <div id="rest-files-container"></div>
                                    <div class="buttons">
                                        <button class="btn btn-add" onclick="addRestRow()">+ Add File</button>
                                    </div>
                                </div>

                                <button class="btn btn-submit" onclick="submitRest()" style="width: 100%; margin-top: 20px;">Execute</button>
                                <div id="rest-msg" class="message-box"></div>
                            </div>
                        </div>
                    </div>

                    <!-- SOAP API Section -->
                    <div id="soap-section" style="display: none;">
                        <div class="api-operation">
                            <div class="api-op-header">
                                <span class="method" style="background: #4caf50;">POST</span>
                                <span class="path">/api/soap/extract</span>
                            </div>
                            <div class="api-op-body">
                                <h3 style="margin-bottom: 8px; font-size: 18px;">SOAP API Extraction</h3>
                                <p style="font-size: 13px; color: #666; margin-bottom: 20px;">Extract data from Excel files using SOAP API with source and template files</p>
                                
                                <div class="form-section">
                                    <label>Add Files to Process</label>
                                    <div id="soap-files-container"></div>
                                    <div class="buttons">
                                        <button class="btn btn-add" onclick="addSoapRow()">+ Add File</button>
                                    </div>
                                </div>

                                <button class="btn btn-submit" onclick="submitSoap()" style="width: 100%; margin-top: 20px;">Execute</button>
                                <div id="soap-msg" class="message-box"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let restCounter = 0, soapCounter = 0;
            let sourceFiles = [], templateFiles = [];

            async function loadFiles() {
                try {
                    const [src, tpl] = await Promise.all([
                        fetch('/api/list-sources').then(r => r.json()),
                        fetch('/api/list-templates').then(r => r.json())
                    ]);
                    sourceFiles = src || [];
                    templateFiles = tpl || [];
                } catch(e) { console.error(e); }
            }

            function showSection(section) {
                console.log('showSection called with:', section);
                
                // Hide both sections first
                const restSection = document.getElementById('rest-section');
                const soapSection = document.getElementById('soap-section');
                
                if(section === 'rest') {
                    if(restSection) restSection.style.display = 'block';
                    if(soapSection) soapSection.style.display = 'none';
                } else if(section === 'soap') {
                    if(restSection) restSection.style.display = 'none';
                    if(soapSection) soapSection.style.display = 'block';
                }
                
                // Update active state in sidebar
                document.querySelectorAll('.endpoint-item').forEach(el => {
                    el.classList.remove('active');
                });
                
                // Find and mark the clicked item as active
                document.querySelectorAll('.endpoint-item').forEach(el => {
                    if(section === 'rest' && el.textContent.includes('REST')) {
                        el.classList.add('active');
                    } else if(section === 'soap' && el.textContent.includes('SOAP')) {
                        el.classList.add('active');
                    }
                });
                
                console.log('Section switched to:', section);
            }

            function makeDatalist(id, files) {
                let dl = document.getElementById(id);
                if(dl) dl.remove();
                dl = document.createElement('datalist');
                dl.id = id;
                files.forEach(f => {
                    const opt = document.createElement('option');
                    opt.value = f;
                    dl.appendChild(opt);
                });
                document.body.appendChild(dl);
            }

            function addRow(prefix) {
                const isRest = prefix === 'rest';
                const container = document.getElementById(isRest ? 'rest-files-container' : 'soap-files-container');
                const counter = isRest ? restCounter++ : soapCounter++;
                const rowId = `${prefix}-${counter}`;
                
                const row = document.createElement('div');
                row.id = rowId;
                row.className = 'file-entry';
                row.innerHTML = `
                    <div class="entry-header">
                        <span class="entry-num">File ${counter + 1}</span>
                        <button class="btn-remove" onclick="document.getElementById('${rowId}').remove()">Remove</button>
                    </div>
                    <div class="form-row">
                        <div class="input-group">
                            <label>Source File</label>
                            <input type="text" class="source-input" list="src-${prefix}" placeholder="Enter source file name...">
                        </div>
                        <div class="input-group">
                            <label>Template File</label>
                            <input type="text" class="template-input" list="tpl-${prefix}" placeholder="Enter template file name...">
                        </div>
                    </div>
                `;
                container.appendChild(row);
                makeDatalist(`src-${prefix}`, sourceFiles);
                makeDatalist(`tpl-${prefix}`, templateFiles);
            }

            function addRestRow() { addRow('rest'); }
            function addSoapRow() { addRow('soap'); }

            async function submit(prefix) {
                const isRest = prefix === 'rest';
                const container = document.getElementById(isRest ? 'rest-files-container' : 'soap-files-container');
                const msgBox = document.getElementById(isRest ? 'rest-msg' : 'soap-msg');
                
                const rows = container.querySelectorAll('.file-entry');
                if(!rows.length) {
                    showMsg(msgBox, 'Please add at least one file', 'error');
                    return;
                }

                const files = [];
                let valid = true;
                rows.forEach(row => {
                    const src = row.querySelector('.source-input').value.trim();
                    const tpl = row.querySelector('.template-input').value.trim();
                    if(!src || !tpl) { valid = false; return; }
                    files.push({source_file_name: src, template_name: tpl});
                });

                if(!valid) {
                    showMsg(msgBox, 'All fields are required', 'error');
                    return;
                }

                for(const file of files) {
                    try {
                        console.log('Creating token...');
                        const tokenResp = await fetch('/api/create-token', {method: 'POST'});
                        const tokenData = await tokenResp.json();
                        const token_id = tokenData.token_id;
                        
                        console.log('Token created:', token_id);
                        
                        if(!token_id) {
                            showMsg(msgBox, `✗ Error: Failed to create token. Response: ${JSON.stringify(tokenData)}`, 'error');
                            return;
                        }
                        
                        showMsg(msgBox, `Processing: ${file.source_file_name}\nToken ID: ${token_id}`, 'processing');

                        const payload = {
                            token_id: token_id,
                            source_file_name: file.source_file_name,
                            template_name: file.template_name
                        };
                        
                        console.log('Sending payload:', payload);
                        
                        const endpoint = isRest ? '/api/rest/extract' : '/api/soap/extract';
                        const response = await fetch(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });

                        console.log('Response status:', response.status);
                        const result = await response.json();
                        console.log('Response data:', result);
                        
                        if(result.status === 'success') {
                            showMsg(msgBox, `✓ Processing Complete\nToken ID: ${token_id}\n\nResult saved in:\nD:\\Users\\Anurag\\Applied_Cognition_Systems\\2026\\Excel_reader\\RESULT\n\nFile: ${result.result_file_name}`, 'success');
                        } else {
                            showMsg(msgBox, `✗ Error: ${result.error_message}`, 'error');
                        }
                    } catch(err) {
                        showMsg(msgBox, `✗ Error: ${err.message}`, 'error');
                    }
                }
            }

            function submitRest() { submit('rest'); }
            function submitSoap() { submit('soap'); }

            function showMsg(box, text, type) {
                box.className = `message-box message-${type}`;
                box.textContent = text;
                box.style.display = 'block';
            }

            // Initial setup
            async function initializeUI() {
                try {
                    console.log('Initializing UI...');
                    
                    // Load files first
                    console.log('Loading source and template files...');
                    await loadFiles();
                    console.log('Files loaded. Source:', sourceFiles.length, 'Template:', templateFiles.length);
                    
                    console.log('Setting up REST section...');
                    showSection('rest');
                    
                    console.log('Adding REST file row...');
                    addRestRow();
                    
                    console.log('Adding SOAP file row...');
                    addSoapRow();
                    
                    console.log('UI initialization complete');
                } catch(e) {
                    console.error('Initialization error:', e);
                }
            }
            
            // Run on page load - trigger initialization
            if(document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', initializeUI);
            } else {
                // Document is already loaded (interactive or complete)
                setTimeout(initializeUI, 100);
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/list-sources")
async def list_sources():
    """
    List all files in the SOURCE directory.
    """
    return JSONResponse(content=get_source_files())

@app.get("/api/list-templates")
async def list_templates():
    """
    List all files in the TEMPLATE directory.
    """
    return JSONResponse(content=get_template_files())

@app.post("/api/create-token")
async def create_token():
    """
    Create a new token and return the token_id.
    """
    token_id = db_create_token()
    if token_id:
        return JSONResponse(content={"token_id": token_id})
    return JSONResponse(status_code=500, content={"error": "Failed to create token"})

@app.post("/api/rest/extract")
async def rest_extract(request: Request):
    """
    REST endpoint — JSON in, JSON out.
    Runs the full extraction pipeline with DB updates and file renaming.
    """
    try:
        body = await request.body()
        logger.info(f"REST │ Received raw body: {body}")
        
        req = json.loads(body.decode('utf-8'))
        logger.info(f"REST │ Parsed JSON: {req}")
        
        token_id = req.get('token_id')
        source_file_name = req.get('source_file_name')
        template_name = req.get('template_name')
        
        logger.info(f"REST │ Extracted fields - token_id={token_id}, source={source_file_name}, template={template_name}")
        
        if not all([token_id, source_file_name, template_name]):
            missing = []
            if not token_id: missing.append('token_id')
            if not source_file_name: missing.append('source_file_name')
            if not template_name: missing.append('template_name')
            return JSONResponse(status_code=400, content={
                "status": "error",
                "error_message": f"Missing required fields: {', '.join(missing)}"
            })
        
        logger.info(f"REST │ All fields present, starting extraction. token={token_id}")
        
        result = run_extraction_pipeline(
            token_id         = token_id,
            source_file_name = source_file_name,
            template_name    = template_name,
            protocol         = "REST",
        )
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
        logger.error(f"REST │ JSON decode error: {str(e)}")
        return JSONResponse(status_code=400, content={
            "status":        "error",
            "error_message": f"Invalid JSON: {str(e)}",
        })

    except FileNotFoundError as e:
        logger.error(f"REST │ File not found: {str(e)}")
        return JSONResponse(status_code=404, content={
            "status":        "error",
            "error_message": str(e),
        })

    except Exception as e:
        logger.error(f"REST │ Error: {str(e)}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "status":        "error",
            "error_message": str(e),
            "message":       "Extraction failed",
        })


@app.post("/api/soap/extract")
async def soap_extract(request: Request):
    """
    SOAP endpoint — XML envelope or JSON in, JSON out.
    Runs the full extraction pipeline with DB updates and file renaming.
    """
    import xml.etree.ElementTree as ET
    
    raw = await request.body()
    logger.info(f"SOAP │ Received request  bytes={len(raw)}")

    token_id = None
    source_file_name = None
    template_name = None

    # Try parsing as JSON first (from frontend)
    try:
        json_data = json.loads(raw.decode('utf-8'))
        token_id = json_data.get('token_id')
        source_file_name = json_data.get('source_file_name')
        template_name = json_data.get('template_name')
    except:
        # Fall back to SOAP XML parsing
        try:
            root = ET.fromstring(raw)
            soap_body = None
            for child in root:
                if "Body" in child.tag:
                    soap_body = child
                    break
            
            if soap_body is None:
                return JSONResponse(status_code=400, content={"error": "No soap:Body found"})
            
            request_elem = None
            for child in soap_body:
                request_elem = child
                break
            
            if request_elem is None:
                return JSONResponse(status_code=400, content={"error": "Empty SOAP Body"})
            
            def get_text(parent, tag: str) -> str:
                for child in parent:
                    local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if local.lower() == tag.lower():
                        return (child.text or "").strip()
                return ""
            
            token_id = get_text(request_elem, "token_id")
            source_file_name = get_text(request_elem, "source_file_name")
            template_name = get_text(request_elem, "template_name")
        
        except ET.ParseError as e:
            return JSONResponse(status_code=400, content={"error": f"Invalid request format: {str(e)}"})

    if not all([token_id, source_file_name, template_name]):
        return JSONResponse(status_code=400, content={"error": "Missing required fields"})

    logger.info(f"SOAP │ token={token_id}  src={source_file_name}  tmpl={template_name}")

    # ── Run pipeline ──────────────────────────────────────────────────────────
    try:
        result = run_extraction_pipeline(
            token_id         = token_id,
            source_file_name = source_file_name,
            template_name    = template_name,
            protocol         = "SOAP",
        )
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
        db_update_status(token_id, "failed")
        return JSONResponse(status_code=404, content={
            "status":        "error",
            "error_message": str(e),
        })

    except Exception as e:
        logger.error(f"SOAP │ Error  token={token_id}  err={e}")
        traceback.print_exc()
        db_update_status(token_id, "failed")
        return JSONResponse(status_code=500, content={
            "status":        "error",
            "error_message": str(e),
        })


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("  Excel Extraction API Server")
    logger.info("=" * 70)
    logger.info("  🌐 Web UI       → http://localhost:8001/")
    logger.info("  📡 REST API     → POST /api/rest/extract")
    logger.info("  📡 SOAP API     → POST /api/soap/extract")
    logger.info("  📋 List Sources → GET /api/list-sources")
    logger.info("  📋 List Templates → GET /api/list-templates")
    logger.info("  🔐 Create Token → POST /api/create-token")
    logger.info("=" * 70)
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")