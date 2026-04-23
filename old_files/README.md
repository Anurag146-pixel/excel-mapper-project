# Excel Mapper — Template Database

## Project Structure

```
excel_mapper/
├── index.html              ← Frontend (open in browser)
└── backend/
    ├── main.py             ← FastAPI app with CRUD endpoints
    ├── database.py         ← SQLite setup
    ├── requirements.txt    ← Python dependencies
    └── templates.db        ← Auto-created on first run
```

---

## Quick Start

### 1. Install dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 2. Start the backend
```bash
cd backend
uvicorn main:app --reload --port 8000
```

### 3. Open the frontend
Open `index.html` in your browser.  
The green status bar at the top confirms the API is connected.

---

## API Endpoints

| Method | Endpoint              | Description                  |
|--------|-----------------------|------------------------------|
| GET    | `/templates`          | List all templates           |
| GET    | `/templates/{id}`     | Get one template by ID       |
| POST   | `/templates`          | Create a new template        |
| PUT    | `/templates/{id}`     | Update an existing template  |
| DELETE | `/templates/{id}`     | Delete a template            |
| GET    | `/health`             | Health / connectivity check  |

### Interactive API Docs
Once the server is running, visit:
- Swagger UI:  http://localhost:8000/docs
- ReDoc:       http://localhost:8000/redoc

---

## POST /templates — Request Body
```json
{
  "name": "Q3_Financial_Report",
  "sections": [ ... ],
  "groups":   [ ... ]
}
```

## PUT /templates/{id} — Partial update supported
Only send the fields you want to change.

---

## Notes
- The SQLite file (`templates.db`) is created automatically in the `backend/` folder.
- To migrate to PostgreSQL later, only `database.py` needs to change.
- CORS is open (`*`) for local development — tighten in production.
