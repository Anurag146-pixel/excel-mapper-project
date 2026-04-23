import pymysql
import json
import csv
import uuid
from pathlib import Path
import openpyxl

# PATHS
BASE = Path(r"D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader")
SOURCE_DIR = BASE / "SOURCE"
TEMPLATE_DIR = BASE / "TEMPLATE"
RESULT_DIR = BASE / "RESULT"

# DB CONFIG
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "database": "excel_reader",
    "cursorclass": pymysql.cursors.DictCursor
}

# ───────── FILE HELPERS ───────---- #

def get_source_file(filename):
    fp = SOURCE_DIR / filename
    return fp if fp.exists() else None

def get_template_file(template_name):
    fp = TEMPLATE_DIR / template_name
    return fp if fp.exists() else None

def to_grid(fp):
    if fp.suffix.lower() == ".csv":
        return list(csv.reader(open(fp, encoding="utf-8-sig", errors="replace")))

    wb = openpyxl.load_workbook(fp, data_only=True)
    ws = wb.active
    grid = [[str(c).strip() if c else "" for c in r] for r in ws.iter_rows(values_only=True)]
    
    print(f"     Loaded Excel file: {fp.name}")
    print(f"     Grid dimensions: {len(grid)} rows x {len(grid[0]) if grid else 0} cols")
    print(f"     First 10 rows:")
    for i, row in enumerate(grid[:10]):
        print(f"       Row {i}: {row[:15]}")  # Show first 15 cols
    
    return grid

# ───────── DB OPS ───────── #

def update_status(token_id, status, source=None, result=None):
    conn = pymysql.connect(**DB_CONFIG)
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE token_details
            SET status=%s,
                source_file_name=COALESCE(%s, source_file_name),
                result_file_name=COALESCE(%s, result_file_name)
            WHERE token_id=%s
        """, (status, source, result, token_id))
    conn.commit()
    conn.close()

# ───────── EXTRACTION HELPERS ───────── #

def find_keyword(grid, keyword):
    if not keyword or not keyword.strip():
        return None, None
    
    kw = keyword.lower()
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            if kw in val.lower():
                print(f"     Keyword '{keyword}' found at row {r}, col {c}")
                return r, c
    
    print(f"     Keyword '{keyword}' NOT FOUND in grid")
    return None, None

# 🔥 NEW SMART FILTERS

def is_number(val):
    try:
        float(val)
        return True
    except:
        return False

def is_mostly_numeric(row):
    numeric = sum(1 for c in row if is_number(c))
    return numeric >= len(row) * 0.6

def is_valid_key(val):
    val = val.strip()

    if is_number(val):
        return False

    if len(val) <= 2:
        return False

    return True

# IMPROVED EXTRACTION 

def extract_key_values(grid, start_r, end_r):
    data = {}
    last_key = None

    for r in range(start_r, end_r):
        row = [c.strip() for c in grid[r] if c.strip()]

        if not row:
            continue

        # skip table rows
        if is_mostly_numeric(row):
            continue

        # skip header rows
        header_keywords = ["product", "qty", "rate", "amount", "mrp"]
        if sum(1 for c in row if any(h in c.lower() for h in header_keywords)) >= 3:
            continue

        i = 0
        while i < len(row):
            cell = row[i]

            # CASE 1: key with colon
            if ":" in cell:
                key = cell
                val = row[i+1] if i+1 < len(row) else ""

                if val:
                    data[key] = val
                    last_key = key

                i += 2
                continue

            # CASE 2: normal key-value
            if is_valid_key(cell):
                if i+1 < len(row):
                    val = row[i+1]

                    if not is_valid_key(val):
                        key = cell

                        if key in data:
                            count = 2
                            while f"{key}_{count}" in data:
                                count += 1
                            key = f"{key}_{count}"

                        data[key] = val
                        last_key = key
                        i += 2
                        continue

            # CASE 3: attach loose values
            if last_key and not is_valid_key(cell):
                data[last_key] += f" {cell}"

            i += 1

    return data

# ───────── TABLE DETECTION & PARSING ───────── #

def is_header_row(row, next_row=None):
    """Detect if a row looks like table headers"""
    if not row or len(row) < 2:
        return False
    
    # Headers are typically non-numeric and descriptive
    non_numeric = sum(1 for c in row if c and not is_number(c))
    has_length = sum(1 for c in row if c and len(str(c).strip()) > 1)
    
    return non_numeric >= len(row) * 0.7 and has_length >= len(row) * 0.6

def is_numeric_data_row(row):
    """Check if row contains actual data (numeric values present)"""
    if not row:
        return False
    numeric_count = sum(1 for c in row if is_number(c))
    return numeric_count >= len(row) * 0.4  # At least 40% numeric

def find_best_header_row(grid, start_r, end_r):
    """
    Intelligently find the header row by looking for:
    1. A row with high non-numeric descriptive content
    2. Followed by rows with numeric data
    Returns (header_row, header_row_idx, data_start_idx)
    """
    candidate_headers = []
    
    for r in range(start_r, min(end_r, len(grid))):
        row = [str(c).strip() if c else "" for c in grid[r]]
        row_clean = [c for c in row if c]
        
        if is_header_row(row_clean):
            candidate_headers.append((r, row_clean))
    
    if not candidate_headers:
        # Fallback: use first non-empty row
        for r in range(start_r, min(end_r, len(grid))):
            row = [str(c).strip() if c else "" for c in grid[r]]
            row_clean = [c for c in row if c]
            if row_clean:
                return row_clean, r, r + 1
        return None, None, None
    
    # Return the last header row found (closest to actual data)
    # candidate_headers contains (row_index, row_list) tuples
    header_idx, header_row = candidate_headers[-1]  # FIXED: correct unpacking
    return header_row, header_idx, header_idx + 1

# 🔍 DATA VALIDATION FUNCTIONS

def is_valid_name(val):
    """Check if value looks like a person's name"""
    if not val or len(val) < 1:
        return False
    # Names should have letters, not be purely numeric
    has_letters = any(c.isalpha() for c in val)
    # Allow if mostly letters or has spaces
    return has_letters

def is_valid_usn(val):
    """Check if value looks like a USN (University Serial Number)"""
    if not val:
        return False
    val_upper = str(val).upper()
    # USN pattern: 1EW24IS001, 1EW24IS002, etc. - Usually alphanumeric with mixed case
    if len(val_upper) >= 5:  # Minimum reasonable length
        has_letters = any(c.isalpha() for c in val_upper)
        has_digits = any(c.isdigit() for c in val_upper)
        return has_letters and has_digits
    return False

def is_valid_sl_no(val):
    """Check if value looks like a serial number"""
    if not val:
        return False
    try:
        num = float(str(val))
        return 1 <= num <= 500  # Reasonable range for student lists
    except:
        return False

def validate_record_coherence(record, headers):
    """
    Validate that extracted record makes sense based on column headers.
    Returns True if record is coherent/valid, False if garbage.
    """
    if not record:
        return False
    
    # Check critical columns
    for header, value in record.items():
        header_lower = header.lower()
        
        # NAMES column validation
        if 'name' in header_lower and 'USN' not in header_lower:
            if value and not is_valid_name(value):
                print(f"     ⚠ Invalid name value '{value}' for header '{header}'")
                return False
        
        # USN/ENROLLMENT column validation
        if 'usn' in header_lower or 'enrollment' in header_lower or 'roll' in header_lower:
            if value and not is_valid_usn(value):
                print(f"     ⚠ Invalid USN value '{value}' for header '{header}'")
                return False
        
        # Serial number validation
        if 'sl' in header_lower or 'no' in header_lower:
            if value and not is_valid_sl_no(value):
                print(f"     ⚠ Invalid SL.NO value '{value}' for header '{header}'")
                return False
    
    return True

# 🆕 TEMPLATE-RESPECTING TABLE EXTRACTION (NEW)

def extract_table_with_template_bounds(grid, sec):
    """
    Extract tabular data while STRICTLY respecting template boundaries.
    
    Parameters from template:
    - from_keyword: Start extraction here
    - until_keyword: Stop extraction here (HARD BOUNDARY)
    - skip_rows: Skip N rows after from_keyword before treating as header
    - skip_columns: Skip N columns from the left
    - extract_upto_rows: Maximum rows to extract (counting from first data row)
    - extract_upto_columns: Maximum columns to extract (counting from first column)
    """
    d = sec.get("data", {})
    end = sec.get("end_section", {})
    
    from_kw = d.get("from_keyword", "")
    until_kw = end.get("until_keyword", "")
    skip_rows = d.get("skip_rows", 0)
    skip_cols = d.get("skip_columns", 0)
    extract_upto_rows = end.get("extract_upto_rows")
    extract_upto_cols = end.get("extract_upto_columns")
    
    # Treat 0 as "unlimited" - convert to None for unlimited extraction
    if extract_upto_rows == 0:
        extract_upto_rows = None
    if extract_upto_cols == 0:
        extract_upto_cols = None
    
    print(f"\n     ┌─ TABLE EXTRACTION WITH TEMPLATE BOUNDS ─┐")
    print(f"     │ from_keyword: '{from_kw}'")
    print(f"     │ until_keyword: '{until_kw}'")
    print(f"     │ skip_rows: {skip_rows}")
    print(f"     │ skip_columns: {skip_cols}")
    print(f"     │ extract_upto_rows: {extract_upto_rows}")
    print(f"     │ extract_upto_cols: {extract_upto_cols}")
    print(f"     └──────────────────────────────────────────┘")
    
    # Step 1: Find start position
    start_r, start_c = find_keyword(grid, from_kw)
    if start_r is None:
        print(f"     ERROR: from_keyword '{from_kw}' not found")
        return {"records": [], "count": 0, "headers": []}
    
    # Step 2: Find end position
    end_r = len(grid)
    if until_kw:
        found_r, _ = find_keyword(grid, until_kw)
        if found_r is not None:
            end_r = found_r
            print(f"     until_keyword found at row {found_r} - HARD BOUNDARY SET")
    
    print(f"     Data range: rows {start_r} to {end_r}")
    
    # Step 3: Apply skip_rows (skip rows immediately after start_r)
    header_start_r = start_r + skip_rows + 1
    data_start_r = header_start_r + 1  # Data starts after header
    
    print(f"     After skip_rows ({skip_rows}): header at row {header_start_r}")
    
    # Step 4: Apply skip_columns
    start_c = max(start_c + skip_cols, 0) if start_c is not None else skip_cols
    
    # Step 5: Calculate end columns
    if extract_upto_cols is not None:
        end_c = start_c + extract_upto_cols
    else:
        end_c = None  # No limit
    
    # Step 6: Find header row using intelligent detection
    header_row = None
    header_row_idx = None
    data_start_r = None
    
    header_row, header_row_idx, data_start_r = find_best_header_row(grid, header_start_r, end_r)
    
    if header_row:
        print(f"     ✓ Best header row found at row {header_row_idx}: {header_row[:10]}")
        print(f"     ✓ Data extraction begins at row {data_start_r}")
    else:
        print(f"     WARNING: No header row found in range {header_start_r} to {end_r}")
        return {"records": [], "count": 0, "headers": []}
    
    # Step 7: Extract data rows, respecting both end_r and extract_upto_rows
    records = []
    data_rows_extracted = 0
    max_data_rows = extract_upto_rows if extract_upto_rows is not None else float('inf')
    
    for r in range(data_start_r, end_r):
        # Stop if we've extracted enough rows
        if data_rows_extracted >= max_data_rows:
            print(f"     ✓ Reached extract_upto_rows limit ({max_data_rows})")
            break
        
        if r >= len(grid):
            break
        
        full_row = grid[r]
        
        if end_c is not None:
            row_slice = full_row[start_c:end_c]
        else:
            row_slice = full_row[start_c:]
        
        clean_row = [str(c).strip() if c else "" for c in row_slice]
        clean_row = [c for c in clean_row if c]
        
        # Skip empty rows
        if not clean_row:
            continue
        
        # Convert to record
        record = {}
        for col_idx, header in enumerate(header_row):
            if col_idx < len(clean_row):
                value = clean_row[col_idx]
                if value:
                    record[header] = value
        
        # VALIDATE record coherence before adding
        if record and validate_record_coherence(record, header_row):
            records.append(record)
            data_rows_extracted += 1
        elif record:
            # Record failed validation - likely wrong data column alignment
            print(f"     ⊘ Skipped incoherent record at row {r} (misaligned columns?)")
            continue
    
    print(f"     ✓ Extracted {len(records)} records (stopped at boundary)")
    print(f"     ✓ Data range: rows {data_start_r} to {end_r}")
    print(f"     ✓ Column range: {start_c} to {end_c if end_c else 'unlimited'}")
    
    return {
        "records": records,
        "count": len(records),
        "headers": header_row
    }

def detect_table_mode(grid, start_r, end_r):
    """
    Detect if the data section contains a true table vs key-value pairs.
    
    True tables: Multiple unique headers (appear once), then many data rows
    Key-value: Same headers repeated (e.g., "PARTY NAME:" appears in many rows)
    """
    if start_r >= end_r or start_r >= len(grid):
        print(f"     DEBUG TABLE DETECT: start_r={start_r}, end_r={end_r}, len(grid)={len(grid)}")
        return False
    
    # Collect first N rows to analyze
    sample_rows = []
    for r in range(start_r, min(start_r + 20, end_r, len(grid))):
        row = [c.strip() for c in grid[r] if c.strip()]
        if row:
            sample_rows.append(row)
    
    if len(sample_rows) < 2:
        return False
    
    # Check pattern: is the first column value repeated across rows?
    first_col_values = [row[0] if row else "" for row in sample_rows]
    first_col_value_counts = {}
    for val in first_col_values:
        first_col_value_counts[val] = first_col_value_counts.get(val, 0) + 1
    
    # If first column value appears 3+ times, it's likely key-value pairs (header repeats)
    max_first_col_count = max(first_col_value_counts.values()) if first_col_value_counts else 0
    is_repeating_header = max_first_col_count >= 3
    
    print(f"     DEBUG TABLE DETECT: Analyzing {len(sample_rows)} sample rows")
    print(f"     DEBUG: First column values: {first_col_value_counts}")
    print(f"     DEBUG: Max first column repeat: {max_first_col_count}")
    print(f"     DEBUG: Is repeating header (key-value pattern): {is_repeating_header}")
    
    if is_repeating_header:
        print(f"     DEBUG: Detected as KEY-VALUE data (repeating first column header)")
        return False
    
    # Check if we have consistent numeric data pattern (true table indicator)
    numeric_rows = 0
    total_data_rows = 0
    
    for row in sample_rows[1:]:  # Skip potential header row
        if row:
            numeric_count = sum(1 for c in row if is_number(c))
            if numeric_count >= len(row) * 0.5:  # At least 50% numeric
                numeric_rows += 1
            total_data_rows += 1
    
    is_numeric_table = total_data_rows > 0 and numeric_rows / total_data_rows >= 0.5
    
    print(f"     DEBUG: Numeric rows: {numeric_rows}/{total_data_rows}")
    print(f"     DEBUG: Is numeric table: {is_numeric_table}")
    
    return is_numeric_table

def extract_table_as_kv(grid, start_r, end_r, start_c=0, end_c=None):
    """Extract table data and convert to structured key-value records"""
    if start_r >= len(grid):
        print(f"     DEBUG: start_r ({start_r}) >= len(grid) ({len(grid)})")
        return {}
    
    records = []
    header_row = None
    header_row_idx = None
    
    print(f"     DEBUG: Extracting from grid rows {start_r} to {end_r}")
    print(f"     DEBUG: Column range: {start_c} to {end_c}")
    print(f"     DEBUG: Total grid rows: {len(grid)}")
    
    # If start_c is None, start from 0
    if start_c is None:
        start_c = 0
    
    # First pass: find the header row (most likely to have many non-numeric/descriptive cells)
    for r in range(start_r, min(end_r, len(grid))):
        full_row = grid[r]
        
        # Apply column slicing for inspection
        if end_c is not None:
            row_slice = full_row[start_c:end_c]
        else:
            row_slice = full_row[start_c:]
        
        clean_row = [str(c).strip() if c else "" for c in row_slice]
        clean_row = [c for c in clean_row if c]  # Remove empty strings
        
        # Check if this looks like a header row
        if is_header_row(clean_row):
            header_row = clean_row
            header_row_idx = r
            print(f"     Found header row at row {r}: {header_row}")
            break
    
    # If no header row found, use the first non-empty row after start_r
    if header_row is None:
        for r in range(start_r, min(end_r, len(grid))):
            full_row = grid[r]
            if end_c is not None:
                row_slice = full_row[start_c:end_c]
            else:
                row_slice = full_row[start_c:]
            
            clean_row = [str(c).strip() if c else "" for c in row_slice]
            clean_row = [c for c in clean_row if c]
            
            if clean_row:
                header_row = clean_row
                header_row_idx = r
                print(f"     No clear header found, using row {r} as header: {header_row}")
                break
    
    if header_row is None:
        print(f"     WARNING: No header row found in range {start_r} to {end_r}")
        return {"records": [], "count": 0, "headers": []}
    
    # Second pass: extract data rows after the header
    for r in range(header_row_idx + 1, min(end_r, len(grid))):
        full_row = grid[r]
        
        if end_c is not None:
            row_slice = full_row[start_c:end_c]
        else:
            row_slice = full_row[start_c:]
        
        clean_row = [str(c).strip() if c else "" for c in row_slice]
        clean_row = [c for c in clean_row if c]  # Remove empty strings
        
        # Skip empty rows
        if not clean_row:
            continue
        
        # Convert row to record using headers
        record = {}
        for col_idx, header in enumerate(header_row):
            if col_idx < len(clean_row):
                value = clean_row[col_idx]
                if value:
                    record[header] = value
        
        if record:
            records.append(record)
    
    print(f"     Result: {len(records)} records extracted")
    return {
        "records": records,
        "count": len(records),
        "headers": header_row
    }

# ───────── SECTION EXTRACTION ───────── #

def extract_section(grid, sec):
    d = sec.get("data", {})
    end = sec.get("end_section", {})

    from_kw = d.get("from_keyword", "")
    until_kw = end.get("until_keyword", "")
    extract_rows = end.get("extract_upto_rows")
    extract_cols = end.get("extract_upto_columns")

    print(f"\n  Extract_section DEBUG:")
    print(f"     section_name: '{sec.get('section_name', 'UNKNOWN')}'")
    print(f"     from_keyword: '{from_kw}'")
    print(f"     until_keyword: '{until_kw}'")
    print(f"     extract_upto_rows: {extract_rows}, extract_upto_cols: {extract_cols}")
    print(f"     Total grid rows: {len(grid)}")

    start_r, start_c = find_keyword(grid, from_kw)
    
    print(f"     Keyword found at: start_r={start_r}, start_c={start_c}")
    
    if start_r is None:
        print(f"     ERROR: from_keyword not found in grid")
        return {}

    # Calculate end row based on keywords
    print(f"     Mode: SMART DETECTION")
    
    if until_kw:
        found_r, _ = find_keyword(grid, until_kw)
        print(f"     until_keyword '{until_kw}' found at row: {found_r}")
    else:
        found_r = None

    # Tentative end_r for key-value extraction
    end_r_base = (found_r + 1) if found_r is not None else len(grid)

    print(f"     Initial extraction range: rows {start_r} to {end_r_base}")

    # For table detection, we need to analyze beyond the until_keyword to see actual data
    # Include more rows to get a better sample of data for pattern detection
    detection_end_r = min(end_r_base + 50, len(grid))
    print(f"     Table detection analysis range: {start_r} to {detection_end_r} (includes data rows)")
    
    # Detect if this is tabular data
    is_table = detect_table_mode(grid, start_r, detection_end_r)
    print(f"     TABLE DETECTION RESULT: {is_table}")
    
    if is_table:
        print(f"     Detected: TABLE DATA (by pattern analysis)")
        print(f"     ✓ Using new TEMPLATE-RESPECTING table extraction")
        
        # Use new template-respecting function for tabular data
        result = extract_table_with_template_bounds(grid, sec)
        return result
    else:
        print(f"     Detected: KEY-VALUE DATA")
        print(f"     Extracting rows {start_r} to {end_r_base}")
        result = extract_key_values(grid, start_r, end_r_base)
        print(f"     Extracted data: {result}")
        return result

# ───────── GROUP ENGINE (UNCHANGED) ───────── #

def build_groups(template_json, section_data):
    groups_result = {}

    def resolve_children(children):
        result = {}

        for child in children:
            ctype = child.get("type")
            name = child.get("name")

            if ctype == "ref-section":
                data = section_data.get(name, {})

                if "children" in child:
                    nested = resolve_children(child["children"])
                    data = {
                        "data": data,
                        **nested
                    }

                result[name] = data

            elif ctype == "ref-group":
                result[name] = groups_result.get(name, {})

        return result

    for grp in template_json.get("groups", []):
        gname = grp.get("group_name")
        children = grp.get("children", [])

        groups_result[gname] = resolve_children(children)

    return groups_result

# ───────── TEMPLATE APPLY ───────── #

def apply_template(template_json, grid):
    result = {
        "sections": {},
        "groups": {}
    }

    print(f"\nAPPLY_TEMPLATE DEBUG:")
    print(f"   Template structure: {json.dumps(template_json, indent=2)[:500]}...")
    
    sections = template_json.get("sections", [])
    print(f"   Found {len(sections)} sections in template")

    for sec in sections:
        name = sec.get("section_name", "unknown")
        print(f"\n   Processing section: '{name}'")
        result["sections"][name] = extract_section(grid, sec)

    if "groups" in template_json:
        groups = template_json.get("groups", [])
        print(f"\n   Processing {len(groups)} groups")
        result["groups"] = build_groups(template_json, result["sections"])

    print(f"\n   Final result: {json.dumps(result, indent=2)[:300]}...")
    return result

# ───────── MAIN ENGINE ───────── #

def extract(job):
    token_id = job["token_id"]
    source_name = job["source_file_name"]
    template_name = job["template_name"]

    print(f"\nProcessing Token: {token_id}")

    try:
        update_status(token_id, "processing")

        src = get_source_file(source_name)
        tmpl = get_template_file(template_name)

        print(f"   Source file path: {src}")
        print(f"   Template file path: {tmpl}")

        if not src:
            raise Exception("Source not found")
        if not tmpl:
            raise Exception("Template not found")

        grid = to_grid(src)
        template_json = json.load(open(tmpl, encoding="utf-8"))

        result = apply_template(template_json, grid)

        RESULT_DIR.mkdir(exist_ok=True)

        # Generate unique ID (8-character hex string)
        unique_id = uuid.uuid4().hex[:8]
        result_name = f"{src.stem}_result_{unique_id}.json"
        result_path = RESULT_DIR / result_name

        with open(result_path, "w") as f:
            json.dump(result, f, indent=2)

        new_source_name = f"{src.stem}_done_{unique_id}{src.suffix}"
        src.rename(SOURCE_DIR / new_source_name)
        
        print(f"\n✅ File renamed: {src.name} → {new_source_name}")

        update_status(token_id, "done", new_source_name, result_name)

        print("SUCCESS - Processing complete")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        update_status(token_id, "failed")