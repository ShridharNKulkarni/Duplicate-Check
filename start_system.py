#!/usr/bin/env python3
"""
Startup script for the Pierian P2P Duplicate Invoice Check System
This script starts both the authentication system and the main application
"""

import subprocess
import sys
import time
import os
from multiprocessing import Process

def start_auth_system():
    """Start the authentication system"""
    print("Starting Authentication System...")
    subprocess.run([sys.executable, 'app.py'])

def start_main_app():
    """Start the main application"""
    print("Starting Main Application...")
    time.sleep(3)  # Wait a bit for the auth system to start
    subprocess.run([sys.executable, 'enhanced_browse_web_app.py'])

def main():
    """Main function to start both applications"""
    print("=" * 60)
    print("Pierian P2P Duplicate Invoice Check System")
    print("=" * 60)
    print()
    print("Starting integrated system...")
    print()
    print("Default Admin Credentials:")
    print("Email: admin@pierian.co.in")
    print("Password: admin123")
    print()
    print("System URLs:")
    print("- Authentication/Landing Page: http://localhost:3000")
    print("- Main Application: http://localhost:5000 (accessible after login)")
    print()
    print("Please change the default admin password after first login!")
    print("=" * 60)
    print()

    try:
        # Start both processes
        auth_process = Process(target=start_auth_system)
        app_process = Process(target=start_main_app)

        auth_process.start()
        app_process.start()

        # Wait for both processes
        auth_process.join()
        app_process.join()

    except KeyboardInterrupt:
        print("\nShutting down system...")
        if 'auth_process' in locals():
            auth_process.terminate()
        if 'app_process' in locals():
            app_process.terminate()
        print("System shutdown complete.")

if __name__ == '__main__':
    main()