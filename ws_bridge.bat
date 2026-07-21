@echo off
cd /d "D:\Zybo Projects\Ongoing Projects\Xracelocal"

call myenv\Scripts\activate.bat

python ws_bridge.py

pause