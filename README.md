# Telegram Gift Shop Bot

این نسخه به‌جای Provider خارجی، مستقیماً از Telegram Bot API برای فروش و تحویل Gift استفاده می‌کند.

## جریان کار

1. ربات Giftهای قابل ارسال را با `getAvailableGifts` می‌گیرد.
2. قیمت فروش را بر اساس `MARKUP_PERCENT` محاسبه می‌کند.
3. کاربر Gift و گیرنده را انتخاب می‌کند.
4. فاکتور با Telegram Stars (`XTR`) ساخته می‌شود.
5. بعد از `successful_payment`، ربات با `sendGift` Gift را مستقیم برای گیرنده می‌فرستد.
6. اگر تحویل ناموفق باشد، ربات تلاش می‌کند پرداخت Stars را با `refundStarPayment` برگرداند.
7. سفارش‌ها در SQLite ثبت می‌شوند و انتقال پرداخت با وضعیت سفارش idempotent شده است.

## نکته مهم درباره گیرنده

Bot API برای یک کاربر خصوصی، تبدیل دلخواه `@username` به `user_id` را در اختیار ربات نمی‌گذارد. بنابراین گیرنده باید قبلاً `/start` را برای همین ربات فرستاده باشد تا username و Telegram ID او در دیتابیس ثبت شود؛ یا مستقیماً Telegram ID عددی او وارد شود.

## پیش‌نیاز

- Ubuntu 22.04/24.04 یا هر Linux مشابه
- Python 3.11+
- یک Bot Token از BotFather
- Telegram Stars کافی در موجودی ربات برای انجام fulfillment

## نصب

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

git clone https://github.com/TheOnlyOneWithAi/v2ray_monitor.git
cd v2ray_monitor

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
nano .env
```

متغیرهای ضروری:

```env
BOT_TOKEN=توکن_ربات
ADMIN_IDS=123456789
```

سپس اجرا:

```bash
set -a
source .env
set +a
python shop_bot.py
```

## راه‌اندازی دائمی با systemd

```bash
sudo mkdir -p /opt/gift-shop
sudo cp -a . /opt/gift-shop/
sudo chown -R $USER:$USER /opt/gift-shop
```

سرویس را بساز:

```bash
sudo nano /etc/systemd/system/gift-shop.service
```

```ini
[Unit]
Description=Telegram Gift Shop Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/gift-shop
EnvironmentFile=/opt/gift-shop/.env
ExecStart=/opt/gift-shop/.venv/bin/python /opt/gift-shop/shop_bot.py
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

سپس:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gift-shop
sudo systemctl status gift-shop
journalctl -u gift-shop -f
```

## دستورات ادمین

- `/syncgifts` همگام‌سازی دستی Giftها
- `/stats` آمار
- `/balance` موجودی Stars ربات

یا از پنل داخل ربات استفاده کن.

## قیمت‌گذاری

مثلاً اگر `MARKUP_PERCENT=10` باشد، Gift با قیمت پایه 100 Stars با قیمت 110 Stars عرضه می‌شود.

## پرداخت

برای Digital Goods داخل Telegram از Telegram Stars (`XTR`) استفاده می‌شود. `provider_token` خارجی لازم نیست.

## تحویل واقعی

این پروژه API جعلی یا Provider فرضی ندارد. خط اصلی تحویل:

```python
await bot.send_gift(user_id=recipient_id, gift_id=gift_id)
```

پس Gift واقعاً توسط Telegram ارسال می‌شود و موفقیت تحویل فقط وقتی ثبت می‌شود که Telegram پاسخ موفق بدهد.
