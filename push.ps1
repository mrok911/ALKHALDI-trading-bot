#  سكربت رفع التعديلات إلى GitHub
cd "D:\PYTHON TELEGRAM\TELE BOT"

Write-Output " تحديث المستودع من GitHub..."
git pull origin main --allow-unrelated-histories

Write-Output " رفع الملفات المعدلة..."
git add .
git commit -m "Auto Update Bot - 2025-09-26 18:17:43"
git push origin main

Write-Output " العملية تمت بنجاح!"
pause
