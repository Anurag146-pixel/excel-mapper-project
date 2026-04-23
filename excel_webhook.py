"""
Unified API Server — REST + SOAP + Service Mediation (Webhook Protocol Transformation)
Supports REST (JSON), SOAP (XML), and bi-directional protocol transformation via webhook.
Maintains same folder structure, naming conventions, DB activity as ext.py extract().
Does NOT modify ext.py in any way.

NEW: /api/webhook/mediate  — converts REST→SOAP or SOAP→REST on the fly.
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

# ─────────────────────────────────────────────────────────────────────────────
# ALL ORIGINAL DB / PIPELINE HELPERS — UNTOUCHED
# ─────────────────────────────────────────────────────────────────────────────

def db_update_status(token_id: str, status: str, source: str = None, result: str = None, extraction_id: str = None):
    rows_affected = 0
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            numeric_id = token_id
            if isinstance(token_id, str) and token_id.startswith("token_"):
                try:
                    numeric_id = int(token_id.split("_")[1])
                except Exception as parse_err:
                    logger.error(f"❌ CANNOT PARSE token_id '{token_id}': {parse_err}")
                    return

            logger.info(f"  🔍 Looking for record: token_id={numeric_id}")
            verify_query = "SELECT token_id, status, source_file_name FROM token_details WHERE token_id = %s LIMIT 1"
            cur.execute(verify_query, (numeric_id,))
            existing_record = cur.fetchone()

            if not existing_record:
                logger.error(f"❌ RECORD NOT FOUND: token_id={numeric_id}")
                logger.error(f"   Will NOT create new record - UPDATE ONLY policy enforced")
                return

            if isinstance(existing_record, dict):
                rec_token_id = existing_record.get('token_id')
                rec_status   = existing_record.get('status')
                rec_source   = existing_record.get('source_file_name')
            else:
                rec_token_id = existing_record[0]
                rec_status   = existing_record[1]
                rec_source   = existing_record[2]

            logger.info(f"  ✓ Found record: token_id={rec_token_id}, current_status={rec_status}, source={rec_source}")

            update_parts = ["status = %s"]
            params = [status]

            if source is not None:
                update_parts.append("source_file_name = %s")
                params.append(source)
            if result is not None:
                update_parts.append("result_file_name = %s")
                params.append(result)
            if extraction_id is not None:
                update_parts.append("extraction_id = %s")
                params.append(extraction_id)

            params.append(numeric_id)
            query = f"UPDATE token_details SET {', '.join(update_parts)} WHERE token_id = %s"
            cur.execute(query, params)
            rows_affected = cur.rowcount

        conn.commit()

        if rows_affected == 1:
            logger.info(f"✅ DB UPDATE SUCCESS: token_id={numeric_id}, status={status}, rows_affected=1")
        elif rows_affected == 0:
            logger.error(f"❌ UPDATE FAILED: token_id={numeric_id}, rows_affected=0")
        else:
            logger.warning(f"⚠️  UNEXPECTED rows_affected={rows_affected} for token_id={numeric_id}")

    except Exception as e:
        logger.error(f"❌ DB Update Error: {str(e)}")
        traceback.print_exc()
    finally:
        try:
            conn.close()
        except:
            pass


def db_fetch_token(token_id: str):
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM token_details WHERE token_id = %s", (token_id,))
            return cur.fetchone()
    finally:
        conn.close()


def db_create_token():
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            logger.info(f"  🔍 Looking for existing pending record...")
            query = "SELECT token_id FROM token_details WHERE status = 'pending' LIMIT 1"
            cur.execute(query)
            result = cur.fetchone()

            if result:
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
    if SOURCE_DIR.exists():
        return sorted([f.name for f in SOURCE_DIR.iterdir() if f.is_file()])
    return []


def get_template_files():
    if TEMPLATE_DIR.exists():
        return sorted([f.name for f in TEMPLATE_DIR.iterdir() if f.is_file()])
    return []


def run_extraction_pipeline(token_id: str, source_file_name: str, template_name: str, protocol: str = "REST"):
    try:
        logger.info(f"[{protocol}] ═══════════════════════════════════════════════════════")
        logger.info(f"[{protocol}] Pipeline START  token={token_id}")
        logger.info(f"[{protocol}] ═══════════════════════════════════════════════════════")

        logger.info(f"[{protocol}] Step 1: Marking token as 'processing'...")
        db_update_status(token_id, "processing")

        logger.info(f"[{protocol}] Step 2: Validating source file: {source_file_name}")
        src = get_source_file(source_file_name)
        if not src:
            raise FileNotFoundError(f"Source file '{source_file_name}' not found in SOURCE directory")

        logger.info(f"[{protocol}] Step 3: Validating template file: {template_name}")
        tmpl = get_template_file(template_name)
        if not tmpl:
            raise FileNotFoundError(f"Template file '{template_name}' not found in TEMPLATE directory")

        logger.info(f"[{protocol}] Step 4: Loading grid and template...")
        grid          = to_grid(src)
        template_json = json.load(open(tmpl, encoding="utf-8"))
        logger.info(f"[{protocol}] Step 5: Running extraction via template...")
        extraction    = apply_template(template_json, grid)

        logger.info(f"[{protocol}] Step 6: Writing result JSON file...")
        RESULT_DIR.mkdir(exist_ok=True)
        unique_id   = uuid.uuid4().hex[:8]
        result_name = f"{src.stem}_result_{unique_id}.json"
        result_path = RESULT_DIR / result_name
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(extraction, f, indent=2)

        logger.info(f"[{protocol}] Step 7: Renaming source file with '_done_{unique_id}' suffix...")
        done_name    = f"{src.stem}_done_{unique_id}{src.suffix}"
        new_src_path = SOURCE_DIR / done_name
        import shutil
        shutil.move(str(src), str(new_src_path))

        logger.info(f"[{protocol}] Step 8: Updating database with 'done' status and extraction_id...")
        db_update_status(token_id, "done", done_name, result_name, unique_id)

        logger.info(f"[{protocol}] Pipeline DONE ✅  token={token_id}")

        return {
            "token_id":         token_id,
            "result_file_name": result_name,
            "done_source_name": done_name,
            "data":             extraction,
            "sections_count":   len(extraction.get("sections", {})),
            "groups_count":     len(extraction.get("groups",   {})),
        }
    except Exception as e:
        logger.error(f"[{protocol}] ❌ Pipeline ERROR: {str(e)}")
        traceback.print_exc()
        db_update_status(token_id, "failed")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# NEW: SERVICE MEDIATION HELPERS (Protocol Transformation)
# ─────────────────────────────────────────────────────────────────────────────

def json_to_soap_envelope(payload: dict) -> str:
    """
    Transforms a REST JSON payload into a SOAP 1.1 XML envelope.
    Field names become child elements inside <ExtractionRequest>.
    """
    fields_xml = ""
    for key, value in payload.items():
        safe_val = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        fields_xml += f"\n        <{key}>{safe_val}</{key}>"

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<soap:Envelope\n'
        '    xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"\n'
        '    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '    xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
        '  <soap:Header/>\n'
        '  <soap:Body>\n'
        '    <ExtractionRequest xmlns="http://extraction.api/soap">'
        f'{fields_xml}\n'
        '    </ExtractionRequest>\n'
        '  </soap:Body>\n'
        '</soap:Envelope>'
    )


def soap_envelope_to_json(xml_text: str) -> dict:
    """
    Parses a SOAP 1.1 XML envelope and extracts all child elements
    of the first Body child into a flat JSON dict.
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)

    soap_body = None
    for child in root:
        if "Body" in child.tag:
            soap_body = child
            break
    if soap_body is None:
        raise ValueError("No soap:Body found in envelope")

    request_elem = None
    for child in soap_body:
        request_elem = child
        break
    if request_elem is None:
        raise ValueError("Empty soap:Body")

    result = {}
    for child in request_elem:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        result[local] = (child.text or "").strip()
    return result


def build_mediation_log(direction: str, input_payload, output_payload, target_endpoint: str) -> dict:
    """Returns a structured mediation audit record."""
    return {
        "mediation_id":      uuid.uuid4().hex[:12],
        "timestamp":         datetime.utcnow().isoformat() + "Z",
        "direction":         direction,
        "target_endpoint":   target_endpoint,
        "input_summary":     (
            f"{len(input_payload)} fields" if isinstance(input_payload, dict)
            else f"{len(input_payload)} chars (XML)"
        ),
        "output_summary":    (
            f"{len(output_payload)} chars (XML)" if direction == "REST_TO_SOAP"
            else f"{len(output_payload)} fields"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── NEW: Webhook Mediation Endpoint ──────────────────────────────────────────

@app.post("/api/webhook/mediate")
async def webhook_mediate(request: Request):
    """
    Service Mediation Webhook — Protocol Transformation.

    Accepts a JSON body:
    {
        "direction":       "REST_TO_SOAP" | "SOAP_TO_REST",
        "payload":         <string|object>,   // raw REST JSON or SOAP XML string
        "forward_to":      "/api/soap/extract" | "/api/rest/extract"  (optional),
        "token_id":        "...",
        "source_file_name":"...",
        "template_name":   "..."
    }

    Returns:
    {
        "mediation_id":     "...",
        "direction":        "REST_TO_SOAP",
        "transformed":      "<soap:Envelope>...</soap:Envelope>"  |  {...},
        "forwarded":        true | false,
        "forward_result":   {...} | null,
        "log":              {...}
    }

    This endpoint NEVER touches the database directly — forwarding calls
    the existing /api/rest/extract or /api/soap/extract which handle all DB ops.
    """
    import xml.etree.ElementTree as ET
    import httpx

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error_message": "Request body must be valid JSON"
        })

    direction      = (body.get("direction") or "").upper().strip()
    raw_payload    = body.get("payload")
    forward_to     = body.get("forward_to")       # optional target endpoint path
    token_id       = body.get("token_id")
    source_file    = body.get("source_file_name")
    template_name  = body.get("template_name")

    if direction not in ("REST_TO_SOAP", "SOAP_TO_REST"):
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error_message": "direction must be 'REST_TO_SOAP' or 'SOAP_TO_REST'"
        })

    logger.info(f"[MEDIATION] direction={direction}  forward_to={forward_to}")

    transformed = None
    forward_result = None

    try:
        # ── REST → SOAP ───────────────────────────────────────────────────────
        if direction == "REST_TO_SOAP":
            # Accept either a dict in 'payload' or build one from top-level fields
            if isinstance(raw_payload, dict):
                rest_json = raw_payload
            elif isinstance(raw_payload, str):
                rest_json = json.loads(raw_payload)
            else:
                # Build from convenience fields
                rest_json = {}
                if token_id:       rest_json["token_id"]         = token_id
                if source_file:    rest_json["source_file_name"] = source_file
                if template_name:  rest_json["template_name"]    = template_name

            if not rest_json:
                return JSONResponse(status_code=400, content={
                    "status": "error",
                    "error_message": "No payload supplied for REST_TO_SOAP transformation"
                })

            transformed = json_to_soap_envelope(rest_json)
            log = build_mediation_log(direction, rest_json, transformed,
                                      forward_to or "/api/soap/extract")

            # Optional: forward the transformed XML to the SOAP endpoint
            if forward_to:
                async with httpx.AsyncClient() as client:
                    host_url  = str(request.base_url).rstrip("/")
                    target    = host_url + forward_to
                    logger.info(f"[MEDIATION] Forwarding transformed SOAP to {target}")
                    fwd_resp  = await client.post(
                        target,
                        content=transformed.encode("utf-8"),
                        headers={"Content-Type": "text/xml; charset=utf-8"},
                        timeout=60,
                    )
                    forward_result = fwd_resp.json()
                    logger.info(f"[MEDIATION] Forward response status={fwd_resp.status_code}")

        # ── SOAP → REST ───────────────────────────────────────────────────────
        else:  # SOAP_TO_REST
            if isinstance(raw_payload, str):
                xml_text = raw_payload
            elif isinstance(raw_payload, dict):
                # Already parsed — use directly
                transformed = raw_payload
                xml_text    = None
            else:
                return JSONResponse(status_code=400, content={
                    "status": "error",
                    "error_message": "For SOAP_TO_REST, 'payload' must be an XML string"
                })

            if xml_text is not None:
                transformed = soap_envelope_to_json(xml_text)

            log = build_mediation_log(direction,
                                      raw_payload if isinstance(raw_payload, str) else str(raw_payload),
                                      transformed,
                                      forward_to or "/api/rest/extract")

            # Optional: forward the REST JSON to the REST endpoint
            if forward_to:
                async with httpx.AsyncClient() as client:
                    host_url  = str(request.base_url).rstrip("/")
                    target    = host_url + forward_to
                    logger.info(f"[MEDIATION] Forwarding transformed REST JSON to {target}")
                    fwd_resp  = await client.post(
                        target,
                        json=transformed,
                        headers={"Content-Type": "application/json"},
                        timeout=60,
                    )
                    forward_result = fwd_resp.json()
                    logger.info(f"[MEDIATION] Forward response status={fwd_resp.status_code}")

    except ET.ParseError as e:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error_message": f"Invalid SOAP/XML: {str(e)}"
        })
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error_message": f"Invalid JSON payload: {str(e)}"
        })
    except Exception as e:
        logger.error(f"[MEDIATION] ❌ Error: {str(e)}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "status": "error",
            "error_message": str(e)
        })

    return JSONResponse(content={
        "status":         "success",
        "mediation_id":   log["mediation_id"],
        "timestamp":      log["timestamp"],
        "direction":      direction,
        "transformed":    transformed,
        "forwarded":      forward_result is not None,
        "forward_result": forward_result,
        "log":            log,
    })


# ── Original Endpoints — UNTOUCHED ───────────────────────────────────────────

@app.get("/")
async def root():
    html_content = r"""
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
            .method-webhook { background: #9c27b0; }
            .content-area { max-width: 900px; }
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
            .input-group input, .input-group select { padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; font-family: 'Roboto Mono', monospace; }
            .input-group input:focus, .input-group select:focus { outline: none; border-color: #2196f3; box-shadow: 0 0 0 2px rgba(33,150,243,0.1); }
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
            .btn-webhook { background: #9c27b0; border-color: #9c27b0; color: white; }
            .btn-webhook:hover:not(:disabled) { background: #7b1fa2; }
            .btn-webhook:disabled { opacity: 0.6; cursor: not-allowed; }
            .message-box { padding: 12px 16px; border-radius: 4px; margin-top: 12px; font-size: 13px; display: none; font-family: 'Roboto Mono', monospace; white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow-y: auto; }
            .message-success { background: #e8f5e9; border: 1px solid #4caf50; color: #2e7d32; }
            .message-error { background: #ffebee; border: 1px solid #f44336; color: #c62828; }
            .message-processing { background: #e3f2fd; border: 1px solid #2196f3; color: #1565c0; }
            .loader { display: inline-block; width: 14px; height: 14px; border: 2px solid #ccc; border-top-color: #2196f3; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; }
            @keyframes spin { to { transform: rotate(360deg); } }

            /* ── Service Mediation Panel ── */
            .mediation-panel { background: #fff; border: 2px solid #9c27b0; border-radius: 6px; overflow: hidden; }
            .mediation-header { background: linear-gradient(90deg, #9c27b0, #673ab7); padding: 16px 20px; display: flex; align-items: center; gap: 10px; }
            .mediation-header h3 { color: #fff; font-size: 16px; font-weight: 500; margin: 0; }
            .mediation-header .badge { background: rgba(255,255,255,0.2); color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
            .mediation-body { padding: 20px; }
            .direction-toggle { display: flex; gap: 0; border: 1px solid #9c27b0; border-radius: 4px; overflow: hidden; margin-bottom: 20px; }
            .dir-btn { flex: 1; padding: 10px; font-size: 12px; font-weight: 700; text-align: center; cursor: pointer; border: none; background: #fff; color: #9c27b0; transition: all 0.2s; }
            .dir-btn.active { background: #9c27b0; color: #fff; }
            .dir-btn:hover:not(.active) { background: #f3e5f5; }
            .mediation-grid { display: grid; grid-template-columns: 1fr 60px 1fr; gap: 12px; align-items: start; margin-bottom: 20px; }
            .pane-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #666; margin-bottom: 6px; }
            .pane-label.rest { color: #1565c0; }
            .pane-label.soap { color: #6a1b9a; }
            textarea.payload-box { width: 100%; height: 180px; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-family: 'Roboto Mono', monospace; font-size: 11.5px; resize: vertical; line-height: 1.5; }
            textarea.payload-box:focus { outline: none; border-color: #9c27b0; }
            .arrow-col { display: flex; flex-direction: column; align-items: center; justify-content: center; padding-top: 24px; }
            .arrow-icon { font-size: 24px; color: #9c27b0; font-weight: 700; }
            .pane-output { background: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 4px; padding: 10px; font-family: 'Roboto Mono', monospace; font-size: 11.5px; min-height: 180px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; color: #333; }
            .mediation-options { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 20px; }
            .mediation-options .input-group select { font-size: 12px; }
            .mediation-footer { display: flex; gap: 12px; align-items: center; }
            .copy-btn { background: #fff; border: 1px solid #9c27b0; color: #9c27b0; padding: 8px 14px; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: 600; }
            .copy-btn:hover { background: #f3e5f5; }
            .forward-badge { font-size: 11px; color: #9c27b0; background: #f3e5f5; padding: 4px 10px; border-radius: 3px; font-weight: 600; }
            .log-box { margin-top: 12px; background: #1a1a2e; border-radius: 4px; padding: 12px; font-family: 'Roboto Mono', monospace; font-size: 11px; color: #a0f0a0; display: none; max-height: 200px; overflow-y: auto; }
            .divider { border: none; border-top: 1px solid #f0f0f0; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="topbar">
            <h1>FastAPI - Excel Extraction Engine</h1>
            <div class="info">
                <div>v0.2.0 | REST · SOAP · Service Mediation</div>
            </div>
        </div>

        <div class="layout">
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
                <div class="endpoint-group">
                    <h3>Service Mediation</h3>
                    <div class="endpoint-item" data-section="mediation">
                        <span class="method-badge method-webhook">WH</span>
                        <span>Protocol Transform</span>
                    </div>
                </div>
            </div>

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

                    <!-- SERVICE MEDIATION Section -->
                    <div id="mediation-section" class="api-section" style="display: none;">
                        <div class="mediation-panel">
                            <div class="mediation-header">
                                <h3>Service Mediation</h3>
                                <span class="badge">Webhook · Protocol Transformation</span>
                            </div>
                            <div class="mediation-body">
                                <p style="font-size: 13px; color: #666; margin-bottom: 16px;">
                                    Bi-directional protocol transformer. Convert a REST JSON payload to a SOAP XML envelope
                                    — or unwrap a SOAP envelope back to REST JSON. Optionally forward the transformed
                                    payload to the target extraction endpoint.
                                </p>

                                <!-- Direction Toggle -->
                                <div class="form-section">
                                    <label>Transformation Direction</label>
                                    <div class="direction-toggle">
                                        <div class="dir-btn active" id="dir-rest2soap" onclick="setDirection('REST_TO_SOAP')">
                                            REST → SOAP
                                        </div>
                                        <div class="dir-btn" id="dir-soap2rest" onclick="setDirection('SOAP_TO_REST')">
                                            SOAP → REST
                                        </div>
                                    </div>
                                </div>

                                <!-- Quick-fill form -->
                                <div class="form-section">
                                    <label>Quick-Fill Fields <span style="font-size:11px;color:#999;">(auto-builds input payload)</span></label>
                                    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px;">
                                        <div class="input-group">
                                            <label>Token ID</label>
                                            <input type="text" id="med-token" placeholder="e.g. token_123">
                                        </div>
                                        <div class="input-group">
                                            <label>Source File</label>
                                            <input type="text" id="med-source" list="med-src-list" placeholder="source.xlsx">
                                        </div>
                                        <div class="input-group">
                                            <label>Template File</label>
                                            <input type="text" id="med-template" list="med-tpl-list" placeholder="template.json">
                                        </div>
                                    </div>
                                    <button class="btn" style="background:#ede7f6;border-color:#9c27b0;color:#6a1b9a;font-size:12px;padding:7px 14px;" onclick="autoFillPayload()">
                                        Auto-build Payload
                                    </button>
                                </div>

                                <hr class="divider">

                                <!-- Payload Panes -->
                                <div class="form-section">
                                    <label>Payload Editor</label>
                                    <div class="mediation-grid">
                                        <div>
                                            <div class="pane-label rest" id="input-pane-label">REST JSON (Input)</div>
                                            <textarea class="payload-box" id="med-input" placeholder='{"token_id":"token_1","source_file_name":"data.xlsx","template_name":"tmpl.json"}'></textarea>
                                        </div>
                                        <div class="arrow-col">
                                            <div class="arrow-icon" id="arrow-icon">→</div>
                                        </div>
                                        <div>
                                            <div class="pane-label soap" id="output-pane-label">SOAP XML (Output)</div>
                                            <div class="pane-output" id="med-output"><span style="color:#bbb;">Transformed payload will appear here...</span></div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Options -->
                                <div class="form-section">
                                    <label>Forwarding Options <span style="font-size:11px;color:#999;">(optional — sends transformed payload to extraction endpoint)</span></label>
                                    <div class="mediation-options">
                                        <div class="input-group">
                                            <label>Forward To</label>
                                            <select id="med-forward">
                                                <option value="">— Transform only (no forward) —</option>
                                                <option value="/api/soap/extract">/api/soap/extract</option>
                                                <option value="/api/rest/extract">/api/rest/extract</option>
                                            </select>
                                        </div>
                                        <div class="input-group">
                                            <label>Mediation ID Preview</label>
                                            <input type="text" id="med-id-preview" readonly placeholder="Generated after transform" style="background:#f9f9f9;color:#999;">
                                        </div>
                                        <div class="input-group">
                                            <label>Timestamp</label>
                                            <input type="text" id="med-ts-preview" readonly placeholder="UTC timestamp" style="background:#f9f9f9;color:#999;">
                                        </div>
                                    </div>
                                </div>

                                <div class="mediation-footer">
                                    <button class="btn btn-webhook" id="med-transform-btn" onclick="runMediation()" style="min-width: 180px;">
                                        Transform &amp; Mediate
                                    </button>
                                    <button class="copy-btn" onclick="copyOutput()" title="Copy output to clipboard">Copy Output</button>
                                    <button class="copy-btn" onclick="swapToExtract()" title="Send transformed payload directly to extraction">Use in Extraction →</button>
                                </div>

                                <!-- Audit Log -->
                                <div class="log-box" id="med-log"></div>
                                <div id="med-msg" class="message-box" style="margin-top:12px;"></div>

                                <!-- Forward result -->
                                <div id="med-forward-result" style="display:none;margin-top:16px;">
                                    <div style="font-size:12px;font-weight:600;color:#6a1b9a;margin-bottom:6px;">Forward Extraction Result</div>
                                    <div class="pane-output" id="med-forward-content" style="max-height:200px;"></div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>
        </div>

        <script>
        // ═══════════════════════════════════════════════════════
        // GLOBAL STATE
        // ═══════════════════════════════════════════════════════
        const state = {
            sourceFiles:    [],
            templateFiles:  [],
            restRowCounter: 0,
            soapRowCounter: 0,
            isProcessing:   false,
            currentSection: null,
            mediationDir:   'REST_TO_SOAP',
            lastOutput:     null,
        };

        // ═══════════════════════════════════════════════════════
        // INIT
        // ═══════════════════════════════════════════════════════
        async function initializeApp() {
            const [sources, templates] = await Promise.all([
                fetch('/api/list-sources').then(r => r.json()).catch(() => []),
                fetch('/api/list-templates').then(r => r.json()).catch(() => [])
            ]);
            state.sourceFiles   = sources   || [];
            state.templateFiles = templates || [];

            document.getElementById('rest-file-count').textContent = `(${state.sourceFiles.length} sources, ${state.templateFiles.length} templates)`;
            document.getElementById('soap-file-count').textContent = `(${state.sourceFiles.length} sources, ${state.templateFiles.length} templates)`;

            ['rest-add-btn','soap-add-btn'].forEach(id => document.getElementById(id).disabled = false);
            ['rest-submit-btn','soap-submit-btn'].forEach(id => document.getElementById(id).disabled = false);

            updateDatalist('med-src-list', state.sourceFiles);
            updateDatalist('med-tpl-list', state.templateFiles);

            setupEventListeners();
            showSection('rest');
        }

        function setupEventListeners() {
            document.querySelectorAll('.endpoint-item').forEach(item => {
                item.addEventListener('click', function() { showSection(this.getAttribute('data-section')); });
            });
            document.getElementById('rest-add-btn').addEventListener('click', () => addRow('rest'));
            document.getElementById('soap-add-btn').addEventListener('click', () => addRow('soap'));
            document.getElementById('rest-submit-btn').addEventListener('click', () => submit('rest'));
            document.getElementById('soap-submit-btn').addEventListener('click', () => submit('soap'));
        }

        function showSection(section) {
            document.querySelectorAll('.api-section').forEach(s => s.style.display = 'none');
            const el = document.getElementById(`${section}-section`);
            if (el) el.style.display = 'block';
            document.querySelectorAll('.endpoint-item').forEach(i => i.classList.remove('active'));
            const active = document.querySelector(`[data-section="${section}"]`);
            if (active) active.classList.add('active');
            state.currentSection = section;
        }

        // ═══════════════════════════════════════════════════════
        // FILE ROWS (REST / SOAP extraction — unchanged)
        // ═══════════════════════════════════════════════════════
        function addRow(protocol) {
            const isRest    = protocol === 'rest';
            const container = document.getElementById(`${protocol}-files-container`);
            const counter   = isRest ? state.restRowCounter++ : state.soapRowCounter++;
            const rowId     = `${protocol}-row-${counter}`;
            const row       = document.createElement('div');
            row.id          = rowId;
            row.className   = 'file-entry';
            row.innerHTML   = `
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
                </div>`;
            row.querySelector('.btn-remove').addEventListener('click', () => row.remove());
            container.appendChild(row);
            updateDatalist(`src-${protocol}`, state.sourceFiles);
            updateDatalist(`tpl-${protocol}`, state.templateFiles);
        }

        function updateDatalist(id, files) {
            let dl = document.getElementById(id);
            if (dl) dl.remove();
            dl = document.createElement('datalist');
            dl.id = id;
            files.forEach(f => { const o = document.createElement('option'); o.value = f; dl.appendChild(o); });
            document.body.appendChild(dl);
        }

        async function submit(protocol) {
            if (state.isProcessing) return;
            const isRest    = protocol === 'rest';
            const container = document.getElementById(`${protocol}-files-container`);
            const msgBox    = document.getElementById(`${protocol}-msg`);
            const rows      = container.querySelectorAll('.file-entry');
            if (rows.length === 0) { showMessage(msgBox, '❌ Error: Please add at least one file', 'error'); return; }
            const files = [];
            for (const row of rows) {
                const src = row.querySelector('.source-input').value.trim();
                const tpl = row.querySelector('.template-input').value.trim();
                if (!src || !tpl) { showMessage(msgBox, '❌ Error: All source and template fields are required', 'error'); return; }
                files.push({ source_file_name: src, template_name: tpl });
            }
            state.isProcessing = true;
            for (let i = 0; i < files.length; i++) {
                const file    = files[i];
                const fileNum = i + 1;
                try {
                    showMessage(msgBox, `⏳ Processing file ${fileNum}/${files.length}...\n📁 Source: ${file.source_file_name}\n🔷 Template: ${file.template_name}`, 'processing');
                    const tokenResp = await fetch('/api/create-token', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
                    if (!tokenResp.ok) throw new Error(`HTTP ${tokenResp.status}: ${tokenResp.statusText}`);
                    const tokenData = await tokenResp.json();
                    const token_id  = tokenData.token_id;
                    if (!token_id) throw new Error(`Invalid token response: ${JSON.stringify(tokenData)}`);
                    const endpoint   = isRest ? '/api/rest/extract' : '/api/soap/extract';
                    const extractResp = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ token_id, source_file_name: file.source_file_name, template_name: file.template_name })
                    });
                    if (!extractResp.ok) { const e = await extractResp.json(); throw new Error(`API Error (${extractResp.status}): ${e.error_message || e.status || 'Unknown'}`); }
                    const result = await extractResp.json();
                    if (result.status === 'success') {
                        const summary = `✅ File ${fileNum}/${files.length} Extraction Complete!\n\n🔐 Token ID: ${result.token_id}\n📊 Sections: ${result.sections_count}\n📦 Groups: ${result.groups_count}\n\n📁 Result File: ${result.result_file_name}\n🔄 Source Renamed: ${result.done_source_name}\n\n📌 Protocol: ${result.protocol.toUpperCase()}\n📝 Message: ${result.message}`;
                        const extra = (i < files.length - 1) ? '\n\n⏳ Processing next file...' : (files.length > 1 ? '\n\n✅ All files processed!' : '');
                        showMessage(msgBox, summary + extra, i < files.length - 1 ? 'processing' : 'success');
                        if (i < files.length - 1) await new Promise(r => setTimeout(r, 1000));
                    } else { throw new Error(result.error_message || 'Extraction failed'); }
                } catch (err) {
                    showMessage(msgBox, `❌ File ${fileNum}/${files.length} Failed\n\n📁 Source: ${file.source_file_name}\n🔷 Template: ${file.template_name}\n\nError: ${err.message}`, 'error');
                    break;
                }
            }
            state.isProcessing = false;
        }

        function showMessage(element, text, type) {
            if (typeof element === 'string') element = document.getElementById(element);
            element.className = `message-box message-${type}`;
            element.textContent = text;
            element.style.display = 'block';
            element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        // ═══════════════════════════════════════════════════════
        // SERVICE MEDIATION
        // ═══════════════════════════════════════════════════════

        function setDirection(dir) {
            state.mediationDir = dir;
            document.getElementById('dir-rest2soap').classList.toggle('active', dir === 'REST_TO_SOAP');
            document.getElementById('dir-soap2rest').classList.toggle('active', dir === 'SOAP_TO_REST');
            document.getElementById('arrow-icon').textContent = '→';

            if (dir === 'REST_TO_SOAP') {
                document.getElementById('input-pane-label').textContent  = 'REST JSON (Input)';
                document.getElementById('input-pane-label').className    = 'pane-label rest';
                document.getElementById('output-pane-label').textContent = 'SOAP XML (Output)';
                document.getElementById('output-pane-label').className   = 'pane-label soap';
                document.getElementById('med-input').placeholder         = '{"token_id":"token_1","source_file_name":"data.xlsx","template_name":"tmpl.json"}';
                // Set sensible default for forward dropdown
                document.getElementById('med-forward').value = '';
            } else {
                document.getElementById('input-pane-label').textContent  = 'SOAP XML (Input)';
                document.getElementById('input-pane-label').className    = 'pane-label soap';
                document.getElementById('output-pane-label').textContent = 'REST JSON (Output)';
                document.getElementById('output-pane-label').className   = 'pane-label rest';
                document.getElementById('med-input').placeholder         = '<?xml version="1.0"?>\n<soap:Envelope ...>...</soap:Envelope>';
                document.getElementById('med-forward').value = '';
            }
            // Clear output
            document.getElementById('med-output').innerHTML = '<span style="color:#bbb;">Transformed payload will appear here...</span>';
            document.getElementById('med-log').style.display = 'none';
            document.getElementById('med-forward-result').style.display = 'none';
            state.lastOutput = null;
        }

        function autoFillPayload() {
            const token    = document.getElementById('med-token').value.trim();
            const source   = document.getElementById('med-source').value.trim();
            const template = document.getElementById('med-template').value.trim();

            if (state.mediationDir === 'REST_TO_SOAP') {
                const obj = {};
                if (token)    obj.token_id          = token;
                if (source)   obj.source_file_name  = source;
                if (template) obj.template_name     = template;
                document.getElementById('med-input').value = JSON.stringify(obj, null, 2);
            } else {
                // Build a SOAP envelope for SOAP→REST input
                const fields = [];
                if (token)    fields.push(`        <token_id>${token}</token_id>`);
                if (source)   fields.push(`        <source_file_name>${source}</source_file_name>`);
                if (template) fields.push(`        <template_name>${template}</template_name>`);
                const envelope =
`<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope
    xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Header/>
  <soap:Body>
    <ExtractionRequest xmlns="http://extraction.api/soap">
${fields.join('\n')}
    </ExtractionRequest>
  </soap:Body>
</soap:Envelope>`;
                document.getElementById('med-input').value = envelope;
            }
        }

        async function runMediation() {
            const btn     = document.getElementById('med-transform-btn');
            const msgBox  = document.getElementById('med-msg');
            const input   = document.getElementById('med-input').value.trim();
            const fwdTo   = document.getElementById('med-forward').value;

            if (!input) {
                showMessage(msgBox, '❌ Please enter an input payload or use Auto-build', 'error');
                return;
            }

            btn.disabled    = true;
            btn.textContent = '⏳ Transforming...';
            msgBox.style.display = 'none';

            try {
                // Determine payload type
                let payload;
                if (state.mediationDir === 'REST_TO_SOAP') {
                    try { payload = JSON.parse(input); } catch { payload = input; }
                } else {
                    payload = input; // Always string XML for SOAP→REST
                }

                const body = {
                    direction:  state.mediationDir,
                    payload:    payload,
                };
                if (fwdTo) body.forward_to = fwdTo;

                const resp = await fetch('/api/webhook/mediate', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(body),
                });

                const data = await resp.json();

                if (!resp.ok || data.status !== 'success') {
                    throw new Error(data.error_message || `HTTP ${resp.status}`);
                }

                state.lastOutput = data.transformed;

                // Render transformed output
                const outEl = document.getElementById('med-output');
                if (typeof data.transformed === 'object') {
                    outEl.textContent = JSON.stringify(data.transformed, null, 2);
                } else {
                    outEl.textContent = data.transformed;
                }

                // Update metadata fields
                document.getElementById('med-id-preview').value = data.mediation_id;
                document.getElementById('med-ts-preview').value = data.timestamp;

                // Show audit log
                const logEl = document.getElementById('med-log');
                logEl.style.display = 'block';
                logEl.textContent = JSON.stringify(data.log, null, 2);

                // Show forward result if present
                const fwdResultEl = document.getElementById('med-forward-result');
                const fwdContentEl = document.getElementById('med-forward-content');
                if (data.forwarded && data.forward_result) {
                    fwdResultEl.style.display = 'block';
                    fwdContentEl.textContent = JSON.stringify(data.forward_result, null, 2);
                    showMessage(msgBox,
                        `✅ Mediation complete!\n` +
                        `🔀 Direction: ${data.direction}\n` +
                        `🆔 Mediation ID: ${data.mediation_id}\n` +
                        `📤 Forwarded to: ${fwdTo}\n` +
                        `📋 Forward status: ${data.forward_result?.status || 'unknown'}`,
                        'success');
                } else {
                    fwdResultEl.style.display = 'none';
                    showMessage(msgBox,
                        `✅ Transformation complete!\n` +
                        `🔀 Direction: ${data.direction}\n` +
                        `🆔 Mediation ID: ${data.mediation_id}\n` +
                        `📋 No forwarding (transform-only mode)`,
                        'success');
                }

            } catch (err) {
                showMessage(msgBox, `❌ Mediation Error\n\n${err.message}`, 'error');
            } finally {
                btn.disabled    = false;
                btn.textContent = 'Transform & Mediate';
            }
        }

        function copyOutput() {
            const text = document.getElementById('med-output').textContent;
            if (!text || text.includes('will appear here')) return;
            navigator.clipboard.writeText(text).then(() => alert('Output copied to clipboard!')).catch(() => alert('Copy failed — please copy manually.'));
        }

        function swapToExtract() {
            if (!state.lastOutput) { alert('Run a transformation first.'); return; }
            if (state.mediationDir === 'REST_TO_SOAP') {
                // Output is SOAP XML → paste into SOAP section
                showSection('soap');
                alert('Transformed SOAP payload generated. Use the SOAP Extract section with this template, or enable forwarding to auto-submit.');
            } else {
                // Output is REST JSON → paste token/source/template into REST section
                const out = state.lastOutput;
                if (out.token_id || out.source_file_name || out.template_name) {
                    showSection('rest');
                    addRow('rest');
                    const rows = document.querySelectorAll('#rest-files-container .file-entry');
                    const last = rows[rows.length - 1];
                    if (last) {
                        if (out.source_file_name) last.querySelector('.source-input').value   = out.source_file_name;
                        if (out.template_name)    last.querySelector('.template-input').value = out.template_name;
                    }
                    if (out.token_id) {
                        alert(`Fields pre-filled in REST Extract. Token: ${out.token_id} (paste manually as needed).`);
                    }
                }
            }
        }

        // ═══════════════════════════════════════════════════════
        // STARTUP
        // ═══════════════════════════════════════════════════════
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initializeApp);
        } else {
            initializeApp();
        }
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
    return JSONResponse(status_code=500, content={"error": "Failed to create token"})


@app.post("/api/rest/extract")
async def rest_extract(request: Request):
    try:
        body = await request.body()
        logger.info(f"REST │ Received raw body: {body}")
        req  = json.loads(body.decode('utf-8'))
        logger.info(f"REST │ Parsed JSON: {req}")
        token_id         = req.get('token_id')
        source_file_name = req.get('source_file_name')
        template_name    = req.get('template_name')
        logger.info(f"REST │ Extracted fields - token_id={token_id}, source={source_file_name}, template={template_name}")
        if not all([token_id, source_file_name, template_name]):
            missing = [f for f, v in [('token_id', token_id), ('source_file_name', source_file_name), ('template_name', template_name)] if not v]
            return JSONResponse(status_code=400, content={"status": "error", "error_message": f"Missing required fields: {', '.join(missing)}"})
        result = run_extraction_pipeline(token_id=token_id, source_file_name=source_file_name, template_name=template_name, protocol="REST")
        return JSONResponse(content={
            "status": "success", "protocol": "REST",
            "token_id": result["token_id"], "result_file_name": result["result_file_name"],
            "done_source_name": result["done_source_name"], "sections_count": result["sections_count"],
            "groups_count": result["groups_count"], "data": result["data"],
            "message": f"Extraction complete. {result['sections_count']} section(s) processed.",
        })
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={"status": "error", "error_message": f"Invalid JSON: {str(e)}"})
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"status": "error", "error_message": str(e)})
    except Exception as e:
        logger.error(f"REST │ Error: {str(e)}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "error_message": str(e), "message": "Extraction failed"})


@app.post("/api/soap/extract")
async def soap_extract(request: Request):
    import xml.etree.ElementTree as ET
    raw = await request.body()
    logger.info(f"SOAP │ Received request  bytes={len(raw)}")
    token_id = source_file_name = template_name = None
    try:
        json_data    = json.loads(raw.decode('utf-8'))
        token_id     = json_data.get('token_id')
        source_file_name = json_data.get('source_file_name')
        template_name = json_data.get('template_name')
    except:
        try:
            root      = ET.fromstring(raw)
            soap_body = next((c for c in root if "Body" in c.tag), None)
            if soap_body is None:
                return JSONResponse(status_code=400, content={"error": "No soap:Body found"})
            request_elem = next((c for c in soap_body), None)
            if request_elem is None:
                return JSONResponse(status_code=400, content={"error": "Empty SOAP Body"})
            def get_text(parent, tag):
                for child in parent:
                    local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if local.lower() == tag.lower():
                        return (child.text or "").strip()
                return ""
            token_id         = get_text(request_elem, "token_id")
            source_file_name = get_text(request_elem, "source_file_name")
            template_name    = get_text(request_elem, "template_name")
        except ET.ParseError as e:
            return JSONResponse(status_code=400, content={"error": f"Invalid request format: {str(e)}"})
    if not all([token_id, source_file_name, template_name]):
        return JSONResponse(status_code=400, content={"error": "Missing required fields"})
    logger.info(f"SOAP │ token={token_id}  src={source_file_name}  tmpl={template_name}")
    try:
        result = run_extraction_pipeline(token_id=token_id, source_file_name=source_file_name, template_name=template_name, protocol="SOAP")
        return JSONResponse(content={
            "status": "success", "protocol": "SOAP",
            "token_id": result["token_id"], "result_file_name": result["result_file_name"],
            "done_source_name": result["done_source_name"], "sections_count": result["sections_count"],
            "groups_count": result["groups_count"], "data": result["data"],
            "message": f"Extraction complete. {result['sections_count']} section(s) processed.",
        })
    except FileNotFoundError as e:
        db_update_status(token_id, "failed")
        return JSONResponse(status_code=404, content={"status": "error", "error_message": str(e)})
    except Exception as e:
        logger.error(f"SOAP │ Error  token={token_id}  err={e}")
        traceback.print_exc()
        db_update_status(token_id, "failed")
        return JSONResponse(status_code=500, content={"status": "error", "error_message": str(e)})


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("  Excel Extraction API Server  v0.2.0")
    logger.info("=" * 70)
    logger.info("  🌐 Web UI            → http://localhost:8002/")
    logger.info("  📡 REST API          → POST /api/rest/extract")
    logger.info("  📡 SOAP API          → POST /api/soap/extract")
    logger.info("  🔀 Webhook Mediation → POST /api/webhook/mediate")
    logger.info("  📋 List Sources      → GET  /api/list-sources")
    logger.info("  📋 List Templates    → GET  /api/list-templates")
    logger.info("  🔐 Create Token      → POST /api/create-token")
    logger.info("=" * 70)
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")