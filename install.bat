@echo off
REM Create virtual environment
python -m venv .venv

REM Activate virtual environment
call .venv\Scripts\activate

REM Install package in development mode
pip install -e .

echo Installation complete! Virtual environment activated.
pause
