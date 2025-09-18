from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, flash, send_file
import csv
import gzip
import os
import uuid
import threading
from werkzeug.utils import secure_filename
from datetime import datetime
import traceback
import base64
import sqlite3
import hashlib
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here-change-this'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size

# Store processing status and results
processing_status = {}
UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

# Database setup
def init_db():
    """Initialize the database with admin and user tables"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()

    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    ''')

    # Create default admin user if not exists
    admin_email = 'admin@pierian.co.in'
    admin_username = 'admin'
    admin_password = 'admin123'  # Default password - should be changed
    admin_hash = hashlib.sha256(admin_password.encode()).hexdigest()

    cursor.execute('SELECT id FROM users WHERE email = ?', (admin_email,))
    if not cursor.fetchone():
        cursor.execute('''
            INSERT INTO users (username, email, password_hash, role)
            VALUES (?, ?, ?, ?)
        ''', (admin_username, admin_email, admin_hash, 'admin'))

    conn.commit()
    conn.close()

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    """Verify password against hash"""
    return hashlib.sha256(password.encode()).hexdigest() == hashed

def login_required(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))

        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user = cursor.fetchone()
        conn.close()

        if not user or user[0] != 'admin':
            flash('Admin access required', 'error')
            return redirect(url_for('login'))

        return f(*args, **kwargs)
    return decorated_function

def get_logo_base64():
    """Convert logo to base64 for embedding in HTML"""
    try:
        # Try current directory first
        logo_path = 'image (3).png'
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as f:
                logo_data = f.read()
                return base64.b64encode(logo_data).decode('utf-8')

        # Fallback to Image directory
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

# Landing page HTML template
LANDING_PAGE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pierian Services - Login</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }

        .login-container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.1);
            padding: 40px;
            width: 100%;
            max-width: 400px;
            text-align: center;
        }

        .logo-container {
            margin-bottom: 30px;
        }

        .logo-image {
            width: 100px;
            height: auto;
            border-radius: 8px;
            margin-bottom: 10px;
            box-shadow: 0 4px 15px rgba(220, 53, 69, 0.3);
        }

        .logo {
            background: #e53e3e;
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 24px;
            font-weight: bold;
            letter-spacing: 1px;
            margin-bottom: 10px;
            display: inline-block;
        }

        .company-name {
            color: #2d3748;
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 10px;
        }

        .app-name {
            color: #2d3748;
            font-size: 16px;
            font-weight: 500;
            margin-bottom: 30px;
        }

        .form-group {
            margin-bottom: 20px;
            text-align: left;
        }

        .form-group label {
            display: block;
            color: #2d3748;
            font-weight: 500;
            margin-bottom: 5px;
            font-size: 14px;
        }

        .form-group input {
            width: 100%;
            padding: 12px 16px;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s ease;
            background: #f7fafc;
        }

        .form-group input:focus {
            outline: none;
            border-color: #e53e3e;
            background: white;
        }

        .form-group input::placeholder {
            color: #a0aec0;
        }

        .remember-me {
            display: flex;
            align-items: center;
            margin-bottom: 25px;
            font-size: 14px;
            color: #4a5568;
        }

        .remember-me input {
            margin-right: 8px;
            width: auto;
        }

        .sign-in-btn {
            background: #e53e3e;
            color: white;
            border: none;
            padding: 14px 32px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            transition: background-color 0.3s ease, transform 0.2s ease;
        }

        .sign-in-btn:hover {
            background: #c53030;
            transform: translateY(-1px);
        }

        .sign-in-btn:active {
            transform: translateY(0);
        }

        .help-section {
            margin-top: 25px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
        }

        .help-title {
            color: #718096;
            font-size: 14px;
            margin-bottom: 5px;
        }

        .help-contact {
            color: #4299e1;
            font-size: 14px;
            text-decoration: none;
        }

        .help-contact:hover {
            text-decoration: underline;
        }

        .alert {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }

        .alert-error {
            background: #fed7d7;
            color: #c53030;
            border: 1px solid #feb2b2;
        }

        .alert-success {
            background: #c6f6d5;
            color: #2f855a;
            border: 1px solid #9ae6b4;
        }

        .admin-link {
            margin-top: 20px;
            padding-top: 15px;
            border-top: 1px solid #e2e8f0;
        }

        .admin-link a {
            color: #4299e1;
            text-decoration: none;
            font-size: 14px;
        }

        .admin-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo-container">
            {% if logo_data %}
            <img src="data:image/png;base64,{{ logo_data }}" alt="Pierian Logo" class="logo-image">
            {% else %}
            <div class="logo">pierian</div>
            {% endif %}
            <div class="company-name">Pierian Services Pvt Ltd</div>
            <div class="app-name">P2P Duplicate Invoice Check</div>
        </div>

        {% if messages %}
            {% for category, message in messages %}
                <div class="alert alert-{{ 'error' if category == 'error' else 'success' }}">
                    {{ message }}
                </div>
            {% endfor %}
        {% endif %}

        <form method="POST" action="{{ url_for('login') }}">
            <div class="form-group">
                <label for="email">Email Address</label>
                <input type="email" id="email" name="email" placeholder="Enter your email" required
                       value="{{ request.form.email if request.form.email else '' }}">
            </div>

            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" placeholder="Enter your password" required>
            </div>

            <div class="remember-me">
                <input type="checkbox" id="remember" name="remember">
                <label for="remember">Remember me</label>
            </div>

            <button type="submit" class="sign-in-btn">SIGN IN</button>
        </form>

        <div class="help-section">
            <div class="help-title">Need Help?</div>
            <a href="mailto:zion.analytics@pierian.co.in" class="help-contact">Contact support: zion.analytics@pierian.co.in</a>
        </div>
    </div>
</body>
</html>
'''

# Admin panel HTML template
ADMIN_PANEL_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Panel - Pierian Services</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            min-height: 100vh;
            padding: 20px;
        }

        .header {
            background: white;
            border-radius: 12px;
            padding: 20px 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .logo {
            background: #e53e3e;
            color: white;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 18px;
            font-weight: bold;
        }

        .header-actions {
            display: flex;
            gap: 15px;
            align-items: center;
        }

        .user-info {
            color: #4a5568;
            font-size: 14px;
        }

        .logout-btn, .app-btn {
            background: #e53e3e;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 14px;
            transition: background-color 0.3s ease;
        }

        .logout-btn:hover, .app-btn:hover {
            background: #c53030;
        }

        .app-btn {
            background: #4299e1;
        }

        .app-btn:hover {
            background: #3182ce;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        .panel {
            background: white;
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
        }

        .panel h2 {
            color: #2d3748;
            margin-bottom: 20px;
            font-size: 24px;
        }

        .form-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }

        .form-group {
            margin-bottom: 15px;
        }

        .form-group label {
            display: block;
            color: #4a5568;
            font-weight: 500;
            margin-bottom: 5px;
            font-size: 14px;
        }

        .form-group input, .form-group select {
            width: 100%;
            padding: 10px 12px;
            border: 2px solid #e2e8f0;
            border-radius: 6px;
            font-size: 14px;
            transition: border-color 0.3s ease;
        }

        .form-group input:focus, .form-group select:focus {
            outline: none;
            border-color: #e53e3e;
        }

        .btn {
            background: #e53e3e;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            font-size: 14px;
            cursor: pointer;
            transition: background-color 0.3s ease;
        }

        .btn:hover {
            background: #c53030;
        }

        .btn-secondary {
            background: #718096;
        }

        .btn-secondary:hover {
            background: #4a5568;
        }

        .btn-danger {
            background: #e53e3e;
        }

        .btn-danger:hover {
            background: #c53030;
        }

        .users-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }

        .users-table th,
        .users-table td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #e2e8f0;
        }

        .users-table th {
            background: #f7fafc;
            color: #4a5568;
            font-weight: 600;
            font-size: 14px;
        }

        .users-table td {
            color: #2d3748;
            font-size: 14px;
        }

        .badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }

        .badge-admin {
            background: #fed7d7;
            color: #c53030;
        }

        .badge-user {
            background: #c6f6d5;
            color: #2f855a;
        }

        .badge-active {
            background: #c6f6d5;
            color: #2f855a;
        }

        .badge-inactive {
            background: #fed7d7;
            color: #c53030;
        }

        .alert {
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 20px;
            font-size: 14px;
        }

        .alert-error {
            background: #fed7d7;
            color: #c53030;
            border: 1px solid #feb2b2;
        }

        .alert-success {
            background: #c6f6d5;
            color: #2f855a;
            border: 1px solid #9ae6b4;
        }

        @media (max-width: 768px) {
            .form-grid {
                grid-template-columns: 1fr;
            }

            .header {
                flex-direction: column;
                gap: 15px;
                text-align: center;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">pierian</div>
            <div class="header-actions">
                <div class="user-info">Welcome, {{ current_user.username }}</div>
                <a href="{{ url_for('main_app') }}" class="app-btn">Invoice App</a>
                <a href="{{ url_for('logout') }}" class="logout-btn">Logout</a>
            </div>
        </div>

        {% if messages %}
            {% for category, message in messages %}
                <div class="alert alert-{{ 'error' if category == 'error' else 'success' }}">
                    {{ message }}
                </div>
            {% endfor %}
        {% endif %}

        <div class="panel">
            <h2>Create New User</h2>
            <form method="POST" action="{{ url_for('admin_panel') }}">
                <div class="form-grid">
                    <div class="form-group">
                        <label for="username">Username</label>
                        <input type="text" id="username" name="username" required>
                    </div>
                    <div class="form-group">
                        <label for="email">Email</label>
                        <input type="email" id="email" name="email" required>
                    </div>
                </div>
                <div class="form-grid">
                    <div class="form-group">
                        <label for="password">Password</label>
                        <input type="password" id="password" name="password" required>
                    </div>
                    <div class="form-group">
                        <label for="role">Role</label>
                        <select id="role" name="role" required>
                            <option value="user">User</option>
                            <option value="admin">Admin</option>
                        </select>
                    </div>
                </div>
                <button type="submit" class="btn">Create User</button>
            </form>
        </div>

        <div class="panel">
            <h2>Manage Users</h2>
            <table class="users-table">
                <thead>
                    <tr>
                        <th>Username</th>
                        <th>Email</th>
                        <th>Role</th>
                        <th>Status</th>
                        <th>Created</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for user in users %}
                    <tr>
                        <td>{{ user.username }}</td>
                        <td>{{ user.email }}</td>
                        <td><span class="badge badge-{{ user.role }}">{{ user.role.title() }}</span></td>
                        <td><span class="badge badge-{{ 'active' if user.is_active else 'inactive' }}">{{ 'Active' if user.is_active else 'Inactive' }}</span></td>
                        <td>{{ user.created_at }}</td>
                        <td>
                            {% if user.id != current_user.id %}
                            <form method="POST" action="{{ url_for('toggle_user_status', user_id=user.id) }}" style="display: inline;">
                                <button type="submit" class="btn btn-secondary">
                                    {{ 'Deactivate' if user.is_active else 'Activate' }}
                                </button>
                            </form>
                            <form method="POST" action="{{ url_for('delete_user', user_id=user.id) }}" style="display: inline;"
                                  onsubmit="return confirm('Are you sure you want to delete this user?')">
                                <button type="submit" class="btn btn-danger">Delete</button>
                            </form>
                            {% else %}
                            <span style="color: #718096; font-size: 12px;">Current User</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
'''

# Main application HTML template
MAIN_APP_TEMPLATE = '''
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
            display: flex; align-items: center; justify-content: flex-start;
        }

        .logo-container {
            width: 120px; height: auto; border-radius: 12px; overflow: hidden;
            box-shadow: 0 6px 20px rgba(220, 53, 69, 0.8);
            transition: transform 0.3s ease;
            flex-shrink: 0;
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

        .header-actions {
            display: flex;
            gap: 15px;
            align-items: center;
        }

        .user-info {
            color: rgba(255, 255, 255, 0.8);
            font-size: 14px;
        }

        .header-btn {
            background: linear-gradient(45deg, #dc3545, #a71e2a);
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 14px;
            transition: all 0.3s ease;
        }

        .header-btn:hover {
            background: linear-gradient(45deg, #e74c3c, #c82333);
            transform: translateY(-1px);
        }

        .admin-btn {
            background: linear-gradient(45deg, #4299e1, #3182ce);
        }

        .admin-btn:hover {
            background: linear-gradient(45deg, #63b3ed, #2b77cb);
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

        @media (max-width: 768px) {
            .main-header {
                flex-direction: column;
                gap: 15px;
                text-align: center;
                padding: 20px;
            }

            .logo-section {
                justify-content: center;
                margin-bottom: 10px;
            }

            .header-content h1 {
                font-size: 1.8rem;
            }

            .header-actions {
                flex-direction: column;
                gap: 10px;
            }
        }

        @media (max-width: 480px) {
            .logo-container {
                width: 80px;
            }

            .header-content h1 {
                font-size: 1.5rem;
            }

            .header-content p {
                font-size: 14px;
            }
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
            <div class="header-actions">
                <div class="user-info">{{ session.username }}</div>
                {% if session.role == 'admin' %}
                <a href="{{ url_for('admin_panel') }}" class="header-btn admin-btn">Admin</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="header-btn">Logout</a>
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

# Routes

@app.route('/')
def login():
    """Landing page with login form"""
    logo_data = get_logo_base64()
    return render_template_string(LANDING_PAGE_TEMPLATE, messages=session.pop('_flashes', []), logo_data=logo_data)

@app.route('/', methods=['POST'])
def login_post():
    """Handle login form submission"""
    email = request.form.get('email')
    password = request.form.get('password')

    if not email or not password:
        flash('Please fill in all fields', 'error')
        return redirect(url_for('login'))

    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, password_hash, role, is_active FROM users WHERE email = ?', (email,))
    user = cursor.fetchone()
    conn.close()

    if user and verify_password(password, user[2]) and user[4]:  # user[4] is is_active
        session['user_id'] = user[0]
        session['username'] = user[1]
        session['role'] = user[3]

        flash(f'Welcome back, {user[1]}!', 'success')

        # Redirect to main application
        return redirect(url_for('main_app'))
    else:
        flash('Invalid email/password or account is deactivated', 'error')
        return redirect(url_for('login'))

@app.route('/admin')
@admin_required
def admin_panel():
    """Admin panel for user management"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()

    # Get current user info
    cursor.execute('SELECT id, username, email, role FROM users WHERE id = ?', (session['user_id'],))
    current_user = cursor.fetchone()

    # Get all users
    cursor.execute('SELECT id, username, email, role, is_active, created_at FROM users ORDER BY created_at DESC')
    users = cursor.fetchall()
    conn.close()

    # Convert to objects for template
    current_user_obj = type('User', (), {
        'id': current_user[0],
        'username': current_user[1],
        'email': current_user[2],
        'role': current_user[3]
    })()

    users_list = []
    for user in users:
        user_obj = type('User', (), {
            'id': user[0],
            'username': user[1],
            'email': user[2],
            'role': user[3],
            'is_active': user[4],
            'created_at': user[5]
        })()
        users_list.append(user_obj)

    return render_template_string(ADMIN_PANEL_TEMPLATE,
                                current_user=current_user_obj,
                                users=users_list,
                                messages=session.pop('_flashes', []))

@app.route('/admin', methods=['POST'])
@admin_required
def create_user():
    """Create new user from admin panel"""
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    role = request.form.get('role')

    if not all([username, email, password, role]):
        flash('Please fill in all fields', 'error')
        return redirect(url_for('admin_panel'))

    if role not in ['user', 'admin']:
        flash('Invalid role selected', 'error')
        return redirect(url_for('admin_panel'))

    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()

    # Check if username or email already exists
    cursor.execute('SELECT id FROM users WHERE username = ? OR email = ?', (username, email))
    if cursor.fetchone():
        flash('Username or email already exists', 'error')
        conn.close()
        return redirect(url_for('admin_panel'))

    # Create new user
    password_hash = hash_password(password)
    try:
        cursor.execute('''
            INSERT INTO users (username, email, password_hash, role)
            VALUES (?, ?, ?, ?)
        ''', (username, email, password_hash, role))
        conn.commit()
        flash(f'User {username} created successfully', 'success')
    except Exception as e:
        flash(f'Error creating user: {str(e)}', 'error')

    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/admin/toggle_user/<int:user_id>', methods=['POST'])
@admin_required
def toggle_user_status(user_id):
    """Toggle user active status"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()

    # Get current status
    cursor.execute('SELECT is_active, username FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()

    if user:
        new_status = 0 if user[0] else 1
        cursor.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_status, user_id))
        conn.commit()

        status_text = 'activated' if new_status else 'deactivated'
        flash(f'User {user[1]} has been {status_text}', 'success')
    else:
        flash('User not found', 'error')

    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Delete user"""
    if user_id == session['user_id']:
        flash('Cannot delete your own account', 'error')
        return redirect(url_for('admin_panel'))

    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()

    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()

    if user:
        cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        flash(f'User {user[0]} has been deleted', 'success')
    else:
        flash('User not found', 'error')

    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/logout')
def logout():
    """Logout user"""
    session.clear()
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('login'))

@app.route('/app')
@login_required
def main_app():
    """Main application page"""
    logo_data = get_logo_base64()
    return render_template_string(MAIN_APP_TEMPLATE, logo_data=logo_data, session=session)

@app.route('/upload', methods=['POST'])
@login_required
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
@login_required
def get_status(task_id):
    status = processing_status.get(task_id, {'status': 'not_found', 'message': 'Task not found'})
    return jsonify(status)

@app.route('/download/<task_id>')
@login_required
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

if __name__ == '__main__':
    # Initialize database
    init_db()
    print("=" * 60)
    print("Pierian P2P Duplicate Invoice Check System")
    print("=" * 60)
    print("✅ Integrated Flask Application Started")
    print()
    print("🔐 Authentication System:")
    print("- Landing Page: http://localhost:5000")
    print("- Admin Panel: http://localhost:5000/admin")
    print()
    print("📊 Main Application:")
    print("- Invoice Processing: http://localhost:5000/app")
    print()
    print("👤 Default Admin Credentials:")
    print("- Email: admin@pierian.co.in")
    print("- Password: admin123")
    print()
    print("⚠️  Please change the default admin password after first login!")
    print("=" * 60)

    app.run(debug=True, host='0.0.0.0', port=5000)