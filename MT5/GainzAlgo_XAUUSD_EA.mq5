//+------------------------------------------------------------------+
//|  GainzAlgo V2 Alpha - XAUUSD EA                                  |
//|  Strategy: 2 Buy Conditions + 2 Sell Conditions                  |
//|  Minimum Risk | Confirmed Entries | MT5 Mobile Compatible         |
//+------------------------------------------------------------------+
#property copyright "Pro-Tech Trading"
#property version   "2.0"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade         trade;
CPositionInfo  posInfo;

//--- Input Parameters
input group    "=== SYMBOL & TIMEFRAME ==="
input string   InpSymbol        = "XAUUSD";       // Symbol (Gold)
input ENUM_TIMEFRAMES InpTF     = PERIOD_M15;      // Timeframe (M15 recommended)

input group    "=== RISK MANAGEMENT ==="
input double   InpLotSize       = 0.01;            // Lot Size (start small)
input double   InpSL_Points     = 150;             // Stop Loss (points) ~15 pips gold
input double   InpTP_Multiplier = 2.0;             // TP = SL x this multiplier (RR ratio)
input double   InpMaxDailyLoss  = 50.0;            // Max daily loss in account currency ($)
input int      InpMaxTrades     = 1;               // Max open trades at once
input double   InpSpreadFilter  = 30;              // Max spread allowed (points)

input group    "=== GAINZALGO V2 ALPHA SETTINGS ==="
input int      InpFastEMA       = 8;               // Fast EMA period
input int      InpSlowEMA       = 21;              // Slow EMA period
input int      InpTrendEMA      = 200;             // Trend EMA (200)
input int      InpRSI_Period    = 14;              // RSI period
input int      InpRSI_OB        = 70;              // RSI Overbought level
input int      InpRSI_OS        = 30;              // RSI Oversold level
input int      InpATR_Period    = 14;              // ATR period (dynamic SL)
input bool     InpUseATR_SL     = true;            // Use ATR for dynamic Stop Loss
input double   InpATR_Mult      = 1.5;             // ATR SL multiplier

input group    "=== MACD SETTINGS ==="
input int      InpMACD_Fast     = 12;              // MACD Fast EMA
input int      InpMACD_Slow     = 26;              // MACD Slow EMA
input int      InpMACD_Signal   = 9;               // MACD Signal

input group    "=== CANDLE PATTERN ==="
input double   InpEngulfFactor  = 1.2;             // Engulf body factor (body ratio)
input bool     InpRequireEngulf = false;           // Require engulfing candle (Cond 2)

input group    "=== ALERTS ==="
input bool     InpAlertBuy      = true;            // Alert on Buy signal
input bool     InpAlertSell     = true;            // Alert on Sell signal
input bool     InpNotification  = true;            // Push notification to phone

//--- Indicator handles
int hFastEMA, hSlowEMA, hTrendEMA, hRSI, hMACD, hATR;

//--- Global vars
datetime lastBarTime  = 0;
double   dailyLoss    = 0;
datetime dailyDate    = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   hFastEMA  = iMA(InpSymbol, InpTF, InpFastEMA,  0, MODE_EMA, PRICE_CLOSE);
   hSlowEMA  = iMA(InpSymbol, InpTF, InpSlowEMA,  0, MODE_EMA, PRICE_CLOSE);
   hTrendEMA = iMA(InpSymbol, InpTF, InpTrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI      = iRSI(InpSymbol, InpTF, InpRSI_Period, PRICE_CLOSE);
   hMACD     = iMACD(InpSymbol, InpTF, InpMACD_Fast, InpMACD_Slow, InpMACD_Signal, PRICE_CLOSE);
   hATR      = iATR(InpSymbol, InpTF, InpATR_Period);

   if(hFastEMA==INVALID_HANDLE || hSlowEMA==INVALID_HANDLE ||
      hTrendEMA==INVALID_HANDLE || hRSI==INVALID_HANDLE ||
      hMACD==INVALID_HANDLE || hATR==INVALID_HANDLE)
   {
      Alert("GainzAlgo EA: Failed to create indicator handles!");
      return(INIT_FAILED);
   }

   trade.SetExpertMagicNumber(20250001);
   trade.SetDeviationInPoints(10);
   Print("GainzAlgo V2 Alpha EA initialized on ", InpSymbol);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(hFastEMA);
   IndicatorRelease(hSlowEMA);
   IndicatorRelease(hTrendEMA);
   IndicatorRelease(hRSI);
   IndicatorRelease(hMACD);
   IndicatorRelease(hATR);
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Only run on new candle (bar)
   datetime currentBar = iTime(InpSymbol, InpTF, 0);
   if(currentBar == lastBarTime) return;
   lastBarTime = currentBar;

   // Reset daily loss tracker at start of new day
   MqlDateTime today;
   TimeToStruct(TimeCurrent(), today);
   MqlDateTime lastDay;
   TimeToStruct(dailyDate, lastDay);
   if(today.day != lastDay.day)
   {
      dailyLoss = 0;
      dailyDate = TimeCurrent();
   }

   // Max daily loss check
   if(dailyLoss >= InpMaxDailyLoss)
   {
      Comment("GainzAlgo: Daily loss limit reached. No new trades today.");
      return;
   }

   // Spread filter
   double spread = SymbolInfoInteger(InpSymbol, SYMBOL_SPREAD) * SymbolInfoDouble(InpSymbol, SYMBOL_POINT);
   if(SymbolInfoInteger(InpSymbol, SYMBOL_SPREAD) > InpSpreadFilter)
   {
      Comment("GainzAlgo: Spread too high (", SymbolInfoInteger(InpSymbol, SYMBOL_SPREAD), "). Waiting...");
      return;
   }

   // Max trades filter
   if(CountOpenTrades() >= InpMaxTrades)
   {
      Comment("GainzAlgo: Max trades open (", InpMaxTrades, ")");
      return;
   }

   // Get indicator values (current bar [1] = last closed bar)
   double fastEMA[3], slowEMA[3], trendEMA[3];
   double rsi[3], macd_main[3], macd_signal[3], atr[3];

   if(!CopyBuffer(hFastEMA,  0, 1, 3, fastEMA))   return;
   if(!CopyBuffer(hSlowEMA,  0, 1, 3, slowEMA))   return;
   if(!CopyBuffer(hTrendEMA, 0, 1, 3, trendEMA))  return;
   if(!CopyBuffer(hRSI,      0, 1, 3, rsi))       return;
   if(!CopyBuffer(hMACD,     0, 1, 3, macd_main)) return;
   if(!CopyBuffer(hMACD,     1, 1, 3, macd_signal)) return;
   if(!CopyBuffer(hATR,      0, 1, 3, atr))       return;

   double price = SymbolInfoDouble(InpSymbol, SYMBOL_BID);

   // Dynamic SL using ATR
   double slDist = InpUseATR_SL
                   ? atr[0] * InpATR_Mult
                   : InpSL_Points * SymbolInfoDouble(InpSymbol, SYMBOL_POINT);
   double tpDist = slDist * InpTP_Multiplier;

   //--- Check candle patterns (for condition 2)
   double open1  = iOpen(InpSymbol, InpTF, 1);
   double close1 = iClose(InpSymbol, InpTF, 1);
   double open2  = iOpen(InpSymbol, InpTF, 2);
   double close2 = iClose(InpSymbol, InpTF, 2);

   bool bullishEngulf = (close1 > open1) &&                        // Current bar bullish
                        (close2 < open2) &&                        // Previous bar bearish
                        (close1 > open2) &&                        // Current close > prev open
                        ((close1 - open1) > (open2 - close2) * InpEngulfFactor);

   bool bearishEngulf = (close1 < open1) &&
                        (close2 > open2) &&
                        (close1 < open2) &&
                        ((open1 - close1) > (close2 - open2) * InpEngulfFactor);

   //--------------------------------------------------------------------
   //  BUY CONDITION 1: Trend UP + EMA Crossover + RSI crosses above 50
   //  - Price above EMA 200 (uptrend)
   //  - Fast EMA crosses ABOVE Slow EMA (momentum bullish)
   //  - RSI: was below 50, now above 50 (confirmation)
   //--------------------------------------------------------------------
   bool buy1 = (price > trendEMA[0]) &&                  // Above EMA200 = uptrend
               (fastEMA[0] > slowEMA[0]) &&              // Fast > Slow (bullish cross)
               (fastEMA[1] <= slowEMA[1]) &&             // Was below (just crossed)
               (rsi[0] > 50) && (rsi[1] <= 50);         // RSI crossed above 50

   //--------------------------------------------------------------------
   //  BUY CONDITION 2: Trend UP + MACD Bullish Cross + Bullish Candle
   //  - Price above EMA 200 (uptrend)
   //  - MACD crosses above signal line from below zero
   //  - Bullish engulfing candle (if enabled) OR strong bullish close
   //--------------------------------------------------------------------
   bool macdBullCross = (macd_main[0] > macd_signal[0]) &&   // MACD above signal now
                        (macd_main[1] <= macd_signal[1]);    // Was below signal

   bool candleBullish = InpRequireEngulf ? bullishEngulf : (close1 > open1 && (close1 - open1) > atr[0] * 0.5);

   bool buy2 = (price > trendEMA[0]) &&    // Above EMA200
               macdBullCross &&            // MACD bullish crossover
               candleBullish &&            // Bullish candle pattern
               (rsi[0] < InpRSI_OB);      // Not overbought

   //--------------------------------------------------------------------
   //  SELL CONDITION 1: Trend DOWN + EMA Crossover + RSI crosses below 50
   //  - Price below EMA 200 (downtrend)
   //  - Fast EMA crosses BELOW Slow EMA (momentum bearish)
   //  - RSI: was above 50, now below 50 (confirmation)
   //--------------------------------------------------------------------
   bool sell1 = (price < trendEMA[0]) &&                  // Below EMA200 = downtrend
                (fastEMA[0] < slowEMA[0]) &&              // Fast < Slow (bearish cross)
                (fastEMA[1] >= slowEMA[1]) &&             // Was above (just crossed)
                (rsi[0] < 50) && (rsi[1] >= 50);         // RSI crossed below 50

   //--------------------------------------------------------------------
   //  SELL CONDITION 2: Trend DOWN + MACD Bearish Cross + Bearish Candle
   //  - Price below EMA 200 (downtrend)
   //  - MACD crosses below signal line from above zero
   //  - Bearish engulfing candle (if enabled) OR strong bearish close
   //--------------------------------------------------------------------
   bool macdBearCross = (macd_main[0] < macd_signal[0]) &&
                        (macd_main[1] >= macd_signal[1]);

   bool candleBearish = InpRequireEngulf ? bearishEngulf : (close1 < open1 && (open1 - close1) > atr[0] * 0.5);

   bool sell2 = (price < trendEMA[0]) &&  // Below EMA200
                macdBearCross &&           // MACD bearish crossover
                candleBearish &&           // Bearish candle pattern
                (rsi[0] > InpRSI_OS);     // Not oversold

   //--- Execute BUY
   if(buy1 || buy2)
   {
      double sl = price - slDist;
      double tp = price + tpDist;
      string reason = buy1 ? "BUY1: EMA Cross + RSI>50" : "BUY2: MACD Cross + Candle";

      if(trade.Buy(InpLotSize, InpSymbol, 0, sl, tp, reason))
      {
         string msg = StringFormat("XAUUSD BUY [%s] | SL: %.2f | TP: %.2f | Price: %.2f", reason, sl, tp, price);
         Print(msg);
         if(InpAlertBuy)       Alert(msg);
         if(InpNotification)   SendNotification(msg);
      }
      return;
   }

   //--- Execute SELL
   if(sell1 || sell2)
   {
      double sl = price + slDist;
      double tp = price - tpDist;
      string reason = sell1 ? "SELL1: EMA Cross + RSI<50" : "SELL2: MACD Cross + Candle";

      if(trade.Sell(InpLotSize, InpSymbol, 0, sl, tp, reason))
      {
         string msg = StringFormat("XAUUSD SELL [%s] | SL: %.2f | TP: %.2f | Price: %.2f", reason, sl, tp, price);
         Print(msg);
         if(InpAlertSell)      Alert(msg);
         if(InpNotification)   SendNotification(msg);
      }
      return;
   }

   // Dashboard comment on chart
   string dashboard = StringFormat(
      "=== GainzAlgo V2 Alpha | XAUUSD ===\n"
      "Price: %.2f | EMA200: %.2f\n"
      "FastEMA: %.2f | SlowEMA: %.2f\n"
      "RSI: %.1f | ATR: %.2f\n"
      "MACD: %.4f | Signal: %.4f\n"
      "Daily Loss: $%.2f / $%.2f\n"
      "BUY1: %s | BUY2: %s\n"
      "SELL1: %s | SELL2: %s",
      price, trendEMA[0],
      fastEMA[0], slowEMA[0],
      rsi[0], atr[0],
      macd_main[0], macd_signal[0],
      dailyLoss, InpMaxDailyLoss,
      buy1  ? "READY" : "---",
      buy2  ? "READY" : "---",
      sell1 ? "READY" : "---",
      sell2 ? "READY" : "---"
   );
   Comment(dashboard);
}

//+------------------------------------------------------------------+
//| Count open trades for this EA                                    |
//+------------------------------------------------------------------+
int CountOpenTrades()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(posInfo.SelectByIndex(i))
         if(posInfo.Symbol() == InpSymbol && posInfo.Magic() == 20250001)
            count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Track daily P&L (called from OnTradeTransaction)                 |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      HistoryDealSelect(trans.deal);
      double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
      if(profit < 0) dailyLoss += MathAbs(profit);
   }
}
//+------------------------------------------------------------------+
