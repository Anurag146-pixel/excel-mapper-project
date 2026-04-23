"""
Excel Mapper — Template CRUD API
Run with:  uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Any, Optional, Union
import json
import pymysql
import os
from contextlib import contextmanager
from datetime import datetime


# ── Database connection ────────────────────────────────────────────────────────
@contextmanager
def get_connection():
    """Get a connection to MySQL database."""
    conn = pymysql.connect(
        host='localhost',
        user='root',
        password='root',
        database='excel_reader',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    try:
        yield conn
    finally:
        conn.close()

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Excel Mapper — Template API",
    description="CRUD endpoints for Excel extraction templates",
    version="1.0.0",
)

# Allow the HTML frontend (file:// or localhost) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ── Static Files ───────────────────────────────────────────────────────────────

@app.get("/", summary="Serve Excel Mapping Wizard HTML")
def serve_html():
    """Serve the Excel Mapping Wizard HTML page."""
    html_file = os.path.join(os.path.dirname(__file__), "index.html")
    return FileResponse(html_file, media_type="text/html")


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class TemplateCreate(BaseModel):
    template_name: str
    template: str


class TemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    template: Optional[str] = None


class TemplateOut(BaseModel):
    id:            int
    template_name: str
    template:      str
    created_at:    str


# ── Helpers ───────────────────────────────────────────────────────────────────

def row_to_dict(row: dict) -> dict:
    """Convert database row to dictionary, handling datetime conversion."""
    if row is None:
        return None
    result = dict(row)
    # Convert datetime objects to ISO format strings
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

# ── 1. GET /templates  — list all templates (summary) ─────────────────────────
@app.get("/templates", response_model=List[TemplateOut], summary="List all templates")
def list_templates():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, template_name, template, created_at FROM templates ORDER BY created_at DESC"
            )
            rows = cursor.fetchall()
    return [row_to_dict(r) for r in rows]


# ── 2. GET /templates/{id}  — fetch one template in full ──────────────────────
@app.get("/templates/{template_id}", response_model=TemplateOut, summary="Get template by ID")
def get_template(template_id: int):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, template_name, template, created_at FROM templates WHERE id = %s", (template_id,)
            )
            row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
    return row_to_dict(row)


# ── 3. POST /templates  — create a new template ───────────────────────────────
@app.post("/templates", response_model=TemplateOut, status_code=201, summary="Create template")
def create_template(payload: TemplateCreate):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO templates (template_name, template)
                       VALUES (%s, %s)""",
                    (payload.template_name, payload.template),
                )
                conn.commit()
                new_id = cursor.lastrowid
                cursor.execute(
                    "SELECT id, template_name, template, created_at FROM templates WHERE id = %s", (new_id,)
                )
                row = cursor.fetchone()
        return row_to_dict(row)
    except pymysql.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Template name '{payload.template_name}' already exists")


# ── 4. PUT /templates/{id}  — full or partial update ─────────────────────────
@app.put("/templates/{template_id}", response_model=TemplateOut, summary="Update template")
def update_template(template_id: int, payload: TemplateUpdate):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, template_name, template, created_at FROM templates WHERE id = %s", (template_id,)
            )
            existing = cursor.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

            # Merge: use payload value if provided, else keep existing
            template_name = payload.template_name if payload.template_name is not None else existing["template_name"]
            template = payload.template if payload.template is not None else existing["template"]

            cursor.execute(
                """UPDATE templates
                   SET template_name = %s, template = %s
                   WHERE id = %s""",
                (template_name, template, template_id),
            )
            conn.commit()
            cursor.execute(
                "SELECT id, template_name, template, created_at FROM templates WHERE id = %s", (template_id,)
            )
            row = cursor.fetchone()
    return row_to_dict(row)


# ── 5. DELETE /templates/{id}  — remove a template ───────────────────────────
@app.delete("/templates/{template_id}", summary="Delete template")
def delete_template(template_id: int):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, template_name FROM templates WHERE id = %s", (template_id,)
            )
            existing = cursor.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
            cursor.execute("DELETE FROM templates WHERE id = %s", (template_id,))
            conn.commit()
    return {"message": f"Template '{existing['template_name']}' (id={template_id}) deleted successfully"}


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", summary="Health check")
def health():
    return {"status": "ok", "db": "mysql"}
