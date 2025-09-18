# Pierian P2P Duplicate Invoice Check System

An integrated system for processing and detecting duplicate invoices with authentication and admin management.

## Features

### Authentication System
- **Landing Page**: Clean login interface matching Pierian theme
- **Admin Panel**: User and admin account management
- **Role-based Access**: Separate admin and user roles
- **Session Management**: Secure login/logout functionality

### Main Application
- **File Upload**: Support for .gz compressed CSV files
- **Duplicate Detection**: Three different CONCAT pattern matching
- **Real-time Processing**: Progress tracking with live updates
- **Download Results**: Processed CSV with duplicate markers
- **Interactive UI**: Modern interface with Pierian branding

## Installation & Setup

### Prerequisites
- Python 3.7 or higher
- Flask framework
- SQLite (included with Python)

### Required Python Packages
```bash
pip install flask
```

### Initial Setup
1. Extract all files to your desired directory
2. The system will automatically create a SQLite database on first run
3. Default admin account will be created automatically

## Running the System

### Option 1: Integrated Startup (Recommended)
```bash
python start_system.py
```
This starts both the authentication system and main application simultaneously.

### Option 2: Manual Startup
Start each component separately:

**Terminal 1 - Authentication System:**
```bash
python app.py
```

**Terminal 2 - Main Application:**
```bash
python enhanced_browse_web_app.py
```

## System Access

### URLs
- **Landing Page**: http://localhost:3000
- **Admin Panel**: http://localhost:3000/admin
- **Main Application**: http://localhost:5000 (accessible after login)

### Default Admin Credentials
- **Email**: admin@pierian.co.in
- **Password**: admin123

⚠️ **Important**: Please change the default admin password after first login!

## User Management

### Admin Functions
Admins can access the admin panel to:
- Create new user accounts
- Create new admin accounts
- Activate/deactivate user accounts
- Delete user accounts
- View all system users

### User Roles
- **Admin**: Full system access + user management
- **User**: Access to main application only

## Application Workflow

1. **Login**: Access the landing page at http://localhost:3000
2. **Authentication**: Enter valid credentials
3. **Redirect**: Automatic redirect to main application
4. **File Processing**: Upload .gz CSV files for duplicate detection
5. **Results**: Download processed files with duplicate analysis

## File Processing

### Input Requirements
- File format: .gz compressed CSV
- Must contain required invoice columns
- Maximum file size: 2GB

### Processing Features
- **Filters Applied**:
  - Excludes cancelled invoices
  - Excludes Dropship invoices
  - Excludes SCR invoices

- **Duplicate Detection Patterns**:
  - **CONCAT 1**: Header PO + Invoice Date + Invoice Amount
  - **CONCAT 2**: Primary Vendor Code + Invoice Year + Invoice Amount
  - **CONCAT 3**: Header PO + Invoice Amount

### Output
- CSV file with original data plus:
  - CONCAT pattern columns
  - Duplicate/Non-duplicate remarks
  - Transformed amount fields
  - Invoice year extraction

## Security Features

- Password hashing (SHA256)
- Session management
- Role-based access control
- Admin-only user management
- Secure file handling

## Troubleshooting

### Common Issues

**Database Error**: If you encounter database issues, delete `users.db` and restart the system.

**Port Conflicts**:
- Authentication system uses port 3000
- Main application uses port 5000
- Ensure these ports are available

**File Upload Issues**:
- Check file format (.gz required)
- Verify file size (max 2GB)
- Ensure file contains required CSV headers

### Support
For technical support, contact: support@pierian.co.in

## File Structure
```
├── app.py                      # Authentication system
├── enhanced_browse_web_app.py  # Main application
├── start_system.py            # Integrated startup script
├── README.md                  # This file
├── users.db                   # SQLite database (created automatically)
├── uploads/                   # Uploaded files directory
├── processed/                 # Processed files directory
└── Image/                     # Logo assets directory
```

## Development

### Adding New Users Programmatically
Users can be added through the admin panel or by directly inserting into the SQLite database.

### Customizing the Theme
The UI styling can be modified in the HTML templates within the Python files.

### Database Schema
The system uses a simple SQLite database with the following structure:

**users table**:
- id (PRIMARY KEY)
- username (UNIQUE)
- email (UNIQUE)
- password_hash
- role (admin/user)
- created_at (TIMESTAMP)
- is_active (BOOLEAN)

## License
© 2024 Pierian Services Pvt Ltd. All rights reserved.