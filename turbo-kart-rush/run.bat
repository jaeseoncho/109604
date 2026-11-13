@echo off
chcp 65001 >nul
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js가 설치되어 있지 않습니다. https://nodejs.org 에서 LTS 버전을 설치한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)
if not exist node_modules (
  echo 처음 실행이라 필요한 파일을 내려받습니다. 1~2분 정도 걸립니다...
  call npm install
)
echo 게임 서버를 시작합니다. 브라우저가 자동으로 열립니다. 종료하려면 이 창을 닫으세요.
start "" http://localhost:5178/
call npx vite --port 5178
pause
