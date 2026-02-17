@echo off
echo Rebuilding app.exe using the working approach...
echo.

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Build with the working spec file
pyinstaller --clean --noconfirm app_working.spec

REM Check if build was successful
if exist dist\app.exe (
    echo.
    echo ✅ Build successful!
    echo 📁 app.exe created in dist folder
    echo 🚀 Ready to test
    echo.
    echo Testing the executable...
    dist\app.exe
) else (
    echo.
    echo ❌ Build failed!
    echo Check the error messages above
)

pause
