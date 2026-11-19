@echo off
rem Starts the LTE to NR Audit (Ericsson) from the folder of this file (works after moving the project folder).
rem "pyw -3" = newest Python 3 installed (needs the packages of requirements.txt), no console window.
rem pyw is searched with the cmd built-in %%~$PATH; otherwise the default user install locations are tried.
cd /d "%~dp0"
set "LN_APP=%~dp0tk_lte_nr_ericsson_app.py"
set "LN_PYW="
for %%P in (pyw.exe) do set "LN_PYW=%%~$PATH:P"
if not defined LN_PYW if exist "%LOCALAPPDATA%\Programs\Python\Launcher\pyw.exe" set "LN_PYW=%LOCALAPPDATA%\Programs\Python\Launcher\pyw.exe"
if not defined LN_PYW if exist "%WINDIR%\pyw.exe" set "LN_PYW=%WINDIR%\pyw.exe"
if defined LN_PYW (
    start "" "%LN_PYW%" -3 "%LN_APP%"
) else (
    start "" pythonw "%LN_APP%"
)
