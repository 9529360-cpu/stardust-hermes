<div dir="rtl">

# Stardust

> ذاتی استعمال کے لیے آزادانہ طور پر برقرار رکھا جانے والا AI اسسٹنٹ۔

Stardust ایک ذاتی AI اسسٹنٹ پروجیکٹ ہے جس کی دیکھ بھال آزادانہ طور پر کی جاتی ہے۔ یہ **Hermes Agent** کے اوپن سورس کو تکنیکی بنیاد کے طور پر استعمال کرتا ہے، لیکن پروڈکٹ کی سمت، ڈیسک ٹاپ تجربہ، ماڈل روٹنگ، اپ ڈیٹس اور مستقبل کی دیکھ بھال اس repository کے اختیار میں ہیں۔

یہ Hermes کا mirror نہیں ہے اور upstream releases کی پیروی نہیں کرتا۔

## بنیادی اصول

- `9529360-cpu/stardust-hermes` اس پروجیکٹ کے source اور releases کی authority ہے۔
- upstream کے ساتھ خودکار merge، rebase یا update نہیں کیا جاتا۔
- ڈیزائن اور workflows ذاتی، طویل مدتی استعمال کو ترجیح دیتے ہیں۔
- حقیقی credentials، conversations، memory، logs، databases اور private runtime state Git میں شامل نہیں کیے جاتے۔
- اندرونی compatibility کی وجہ سے بعض package، command یا path ناموں میں `hermes` باقی رہ سکتا ہے۔

## انسٹالیشن

صرف Stardust کے اپنے installer استعمال کریں۔

### Linux / macOS / WSL

<div dir="ltr">

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

</div>

### Windows PowerShell

<div dir="ltr">

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

</div>

اصل Hermes installer استعمال کرنے سے upstream Hermes نصب ہوگا، Stardust نہیں۔

مزید maintenance اور privacy قواعد کے لیے [`STARDUST.md`](STARDUST.md) اور security reporting کے لیے [`SECURITY.md`](SECURITY.md) دیکھیں۔

## تکنیکی بنیاد اور لائسنس

Stardust ابتدا میں [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) کے اوپن سورس کوڈ پر بنایا گیا تھا اور اصل Git history، copyright notices اور MIT License برقرار رکھتا ہے۔

**Hermes Agent تکنیکی بنیاد اور code origin ہے؛ Stardust اسی بنیاد پر آزادانہ طور پر تیار اور برقرار رکھا جانے والا ذاتی اسسٹنٹ ہے۔**

یہ Nous Research کی سرکاری distribution نہیں ہے۔

</div>
