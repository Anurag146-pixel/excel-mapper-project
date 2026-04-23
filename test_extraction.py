#!/usr/bin/env python3
"""
Test script to verify extraction logic without database dependency
"""
import json
import sys
from pathlib import Path

# Add current dir to path
sys.path.insert(0, str(Path(__file__).parent))

from ext import to_grid, apply_template

# Test file paths
BASE = Path(r"D:\Users\Anurag\Applied_Cognition_Systems\2026\Excel_reader")
SOURCE_DIR = BASE / "SOURCE"
TEMPLATE_DIR = BASE / "TEMPLATE"

def test_extraction():
    print("=" * 80)
    print("EXTRACTION TEST")
    print("=" * 80)
    
    # Test file details
    source_file = "Student list 2025-2026_Updated_28_Jan_2025.xlsx"
    template_file = "TEMP1.JSON"
    
    source_path = SOURCE_DIR / source_file
    template_path = TEMPLATE_DIR / template_file
    
    print(f"\nSource file: {source_path}")
    print(f"Exists: {source_path.exists()}")
    
    print(f"\nTemplate file: {template_path}")
    print(f"Exists: {template_path.exists()}")
    
    if not source_path.exists() or not template_path.exists():
        print("\nERROR: Files not found!")
        return
    
    # Load files
    print("\n" + "=" * 80)
    print("LOADING EXCEL FILE")
    print("=" * 80)
    grid = to_grid(source_path)
    
    print("\n" + "=" * 80)
    print("LOADING TEMPLATE")
    print("=" * 80)
    with open(template_path, encoding="utf-8") as f:
        template_json = json.load(f)
    print(f"Template: {json.dumps(template_json, indent=2)}")
    
    # Apply template
    print("\n" + "=" * 80)
    print("APPLYING TEMPLATE")
    print("=" * 80)
    result = apply_template(template_json, grid)
    
    print("\n" + "=" * 80)
    print("FINAL RESULT")
    print("=" * 80)
    print(json.dumps(result, indent=2))
    
    # Save result to file
    result_file = Path("test_result.json")
    with open(result_file, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResult saved to: {result_file}")

if __name__ == "__main__":
    test_extraction()
