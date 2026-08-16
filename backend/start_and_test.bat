@echo off
cd /d "%~dp1"
set NODE_ENV=production
node dist/server.js > server.log 2>&1
timeout /t 3 /nobreak >nul
echo Testing health endpoints...
curl -s http://localhost:4000/ 2>&1
echo.
curl -s http://localhost:4000/ready 2>&1
echo.
echo Done.