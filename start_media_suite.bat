@echo off
setlocal
set "APP=%~dp0MediaTranscriberSuite\MediaTranscriberSuite.exe"
if not exist "%APP%" (
  echo Application not found: "%APP%"
  pause
  exit /b 1
)
start "" "%APP%"
endlocal
