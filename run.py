"""
Single-command launcher: installs dependencies then starts the server.
Run:  python run.py
"""
import os
import sys
import subprocess
import socket

# Change to this script's folder
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Install dependencies (quiet)
print("Checking dependencies...")
r = subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
    capture_output=True,
    text=True,
)
if r.returncode != 0:
    print("Installing dependencies failed. Run: pip install -r requirements.txt")
    sys.exit(1)

# Start the app
import app
app.init_db()

# Get this PC's IP for phone access
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

port = 8080
use_https = "--https" in sys.argv or os.environ.get("HTTPS", "").lower() in ("1", "true", "yes")
ip = get_local_ip()

if use_https:
    print("\n--- Server starting (HTTPS) ---")
    print("LAPTOP:  https://127.0.0.1:%s" % port)
    if ip:
        print("PHONE:   https://%s:%s" % (ip, port))
    else:
        print("PHONE:   https://YOUR_PC_IP:%s  (run ipconfig for IP)" % port)
    print("--------------------------------\n")
    try:
        app.app.run(host="0.0.0.0", port=port, debug=True, ssl_context="adhoc")
    except Exception as e:
        print("HTTPS failed:", e)
        app.app.run(host="0.0.0.0", port=port, debug=True)
else:
    print("\n" + "="*50)
    print("  ATTENDANCE SERVER")
    print("="*50)
    print("  LAPTOP:  http://127.0.0.1:%s" % port)
    if ip:
        print("  PHONE:   http://%s:%s" % (ip, port))
        print("")
        print("  Type the PHONE URL in your phone browser.")
        print("  Phone and laptop must be on the SAME Wi-Fi.")
    else:
        print("  PHONE:   http://YOUR_IP:%s  (run ipconfig to get IP)" % port)
    print("")
    print("  If phone says 'can't connect':")
    print("  1. Windows Search -> Windows Defender Firewall")
    print("  2. Allow an app -> Change settings -> Allow another app")
    print("  3. Browse -> find python.exe -> Add -> tick Private -> OK")
    print("  4. Try the PHONE URL again")
    print("="*50 + "\n")
    app.app.run(host="0.0.0.0", port=port, debug=True)
