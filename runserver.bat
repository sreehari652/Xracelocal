@echo off
cd /d "D:\Zybo Projects\Ongoing Projects\Xracelocal"

call myenv\Scripts\activate.bat

python manage.py runserver 0.0.0.0:8000

pause