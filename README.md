# ربات نمونه — اطلاعات اشتراک 3x-ui

ربات تلگرام که کاربر **لینک subscription** می‌فرستد و ربات از API پنل [3x-ui](https://github.com/MHSanaei/3x-ui/wiki/Configuration#api-documentation) اطلاعات کلاینت را می‌خواند.

## راه‌اندازی

```bash
cd subbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp panels.json.example panels.json
cp .env.example .env
# ویرایش panels.json و .env
python bot.py
```

## `panels.json`

کلید هر پنل = **آدرس پایهٔ لینک ساب** (همان چیزی که قبل از توکن می‌آید):

```json
{
  "https://206.71.158.69:2096/sub": {
    "name": "سرور ۱",
    "api_url": "https://206.71.158.69:2053",
    "username": "admin",
    "password": "رمز پنل"
  }
}
```

- `api_url`: آدرس وب‌پنل (معمولاً پورت 2053)
- لینک ساب معمولاً پورت 2096 است (`/sub/TOKEN`)

## نحوه کار

1. کاربر لینکی مثل `https://host:2096/sub/a09sdzfhq22n0lor` می‌فرستد
2. ربات `sub_base` و `subId` را استخراج می‌کند
3. پنل متناظر از `panels.json` پیدا می‌شود
4. لاگین API → جستجوی کلاینت با `subId` در اینباندها → `getClientTraffics`
5. نمایش حجم، انقضا، وضعیت فعال/غیرفعال

## متغیرهای محیطی

| متغیر | توضیح |
|--------|--------|
| `TELEGRAM_BOT_TOKEN` | توکن ربات از [@BotFather](https://t.me/BotFather) |
| `PANELS_FILE` | مسیر فایل پنل‌ها (پیش‌فرض: `panels.json`) |
| `VERIFY_SSL` | `true` اگر گواهی SSL معتبر است |
