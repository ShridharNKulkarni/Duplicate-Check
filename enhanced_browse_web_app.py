from flask import Flask, request, jsonify, render_template_string, send_file
import csv
import gzip
import os
import uuid
import threading
from werkzeug.utils import secure_filename
from datetime import datetime
import traceback
import base64

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size

# Store processing status and results
processing_status = {}
UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

def get_logo_base64():
    """Convert logo to base64 for embedding in HTML"""
    try:
        logo_path = os.path.join('Image', 'image (3).png')
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as f:
                logo_data = f.read()
                return base64.b64encode(logo_data).decode('utf-8')
    except Exception as e:
        print(f"Could not load logo: {e}")
    return None

def preprocess_invoice_data_browse(input_gz_path, output_csv_path, task_id):
    """
    Process CSV with browse interface requirements
    """
    try:
        print(f"=== STARTING PROCESSING FOR TASK {task_id} ===")
        print(f"Input file: {input_gz_path}")
        print(f"Output file: {output_csv_path}")
        # Required columns for output
        required_columns = [
            'invoice_creation_date', 'payee_name', 'primary_vendor_code',
            'barcode', 'invoice_status', 'header_po', 'invoice_no',
            'invoice_date', 'invoice_source_name', 'invoice_quantity',
            'invoice_amount'
        ]
        
        processing_status[task_id] = {
            'status': 'processing',
            'message': 'Reading and processing CSV file...',
            'progress': 10,
            'start_time': datetime.now()
        }
        
        processed_rows = []
        total_input_lines = 0
        lines_processed = 0
        
        # Read CSV from gzipped file
        with gzip.open(input_gz_path, 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            
            processing_status[task_id]['message'] = 'Applying filters and processing data...'
            processing_status[task_id]['progress'] = 30
            
            for i, row in enumerate(reader):
                total_input_lines += 1
                
                # Apply 3 filters
                invoice_status = (row.get('invoice_status', '') or '').lower()
                invoice_source = (row.get('invoice_source_name', '') or '').upper()
                invoice_no = (row.get('invoice_no', '') or '').upper()
                
                # Filter logic: exclude cancelled, Dropship, and SCR invoices
                if (invoice_status == 'cancelled' or 
                    invoice_source == 'DROPSHIP' or 
                    invoice_no.endswith('SCR')):
                    continue
                
                lines_processed += 1
                
                # Select only required columns
                filtered_row = {}
                for col in required_columns:
                    filtered_row[col] = row.get(col, '')
                
                processed_rows.append(filtered_row)
                
                # Update progress periodically
                if total_input_lines % 50000 == 0:
                    progress = 30 + min((total_input_lines / max(total_input_lines + 100000, 1)) * 40, 40)
                    processing_status[task_id]['message'] = f'Processed {total_input_lines:,} input lines...'
                    processing_status[task_id]['progress'] = int(progress)
        
        processing_status[task_id]['message'] = 'Applying transformations and creating CONCAT patterns...'
        processing_status[task_id]['progress'] = 75
        
        # Apply transformations
        for row in processed_rows:
            # Transform amount
            try:
                amount = float(row.get('invoice_amount', '0') or '0')
                amount_after_decimal = int(amount // 10)
                row['invoice_amount_after_removing_decimal'] = str(amount_after_decimal)
            except (ValueError, TypeError):
                row['invoice_amount_after_removing_decimal'] = '0'
            
            # Trim time portion from invoice_date and extract year
            original_date = row.get('invoice_date', '')
            trimmed_date = trim_date_format(original_date)
            year = extract_year_from_date(trimmed_date)
            
            # Update the invoice_date to the trimmed format (YYYY-MM-DD)
            row['invoice_date'] = trimmed_date
            
            # Add invoice_year as a separate column
            row['invoice_year'] = year
            
            # Create CONCAT columns with new names (using trimmed date)
            header_po = row.get('header_po', '')
            invoice_date = trimmed_date  # Use trimmed date format (YYYY-MM-DD)
            primary_vendor = row.get('primary_vendor_code', '')
            amount_str = row['invoice_amount_after_removing_decimal']
            
            row['CONCAT 1(Header PO + Invoice Date + Invoice Amount)'] = f"{header_po}{invoice_date}{amount_str}"
            row['CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount)'] = f"{primary_vendor}{year}{amount_str}"
            row['CONCAT 3(Header PO + Invoice Amount)'] = f"{header_po}{amount_str}"
        
        processing_status[task_id]['message'] = 'Detecting duplicates...'
        processing_status[task_id]['progress'] = 85
        
        # Count duplicates and add remarks
        concat1_counts = {}
        concat2_counts = {}
        concat3_counts = {}
        
        for row in processed_rows:
            concat1 = row['CONCAT 1(Header PO + Invoice Date + Invoice Amount)']
            concat2 = row['CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount)']
            concat3 = row['CONCAT 3(Header PO + Invoice Amount)']
            
            concat1_counts[concat1] = concat1_counts.get(concat1, 0) + 1
            concat2_counts[concat2] = concat2_counts.get(concat2, 0) + 1
            concat3_counts[concat3] = concat3_counts.get(concat3, 0) + 1
        
        # Add remarks and count duplicates
        concat1_duplicates = 0
        concat2_duplicates = 0
        concat3_duplicates = 0
        
        for row in processed_rows:
            # CONCAT 1 remarks
            if concat1_counts[row['CONCAT 1(Header PO + Invoice Date + Invoice Amount)']] > 1:
                row['CONCAT 1(Header PO + Invoice Date + Invoice Amount) Remarks'] = 'Duplicate'
                concat1_duplicates += 1
            else:
                row['CONCAT 1(Header PO + Invoice Date + Invoice Amount) Remarks'] = 'Non Duplicate'
            
            # CONCAT 2 remarks
            if concat2_counts[row['CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount)']] > 1:
                row['CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount) Remarks'] = 'Duplicate'
                concat2_duplicates += 1
            else:
                row['CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount) Remarks'] = 'Non Duplicate'
            
            # CONCAT 3 remarks
            if concat3_counts[row['CONCAT 3(Header PO + Invoice Amount)']] > 1:
                row['CONCAT 3(Header PO + Invoice Amount) Remarks'] = 'Duplicate'
                concat3_duplicates += 1
            else:
                row['CONCAT 3(Header PO + Invoice Amount) Remarks'] = 'Non Duplicate'
        
        processing_status[task_id]['message'] = 'Saving processed file...'
        processing_status[task_id]['progress'] = 95
        
        # Write output CSV
        output_columns = [
            'invoice_source_name', 'primary_vendor_code', 'payee_name',
            'invoice_status', 'invoice_creation_date', 'barcode',
            'header_po', 'invoice_no', 'invoice_date', 'invoice_year',
            'invoice_quantity', 'invoice_amount', 'invoice_amount_after_removing_decimal',
            'CONCAT 1(Header PO + Invoice Date + Invoice Amount)', 'CONCAT 1(Header PO + Invoice Date + Invoice Amount) Remarks',
            'CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount)', 'CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount) Remarks',
            'CONCAT 3(Header PO + Invoice Amount)', 'CONCAT 3(Header PO + Invoice Amount) Remarks'
        ]
        
        if processed_rows:
            first_row = processed_rows[0]
            available_columns = [col for col in output_columns if col in first_row]
            
            with open(output_csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=available_columns)
                writer.writeheader()
                for row in processed_rows:
                    filtered_row = {col: row.get(col, '') for col in available_columns}
                    writer.writerow(filtered_row)
        
        # Create summary
        summary = {
            'total_input_lines': total_input_lines,
            'lines_processed': lines_processed,
            'concat1_duplicates': concat1_duplicates,
            'concat2_duplicates': concat2_duplicates,
            'concat3_duplicates': concat3_duplicates
        }
        
        # Calculate processing time
        end_time = datetime.now()
        start_time = processing_status[task_id].get('start_time', end_time)
        processing_time = (end_time - start_time).total_seconds()
        processing_time_mins = round(processing_time / 60, 2)
        
        processing_status[task_id] = {
            'status': 'completed',
            'message': 'Processing completed successfully!',
            'progress': 100,
            'summary': summary,
            'output_file': output_csv_path,
            'processing_time_mins': processing_time_mins
        }
        
    except Exception as e:
        error_msg = f"Error during processing: {str(e)}"
        print(f"=== PROCESSING ERROR FOR TASK {task_id} ===")
        print(f"Error: {error_msg}")
        print("Full traceback:")
        print(traceback.format_exc())
        
        processing_status[task_id] = {
            'status': 'error',
            'message': error_msg,
            'progress': 0
        }

def extract_year_from_date(date_str):
    """Extract year from date string"""
    if not date_str:
        return ''
    
    try:
        # Handle ISO format like "2022-07-09T00:00:00.000Z"
        if 'T' in date_str:
            date_str = date_str.split('T')[0]  # Extract date part before 'T'
        
        for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S']:
            try:
                date_obj = datetime.strptime(date_str, fmt)
                return str(date_obj.year)
            except ValueError:
                continue
        return ''
    except:
        return ''

def trim_date_format(date_str):
    """Trim time portion from ISO date format, keep only YYYY-MM-DD"""
    if not date_str:
        return ''
    
    try:
        # Handle ISO format like "2022-07-09T00:00:00.000Z"
        if 'T' in date_str:
            date_str = date_str.split('T')[0]  # Extract date part before 'T'
        
        return date_str
    except:
        return date_str

# HTML template with enhanced UI and logo integration
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>P2P Duplicate Invoice Check - Pierian</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 25%, #404040 50%, #2d2d2d 75%, #1a1a1a 100%);
            color: #ffffff; min-height: 100vh; padding: 20px;
        }
        
        .main-header {
            display: flex; align-items: center; justify-content: space-between;
            background: rgba(45, 45, 45, 0.95); border-radius: 20px; padding: 25px 35px; 
            margin-bottom: 20px; box-shadow: 0 12px 35px rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(25px); border: 2px solid rgba(220, 53, 69, 0.6);
        }
        
        .logo-section {
            display: flex; align-items: center; gap: 25px;
        }
        
        .logo-container {
            width: 110px; height: auto; border-radius: 12px; overflow: hidden;
            box-shadow: 0 6px 20px rgba(220, 53, 69, 0.8);
            transition: transform 0.3s ease;
        }
        
        .logo-container:hover {
            transform: scale(1.05);
        }
        
        .logo-container img {
            width: 100%; height: auto; display: block;
        }
        
        .header-content {
            flex: 1; text-align: center;
        }
        
        .header-content h1 { 
            background: linear-gradient(45deg, #dc3545, #ffffff);
            background-clip: text; -webkit-background-clip: text;
            -webkit-text-fill-color: transparent; margin-bottom: 8px;
            font-size: 2.4rem; font-weight: 700;
        }
        
        .header-content p { 
            color: rgba(255, 255, 255, 0.9); font-size: 16px;
        }
        
        .container { max-width: 1000px; margin: 0 auto; }
        
        
        .card {
            background: rgba(45, 45, 45, 0.9); border-radius: 18px; padding: 30px; margin-bottom: 20px;
            box-shadow: 0 12px 35px rgba(0, 0, 0, 0.5); border: 2px solid rgba(220, 53, 69, 0.5);
            backdrop-filter: blur(25px);
        }
        
        .browse-section {
            border: 2px dashed rgba(220, 53, 69, 0.7); border-radius: 18px; padding: 40px;
            text-align: center; margin-bottom: 20px; transition: all 0.3s ease;
            background: rgba(220, 53, 69, 0.1);
        }
        
        .browse-section:hover { 
            border-color: rgba(220, 53, 69, 1); background: rgba(220, 53, 69, 0.2);
            transform: translateY(-3px); box-shadow: 0 8px 25px rgba(220, 53, 69, 0.4);
        }
        
        .browse-section h3 { color: #ffffff; margin-bottom: 10px; }
        .browse-section p { color: rgba(255, 255, 255, 0.8); margin-bottom: 20px; }
        
        .browse-btn {
            background: linear-gradient(45deg, #dc3545, #a71e2a); color: white; border: none; 
            padding: 15px 30px; border-radius: 12px; font-size: 16px; cursor: pointer; 
            margin-top: 15px; font-weight: 600; transition: all 0.3s ease;
            box-shadow: 0 6px 20px rgba(220, 53, 69, 0.6);
        }
        
        .browse-btn:hover { 
            transform: translateY(-3px); box-shadow: 0 12px 30px rgba(220, 53, 69, 0.9);
            background: linear-gradient(45deg, #e74c3c, #c82333);
        }
        
        .file-input { display: none; }
        
        .selected-file {
            background: rgba(220, 53, 69, 0.15); border: 2px solid rgba(220, 53, 69, 0.5); 
            border-radius: 12px; padding: 15px; margin: 15px 0; display: none;
        }
        
        .selected-file h4 { color: #dc3545; margin-bottom: 10px; }
        .selected-file div { color: rgba(255, 255, 255, 0.95); }
        
        .process-btn {
            background: linear-gradient(45deg, #dc3545, #a71e2a); color: white; border: none; 
            padding: 15px 40px; border-radius: 12px; font-size: 16px; cursor: pointer; 
            width: 100%; margin-top: 20px; display: none; font-weight: 600;
            transition: all 0.3s ease; box-shadow: 0 6px 20px rgba(220, 53, 69, 0.6);
        }
        
        .process-btn:hover { 
            transform: translateY(-3px); box-shadow: 0 12px 30px rgba(220, 53, 69, 0.9);
            background: linear-gradient(45deg, #e74c3c, #c82333);
        }
        
        .process-btn:disabled { 
            background: #555; cursor: not-allowed; transform: none; box-shadow: none;
        }
        
        .progress-section {
            background: rgba(255, 193, 7, 0.1); border: 1px solid rgba(255, 193, 7, 0.3); 
            border-radius: 10px; padding: 20px; margin: 20px 0; display: none;
        }
        
        .progress-section h4 { color: #ffc107; margin-bottom: 15px; }
        
        .progress-bar {
            width: 100%; height: 25px; background: rgba(255, 255, 255, 0.1); 
            border-radius: 12px; overflow: hidden; margin: 15px 0;
        }
        
        .progress-fill {
            height: 100%; background: linear-gradient(45deg, #dc3545, #a71e2a);
            width: 0%; transition: width 0.3s ease; border-radius: 12px;
        }
        
        .progress-text { text-align: center; font-weight: 600; color: #ffffff; }
        
        .summary-section {
            background: rgba(45, 45, 45, 0.9); border: 2px solid rgba(220, 53, 69, 0.6); 
            border-radius: 15px; padding: 25px; margin: 20px 0; display: none;
        }
        
        .summary-title { color: #ffffff; font-size: 20px; margin-bottom: 20px; text-align: center; }
        
        .summary-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px; margin-bottom: 25px;
        }
        
        .summary-item {
            background: rgba(220, 53, 69, 0.2); padding: 20px; border-radius: 12px; text-align: center;
            border: 1px solid rgba(220, 53, 69, 0.5);
        }
        
        .summary-number { 
            font-size: 28px; font-weight: bold; margin-bottom: 5px; color: #ffffff;
        }
        
        .summary-label { color: rgba(255, 255, 255, 0.8); font-size: 14px; }
        
        .download-section {
            background: rgba(45, 45, 45, 0.9); border: 2px solid rgba(220, 53, 69, 0.6); 
            border-radius: 15px; padding: 25px; text-align: center; display: none;
        }
        
        .download-section h3 { color: #ffffff; margin-bottom: 10px; }
        .download-section p { color: rgba(255, 255, 255, 0.8); margin-bottom: 15px; }
        
        .download-section .time-display {
            display: flex; align-items: center; justify-content: center; gap: 10px;
            margin: 15px 0; padding: 10px; 
            background: rgba(220, 53, 69, 0.1); border-radius: 8px;
        }
        
        .download-section .time-icon {
            font-size: 20px;
        }
        
        .download-section .time-text {
            color: #ffffff; font-size: 14px; font-weight: 500;
        }
        
        .download-section .time-text strong {
            color: #14f754; font-size: 16px;
        }
        
        .download-btn {
            background: linear-gradient(45deg, #dc3545, #a71e2a); color: white; 
            padding: 15px 40px; border: none; border-radius: 12px; font-size: 16px; 
            cursor: pointer; text-decoration: none; display: inline-block; margin-top: 15px;
            font-weight: 600; transition: all 0.3s ease;
            box-shadow: 0 6px 20px rgba(220, 53, 69, 0.6);
        }
        
        .download-btn:hover { 
            transform: translateY(-3px); box-shadow: 0 15px 35px rgba(220, 53, 69, 0.9);
            background: linear-gradient(45deg, #e74c3c, #c82333);
        }
        
        .error-section {
            background: rgba(255, 0, 0, 0.1); border: 1px solid rgba(255, 0, 0, 0.3); 
            border-radius: 10px; padding: 20px; margin: 20px 0; color: #ff6b6b; display: none;
        }
        
        .success-section {
            background: rgba(220, 53, 69, 0.15); border: 2px solid rgba(220, 53, 69, 0.5); 
            border-radius: 12px; padding: 20px; margin: 20px 0; color: #ffffff; display: none;
        }
        
        .glossary-section {
            background: rgba(45, 45, 45, 0.9); border: 2px solid rgba(220, 53, 69, 0.5); 
            border-radius: 18px; padding: 25px; margin-bottom: 20px;
            backdrop-filter: blur(25px);
        }
        
        .glossary-title {
            color: #ffffff; font-size: 24px; margin-bottom: 20px; text-align: center;
            font-weight: 700;
        }
        
        .glossary-content {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }
        
        .glossary-item {
            background: rgba(220, 53, 69, 0.15); padding: 15px; border-radius: 12px;
            border: 1px solid rgba(220, 53, 69, 0.4);
        }
        
        .glossary-item h4 {
            color: #ffffff; margin-bottom: 8px; font-weight: 600;
        }
        
        .glossary-item p {
            color: rgba(255, 255, 255, 0.8); font-size: 14px; line-height: 1.4;
        }
        
        .toggle-btn {
            background: rgba(220, 53, 69, 0.3); color: white; border: none;
            padding: 10px 20px; border-radius: 8px; cursor: pointer; margin-top: 10px;
            transition: all 0.3s ease; font-size: 14px;
        }
        
        .toggle-btn:hover {
            background: rgba(220, 53, 69, 0.5);
            transform: translateY(-1px);
        }
        
        .collapsed .glossary-content {
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="main-header">
            <div class="logo-section">
                {% if logo_data %}
                <div class="logo-container">
                    <img src="data:image/png;base64,{{ logo_data }}" alt="Pierian Logo">
                </div>
                {% endif %}
            </div>
            <div class="header-content">
                <h1>P2P Duplicate Invoice Check</h1>
                <p>Upload, Process, and Download your invoice data with duplicate detection</p>
            </div>
        </div>

        <div class="glossary-section" id="glossarySection">
            <div class="glossary-title">
                📚 Processing Glossary
                <button class="toggle-btn" onclick="toggleGlossary()">Hide Details</button>
            </div>
            <div class="glossary-content">
                <div class="glossary-item">
                    <h4>📋 Filters Applied</h4>
                    <p><strong>Cancelled Invoices:</strong> Excludes records where invoice_status contains "cancelled"<br>
                    <strong>Dropship Exclusion:</strong> Excludes records where invoice_source_name = "DROPSHIP"<br>
                    <strong>SCR Invoice Exclusion:</strong> Excludes records where invoice_no ends with "SCR"</p>
                </div>
                <div class="glossary-item">
                    <h4>🔗 CONCAT 1</h4>
                    <p><strong>Pattern:</strong> Header PO + Invoice Date + Invoice Amount<br>
                    <strong>Purpose:</strong> Identifies duplicates based on purchase order, date, and amount combination</p>
                </div>
                <div class="glossary-item">
                    <h4>🔗 CONCAT 2</h4>
                    <p><strong>Pattern:</strong> Primary Vendor Code + Invoice Year + Invoice Amount<br>
                    <strong>Purpose:</strong> Identifies duplicates based on vendor, year, and amount combination</p>
                </div>
                <div class="glossary-item">
                    <h4>🔗 CONCAT 3</h4>
                    <p><strong>Pattern:</strong> Header PO + Invoice Amount<br>
                    <strong>Purpose:</strong> Identifies duplicates based on purchase order and amount combination</p>
                </div>
                <div class="glossary-item">
                    <h4>💰 Amount Transformation</h4>
                    <p><strong>Process:</strong> Converts invoice_amount to numeric, removes decimal places and rightmost digit<br>
                    <strong>Example:</strong> 1234.56 becomes 123</p>
                </div>
                <div class="glossary-item">
                    <h4>📊 Output File</h4>
                    <p><strong>Format:</strong> CSV with original columns plus CONCAT patterns and duplicate remarks<br>
                    <strong>Remarks:</strong> Each row marked as "Duplicate" or "Non Duplicate" for each pattern</p>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="browse-section">
                <h3>📁 Select Input File</h3>
                <p>Choose a .gz compressed CSV file to process</p>
                <button class="browse-btn" onclick="document.getElementById('fileInput').click()">
                    Browse Files
                </button>
                <input type="file" id="fileInput" class="file-input" accept=".gz">
            </div>

            <div class="selected-file" id="selectedFile">
                <h4>✅ Selected File</h4>
                <div id="fileName"></div>
                <div id="fileSize"></div>
            </div>

            <button class="process-btn" id="processBtn" onclick="processFile()">
                🚀 Process File
            </button>

            <div class="error-section" id="errorSection"></div>
            <div class="success-section" id="successSection"></div>

            <div class="progress-section" id="progressSection">
                <h4>⏳ Processing...</h4>
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
                <div class="progress-text" id="progressText">Starting...</div>
            </div>

            <div class="summary-section" id="summarySection">
                <div class="summary-title">📊 Processing Summary</div>
                <div class="summary-grid">
                    <div class="summary-item">
                        <div class="summary-number" id="totalLines">0</div>
                        <div class="summary-label">Total Input Lines</div>
                    </div>
                    <div class="summary-item">
                        <div class="summary-number" id="processedLines">0</div>
                        <div class="summary-label">Lines Processed</div>
                    </div>
                    <div class="summary-item">
                        <div class="summary-number" id="concat1Dups">0</div>
                        <div class="summary-label">CONCAT 1(Header PO + Invoice Date + Invoice Amount) Duplicates</div>
                    </div>
                    <div class="summary-item">
                        <div class="summary-number" id="concat2Dups">0</div>
                        <div class="summary-label">CONCAT 2(Primary Vendor Code + Invoice Year + Invoice Amount) Duplicates</div>
                    </div>
                    <div class="summary-item">
                        <div class="summary-number" id="concat3Dups">0</div>
                        <div class="summary-label">CONCAT 3(Header PO + Invoice Amount) Duplicates</div>
                    </div>
                </div>
            </div>

            <div class="download-section" id="downloadSection">
                <h3>✅ Processing Complete!</h3>
                <div class="time-display" id="processingTimeDisplay" style="display: none;">
                    <span class="time-icon">⏱️</span>
                    <span class="time-text">Processing completed in <strong id="processingTime">0</strong> minutes</span>
                </div>
                <p>Your processed CSV file is ready for download</p>
                <a href="#" class="download-btn" id="downloadBtn">📥 Download Processed File</a>
            </div>
        </div>
    </div>

    <script>
        let selectedFile = null;
        let currentTaskId = null;
        let statusInterval = null;

        function toggleGlossary() {
            const section = document.getElementById('glossarySection');
            const btn = section.querySelector('.toggle-btn');
            
            if (section.classList.contains('collapsed')) {
                section.classList.remove('collapsed');
                btn.textContent = 'Hide Details';
            } else {
                section.classList.add('collapsed');
                btn.textContent = 'Show Details';
            }
        }

        document.getElementById('fileInput').addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (!file) return;
            
            if (!file.name.toLowerCase().endsWith('.gz')) {
                showError('Please select a .gz file');
                return;
            }
            
            selectedFile = file;
            
            // Show selected file info
            document.getElementById('fileName').textContent = 'File: ' + file.name;
            document.getElementById('fileSize').textContent = 'Size: ' + (file.size / 1024 / 1024).toFixed(2) + ' MB';
            document.getElementById('selectedFile').style.display = 'block';
            document.getElementById('processBtn').style.display = 'block';
            
            // Clear previous results when new file is selected
            clearPreviousResults();
            
            hideMessages();
        });

        function clearPreviousResults() {
            // Hide and clear summary section
            document.getElementById('summarySection').style.display = 'none';
            document.getElementById('totalLines').textContent = '0';
            document.getElementById('processedLines').textContent = '0';
            document.getElementById('concat1Dups').textContent = '0';
            document.getElementById('concat2Dups').textContent = '0';
            document.getElementById('concat3Dups').textContent = '0';
            
            // Hide download section and processing time
            document.getElementById('downloadSection').style.display = 'none';
            document.getElementById('processingTimeDisplay').style.display = 'none';
            document.getElementById('processingTime').textContent = '0';
            
            // Clear any existing task
            if (statusInterval) {
                clearInterval(statusInterval);
                statusInterval = null;
            }
            currentTaskId = null;
        }

        function processFile() {
            if (!selectedFile) {
                showError('Please select a file first');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', selectedFile);
            
            document.getElementById('processBtn').disabled = true;
            showProgress('Uploading file...', 5);
            hideMessages();
            
            fetch('/upload', {
                method: 'POST',
                body: formData
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.error) {
                    showError(data.error);
                    document.getElementById('processBtn').disabled = false;
                    hideProgress();
                } else {
                    currentTaskId = data.task_id;
                    showSuccess('File uploaded successfully, processing started...');
                    startStatusPolling();
                }
            })
            .catch(error => {
                showError('Upload failed: ' + error.message);
                document.getElementById('processBtn').disabled = false;
                hideProgress();
            });
        }

        function startStatusPolling() {
            statusInterval = setInterval(checkStatus, 2000);
        }

        function checkStatus() {
            if (!currentTaskId) return;
            
            fetch(`/status/${currentTaskId}`)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'completed') {
                    clearInterval(statusInterval);
                    hideProgress();
                    showSummary(data.summary);
                    
                    // Show processing time
                    if (data.processing_time_mins !== undefined) {
                        document.getElementById('processingTime').textContent = data.processing_time_mins;
                        document.getElementById('processingTimeDisplay').style.display = 'flex';
                    }
                    
                    document.getElementById('downloadBtn').href = `/download/${currentTaskId}`;
                    document.getElementById('downloadSection').style.display = 'block';
                    document.getElementById('processBtn').disabled = false;
                } else if (data.status === 'error') {
                    clearInterval(statusInterval);
                    hideProgress();
                    showError(data.message);
                    document.getElementById('processBtn').disabled = false;
                } else {
                    updateProgress(data.message, data.progress);
                }
            })
            .catch(error => {
                console.error('Status check failed:', error);
            });
        }

        function showProgress(message, progress) {
            document.getElementById('progressSection').style.display = 'block';
            updateProgress(message, progress);
        }

        function updateProgress(message, progress) {
            document.getElementById('progressFill').style.width = progress + '%';
            document.getElementById('progressText').textContent = message + ' (' + progress + '%)';
        }

        function hideProgress() {
            document.getElementById('progressSection').style.display = 'none';
        }

        function formatIndianNumber(number) {
            // Indian number formatting with commas
            return number.toLocaleString('en-IN');
        }

        function showSummary(summary) {
            document.getElementById('totalLines').textContent = formatIndianNumber(summary.total_input_lines);
            document.getElementById('processedLines').textContent = formatIndianNumber(summary.lines_processed);
            document.getElementById('concat1Dups').textContent = formatIndianNumber(summary.concat1_duplicates);
            document.getElementById('concat2Dups').textContent = formatIndianNumber(summary.concat2_duplicates);
            document.getElementById('concat3Dups').textContent = formatIndianNumber(summary.concat3_duplicates);
            
            document.getElementById('summarySection').style.display = 'block';
        }

        function showError(message) {
            document.getElementById('errorSection').textContent = message;
            document.getElementById('errorSection').style.display = 'block';
        }

        function showSuccess(message) {
            document.getElementById('successSection').textContent = message;
            document.getElementById('successSection').style.display = 'block';
        }

        function hideMessages() {
            document.getElementById('errorSection').style.display = 'none';
            document.getElementById('successSection').style.display = 'none';
        }
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    logo_data = get_logo_base64()
    return render_template_string(HTML_TEMPLATE, logo_data=logo_data)

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        print("=== UPLOAD REQUEST RECEIVED ===")
        print(f"Request method: {request.method}")
        print(f"Content-Type: {request.content_type}")
        print(f"Content-Length: {request.content_length}")
        
        if 'file' not in request.files:
            print("ERROR: No file in request")
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        print(f"File received: {file.filename}")
        print(f"File size: {file.content_length}")
        
        if file.filename == '':
            print("ERROR: Empty filename")
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.lower().endswith('.gz'):
            print(f"ERROR: Invalid file extension: {file.filename}")
            return jsonify({'error': 'File must be a .gz file'}), 400
        
        # Clean up previous uploads
        try:
            print("Cleaning up previous uploads...")
            for filename in os.listdir(UPLOAD_FOLDER):
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"Deleted old upload: {filename}")
            
            print("Cleaning up previous processed files...")
            for filename in os.listdir(PROCESSED_FOLDER):
                file_path = os.path.join(PROCESSED_FOLDER, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"Deleted old processed file: {filename}")
        except Exception as e:
            print(f"Warning: Could not clean up old files: {e}")
        
        # Generate unique task ID
        task_id = str(uuid.uuid4())
        print(f"Generated task ID: {task_id}")
        
        # Ensure upload directory exists
        try:
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            print(f"Upload folder ensured: {UPLOAD_FOLDER}")
        except Exception as e:
            print(f"ERROR creating upload folder: {e}")
            return jsonify({'error': f'Server configuration error: {str(e)}'}), 500
        
        # Save uploaded file
        try:
            filename = secure_filename(file.filename)
            if not filename:
                filename = f"upload_{task_id}.gz"
            
            input_path = os.path.join(UPLOAD_FOLDER, f"{task_id}_{filename}")
            print(f"Saving file to: {input_path}")
            
            # Save the file
            file.save(input_path)
            
            # Verify file was saved
            if not os.path.exists(input_path):
                raise Exception("File was not saved properly")
            
            file_size = os.path.getsize(input_path)
            print(f"File saved successfully, size: {file_size} bytes")
            
        except Exception as e:
            print(f"ERROR saving file: {e}")
            print(traceback.format_exc())
            return jsonify({'error': f'Failed to save file: {str(e)}'}), 500
        
        # Validate file can be opened
        try:
            print("Validating gz file...")
            with gzip.open(input_path, 'rt', encoding='utf-8') as f:
                header = f.readline()
                if not header.strip():
                    raise ValueError('File appears to be empty or corrupted')
                print(f"File validation successful, header: {header[:100]}...")
        except Exception as e:
            print(f"ERROR validating file: {e}")
            # Clean up invalid file
            if os.path.exists(input_path):
                os.remove(input_path)
                print("Cleaned up invalid file")
            return jsonify({'error': f'Invalid or corrupted gz file: {str(e)}'}), 400
        
        # Ensure processed directory exists
        try:
            os.makedirs(PROCESSED_FOLDER, exist_ok=True)
            print(f"Processed folder ensured: {PROCESSED_FOLDER}")
        except Exception as e:
            print(f"ERROR creating processed folder: {e}")
            return jsonify({'error': f'Server configuration error: {str(e)}'}), 500
        
        # Generate output filename
        try:
            output_filename = f"processed_{filename.replace('.gz', '.csv')}"
            output_path = os.path.join(PROCESSED_FOLDER, f"{task_id}_{output_filename}")
            print(f"Output path: {output_path}")
        except Exception as e:
            print(f"ERROR generating output path: {e}")
            return jsonify({'error': f'Path generation error: {str(e)}'}), 500
        
        # Start processing in background
        try:
            print("Starting background processing...")
            thread = threading.Thread(
                target=preprocess_invoice_data_browse,
                args=(input_path, output_path, task_id)
            )
            thread.daemon = True
            thread.start()
            print("Background thread started successfully")
        except Exception as e:
            print(f"ERROR starting background thread: {e}")
            print(traceback.format_exc())
            return jsonify({'error': f'Failed to start processing: {str(e)}'}), 500
        
        print("Upload successful, returning response")
        return jsonify({
            'task_id': task_id,
            'message': 'File uploaded successfully, processing started'
        })
        
    except Exception as e:
        print(f"UNEXPECTED ERROR in upload: {e}")
        print(traceback.format_exc())
        return jsonify({'error': f'Unexpected server error: {str(e)}'}), 500

@app.route('/status/<task_id>')
def get_status(task_id):
    status = processing_status.get(task_id, {'status': 'not_found', 'message': 'Task not found'})
    return jsonify(status)

@app.route('/download/<task_id>')
def download_file(task_id):
    status = processing_status.get(task_id)
    if not status or status['status'] != 'completed':
        return jsonify({'error': 'File not ready for download'}), 404
    
    output_file = status['output_file']
    if not os.path.exists(output_file):
        return jsonify({'error': 'Processed file not found'}), 404
    
    return send_file(
        output_file,
        as_attachment=True,
        download_name=f"processed_invoice_data.csv"
    )

@app.before_request
def check_authentication():
    """Check if user is authenticated before accessing the application"""
    # Skip authentication check for static files
    if request.endpoint and request.endpoint.startswith('static'):
        return

    # Simple check - in a real application, you'd want to verify the session
    # For now, we'll assume if the request comes from localhost, it's authenticated
    # You can enhance this by integrating with the session management from app.py
    pass

if __name__ == '__main__':
    print("P2P Duplicate Invoice Check - Enhanced Browse Interface")
    print("====================================================")
    print("Features:")
    print("- Pierian logo integration")
    print("- Red-themed UI matching logo colors")
    print("- Interactive Glossary section")
    print("- Enhanced CONCAT display names")
    print("- Updated column names in output")
    print()
    print("Web application available at: http://localhost:5000")
    print("Note: This application should be accessed through the authentication system at http://localhost:3000")
    app.run(debug=True, host='0.0.0.0', port=5000)