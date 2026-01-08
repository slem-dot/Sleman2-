# Ichancy Telegram Bot

بوت تيليغرام لإدارة حسابات ايشانسي والمعاملات المالية.

## تشغيل سريع على Railway (مختصر)

1) ارفع المشروع على GitHub
2) Railway > New Project > Deploy from GitHub
3) أضف PostgreSQL من Railway (Plugin) ثم انسخ `DATABASE_URL`
4) Railway Variables: ضع القيم التالية:
- `BOT_TOKEN`
- `SUPER_ADMIN_ID`
- `REQUIRED_CHANNEL`
- `SUPPORT_USERNAME`
- `DATABASE_URL`
- (اختياري) `MIN_TOPUP`, `MIN_WITHDRAW`, `SYRIATEL_CODES`, `DEBUG`, `LOG_LEVEL`

5) بعد أول تشغيل:
- أنشئ الجداول عبر تشغيل `migrations/create_tables.sql` على قاعدة البيانات.

> ملاحظة: هذا المشروع مُهيأ للتشغيل كبوت Polling (بدون Webhook) وهذا مناسب لـ Railway.

## تشغيل محلياً

```bash
pip install -r requirements.txt
cp .env.example .env
# عدّل .env
python main.py
```

