@echo off
:: Run this as Administrator so your phone can connect.
:: Right-click allow_phone_access.bat -> Run as administrator

netsh advfirewall firewall add rule name="Attendance App 8080" dir=in action=allow protocol=TCP localport=8080
if %errorlevel% equ 0 (
    echo Firewall rule added. Your phone should now connect to http://YOUR_PC_IP:8080
    echo Find your PC IP in the server window when you run python run.py
) else (
    echo Run this file as Administrator: right-click -> Run as administrator
)
pause
