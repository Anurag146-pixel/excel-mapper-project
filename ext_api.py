"""
Unified API Server — REST + SOAP
Supports both REST (JSON) and SOAP (XML) protocols for Excel data extraction.
Maintains same folder structure, naming conventions, DB activity as ext.py extract().
Does NOT modify ext.py in any way.
"""

import json
import logging
import traceback
import uuid
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

def db_update_status(token_id: str, status: str, source: str = None, result: str = None, extraction_id: str = None):
    """
    PURE UPDATE ONLY — NEVER inserts new records.
    CRITICAL: Must verify record exists AND use correct column types.
    Uses parameterized queries to ensure WHERE clause matches correctly.
    """
    rows_affected = 0
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            # Extract numeric ID from formatted token_id (e.g., "token_123" → 123)
            numeric_id = token_id
            if isinstance(token_id, str) and token_id.startswith("token_"):
                try:
                    numeric_id = int(token_id.split("_")[1])
                except Exception as parse_err:
                    logger.error(f"❌ CANNOT PARSE token_id '{token_id}': {parse_err}")
                    return
            
            logger.info(f"  🔍 Looking for record: token_id={numeric_id}")
            
            # STEP 1: Verify record EXISTS (using parameterized query)
            verify_query = "SELECT token_id, status, source_file_name FROM token_details WHERE token_id = %s LIMIT 1"
            cur.execute(verify_query, (numeric_id,))
            existing_record = cur.fetchone()
            
            if not existing_record:
                logger.error(f"❌ RECORD NOT FOUND: token_id={numeric_id}")
                logger.error(f"   Will NOT create new record - UPDATE ONLY policy enforced")
                logger.error(f"   Existing records: Check database for token_id values")
                return
            
            # Handle both tuple and dict return formats
            if isinstance(existing_record, dict):
                rec_token_id = existing_record.get('token_id')
                rec_status = existing_record.get('status')
                rec_source = existing_record.get('source_file_name')
            else:
                rec_token_id = existing_record[0]
                rec_status = existing_record[1]
                rec_source = existing_record[2]
            
            logger.info(f"  ✓ Found record: token_id={rec_token_id}, current_status={rec_status}, source={rec_source}")
            
            # STEP 2: Build UPDATE query with ALL fields
            update_parts = ["status = %s"]
            params = [status]
            
            if source is not None:
                update_parts.append("source_file_name = %s")
                params.append(source)
                logger.info(f"    → Will set source_file_name = {source}")
            
            if result is not None:
                update_parts.append("result_file_name = %s")
                params.append(result)
                logger.info(f"    → Will set result_file_name = {result}")
            
            if extraction_id is not None:
                update_parts.append("extraction_id = %s")
                params.append(extraction_id)
                logger.info(f"    → Will set extraction_id = {extraction_id}")
            
            # STEP 3: Build WHERE clause - token_id as NUMBER for exact match
            # DO NOT add updated_at (column may not exist in database)
            params.append(numeric_id)  # Add numeric_id for WHERE clause
            
            query = f"UPDATE token_details SET {', '.join(update_parts)} WHERE token_id = %s"
            
            logger.info(f"  📝 Executing UPDATE query...")
            logger.info(f"     Query: UPDATE token_details SET ... WHERE token_id = {numeric_id}")
            
            # STEP 4: Execute UPDATE (parameterized)
            cur.execute(query, params)
            rows_affected = cur.rowcount
            
        conn.commit()
        
        if rows_affected == 1:
            logger.info(f"✅ DB UPDATE SUCCESS: token_id={numeric_id}, status={status}, rows_affected=1")
        elif rows_affected == 0:
            logger.error(f"❌ UPDATE FAILED: token_id={numeric_id}, rows_affected=0 (WHERE clause did not match)")
        else:
            logger.warning(f"⚠️  UNEXPECTED rows_affected={rows_affected} for token_id={numeric_id} (expected 1)")
            
    except Exception as e:
        logger.error(f"❌ DB Update Error: {str(e)}")
        traceback.print_exc()
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
    GET EXISTING PENDING RECORD — NO INSERT.
    Returns token_id of first available pending record in database.
    This ensures we reuse existing records instead of creating new ones.
    Works with both REST and SOAP APIs uniformly.
    """
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            # FIND first pending record (don't create new one)
            logger.info(f"  🔍 Looking for existing pending record...")
            query = "SELECT token_id FROM token_details WHERE status = 'pending' LIMIT 1"
            cur.execute(query)
            result = cur.fetchone()
            
            if result:
                # Handle both tuple and dict return formats
                if isinstance(result, dict):
                    token_id = result.get('token_id')
                else:
                    token_id = result[0]
                
                if token_id:
                    token_id_formatted = f"token_{token_id}"
                    logger.info(f"  ✅ Found existing pending record: {token_id_formatted}")
                    return token_id_formatted
                else:
                    logger.error(f"  ❌ Token ID is None in result: {result}")
                    return None
            else:
                logger.error(f"  ❌ NO pending records available in database")
                logger.error(f"     Please add pending records to token_details table first")
                return None
                
    except Exception as e:
        logger.error(f"❌ Error finding token: {str(e)}")
        traceback.print_exc()
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

def rows_to_columns(records):
    """
    ═══════════════════════════════════════════════════════════════════════════
    ROWS TO COLUMNS TRANSFORMATION
    ═══════════════════════════════════════════════════════════════════════════
    Converts row-based records into column-based format.
    Eliminates header repetition by organizing data as columns.
    
    Input:  [{"col1": "val1", "col2": "val2"}, {"col1": "val3", "col2": "val4"}]
    Output: {"col1": ["val1", "val3"], "col2": ["val2", "val4"]}
    """
    columns = {}
    
    for row in records:
        for key, value in row.items():
            columns.setdefault(key, []).append(value)
    
    return columns

def transform_extraction_to_columns(extraction_data):
    """
    ═══════════════════════════════════════════════════════════════════════════
    TRANSFORM EXTRACTION DATA TO COLUMNAR FORMAT
    ═══════════════════════════════════════════════════════════════════════════
    Creates a transformed version of extraction data where each section's
    records are converted to columns format without headers repetition.
    
    Returns new dict with structure:
    {
        "sections": {
            "SEC1": {
                "columns": {...},
                "count": N,
                "original_records_count": N
            }
        },
        "groups": {...}
    }
    """
    try:
        transformed = {
            "sections": {},
            "groups": extraction_data.get("groups", {})
        }
        
        for section_id, section_data in extraction_data.get("sections", {}).items():
            records = section_data.get("records", [])
            
            if records:
                # Convert rows to columns
                columns = rows_to_columns(records)
                
                # Create transformed section
                transformed["sections"][section_id] = {
                    "columns": columns,
                    "count": len(records),
                    "original_records_count": len(records)
                }
                
                logger.info(f"  ✓ Transformed {section_id}: {len(records)} records → columnar format")
            else:
                logger.warning(f"  ⚠️  Section {section_id} has no records to transform")
        
        return transformed
    except Exception as e:
        logger.error(f"  ❌ Error transforming to columns: {str(e)}")
        traceback.print_exc()
        return None

def run_extraction_pipeline(token_id: str, source_file_name: str, template_name: str, protocol: str = "REST"):
    """
    Full extraction pipeline — identical discipline to ext.extract():
      1. Mark DB → 'processing'
      2. Validate files
      3. to_grid  →  apply_template
      4. Write result JSON  →  RESULT_DIR/<stem>_result_<unique_id>.json
      5. Rename source      →  SOURCE_DIR/<stem>_done_<unique_id><ext>
      6. Mark DB → 'done'  with new filenames

    Returns a result dict on success, raises on failure (caller handles DB 'failed').
    Unique ID is an 8-character hex string (UUID4 truncated).
    """
    try:
        logger.info(f"[{protocol}] ═══════════════════════════════════════════════════════")
        logger.info(f"[{protocol}] Pipeline START  token={token_id}")
        logger.info(f"[{protocol}] ═══════════════════════════════════════════════════════")

        # ── 1. Mark processing ────────────────────────────────────────────────────
        logger.info(f"[{protocol}] Step 1: Marking token as 'processing'...")
        # Create/Update DB record with processing status (first touch to DB)
        db_update_status(token_id, "processing")

        # ── 2. Validate source file ───────────────────────────────────────────────
        logger.info(f"[{protocol}] Step 2: Validating source file: {source_file_name}")
        src = get_source_file(source_file_name)
        if not src:
            raise FileNotFoundError(f"Source file '{source_file_name}' not found in SOURCE directory")
        logger.info(f"[{protocol}]   ✓ Source file found at: {src}")

        # ── 3. Validate template file ─────────────────────────────────────────────
        logger.info(f"[{protocol}] Step 3: Validating template file: {template_name}")
        tmpl = get_template_file(template_name)
        if not tmpl:
            raise FileNotFoundError(f"Template file '{template_name}' not found in TEMPLATE directory")
        logger.info(f"[{protocol}]   ✓ Template file found at: {tmpl}")

        # ── 4. Load grid + template, run extraction ───────────────────────────────
        logger.info(f"[{protocol}] Step 4: Loading grid and template...")
        grid          = to_grid(src)
        template_json = json.load(open(tmpl, encoding="utf-8"))
        logger.info(f"[{protocol}]   ✓ Grid loaded: {len(grid)} rows")
        logger.info(f"[{protocol}] Step 5: Running extraction via template...")
        extraction    = apply_template(template_json, grid)
        logger.info(f"[{protocol}]   ✓ Extraction complete")

        # ── 6. Write result JSON ──────────────────────────────────────────────────
        logger.info(f"[{protocol}] Step 6: Writing result JSON file...")
        RESULT_DIR.mkdir(exist_ok=True)
        # Generate unique ID (8-character hex string)
        unique_id = uuid.uuid4().hex[:8]
        result_name = f"{src.stem}_result_{unique_id}.json"
        result_path = RESULT_DIR / result_name
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(extraction, f, indent=2)
        logger.info(f"[{protocol}]   ✓ Result file written: {result_path.name}")
        logger.info(f"[{protocol}]   ✓ Full path: {result_path}")
        logger.info(f"[{protocol}]   ✓ Unique ID: {unique_id}")

        # ── 7. Rename source file (_done convention with unique ID) ──────────────────────────────
        logger.info(f"[{protocol}] Step 7: Renaming source file with '_done_{unique_id}' suffix...")
        done_name = f"{src.stem}_done_{unique_id}{src.suffix}"
        new_src_path = SOURCE_DIR / done_name
        
        # Explicitly rename using shutil to ensure operation completes
        import shutil
        shutil.move(str(src), str(new_src_path))
        logger.info(f"[{protocol}]   ✓ Source file renamed: {src.name} → {done_name}")
        logger.info(f"[{protocol}]   ✓ New path: {new_src_path}")

        # ── 8. Mark DB done with extraction_id ───────────────────────────────────────────────────────
        logger.info(f"[{protocol}] Step 8: Updating database with 'done' status and extraction_id...")
        logger.info(f"[{protocol}]   - Updating token: {token_id}")
        logger.info(f"[{protocol}]   - Status: done")
        logger.info(f"[{protocol}]   - Extraction ID: {unique_id}")
        logger.info(f"[{protocol}]   - Source file name: {done_name}")
        logger.info(f"[{protocol}]   - Result file name: {result_name}")
        db_update_status(token_id, "done", done_name, result_name, unique_id)
        logger.info(f"[{protocol}]   ✓ Database update complete")

        logger.info(f"[{protocol}] ═══════════════════════════════════════════════════════")
        logger.info(f"[{protocol}] Pipeline DONE ✅  token={token_id}")
        logger.info(f"[{protocol}] ═══════════════════════════════════════════════════════")

        # ── BONUS: Transform to columnar format (rows → columns) ──────────────────
        logger.info(f"[{protocol}] Step 9: Generating columnar format (Rows to JSON)...")
        columns_format = transform_extraction_to_columns(extraction)
        if columns_format:
            logger.info(f"[{protocol}]   ✓ Columnar transformation complete")
        else:
            logger.warning(f"[{protocol}]   ⚠️  Columnar transformation skipped")
            columns_format = {}

        return {
            "token_id":         token_id,
            "result_file_name": result_name,
            "done_source_name": done_name,
            "data":             extraction,
            "columns_format":   columns_format,
            "sections_count":   len(extraction.get("sections", {})),
            "groups_count":     len(extraction.get("groups",   {})),
        }
    except Exception as e:
        logger.error(f"[{protocol}] ❌ Pipeline ERROR: {str(e)}")
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
            .endpoint-item { padding: 8px 12px; margin-bottom: 4px; border-radius: 4px; cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 8px; transition: all 0.2s; user-select: none; }
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
            .btn-add:disabled { opacity: 0.6; cursor: not-allowed; background: #ccc; border-color: #999; }
            .btn-submit { background: #2196f3; border-color: #2196f3; color: white; }
            .btn-submit:hover:not(:disabled) { background: #1976d2; }
            .btn-submit:disabled { opacity: 0.6; cursor: not-allowed; }
            .message-box { padding: 12px 16px; border-radius: 4px; margin-top: 12px; font-size: 13px; display: none; font-family: 'Roboto Mono', monospace; white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow-y: auto; }
            .message-success { background: #e8f5e9; border: 1px solid #4caf50; color: #2e7d32; }
            .message-error { background: #ffebee; border: 1px solid #f44336; color: #c62828; }
            .message-processing { background: #e3f2fd; border: 1px solid #2196f3; color: #1565c0; }
            .loader { display: inline-block; width: 14px; height: 14px; border: 2px solid #ccc; border-top-color: #2196f3; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; }
            @keyframes spin { to { transform: rotate(360deg); } }
            .status-badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; margin-left: 8px; }
            .badge-pending { background: #fff3cd; color: #856404; }
            .badge-processing { background: #d1ecf1; color: #0c5460; }
            .badge-done { background: #d4edda; color: #155724; }
            .badge-failed { background: #f8d7da; color: #721c24; }
        </style>
    </head>
    <body>
        <div class="topbar">
            <h1>FastAPI - Excel Extraction Engine</h1>
            <div class="info">
                <div>v0.1.0 | Complete Extraction Cycle</div>
            </div>
        </div>

        <div class="layout">
            <!-- SIDEBAR -->
            <div class="sidebar">
                <div class="endpoint-group">
                    <h3>Extraction APIs</h3>
                    <div class="endpoint-item" data-section="rest">
                        <span class="method-badge method-post">POST</span>
                        <span>REST Extract</span>
                    </div>
                    <div class="endpoint-item" data-section="soap">
                        <span class="method-badge method-post">POST</span>
                        <span>SOAP Extract</span>
                    </div>
                </div>
            </div>

            <!-- MAIN CONTENT -->
            <div class="main">
                <div class="content-area">
                    <!-- REST API Section -->
                    <div id="rest-section" class="api-section" style="display: none;">
                        <div class="api-operation">
                            <div class="api-op-header">
                                <span class="method" style="background: #4caf50;">POST</span>
                                <span class="path">/api/rest/extract</span>
                            </div>
                            <div class="api-op-body">
                                <h3 style="margin-bottom: 8px; font-size: 18px;">REST API Extraction</h3>
                                <p style="font-size: 13px; color: #666; margin-bottom: 20px;">Extract data from Excel files using REST API with JSON payloads</p>
                                
                                <div class="form-section">
                                    <label>Available Files <span id="rest-file-count" style="font-size: 11px; color: #999;">(loading...)</span></label>
                                    <div id="rest-files-container"></div>
                                    <div class="buttons">
                                        <button class="btn btn-add" id="rest-add-btn" disabled>+ Add File</button>
                                    </div>
                                </div>

                                <button class="btn btn-submit" id="rest-submit-btn" disabled style="width: 100%; margin-top: 20px;">Execute Extraction</button>
                                <div id="rest-msg" class="message-box"></div>
                            </div>
                        </div>
                    </div>

                    <!-- SOAP API Section -->
                    <div id="soap-section" class="api-section" style="display: none;">
                        <div class="api-operation">
                            <div class="api-op-header">
                                <span class="method" style="background: #4caf50;">POST</span>
                                <span class="path">/api/soap/extract</span>
                            </div>
                            <div class="api-op-body">
                                <h3 style="margin-bottom: 8px; font-size: 18px;">SOAP API Extraction</h3>
                                <p style="font-size: 13px; color: #666; margin-bottom: 20px;">Extract data from Excel files using SOAP protocol with XML envelopes</p>
                                
                                <div class="form-section">
                                    <label>Available Files <span id="soap-file-count" style="font-size: 11px; color: #999;">(loading...)</span></label>
                                    <div id="soap-files-container"></div>
                                    <div class="buttons">
                                        <button class="btn btn-add" id="soap-add-btn" disabled>+ Add File</button>
                                    </div>
                                </div>

                                <button class="btn btn-submit" id="soap-submit-btn" disabled style="width: 100%; margin-top: 20px;">Execute Extraction</button>
                                <div id="soap-msg" class="message-box"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            // ═══════════════════════════════════════════════════════════════════════════
            // GLOBAL STATE
            // ═══════════════════════════════════════════════════════════════════════════
            const state = {
                sourceFiles: [],
                templateFiles: [],
                restRowCounter: 0,
                soapRowCounter: 0,
                isProcessing: false,
                currentSection: null
            };

            // ═══════════════════════════════════════════════════════════════════════════
            // INITIALIZATION
            // ═══════════════════════════════════════════════════════════════════════════
            
            async function initializeApp() {
                console.log('🟦 Initializing app...');
                try {
                    // Load files from endpoints
                    console.log('📂 Fetching source and template files...');
                    const [sources, templates] = await Promise.all([
                        fetch('/api/list-sources').then(r => r.json()).catch(e => { console.error('Sources fetch error:', e); return []; }),
                        fetch('/api/list-templates').then(r => r.json()).catch(e => { console.error('Templates fetch error:', e); return []; })
                    ]);

                    state.sourceFiles = sources || [];
                    state.templateFiles = templates || [];

                    console.log(`✅ Files loaded: ${state.sourceFiles.length} sources, ${state.templateFiles.length} templates`);
                    
                    // Update file counts in UI
                    document.getElementById('rest-file-count').textContent = `(${state.sourceFiles.length} sources, ${state.templateFiles.length} templates)`;
                    document.getElementById('soap-file-count').textContent = `(${state.sourceFiles.length} sources, ${state.templateFiles.length} templates)`;

                    // Enable buttons
                    document.getElementById('rest-add-btn').disabled = false;
                    document.getElementById('soap-add-btn').disabled = false;
                    document.getElementById('rest-submit-btn').disabled = false;
                    document.getElementById('soap-submit-btn').disabled = false;

                    // Setup event listeners
                    setupEventListeners();

                    // Show REST section by default
                    showSection('rest');

                    console.log('🟩 App initialization complete!');
                } catch (e) {
                    console.error('💥 Initialization error:', e);
                    showMessage('rest-msg', `Initialization failed: ${e.message}`, 'error');
                }
            }

            function setupEventListeners() {
                // Sidebar navigation
                document.querySelectorAll('.endpoint-item').forEach(item => {
                    item.addEventListener('click', function() {
                        const section = this.getAttribute('data-section');
                        showSection(section);
                    });
                });

                // Add file buttons
                document.getElementById('rest-add-btn').addEventListener('click', () => addRow('rest'));
                document.getElementById('soap-add-btn').addEventListener('click', () => addRow('soap'));

                // Submit buttons
                document.getElementById('rest-submit-btn').addEventListener('click', () => submit('rest'));
                document.getElementById('soap-submit-btn').addEventListener('click', () => submit('soap'));
            }

            function showSection(section) {
                console.log(`📍 Switching to ${section.toUpperCase()} section`);
                
                // Hide all sections
                document.querySelectorAll('.api-section').forEach(s => s.style.display = 'none');
                
                // Show selected section
                const sectionEl = document.getElementById(`${section}-section`);
                if (sectionEl) {
                    sectionEl.style.display = 'block';
                }

                // Update active state in sidebar
                document.querySelectorAll('.endpoint-item').forEach(item => {
                    item.classList.remove('active');
                });
                document.querySelector(`[data-section="${section}"]`).classList.add('active');

                state.currentSection = section;
            }

            // ═══════════════════════════════════════════════════════════════════════════
            // FILE MANAGEMENT
            // ═══════════════════════════════════════════════════════════════════════════

            function addRow(protocol) {
                console.log(`➕ Adding file row for ${protocol.toUpperCase()}`);
                
                const isRest = protocol === 'rest';
                const container = document.getElementById(`${protocol}-files-container`);
                const counter = isRest ? state.restRowCounter++ : state.soapRowCounter++;
                const rowId = `${protocol}-row-${counter}`;

                const row = document.createElement('div');
                row.id = rowId;
                row.className = 'file-entry';

                row.innerHTML = `
                    <div class="entry-header">
                        <span class="entry-num">File #${counter + 1}</span>
                        <button type="button" class="btn-remove">Remove</button>
                    </div>
                    <div class="form-row">
                        <div class="input-group">
                            <label>Source File</label>
                            <input type="text" class="source-input" list="src-${protocol}" placeholder="Enter source file name..." autocomplete="off">
                        </div>
                        <div class="input-group">
                            <label>Template File</label>
                            <input type="text" class="template-input" list="tpl-${protocol}" placeholder="Enter template file name..." autocomplete="off">
                        </div>
                    </div>
                `;

                // Event listener for remove button
                row.querySelector('.btn-remove').addEventListener('click', () => {
                    console.log(`🗑️ Removing row ${rowId}`);
                    row.remove();
                });

                container.appendChild(row);

                // Create/update datalists
                updateDatalist(`src-${protocol}`, state.sourceFiles);
                updateDatalist(`tpl-${protocol}`, state.templateFiles);
            }

            function updateDatalist(id, files) {
                let datalist = document.getElementById(id);
                if (datalist) {
                    datalist.remove();
                }

                datalist = document.createElement('datalist');
                datalist.id = id;
                files.forEach(file => {
                    const option = document.createElement('option');
                    option.value = file;
                    datalist.appendChild(option);
                });
                document.body.appendChild(datalist);
            }

            // ═══════════════════════════════════════════════════════════════════════════
            // EXTRACTION WORKFLOW
            // ═══════════════════════════════════════════════════════════════════════════

            async function submit(protocol) {
                if (state.isProcessing) {
                    console.warn('⚠️ Another process is running');
                    return;
                }

                console.log(`🚀 Starting ${protocol.toUpperCase()} extraction cycle`);
                const isRest = protocol === 'rest';
                const container = document.getElementById(`${protocol}-files-container`);
                const msgBox = document.getElementById(`${protocol}-msg`);

                // Validate inputs
                const rows = container.querySelectorAll('.file-entry');
                if (rows.length === 0) {
                    showMessage(msgBox, '❌ Error: Please add at least one file', 'error');
                    return;
                }

                const files = [];
                for (const row of rows) {
                    const src = row.querySelector('.source-input').value.trim();
                    const tpl = row.querySelector('.template-input').value.trim();
                    if (!src || !tpl) {
                        showMessage(msgBox, '❌ Error: All source and template fields are required', 'error');
                        return;
                    }
                    files.push({ source_file_name: src, template_name: tpl });
                }

                state.isProcessing = true;
                console.log(`📋 Processing ${files.length} file(s)`);

                // Process each file
                for (let i = 0; i < files.length; i++) {
                    const file = files[i];
                    const fileNum = i + 1;
                    
                    try {
                        showMessage(msgBox, `⏳ Processing file ${fileNum}/${files.length}...\\n📁 Source: ${file.source_file_name}\\n🔷 Template: ${file.template_name}`, 'processing');

                        // Step 1: Create token
                        console.log(`[${fileNum}/${files.length}] 🔐 Creating token...`);
                        const tokenResp = await fetch('/api/create-token', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' }
                        });

                        if (!tokenResp.ok) {
                            throw new Error(`HTTP ${tokenResp.status}: ${tokenResp.statusText}`);
                        }

                        const tokenData = await tokenResp.json();
                        const token_id = tokenData.token_id;

                        if (!token_id) {
                            throw new Error(`Invalid token response: ${JSON.stringify(tokenData)}`);
                        }

                        console.log(`[${fileNum}/${files.length}] ✅ Token created: ${token_id}`);

                        // Step 2: Submit extraction request
                        console.log(`[${fileNum}/${files.length}] 📤 Submitting extraction request...`);
                        const payload = {
                            token_id: token_id,
                            source_file_name: file.source_file_name,
                            template_name: file.template_name
                        };

                        const endpoint = isRest ? '/api/rest/extract' : '/api/soap/extract';
                        const extractResp = await fetch(endpoint, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload)
                        });

                        console.log(`[${fileNum}/${files.length}] 📥 Response status: ${extractResp.status}`);

                        if (!extractResp.ok) {
                            const errData = await extractResp.json();
                            throw new Error(`API Error (${extractResp.status}): ${errData.error_message || errData.status || 'Unknown error'}`);
                        }

                        const result = await extractResp.json();
                        console.log(`[${fileNum}/${files.length}] Result:`, result);

                        // Step 3: Display successful result
                        if (result.status === 'success') {
                            const summary = `✅ File ${fileNum}/${files.length} Extraction Complete!

🔐 Token ID: ${result.token_id}
📊 Sections: ${result.sections_count}
📦 Groups: ${result.groups_count}

📁 Result File: ${result.result_file_name}
🔄 Source Renamed: ${result.done_source_name}

📌 Protocol: ${result.protocol.toUpperCase()}
📝 Message: ${result.message}`;

                            if (files.length === 1) {
                                showMessage(msgBox, summary, 'success');
                            } else if (i < files.length - 1) {
                                showMessage(msgBox, summary + '\\n\\n⏳ Processing next file...', 'processing');
                                // Small delay before next file
                                await new Promise(r => setTimeout(r, 1000));
                            } else {
                                showMessage(msgBox, summary + '\\n\\n✅ All files processed successfully!', 'success');
                            }
                        } else {
                            throw new Error(result.error_message || 'Extraction failed');
                        }

                    } catch (err) {
                        console.error(`[${fileNum}/${files.length}] 💥 Error:`, err);
                        const errMsg = `❌ File ${fileNum}/${files.length} Failed

📁 Source: ${file.source_file_name}
🔷 Template: ${file.template_name}

Error: ${err.message}`;
                        showMessage(msgBox, errMsg, 'error');
                        break; // Stop on first error
                    }
                }

                state.isProcessing = false;
                console.log('✅ Extraction cycle complete');
            }

            function showMessage(element, text, type) {
                element.className = `message-box message-${type}`;
                element.textContent = text;
                element.style.display = 'block';
                element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }

            // ═══════════════════════════════════════════════════════════════════════════
            // APP STARTUP
            // ═══════════════════════════════════════════════════════════════════════════

            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', initializeApp);
            } else {
                // DOM is already loaded
                initializeApp();
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
            "columns_format":   result["columns_format"],
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
            "columns_format":   result["columns_format"],
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
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")