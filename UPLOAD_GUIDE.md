# 🚀 Upload Guide to Contabo Server

## 📦 **Files Ready for Upload:**
- `luxeoverhemden-server.tar.gz` (56.7 MB) - Compressed deployment package
- `deployment_final/` folder - Uncompressed files

## 🔧 **Upload Methods:**

### **Method 1: SCP (Secure Copy) - Recommended**

```bash
# Upload the compressed file
scp luxeoverhemden-server.tar.gz username@your-contabo-ip:/path/to/robot/

# Then SSH to your server and extract
ssh username@your-contabo-ip
cd /path/to/robot/
tar -xzf luxeoverhemden-server.tar.gz
```

### **Method 2: SFTP (File Transfer Protocol)**

```bash
# Connect via SFTP
sftp username@your-contabo-ip

# Navigate to robot directory
cd /path/to/robot/

# Upload the compressed file
put luxeoverhemden-server.tar.gz

# Exit SFTP and SSH to extract
exit
ssh username@your-contabo-ip
cd /path/to/robot/
tar -xzf luxeoverhemden-server.tar.gz
```

### **Method 3: Windows Remote Desktop + File Transfer**

1. **Connect to your Contabo server via Remote Desktop**
2. **Open File Explorer on the server**
3. **Navigate to your robot folder** (usually `C:\robot\`)
4. **Copy the `deployment_final` folder contents** to the robot directory
5. **Replace existing files** (backup old server.exe first)

### **Method 4: Using WinSCP (Windows GUI)**

1. **Download WinSCP** (free SFTP client)
2. **Connect to your Contabo server**
3. **Navigate to robot directory**
4. **Upload `luxeoverhemden-server.tar.gz`**
5. **Extract using built-in archive manager**

## 📋 **Step-by-Step Instructions:**

### **Option A: Command Line (Linux/Mac)**

```bash
# 1. Upload the compressed file
scp luxeoverhemden-server.tar.gz username@your-contabo-ip:/home/username/robot/

# 2. SSH to your server
ssh username@your-contabo-ip

# 3. Navigate to robot directory
cd /home/username/robot/

# 4. Backup existing server.exe (optional)
mv server.exe server.exe.backup

# 5. Extract new files
tar -xzf luxeoverhemden-server.tar.gz

# 6. Set executable permissions
chmod +x server.exe

# 7. Test the new server
./server.exe
```

### **Option B: Windows Remote Desktop**

1. **Connect to Contabo server via Remote Desktop**
2. **Open File Explorer**
3. **Navigate to `C:\robot\` (or your robot folder)**
4. **Copy all files from `deployment_final` folder**
5. **Paste into robot directory** (replace existing files)
6. **Double-click `server.exe` to test**

## 🔍 **Verification Steps:**

After upload, verify these files exist:
- ✅ `server.exe` (57.1 MB)
- ✅ `app.ico`
- ✅ `config.py`
- ✅ `.env`
- ✅ `translate_mapping.txt`
- ✅ `input.csv`
- ✅ `autoimport.txt`
- ✅ `products/` folder

## 🚀 **Testing the Upload:**

```bash
# Test GUI mode
./server.exe

# Test command line mode
./server.exe profuomo
```

## 📞 **Troubleshooting:**

- **Permission denied**: Run `chmod +x server.exe`
- **File not found**: Check file paths and extraction
- **Missing dependencies**: Ensure all files were uploaded
- **Icon not showing**: Verify `app.ico` is in the same directory

## 🎯 **Quick Commands:**

```bash
# Upload and extract in one go
scp luxeoverhemden-server.tar.gz user@ip:/path/ && ssh user@ip "cd /path && tar -xzf luxeoverhemden-server.tar.gz && chmod +x server.exe"
```
