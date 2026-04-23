# Excel Data Extraction API

## Overview

This is an independent API server (`ext_api.py`) that provides a **modern REST API** endpoint for extracting data from Excel files using templates. It leverages the existing extraction logic in `ext.py` without modifying it.

**Note:** The `ext.py` file remains **untouched** - this is a separate wrapper that provides API access to its functionality.

---

## Features

✅ **REST API** (JSON-based, industry standard)
- Easy to use with standard HTTP clients
- Automatic OpenAPI/Swagger documentation
- Interactive API explorer at `/docs`
- Works with all programming languages

✅ **No Modifications to ext.py**
- Independent file (`ext_api.py`)
- Pure wrapper around existing functions
- Can run alongside existing extraction workflow

---

## Installation

### 1. Install Dependencies

```bash
pip install -r api_requirements.txt
```

Or install individually:

```bash
pip install fastapi uvicorn pydantic requests openpyxl pymysql lxml
```

### 2. Verify ext.py Configuration

Make sure `ext.py` paths are correctly set:

```python
BASE = Path(r"D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader")
SOURCE_DIR = BASE / "SOURCE"        # Input Excel files
TEMPLATE_DIR = BASE / "TEMPLATE"    # Template JSON files
RESULT_DIR = BASE / "RESULT"        # Output JSON results
```

---

## Running the Server

### Single Command - REST API Only

```bash
python ext_api.py
```

Or with more control:

```bash
python -m uvicorn ext_api:app --host 0.0.0.0 --port 8000 --reload
```

The server will start at: **http://localhost:8000**

---

## API Documentation

### Base URLs

| Protocol | URL | Docs |
|----------|-----|------|
| REST | `http://localhost:8000` | `http://localhost:8000/docs` |

---

## REST API Endpoints

### 1. Extract Data

**Endpoint:** `POST /api/extract`

**Description:** Extract data from Excel file using a template

**Request Body:**

```json
{
  "token_id": "unique_token_12345",
  "source_file_name": "student_list.xlsx",
  "template_name": "TEMPSTU.json"
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `token_id` | string | ✓ | Unique identifier for this extraction job |
| `source_file_name` | string | ✓ | Name of file in `SOURCE_DIR` (e.g., "student.xlsx") |
| `template_name` | string | ✓ | Name of template in `TEMPLATE_DIR` (e.g., "TEMPSTU.json") |

**Success Response (200):**

```json
{
  "status": "success",
  "token_id": "unique_token_12345",
  "result_file_name": "student_list_result.json",
  "message": "Data extraction completed successfully. 3 sections processed.",
  "data": {
    "sections": {
      "section_1": {...},
      "section_2": {...}
    },
    "groups": {
      "group_1": {...}
    }
  }
}
```

**Error Response (4xx/5xx):**

```json
{
  "status": "error",
  "token_id": "unique_token_12345",
  "error_message": "Source file 'invalid.xlsx' not found in SOURCE directory",
  "message": "Extraction failed"
}
```

---

### 2. Health Check

**Endpoint:** `GET /api/health`

**Description:** Verify API is running

**Response:**

```json
{
  "status": "healthy",
  "message": "API server is running"
}
```

---

### 3. API Information

**Endpoint:** `GET /api/info`

**Description:** Get API details and available endpoints

**Response:**

```json
{
  "api_name": "Excel Data Extraction API",
  "version": "1.0.0",
  "protocol": "REST (JSON-based)",
  "endpoints": {
    "extract": "/api/extract (POST)",
    "health": "/api/health (GET)",
    "info": "/api/info (GET)",
    "docs": "/docs (Swagger UI)",
    "redoc": "/redoc (ReDoc)"
  },
  "description": "Provides REST API endpoint for extracting data from Excel files using templates"
}
```

---

## Usage Examples

### Example 1: Using cURL

```bash
curl -X POST "http://localhost:8000/api/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "token_id": "curl_test_001",
    "source_file_name": "EMB_improvedexample_2025.xlsx",
    "template_name": "TEMPEX.json"
  }'
```

### Example 2: Using Python

```python
import requests
import json

url = "http://localhost:8000/api/extract"
payload = {
    "token_id": "python_test_001",
    "source_file_name": "student_list.xlsx",
    "template_name": "TEMPSTU.json"
}

response = requests.post(url, json=payload)
result = response.json()

print(f"Status: {result['status']}")
print(f"Result file: {result['result_file_name']}")
print(f"Data: {json.dumps(result['data'], indent=2)}")
```

### Example 3: Using JavaScript/Node.js

```javascript
const axios = require('axios');

async function extractData() {
  try {
    const response = await axios.post('http://localhost:8000/api/extract', {
      token_id: 'js_test_001',
      source_file_name: 'student_list.xlsx',
      template_name: 'TEMPSTU.json'
    });
    
    console.log('Status:', response.data.status);
    console.log('Result File:', response.data.result_file_name);
    console.log('Data:', JSON.stringify(response.data.data, null, 2));
  } catch (error) {
    console.error('Error:', error.response.data);
  }
}

extractData();
```

### Example 4: Using Postman

1. Open Postman
2. Select `POST` method
3. Enter URL: `http://localhost:8000/api/extract`
4. Go to **Body** tab, select **raw** → **JSON**
5. Paste:
```json
{
  "token_id": "postman_test_001",
  "source_file_name": "student_list.xlsx",
  "template_name": "TEMPSTU.json"
}
```
6. Click **Send**

---

## Interactive API Documentation

### Swagger UI (Recommended)

Open in browser: **http://localhost:8000/docs**

- Try out any endpoint directly
- See request/response schemas
- View parameter descriptions

### ReDoc

Open in browser: **http://localhost:8000/redoc**

- Beautiful documentation layout
- Search capabilities
- Offline-friendly

---

## Configuration

### Changing Port Numbers

**REST API on different port:**

```bash
python -m uvicorn ext_api:app --host 0.0.0.0 --port 9000
```

### Enabling HTTPS (Production)

```bash
python -m uvicorn ext_api:app --host 0.0.0.0 --port 8000 \
  --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

### Multiple Worker Processes (Production)

```bash
python -m uvicorn ext_api:app --workers 4 --host 0.0.0.0 --port 8000
```

### Adding API Key Authentication

Edit `ext_api.py` to add:

```python
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key")

@app.post("/api/extract")
async def rest_extract(request: ExtractionRequest, api_key: str = Depends(api_key_header)):
    if api_key != "your_secret_key":
        raise HTTPException(status_code=403, detail="Invalid API key")
    # ... rest of function
```

Then call with:

```bash
curl -X POST "http://localhost:8000/api/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_secret_key" \
  -d '{...}'
```

---

## Monitoring & Logging

All API calls are logged with timestamps and status. Check the console output for:

```
2026-04-13 10:30:45 - INFO - REST API - Extract request received for token: token_001
2026-04-13 10:30:45 - INFO - Files validated. Processing extraction...
2026-04-13 10:30:46 - INFO - Extraction successful for token: token_001
```

---

## Troubleshooting

### Issue: `ModuleNotFoundError: No module named 'fastapi'`

**Solution:**

```bash
pip install -r api_requirements.txt
```

### Issue: `Source file not found in SOURCE directory`

**Solution:** Ensure the file exists in:
```
D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader\SOURCE\
```

### Issue: `Template file not found in TEMPLATE directory`

**Solution:** Ensure the template exists in:
```
D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader\TEMPLATE\
```

### Issue: Connection refused on port 8000

**Solution:** Port might be in use. Try different port:
```bash
python -m uvicorn ext_api:app --host 0.0.0.0 --port 8001
```

### Issue: API starts but extraction fails

**Solution:** Check that `ext.py` is in the same directory as `ext_api.py` and verify paths:
```python
# In ext.py
BASE = Path(r"D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader")
```

---

## Architecture

```
┌─────────────────────────────────────────┐
│   Client Requests (REST - JSON)         │
└────────────┬────────────────────────────┘
             │
      ┌──────▼──────┐
      │  REST API   │
      │ (FastAPI)   │
      └──────┬──────┘
             │
    ┌────────▼────────┐
    │   ext_api.py    │ (Wrapper)
    │  - REST handler │
    │  - Validation   │
    │  - Logging      │
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │   ext.py        │ (Original - UNCHANGED)
    │ - extract()     │
    │ - to_grid()     │
    │ - apply_template│
    │ - etc...        │
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │  File System    │
    │ - SOURCE/       │
    │ - TEMPLATE/     │
    │ - RESULT/       │
    └─────────────────┘
```

---

## Performance

- **Extraction Speed:** Depends on file size and template complexity
- **Concurrent Requests:** FastAPI handles multiple requests efficiently
- **Memory:** Reasonable for files up to ~100MB
- **Scaling:** Use multiple workers for production:

```bash
python -m uvicorn ext_api:app --workers 4
```

---

## Support & Documentation

- **OpenAPI/Swagger**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Status**: http://localhost:8000/api/health

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-04-13 | Initial release - REST API only |

---

## Notes

- ✅ `ext.py` is **NOT** modified - completely independent
- ✅ REST API is modern, lightweight, and widely supported
- ✅ Results are saved to `RESULT_DIR` as JSON files
- ✅ All requests are logged for debugging
- ✅ Supports concurrent requests (thread-safe)
- ✅ Easy integration with any programming language or platform
