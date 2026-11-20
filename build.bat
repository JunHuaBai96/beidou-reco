@echo off
REM 北斗短剧素材推荐助手 打包脚本
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --onefile --noconsole --name BeidouReco ^
  --add-data "web;web" --add-data "assets;assets" app.py
echo 产物: dist\BeidouReco.exe
echo 自检: set BEIDOU_API_KEY=bd_xxx && dist\BeidouReco.exe --selftest
