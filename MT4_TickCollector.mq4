#property copyright "MT4 Tick Lab contributors"
#property link      "https://github.com/HosseinMirhaj/mt4-tick-lab"
#property version   "1.100"
#property strict
#property indicator_chart_window
#property indicator_buffers 0

input string OutputRoot             = "MT4TickLab";
input bool   UseCommonFilesFolder    = false;
input bool   IncludeAccountLogin     = false;
input bool   RequireConnectedAccount = true;
input int    FlushEveryTicks         = 100;

int    g_file = INVALID_HANDLE;
string g_session_id;
string g_terminal_id;
string g_day_key;
string g_base_path;
ulong  g_sequence = 0;
int    g_unflushed = 0;
bool   g_skip_initial_snapshot = true;

string SafeName(string value)
{
   string result = "";
   int length = StringLen(value);
   for(int i = 0; i < length && i < 80; i++)
   {
      ushort c = StringGetCharacter(value, i);
      bool allowed = ((c >= '0' && c <= '9') ||
                      (c >= 'A' && c <= 'Z') ||
                      (c >= 'a' && c <= 'z') ||
                       c == '-' || c == '_' || c == '.');
      result += allowed ? StringSubstr(value, i, 1) : "_";
   }
   return StringLen(result) > 0 ? result : "unknown";
}

string DateKey(datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return StringFormat("%04d%02d%02d", parts.year, parts.mon, parts.day);
}

string IsoTime(datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",
                       parts.year, parts.mon, parts.day,
                       parts.hour, parts.min, parts.sec);
}

string LastPathPart(string value)
{
   StringReplace(value, "/", "\\");
   int last = -1;
   for(int i = 0; i < StringLen(value); i++)
      if(StringSubstr(value, i, 1) == "\\") last = i;
   return SafeName(last >= 0 ? StringSubstr(value, last + 1) : value);
}

int FileFlags(bool write_only = false)
{
   int flags = FILE_CSV | FILE_ANSI | FILE_SHARE_READ;
   flags |= write_only ? FILE_WRITE : (FILE_READ | FILE_WRITE);
   if(UseCommonFilesFolder) flags |= FILE_COMMON;
   return flags;
}

void EnsureFolderPath(string path)
{
   StringReplace(path, "/", "\\");
   string current = "";
   int start = 0;
   int common_flag = UseCommonFilesFolder ? FILE_COMMON : 0;

   while(start < StringLen(path))
   {
      int separator = StringFind(path, "\\", start);
      string part = separator < 0
                    ? StringSubstr(path, start)
                    : StringSubstr(path, start, separator - start);
      start = separator < 0 ? StringLen(path) : separator + 1;
      if(StringLen(part) == 0) continue;

      current = StringLen(current) == 0 ? part : current + "\\" + part;
      ResetLastError();
      FolderCreate(current, common_flag);
   }
}

void WriteMetadata(string folder)
{
   string path = folder + "\\metadata_" + g_session_id + ".csv";
   int handle = FileOpen(path, FileFlags(true), ',');
   if(handle == INVALID_HANDLE)
   {
      Print("MT4 Tick Lab: metadata open failed. Error=", GetLastError());
      return;
   }

   FileWrite(handle, "key", "value");
   FileWrite(handle, "schema_version", "2");
   FileWrite(handle, "session_id", g_session_id);
   FileWrite(handle, "broker_company", AccountInfoString(ACCOUNT_COMPANY));
   FileWrite(handle, "broker_server", AccountInfoString(ACCOUNT_SERVER));
   FileWrite(handle, "account_login", IncludeAccountLogin ? IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) : "redacted");
   FileWrite(handle, "account_currency", AccountInfoString(ACCOUNT_CURRENCY));
   FileWrite(handle, "terminal_name", TerminalInfoString(TERMINAL_NAME));
   FileWrite(handle, "terminal_id", g_terminal_id);
   FileWrite(handle, "terminal_build", IntegerToString((int)TerminalInfoInteger(TERMINAL_BUILD)));
   FileWrite(handle, "symbol", _Symbol);
   FileWrite(handle, "digits", IntegerToString((int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
   FileWrite(handle, "point", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_POINT), 12));
   FileWrite(handle, "tick_size", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), 12));
   FileWrite(handle, "tick_value", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), 8));
   FileWrite(handle, "contract_size", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE), 4));
   FileWrite(handle, "volume_min", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 4));
   FileWrite(handle, "volume_step", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), 4));
   FileWrite(handle, "volume_max", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), 4));
   FileWrite(handle, "collector_version", "1.100");
   FileWrite(handle, "price_policy", "prefer_marketinfo_when_valid");
   FileWrite(handle, "collector_started_utc", IsoTime(TimeGMT()) + "Z");
   FileFlush(handle);
   FileClose(handle);
}

bool OpenTickFile(datetime received_utc)
{
   if(g_file != INVALID_HANDLE)
   {
      FileFlush(g_file);
      FileClose(g_file);
      g_file = INVALID_HANDLE;
   }

   g_day_key = DateKey(received_utc);
   string year = StringSubstr(g_day_key, 0, 4);
   string month = StringSubstr(g_day_key, 4, 2);
   string folder = g_base_path + "\\" + year + "\\" + month;
   string path = folder + "\\ticks_" + g_day_key + "_" + g_session_id + ".csv";

   EnsureFolderPath(folder);

   ResetLastError();
   g_file = FileOpen(path, FileFlags(false), ',');
   if(g_file == INVALID_HANDLE)
   {
      Print("MT4 Tick Lab: tick file open failed. Path=", path,
            " Error=", GetLastError());
      return false;
   }

   if(FileSize(g_file) == 0)
      FileWrite(g_file, "schema_version", "session_id", "sequence",
                "broker_time", "received_utc", "monotonic_us",
                "bid", "ask", "last", "volume", "spread_points",
                "raw_tick_bid", "raw_tick_ask",
                "market_bid", "market_ask", "quote_source");
   else
      FileSeek(g_file, 0, SEEK_END);

   WriteMetadata(folder);
   FileFlush(g_file);
   Print("MT4 Tick Lab: recording ", _Symbol, " to ", path);
   return true;
}

int OnInit()
{
   if(MQLInfoInteger(MQL_TESTER))
   {
      Print("MT4 Tick Lab: collector is disabled in Strategy Tester.");
      return INIT_FAILED;
   }

   if(RequireConnectedAccount &&
      (!TerminalInfoInteger(TERMINAL_CONNECTED) ||
       StringLen(AccountInfoString(ACCOUNT_SERVER)) == 0))
   {
      Print("MT4 Tick Lab: connect this terminal to the broker before attaching the collector.");
      return INIT_FAILED;
   }

   g_terminal_id = LastPathPart(TerminalInfoString(TERMINAL_DATA_PATH));
   g_session_id = DateKey(TimeGMT()) + "_" +
                  IntegerToString((int)TimeLocal()) + "_" +
                  IntegerToString((int)GetTickCount());

   string company = SafeName(AccountInfoString(ACCOUNT_COMPANY));
   string server = SafeName(AccountInfoString(ACCOUNT_SERVER));
   string symbol = SafeName(_Symbol);
   string account = IncludeAccountLogin
                    ? SafeName(IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)))
                    : "account_redacted";

   g_base_path = SafeName(OutputRoot) + "\\" + company + "\\" + server +
                 "\\" + account + "\\" + g_terminal_id + "\\" + symbol;

   if(!OpenTickFile(TimeGMT())) return INIT_FAILED;
   EventSetTimer(1);
   IndicatorShortName("MT4 Tick Collector [" + _Symbol + "]");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(g_file != INVALID_HANDLE)
   {
      FileFlush(g_file);
      FileClose(g_file);
      g_file = INVALID_HANDLE;
   }
}

void OnTimer()
{
   if(g_file != INVALID_HANDLE && g_unflushed > 0)
   {
      FileFlush(g_file);
      g_unflushed = 0;
   }
}

int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(g_skip_initial_snapshot)
   {
      g_skip_initial_snapshot = false;
      return rates_total;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || g_file == INVALID_HANDLE)
      return rates_total;

   datetime received_utc = TimeGMT();
   if(DateKey(received_utc) != g_day_key && !OpenTickFile(received_utc))
      return rates_total;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double market_bid = MarketInfo(_Symbol, MODE_BID);
   double market_ask = MarketInfo(_Symbol, MODE_ASK);
   bool market_valid = (market_bid > 0.0 && market_ask >= market_bid);
   bool raw_valid = (tick.bid > 0.0 && tick.ask >= tick.bid);

   double selected_bid = market_valid ? market_bid : tick.bid;
   double selected_ask = market_valid ? market_ask : tick.ask;
   string quote_source = market_valid ? "market_info" : "mql_tick";
   if(!market_valid && !raw_valid) return rates_total;

   double spread_points = point > 0.0
                          ? (selected_ask - selected_bid) / point
                          : 0.0;

   g_sequence++;
   FileWrite(g_file,
             "2",
             g_session_id,
             (long)g_sequence,
             IsoTime(tick.time),
             IsoTime(received_utc) + "Z",
             (long)GetMicrosecondCount(),
             DoubleToString(selected_bid, digits),
             DoubleToString(selected_ask, digits),
             DoubleToString(tick.last, digits),
             (long)tick.volume,
             DoubleToString(spread_points, 2),
             DoubleToString(tick.bid, digits),
             DoubleToString(tick.ask, digits),
             DoubleToString(market_bid, digits),
             DoubleToString(market_ask, digits),
             quote_source);

   g_unflushed++;
   if(FlushEveryTicks > 0 && g_unflushed >= FlushEveryTicks)
   {
      FileFlush(g_file);
      g_unflushed = 0;
   }
   return rates_total;
}
