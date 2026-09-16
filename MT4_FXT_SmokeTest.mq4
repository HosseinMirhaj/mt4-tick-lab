#property strict
#property version   "1.000"
#property description "Read-only FXT smoke test: counts tester ticks and never trades"

long     g_ticks = 0;
datetime g_first_time = 0;
datetime g_last_time = 0;
double   g_first_bid = 0.0;
double   g_last_bid = 0.0;
double   g_min_bid = 0.0;
double   g_max_bid = 0.0;
double   g_min_spread = 0.0;
double   g_max_spread = 0.0;

int OnInit()
{
   if(!MQLInfoInteger(MQL_TESTER))
   {
      Print("MT4 Tick Lab: this diagnostic EA only runs in Strategy Tester.");
      return INIT_FAILED;
   }
   Print("MT4 Tick Lab FXT smoke test started: ", _Symbol, " M", _Period);
   return INIT_SUCCEEDED;
}

void OnTick()
{
   RefreshRates();
   double point = MarketInfo(_Symbol, MODE_POINT);
   double spread = point > 0.0 ? (Ask - Bid) / point : 0.0;
   datetime now = TimeCurrent();
   if(g_ticks == 0)
   {
      g_first_time = now;
      g_first_bid = Bid;
      g_min_bid = Bid;
      g_max_bid = Bid;
      g_min_spread = spread;
      g_max_spread = spread;
   }
   else
   {
      if(Bid < g_min_bid) g_min_bid = Bid;
      if(Bid > g_max_bid) g_max_bid = Bid;
      if(spread < g_min_spread) g_min_spread = spread;
      if(spread > g_max_spread) g_max_spread = spread;
   }
   g_ticks++;
   g_last_time = now;
   g_last_bid = Bid;
}

void OnDeinit(const int reason)
{
   string root = "MT4TickLab";
   FolderCreate(root);
   string path = root + "\\fxt_smoke_result.csv";
   int handle = FileOpen(path, FILE_CSV|FILE_WRITE|FILE_ANSI|FILE_SHARE_READ, ',');
   if(handle != INVALID_HANDLE)
   {
      FileWrite(handle, "key", "value");
      FileWrite(handle, "symbol", _Symbol);
      FileWrite(handle, "period_minutes", IntegerToString(_Period));
      FileWrite(handle, "ticks_seen", IntegerToString((int)g_ticks));
      FileWrite(handle, "first_time", TimeToString(g_first_time, TIME_DATE|TIME_SECONDS));
      FileWrite(handle, "last_time", TimeToString(g_last_time, TIME_DATE|TIME_SECONDS));
      FileWrite(handle, "first_bid", DoubleToString(g_first_bid, _Digits));
      FileWrite(handle, "last_bid", DoubleToString(g_last_bid, _Digits));
      FileWrite(handle, "min_bid", DoubleToString(g_min_bid, _Digits));
      FileWrite(handle, "max_bid", DoubleToString(g_max_bid, _Digits));
      FileWrite(handle, "min_spread_points", DoubleToString(g_min_spread, 2));
      FileWrite(handle, "max_spread_points", DoubleToString(g_max_spread, 2));
      FileWrite(handle, "orders_total", IntegerToString(OrdersTotal()));
      FileFlush(handle);
      FileClose(handle);
   }
   Print("MT4 Tick Lab FXT smoke result: ticks=", g_ticks,
         " first=", TimeToString(g_first_time, TIME_DATE|TIME_SECONDS),
         " last=", TimeToString(g_last_time, TIME_DATE|TIME_SECONDS),
         " orders=", OrdersTotal());
}
