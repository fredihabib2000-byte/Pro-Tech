# GainzAlgo V2 Alpha - XAUUSD EA | دليل الإعداد

## الشروط الأربعة | 4 Trade Conditions

### BUY 1 - شراء اول ✅
- السعر فوق EMA 200 (ترند صاعد)
- Fast EMA (8) تقطع فوق Slow EMA (21)
- RSI يعبر من تحت 50 الى فوق 50
- **= 3 شروط مجتمعة = دخول مضمون**

### BUY 2 - شراء ثاني ✅
- السعر فوق EMA 200
- MACD يعبر فوق Signal Line
- شمعة صاعدة قوية (أكبر من 50% ATR)
- RSI مش في منطقة overbought
- **= 4 شروط مجتمعة = أقوى إشارة**

### SELL 1 - بيع اول 🔻
- السعر تحت EMA 200 (ترند هابط)
- Fast EMA (8) تقطع تحت Slow EMA (21)
- RSI يعبر من فوق 50 الى تحت 50
- **= 3 شروط مجتمعة = دخول مضمون**

### SELL 2 - بيع ثاني 🔻
- السعر تحت EMA 200
- MACD يعبر تحت Signal Line
- شمعة هابطة قوية
- RSI مش في منطقة oversold
- **= 4 شروط مجتمعة = أقوى إشارة**

---

## إعداد Risk Management

| Setting | القيمة المنصوحة | الشرح |
|---------|----------------|-------|
| Lot Size | 0.01 | ابدا صغير |
| Stop Loss | ATR x 1.5 | ديناميكي حسب السوق |
| Take Profit | SL x 2.0 | نسبة 1:2 |
| Max Daily Loss | $50 | وقف اليوم |
| Max Trades | 1 | صفقة واحدة بس |
| Spread Filter | 30 points | ما تدخل وقت spread عالي |

---

## طريقة التثبيت على MT5 Mobile

1. **انسخ الملف** `GainzAlgo_XAUUSD_EA.mq5` على الكمبيوتر
2. **MT5 Desktop**: افتح MetaEditor → File → Open → حدد الملف → Compile
3. **نقل للموبايل**: افتح MT5 → Chart → Expert Advisors → ضع EA على الشارت
4. **الإشعارات على الموبايل**:
   - MT5 Desktop: Tools → Options → Notifications → ادخل MetaQuotes ID
   - من الموبايل: افتح MT5 → Settings → Messages → اشتقل MetaQuotes ID
   - ارجع للكمبيوتر والصق الـ ID في الإعدادات

---

## الـ Timeframes المنصوحة لـ XAUUSD

| Timeframe | نوع التداول | عدد الإشارات |
|-----------|------------|-------------|
| M15 | Scalping سريع | كثيرة |
| H1 | Intraday | معتدلة ✅ منصوح |
| H4 | Swing | قليلة لكن أقوى |

---

## ملاحظات مهمة

- **GainzAlgo V2 Alpha** الأصلي على TradingView: استخدمه كـ confirmation إضافية
- EA بيشتغل على **آخر شمعة مغلقة** = ما في repaint
- `InpRequireEngulf = true` = شرط أصعب لكن أكثر دقة
- ابدا دايماً بـ **Demo Account** قبل الحساب الحقيقي
- الـ EA ما يضمن ربح — إدارة رأس المال هي الأهم
