import os
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime
import shutil

class ExcelTemplateMapper:
    def __init__(self, db_path='excel_reader.db', result_folder='results'):
        self.db_path = db_path
        self.result_folder = result_folder
        self.processed_suffix = '_its_done'
        
        # Create results folder if it doesn't exist
        Path(self.result_folder).mkdir(exist_ok=True)
    
    def get_extraction_records(self):
        """Fetch all records from excel_reader.extraction table"""
        try:
            conn = sqlite3.connect(self.db_path)
            connection = conn.cursor()
            connection.execute("SELECT * FROM excel_reader.extraction")
            
            columns = [description[0] for description in connection.description]
            records = connection.fetchall()
            conn.close()
            
            return [dict(zip(columns, record)) for record in records]
        except Exception as e:
            print(f"Error fetching records: {e}")
            return []
    
    def get_template(self, template_id):
        """Fetch template from database by ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            connection = conn.cursor()
            connection.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
            
            columns = [description[0] for description in connection.description]
            record = connection.fetchone()
            conn.close()
            
            if record:
                return dict(zip(columns, record))
            return None
        except Exception as e:
            print(f"Error fetching template: {e}")
            return None
    
    def is_already_processed(self, file_path):
        """Check if file has already been processed (has _its_done suffix)"""
        file_name = Path(file_path).stem
        return self.processed_suffix in file_name
    
    def add_processed_suffix(self, file_path):
        """Add _its_done suffix to processed file"""
        try:
            path = Path(file_path)
            new_name = f"{path.stem}{self.processed_suffix}{path.suffix}"
            new_path = path.parent / new_name
            path.rename(new_path)
            print(f"Renamed: {file_path} → {new_path}")
            return str(new_path)
        except Exception as e:
            print(f"Error renaming file: {e}")
            return file_path
    
    def read_source_file(self, file_path):
        """Read source file (Excel or CSV)"""
        try:
            if file_path.endswith(('.xlsx', '.xls')):
                return pd.read_excel(file_path)
            elif file_path.endswith('.csv'):
                return pd.read_csv(file_path)
            else:
                print(f"Unsupported file format: {file_path}")
                return None
        except Exception as e:
            print(f"Error reading file {file_path}: {e}")
            return None
    
    def apply_template(self, data, template_content):
        """Apply template to data"""
        try:
            result_data = data.copy()
            return result_data
        except Exception as e:
            print(f"Error applying template: {e}")
            return None
    
    def save_result(self, data, output_name):
        """Save processed data to result folder"""
        try:
            output_path = Path(self.result_folder) / f"{output_name}_result.xlsx"
            data.to_excel(output_path, index=False)
            print(f"Result saved: {output_path}")
            return str(output_path)
        except Exception as e:
            print(f"Error saving result: {e}")
            return None
    
    def process_all_extractions(self):
        """Main function to process all extraction records"""
        records = self.get_extraction_records()
        
        if not records:
            print("No extraction records found!")
            return
        
        print(f"Found {len(records)} extraction records\n")
        
        for record in records:
            source_path = record.get('source_path')
            template_id = record.get('template_id')
            extraction_id = record.get('id')
            
            if not source_path:
                print(f"⚠️  Skipping record {extraction_id}: No source path")
                continue
            
            if self.is_already_processed(source_path):
                print(f"⏭️  Skipping (already processed): {source_path}")
                continue
            
            if not os.path.exists(source_path):
                print(f"❌ File not found: {source_path}")
                continue
            
            print(f"\n📄 Processing: {source_path}")
            
            source_data = self.read_source_file(source_path)
            if source_data is None:
                continue
            
            template = self.get_template(template_id) if template_id else None
            if template:
                print(f"   Template ID: {template_id}")
            
            processed_data = self.apply_template(source_data, template)
            if processed_data is None:
                continue
            
            file_name = Path(source_path).stem
            output_path = self.save_result(processed_data, file_name)
            
            self.add_processed_suffix(source_path)
            
            print(f"✅ Successfully processed extraction ID: {extraction_id}")

if __name__ == "__main__":
    DATABASE_PATH = r'd:\Users\Anurag\Applied_Cognition_Systems\2026\excel_reader.db'
    RESULT_FOLDER = r'd:\Users\Anurag\Applied_Cognition_Systems\2026\results'
    
    mapper = ExcelTemplateMapper(db_path=DATABASE_PATH, result_folder=RESULT_FOLDER)
    mapper.process_all_extractions()
    
    print("\n✨ Processing complete!")