@echo off
REM ---------------------------------------------------------------------------------------------
REM  Installs the "Import Structural JSON" add-in for the CURRENT USER. No admin rights, no Python.
REM  Usage:  double-click  (installs for every Revit 2022-2026 found)   or   Install.bat 2024
REM ---------------------------------------------------------------------------------------------
setlocal
set "KIT=%~dp0"
set "FOUND=0"

if not "%~1"=="" goto :forced

for %%Y in (2022 2023 2024 2025 2026) do (
    if exist "%ProgramFiles%\Autodesk\Revit %%Y\Revit.exe" call :install %%Y
)
if "%FOUND%"=="0" goto :ask
goto :finish

:forced
call :install %~1
goto :finish

:ask
echo Revit was not found in the default location.
set /p YEAR=Which Revit year should the add-in be installed for? [2022-2026]:
call :install %YEAR%
goto :finish

:finish
echo.
if "%FOUND%"=="0" goto :nothing
echo Done. Restart Revit, then open:  Add-Ins tab ^> External Tools ^> Import Structural JSON
echo If Revit asks whether to load the add-in, choose "Always Load".
goto :end

:nothing
echo Nothing was installed.

:end
echo.
pause
exit /b 0

:install
set "Y=%~1"
set "SRC=%KIT%%Y%\StructuralImport.dll"
if not exist "%SRC%" goto :no_build
set "ADDINS=%APPDATA%\Autodesk\Revit\Addins\%Y%"
set "DEST=%ADDINS%\StructuralImport"
if not exist "%DEST%" mkdir "%DEST%"
copy /y "%SRC%" "%DEST%\StructuralImport.dll" >nul
if errorlevel 1 goto :copy_failed
REM files that arrive by zip / download / network share are marked "blocked" by Windows - clear the mark (best effort)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Unblock-File -LiteralPath '%DEST%\StructuralImport.dll'" >nul 2>nul

set "MANIFEST=%ADDINS%\StructuralImport.addin"
> "%MANIFEST%" echo ^<?xml version="1.0" encoding="utf-8" standalone="no"?^>
>> "%MANIFEST%" echo ^<RevitAddIns^>
>> "%MANIFEST%" echo   ^<AddIn Type="Command"^>
>> "%MANIFEST%" echo     ^<Name^>Import Structural JSON^</Name^>
>> "%MANIFEST%" echo     ^<Assembly^>%DEST%\StructuralImport.dll^</Assembly^>
>> "%MANIFEST%" echo     ^<AddInId^>8B3C6A2E-4D1F-4E7B-9A55-2F0C7D91B3E4^</AddInId^>
>> "%MANIFEST%" echo     ^<FullClassName^>StructuralImport.ImportCommand^</FullClassName^>
>> "%MANIFEST%" echo     ^<Text^>Import Structural JSON^</Text^>
>> "%MANIFEST%" echo     ^<Description^>Builds beams, columns, slabs and walls from a Stage 1 structural JSON.^</Description^>
>> "%MANIFEST%" echo     ^<VisibilityMode^>AlwaysVisible^</VisibilityMode^>
>> "%MANIFEST%" echo     ^<VendorId^>ETBQ^</VendorId^>
>> "%MANIFEST%" echo     ^<VendorDescription^>Structural CAD to Revit^</VendorDescription^>
>> "%MANIFEST%" echo   ^</AddIn^>
>> "%MANIFEST%" echo ^</RevitAddIns^>
echo [%Y%] Installed: %DEST%
set "FOUND=1"
exit /b 0

:no_build
echo [%Y%] No build for this Revit year in this kit - skipped.
exit /b 0

:copy_failed
echo [%Y%] Could not copy the DLL to %DEST%
exit /b 0
