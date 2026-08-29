# Telegram Gift Shop Bot

این نسخه مستقیماً از Telegram Bot API برای فروش و تحویل Gift استفاده می‌کند.

## جریان خرید

1. کاربر `/start` را می‌زند و در دیتابیس ثبت می‌شود.
2. کاربر Gift موردنظر را انتخاب می‌کند.
3. روی **خرید برای خودم** می‌زند.
4. ربات برای همان کاربر فاکتور Telegram Stars (`XTR`) می‌فرستد.
5. کاربر خودش پرداخت را انجام می‌دهد.
6. بعد از `successful_payment`، ربات همان Gift را مستقیماً به **همان Telegram user ID پرداخت‌کننده** ارسال می‌کند.
7. اگر تحویل ناموفق باشد، ربات تلاش می‌کند پرداخت Stars را refund کند.

**هیچ مرحله‌ای برای وارد کردن گیرنده وجود ندارد.** در هر سفارش، `buyer_id == recipient_id` است.

## پیش‌نیاز

- Ubuntu 22.04/24.04 یا Linux مشابه
- Python 3.11+
- Bot Token
- Telegram Stars کافی در موجودی ربات برای fulfillment

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

`.env`:

```env
BOT_TOKEN=توکن_ربات
ADMIN_IDS=123456789
MARKUP_PERCENT=10
AUTO_SYNC_SECONDS=300
```

اجرا:

```bash
set -a
source .env
set +a
python shop_bot.py
```

## systemd

```bash
sudo mkdir -p /opt/gift-shop
sudo cp -a . /opt/gift-shop/
sudo chown -R $USER:$USER /opt/gift-shop
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

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gift-shop
sudo systemctl status gift-shop
journalctl -u gift-shop -f
```

## دستورات ادمین

- `/syncgifts` — همگام‌سازی Giftها
- `/stats` — آمار کاربران و سفارش‌ها
- `/balance` — موجودی Stars ربات

## قیمت‌گذاری

اگر `MARKUP_PERCENT=10` باشد، Gift با قیمت پایه 100 Stars با قیمت 110 Stars عرضه می‌شود.

## پرداخت و تحویل

برای Digital Goods از Telegram Stars (`XTR`) استفاده می‌شود. بعد از پرداخت، مقصد از روی `message.from_user.id` تعیین می‌شود؛ بنابراین کاربر فقط Gift را انتخاب و خودش پرداخت می‌کند و Gift برای خودش ارسال می‌شود.

تحویل با Bot API انجام می‌شود:

```python
await bot.send_gift(user_id=buyer_id, gift_id=gift_id)
```

در صورت شکست تحویل، ربات تلاش می‌کند پرداخت را با `refundStarPayment` برگرداند.
