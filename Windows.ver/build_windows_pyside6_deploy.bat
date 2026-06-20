@echo off
cd /d "%~dp0src"
pyside6-deploy main.py --name ShangBackground --extra-modules Svg,Network --extra-ignore-dirs __pycache__,build,dist -f --keep-deployment-files
