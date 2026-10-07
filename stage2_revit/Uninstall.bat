@echo off
REM Removes the "Import Structural JSON" add-in (current user only). Does not touch Revit projects or your JSON files.
setlocal
set "GONE=0"
for %%Y in (2022 2023 2024 2025 2026) do (
    if exist "%APPDATA%\Autodesk\Revit\Addins\%%Y\StructuralImport.addin" (
        del /q "%APPDATA%\Autodesk\Revit\Addins\%%Y\StructuralImport.addin"
        set "GONE=1"
        echo [%%Y] manifest removed
    )
    if exist "%APPDATA%\Autodesk\Revit\Addins\%%Y\StructuralImport\StructuralImport.dll" (
        del /q "%APPDATA%\Autodesk\Revit\Addins\%%Y\StructuralImport\StructuralImport.dll"
        rmdir "%APPDATA%\Autodesk\Revit\Addins\%%Y\StructuralImport" 2>nul
        set "GONE=1"
        echo [%%Y] add-in files removed
    )
)
if "%GONE%"=="0" echo Nothing to remove.
echo.
echo Restart Revit if it is open.
pause
