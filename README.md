# Almokhtar Telegram Bot — JSON Storage (بدون PostgreSQL)

بوت منظم (مستخدم + أدمن) مع اشتراك إجباري بالقناة + Wallet (balance/hold) + نظام طلبات (pending/approved/rejected)
وتخزين كامل داخل ملفات JSON ضمن مجلد data/ (أو DATA_DIR).

## المتطلبات
- Python 3.10+ (يفضل 3.11)
- حساب بوت من BotFather

## التثبيت محلياً
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## إعداد ENV
انسخ `.env.example` إلى `.env` وعدّل القيم.

## التشغيل
```bash
python main.py
```

## Railway (Polling)
- ارفع المشروع إلى GitHub
- اربطه في Railway
- ضع المتغيرات في Variables
- تشغيل Polling (بدون webhook)
