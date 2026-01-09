from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def get_admin_menu(user_id: int, super_admin_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📋 الطلبات المعلقة", callback_data="admin:pending_orders"),
            InlineKeyboardButton("📜 آخر الطلبات", callback_data="admin:recent_orders"),
        ],
        [
            InlineKeyboardButton("🔢 إدارة أكواد سيرياتيل", callback_data="admin:syriatel_codes"),
            InlineKeyboardButton("👥 مخزون ايشانسي", callback_data="admin:eish_pool"),
        ],
        [
            InlineKeyboardButton("📢 رسالة جماعية", callback_data="admin:broadcast"),
            InlineKeyboardButton("🔧 وضع الصيانة", callback_data="admin:maintenance"),
        ],
    ]
    if user_id == super_admin_id:
        keyboard.append([InlineKeyboardButton("👨‍💼 إدارة الأدمن", callback_data="admin:manage_admins")])
    keyboard.append([InlineKeyboardButton("🔙 إغلاق اللوحة", callback_data="main:back")])
    return InlineKeyboardMarkup(keyboard)
