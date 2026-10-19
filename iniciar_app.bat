@echo off
REM Abre el visor SAR. La primera vez crea el entorno e instala todo (unos minutos).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creando entorno de Python...
  python -m venv .venv || (echo No se encontro Python. Instalalo desde python.org ^(3.12 o 3.13^) y marca "Add to PATH". & pause & exit /b)
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m streamlit run app.py
pause
