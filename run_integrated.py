#!/usr/bin/env python3
"""
Simple launcher for the Pierian P2P Duplicate Invoice Check Integrated System
"""

import subprocess
import sys
import os

def main():
    """Main function to start the integrated application"""
    print("=" * 60)
    print("🚀 Starting Pierian P2P Duplicate Invoice Check System")
    print("=" * 60)
    print()
    print("📋 System Information:")
    print("- Single integrated Flask application")
    print("- All features on one port: 5000")
    print("- Authentication + Invoice Processing")
    print()
    print("🌐 Access URLs:")
    print("- Landing Page: http://localhost:5000")
    print("- Admin Panel: http://localhost:5000/admin")
    print("- Invoice App: http://localhost:5000/app")
    print()
    print("👤 Default Admin Credentials:")
    print("- Email: admin@pierian.co.in")
    print("- Password: admin123")
    print()
    print("⚠️  Remember to change the default admin password!")
    print("=" * 60)
    print()

    try:
        # Check if the integrated app file exists
        if not os.path.exists('integrated_app.py'):
            print("❌ Error: integrated_app.py not found!")
            print("Please ensure the file exists in the current directory.")
            return

        # Start the integrated application
        print("🔄 Starting integrated application...")
        subprocess.run([sys.executable, 'integrated_app.py'])

    except KeyboardInterrupt:
        print("\n🛑 Shutting down system...")
        print("✅ System shutdown complete.")
    except Exception as e:
        print(f"❌ Error starting application: {e}")

if __name__ == '__main__':
    main()