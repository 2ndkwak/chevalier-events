"""
Run this to test email settings directly.
python debug_email.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import create_app
app = create_app()

with app.app_context():
    # Show what Flask thinks the email config is
    print("\nEmail configuration Flask is using:")
    print(f"  MAIL_SERVER:   {app.config.get('MAIL_SERVER')}")
    print(f"  MAIL_PORT:     {app.config.get('MAIL_PORT')}")
    print(f"  MAIL_USE_TLS:  {app.config.get('MAIL_USE_TLS')}")
    print(f"  MAIL_USERNAME: {app.config.get('MAIL_USERNAME')}")
    pw = app.config.get('MAIL_PASSWORD', '')
    print(f"  MAIL_PASSWORD: {pw[:4]}{'*' * (len(pw)-4)} ({len(pw)} chars)")
    print()

    # Try sending a test email directly via smtplib
    import smtplib
    try:
        print("Connecting to smtp.gmail.com:587...")
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.ehlo()
        server.starttls()
        server.ehlo()
        print("Attempting login...")
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        print("LOGIN SUCCESSFUL!")
        server.quit()
    except Exception as e:
        print(f"FAILED: {e}")
