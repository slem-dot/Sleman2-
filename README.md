# Railway Telegram Bot (Python)

## الملفات
- `main.py` كود البوت
- `requirements.txt` المتطلبات
- `.env.example` مثال متغيرات البيئة

## النشر على Railway (مختصر)
1) ارفع هذا المشروع إلى GitHub (أو ارفع ZIP في Railway إن كنت تستخدم Deploy from repo/zip)
2) في Railway > Variables ضع:
   - `BOT_TOKEN`
   - `SUPER_ADMIN_ID`
   - `SYRIATEL_USERNAME`
   - `SYRIATEL_PASSWORD`
   - `SYRIATEL_CASH_CODE` (اختياري)
   - `DATA_DIR=/app/data`

3) Start Command (إذا طلبه Railway):
   - `python main.py`

## ملاحظات مهمة
- هذا المشروع يستخدم Selenium، وقد تحتاج بيئة فيها Chromium/Chrome لكي يعمل جزء التحقق.
- يفضل تفعيل Volume على Railway وربطه بمسار `/app/data` حتى لا تضيع ملفات JSON عند إعادة التشغيل.
