# 🔧 Windows Compatibility Fix Guide

## ❌ **Problem:**
The `server.exe` built on macOS is not compatible with your Windows Contabo server, showing "This app can't run on your PC" error.

## ✅ **Solutions:**

### **Solution 1: Run Python Source Directly (Recommended)**

This is the most reliable approach - run the Python source code directly on your Windows server.

#### **Step 1: Upload Source Code**
```bash
# Upload the source code package
scp luxeoverhemden-source.tar.gz username@your-contabo-ip:/path/to/robot/
```

#### **Step 2: Extract and Setup on Windows Server**
```bash
# SSH to your server
ssh username@your-contabo-ip

# Navigate to robot directory
cd /path/to/robot/

# Extract source code
tar -xzf luxeoverhemden-source.tar.gz

# Install Python (if not already installed)
# Download Python from python.org and install

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

#### **Step 3: Create Windows Batch Files**
Create these batch files for easy execution:

**`start_server.bat`:**
```batch
@echo off
cd /d "%~dp0"
python app.py
pause
```

**`run_casamoda.bat`:**
```batch
@echo off
cd /d "%~dp0"
python app.py casamoda
pause
```

**`run_profuomo.bat`:**
```batch
@echo off
cd /d "%~dp0"
python app.py profuomo
pause
```

**`run_autoimport.bat`:**
```batch
@echo off
cd /d "%~dp0"
python app.py autoimport
pause
```

### **Solution 2: Build on Windows Machine**

If you have access to a Windows machine:

#### **Step 1: Transfer Source Code**
```bash
# Copy source code to Windows machine
scp -r luxeoverhemden-source.tar.gz user@windows-machine:/path/
```

#### **Step 2: Build on Windows**
```bash
# On Windows machine
tar -xzf luxeoverhemden-source.tar.gz
cd luxeoverhemden-app
pip install -r requirements.txt
pyinstaller --clean --noconfirm app.spec
```

#### **Step 3: Upload Windows-built Executable**
```bash
# Upload the Windows-built server.exe
scp dist/server.exe username@your-contabo-ip:/path/to/robot/
```

### **Solution 3: Use Docker (Advanced)**

Create a Docker container that runs on Windows:

#### **Dockerfile:**
```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

EXPOSE 8000
CMD ["python", "app.py"]
```

#### **Build and Run:**
```bash
docker build -t luxeoverhemden .
docker run -p 8000:8000 luxeoverhemden
```

## 🚀 **Recommended Approach:**

**Use Solution 1 (Python Source)** because:
- ✅ No architecture compatibility issues
- ✅ Easy to debug and modify
- ✅ No need for Windows build machine
- ✅ Direct access to source code
- ✅ Easy to update and maintain

## 📋 **Quick Setup Commands:**

```bash
# 1. Upload source code
scp luxeoverhemden-source.tar.gz username@your-contabo-ip:/path/to/robot/

# 2. SSH to server
ssh username@your-contabo-ip

# 3. Extract and setup
cd /path/to/robot/
tar -xzf luxeoverhemden-source.tar.gz
pip install -r requirements.txt

# 4. Run application
python app.py
```

## 🔍 **Verification:**

After setup, verify:
- ✅ Python is installed: `python --version`
- ✅ Dependencies installed: `pip list`
- ✅ Application runs: `python app.py`
- ✅ Web interface accessible: `http://localhost:8000`

## 📞 **Troubleshooting:**

- **Python not found**: Install Python from python.org
- **Permission denied**: Run as administrator
- **Dependencies missing**: Run `pip install -r requirements.txt`
- **Port 8000 in use**: Change port in app.py or kill existing process

## 🎯 **Files Created:**
- `luxeoverhemden-source.tar.gz` - Complete source code package
- `app.spec` - Windows build spec
- `WINDOWS_FIX_GUIDE.md` - This guide
