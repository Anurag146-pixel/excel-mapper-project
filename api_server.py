"""
API Server for Excel Data Extraction
Provides REST API and SOAP API endpoints for triggering data extraction
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from spyne import Application, rpc, ServiceBase, Unicode, ComplexModel
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication

# Import extraction functions from ext.py
from ext import extract, get_source_file, get_template_file, to_grid, apply_template

# ─────────── LOGGING SETUP ─────────── #

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─────────── FASTAPI REST API SETUP ─────────── #

app = FastAPI(
    title="Excel Data Extraction API",
    description="REST API for extracting data from Excel files using templates",
    version="1.0.0"
)

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────── PYDANTIC MODELS ─────────── #

class ExtractionRequest(BaseModel):
    """Request model for extraction API"""
    token_id: str = Field(..., description="Unique token identifier")
    source_file_name: str = Field(..., description="Name of the source file in SOURCE directory")
    template_name: str = Field(..., description="Name of the template file in TEMPLATE directory")
    
    class Config:
        json_schema_extra = {
            "example": {
                "token_id": "token_12345",
                "source_file_name": "student_list.xlsx",
                "template_name": "TEMPSTU.json"
            }
        }


class ExtractionResponse(BaseModel):
    """Response model for extraction API"""
    status: str = Field(..., description="Status of the extraction (success/error)")
    token_id: str = Field(..., description="Token ID from the request")
    result_file_name: Optional[str] = Field(None, description="Name of the result JSON file")
    data: Optional[Dict[str, Any]] = Field(None, description="Extracted data")
    error_message: Optional[str] = Field(None, description="Error message if extraction failed")
    message: str = Field(..., description="Status message")


# ─────────── REST API ENDPOINTS ─────────── #

@app.get("/", response_class=HTMLResponse)
async def home():
    """Home page with API documentation"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Excel Data Extraction API</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            .container {
                background: white;
                border-radius: 10px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                max-width: 800px;
                width: 100%;
                padding: 40px;
            }
            h1 {
                color: #333;
                margin-bottom: 10px;
                font-size: 2.5em;
            }
            .subtitle {
                color: #666;
                margin-bottom: 30px;
                font-size: 1.1em;
            }
            .section {
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 1px solid #eee;
            }
            .section:last-child {
                border-bottom: none;
            }
            h2 {
                color: #667eea;
                margin-bottom: 15px;
                font-size: 1.5em;
            }
            .link-group {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            a {
                display: inline-block;
                padding: 12px 20px;
                background: #667eea;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                transition: all 0.3s;
                width: fit-content;
            }
            a:hover {
                background: #764ba2;
                transform: translateX(5px);
            }
            .code-block {
                background: #f5f5f5;
                padding: 15px;
                border-radius: 5px;
                margin-top: 10px;
                font-family: 'Courier New', monospace;
                overflow-x: auto;
                font-size: 0.9em;
            }
            .status {
                display: inline-block;
                background: #4CAF50;
                color: white;
                padding: 5px 10px;
                border-radius: 3px;
                font-weight: bold;
                margin-bottom: 20px;
            }
            .endpoints {
                display: grid;
                gap: 15px;
            }
            .endpoint {
                background: #f9f9f9;
                padding: 15px;
                border-left: 4px solid #667eea;
                border-radius: 3px;
            }
            .endpoint-method {
                display: inline-block;
                background: #667eea;
                color: white;
                padding: 3px 8px;
                border-radius: 3px;
                font-weight: bold;
                margin-right: 10px;
                font-size: 0.85em;
            }
            .endpoint-path {
                font-family: monospace;
                color: #333;
                font-weight: 500;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✓ Excel Data Extraction API</h1>
            <p class="subtitle">Your API Server is Running Successfully!</p>
            
            <div class="status">✓ Server Status: ONLINE</div>
            
            <div class="section">
                <h2>📚 Quick Access</h2>
                <div class="link-group">
                    <a href="/docs">📖 Interactive API Documentation (Swagger UI)</a>
                    <a href="/redoc">📘 Alternative API Documentation (ReDoc)</a>
                    <a href="/api/info">ℹ️ API Information</a>
                    <a href="/api/health">❤️ Health Check</a>
                </div>
            </div>
            
            <div class="section">
                <h2>🔌 Available Endpoints</h2>
                <div class="endpoints">
                    <div class="endpoint">
                        <span class="endpoint-method">POST</span>
                        <span class="endpoint-path">/api/extract</span>
                        <p style="margin-top: 8px; color: #666; font-size: 0.9em;">Extract data from Excel files using templates</p>
                    </div>
                    <div class="endpoint">
                        <span class="endpoint-method">GET</span>
                        <span class="endpoint-path">/api/health</span>
                        <p style="margin-top: 8px; color: #666; font-size: 0.9em;">Check if the API is running</p>
                    </div>
                    <div class="endpoint">
                        <span class="endpoint-method">GET</span>
                        <span class="endpoint-path">/api/info</span>
                        <p style="margin-top: 8px; color: #666; font-size: 0.9em;">Get API information and available endpoints</p>
                    </div>
                </div>
            </div>
            
            <div class="section">
                <h2>🚀 Getting Started</h2>
                <p>To make a request to the API:</p>
                <div class="code-block">
curl -X POST http://localhost:8000/api/extract \\
  -H "Content-Type: application/json" \\
  -d '{
    "token_id": "token_123",
    "source_file_name": "your_file.xlsx",
    "template_name": "your_template.json"
  }'
                </div>
            </div>
            
            <div class="section">
                <h2>❓ Need Help?</h2>
                <p>Visit the <strong>Swagger UI</strong> for interactive API testing and full documentation.</p>
                <p style="margin-top: 10px; color: #666; font-size: 0.9em;">The API server is bound to <code>0.0.0.0:8000</code>, which means it listens on all network interfaces. Access it via:</p>
                <ul style="margin-left: 20px; margin-top: 10px; color: #666;">
                    <li><code>http://localhost:8000</code> (local machine)</li>
                    <li><code>http://127.0.0.1:8000</code> (local machine)</li>
                    <li><code>http://YOUR_IP:8000</code> (from other machines)</li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """


@app.post("/api/extract", response_model=ExtractionResponse)
async def rest_extract(request: ExtractionRequest) -> ExtractionResponse:
    """
    REST API endpoint for data extraction
    
    Parameters:
    - token_id: Unique identifier for the extraction job
    - source_file_name: Name of source file in SOURCE directory
    - template_name: Name of template file in TEMPLATE directory
    
    Returns:
    - Extracted data with status and file names
    """
    logger.info(f"REST API - Extract request received for token: {request.token_id}")
    
    try:
        # Validate files exist
        source_file = get_source_file(request.source_file_name)
        if not source_file:
            logger.error(f"Source file not found: {request.source_file_name}")
            raise HTTPException(
                status_code=404,
                detail=f"Source file '{request.source_file_name}' not found in SOURCE directory"
            )
        
        template_file = get_template_file(request.template_name)
        if not template_file:
            logger.error(f"Template file not found: {request.template_name}")
            raise HTTPException(
                status_code=404,
                detail=f"Template file '{request.template_name}' not found in TEMPLATE directory"
            )
        
        logger.info(f"Files validated. Processing extraction...")
        
        # Load and process data
        grid = to_grid(source_file)
        template_json = json.load(open(template_file, encoding="utf-8"))
        
        # Apply template
        extraction_result = apply_template(template_json, grid)
        
        # Prepare result file name
        result_file_name = f"{source_file.stem}_result.json"
        
        logger.info(f"Extraction successful for token: {request.token_id}")
        
        return ExtractionResponse(
            status="success",
            token_id=request.token_id,
            result_file_name=result_file_name,
            data=extraction_result,
            message=f"Data extraction completed successfully. {len(extraction_result.get('sections', {}))} sections processed."
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during extraction: {str(e)}", exc_info=True)
        return ExtractionResponse(
            status="error",
            token_id=request.token_id,
            error_message=str(e),
            message="Extraction failed"
        )


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "message": "API server is running"
    }


@app.get("/api/info")
async def api_info():
    """Get API information"""
    return {
        "api_name": "Excel Data Extraction API",
        "version": "1.0.0",
        "endpoints": {
            "rest": "/api/extract (POST)",
            "soap": "/soap (SOAP Service)",
            "health": "/api/health (GET)",
            "info": "/api/info (GET)"
        },
        "description": "Provides REST and SOAP endpoints for extracting data from Excel files using templates"
    }


# ─────────── SOAP API SETUP ─────────── #

# Define SOAP complex types
class ExtractionRequestSOAP(ComplexModel):
    """SOAP Request type"""
    __namespace__ = "extraction.api.excel"
    
    token_id = Unicode(min_occurs=1, min_len=1)
    source_file_name = Unicode(min_occurs=1, min_len=1)
    template_name = Unicode(min_occurs=1, min_len=1)


class ExtractionResponseSOAP(ComplexModel):
    """SOAP Response type"""
    __namespace__ = "extraction.api.excel"
    
    status = Unicode
    token_id = Unicode
    result_file_name = Unicode(min_occurs=0)
    error_message = Unicode(min_occurs=0)
    message = Unicode
    result_summary = Unicode(min_occurs=0)


class ExtractionService(ServiceBase):
    """SOAP Service for data extraction"""
    
    @rpc(ExtractionRequestSOAP, _returns=ExtractionResponseSOAP)
    def extract_data(ctx, request):
        """
        SOAP RPC method for data extraction
        
        Parameters:
        - request: ExtractionRequestSOAP object containing token_id, source_file_name, template_name
        
        Returns:
        - ExtractionResponseSOAP object with extraction results
        """
        logger.info(f"SOAP API - Extract request received for token: {request.token_id}")
        
        try:
            # Validate files exist
            source_file = get_source_file(request.source_file_name)
            if not source_file:
                logger.error(f"Source file not found: {request.source_file_name}")
                raise Exception(f"Source file '{request.source_file_name}' not found in SOURCE directory")
            
            template_file = get_template_file(request.template_name)
            if not template_file:
                logger.error(f"Template file not found: {request.template_name}")
                raise Exception(f"Template file '{request.template_name}' not found in TEMPLATE directory")
            
            logger.info(f"Files validated. Processing SOAP extraction...")
            
            # Load and process data
            grid = to_grid(source_file)
            template_json = json.load(open(template_file, encoding="utf-8"))
            
            # Apply template
            extraction_result = apply_template(template_json, grid)
            
            # Prepare result file name
            result_file_name = f"{source_file.stem}_result.json"
            
            # Create summary
            sections_count = len(extraction_result.get('sections', {}))
            groups_count = len(extraction_result.get('groups', {}))
            summary = f"Processed {sections_count} sections and {groups_count} groups"
            
            logger.info(f"SOAP extraction successful for token: {request.token_id}")
            
            return ExtractionResponseSOAP(
                status="success",
                token_id=request.token_id,
                result_file_name=result_file_name,
                message="Data extraction completed successfully",
                result_summary=summary
            )
        
        except Exception as e:
            logger.error(f"Error during SOAP extraction: {str(e)}", exc_info=True)
            return ExtractionResponseSOAP(
                status="error",
                token_id=request.token_id,
                error_message=str(e),
                message="Extraction failed"
            )
    
    @rpc(_returns=Unicode)
    def get_service_info(ctx):
        """Get SOAP service information"""
        logger.info("SOAP API - Info request received")
        return "Excel Data Extraction SOAP Service v1.0"


# Create SOAP application
soap_application = Application(
    [ExtractionService],
    tns='extraction.api.excel',
    in_protocol=Soap11(validator='lxml'),
    out_protocol=Soap11()
)

# Create WSGI application for SOAP
soap_wsgi = WsgiApplication(soap_application)


# Mount SOAP application to FastAPI
@app.post("/soap")
@app.get("/soap")
async def soap_handler(body: str = Body(...)):
    """SOAP endpoint - handles SOAP requests"""
    # This is a workaround as spyne uses WSGI
    # In production, consider running spyne separately or using proper WSGI integration
    logger.info("SOAP request received")
    raise HTTPException(
        status_code=501,
        detail="SOAP endpoint requires WSGI runner. Run with: gunicorn -w 1 api_server:soap_wsgi"
    )


# ─────────── HYBRID WSGI/ASGI WRAPPER ─────────── #

def create_hybrid_app():
    """
    Create a hybrid application that can run both ASGI (FastAPI) and WSGI (SOAP)
    For production use, consider:
    1. Running FastAPI with: uvicorn api_server:app --host 0.0.0.0 --port 8000
    2. Running SOAP with: gunicorn -w 1 -k spyne.server.wsgi.WsgiToAscgi api_server:soap_wsgi --bind 0.0.0.0:8001
    """
    return app


# ─────────── MAIN ENTRY POINT ─────────── #

if __name__ == "__main__":
    import uvicorn
    
    logger.info("=" * 80)
    logger.info("Starting Excel Data Extraction API Server...")
    logger.info("=" * 80)
    logger.info("")
    logger.info("🌐 ACCESS THE API:")
    logger.info("   • Home Page:        http://localhost:8000/")
    logger.info("   • API Docs (Swagger): http://localhost:8000/docs")
    logger.info("   • API Docs (ReDoc):   http://localhost:8000/redoc")
    logger.info("")
    logger.info("📡 REST API ENDPOINTS:")
    logger.info("   • POST /api/extract - Extract data from Excel")
    logger.info("   • GET  /api/health  - Health check")
    logger.info("   • GET  /api/info    - API information")
    logger.info("")
    logger.info("⚠️  NOTE: 0.0.0.0 is server-side bind address")
    logger.info("   Use http://localhost:8000 to access from your browser")
    logger.info("")
    logger.info("=" * 80)
    
    # Run FastAPI server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
