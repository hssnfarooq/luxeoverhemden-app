@echo off
echo Rebuilding app.exe using the working approach...
echo.

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Build the folder-based Windows app bundle
pyinstaller --clean --noconfirm app.spec

REM Check if build was successful
if exist dist\app\app.exe (
    echo.
    echo ✅ Build successful!
    echo 📁 app.exe created in dist folder
    echo 🚀 Ready to test
    echo.
    echo Testing the executable...
    dist\app\app.exe
) else (
    echo.
    echo ❌ Build failed!
    echo Check the error messages above
)

pause
