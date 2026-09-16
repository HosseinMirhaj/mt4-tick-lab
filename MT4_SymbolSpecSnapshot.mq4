#property strict
#property version   "1.000"
#property description "Read-only MT4 symbol specification snapshot for FXT generation"

input string OutputRoot = "MT4TickLab";

string SafeName(string value)
{
   string result = "";
   for(int i = 0; i < StringLen(value); i++)
   {
      ushort c = StringGetCharacter(value, i);
      bool allowed = ((c >= '0' && c <= '9') ||
                      (c >= 'A' && c <= 'Z') ||
                      (c >= 'a' && c <= 'z') ||
                      c == '.' || c == '_' || c == '-');
      result += allowed ? ShortToString(c) : "_";
   }
   return StringLen(result) > 0 ? result : "unknown";
}

string LastPathPart(string path)
{
   StringReplace(path, "/", "\\");
   while(StringLen(path) > 0 && StringSubstr(path, StringLen(path) - 1, 1) == "\\")
      path = StringSubstr(path, 0, StringLen(path) - 1);
   int position = StringLen(path) - 1;
   while(position >= 0 && StringSubstr(path, position, 1) != "\\") position--;
   return StringSubstr(path, position + 1);
}

void Put(int handle, string key, string value)
{
   FileWrite(handle, key, value);
}

void PutNumber(int handle, string key, double value)
{
   FileWrite(handle, key, DoubleToString(value, 12));
}

void OnStart()
{
   if(MQLInfoInteger(MQL_TESTER))
   {
      Print("MT4 Tick Lab: run SymbolSpecSnapshot on a connected live/demo chart, not in Tester.");
      return;
   }
   if(!TerminalInfoInteger(TERMINAL_CONNECTED) || StringLen(AccountInfoString(ACCOUNT_SERVER)) == 0)
   {
      Print("MT4 Tick Lab: terminal must be connected to the target broker server.");
      return;
   }

   string root = SafeName(OutputRoot);
   string folder = root + "\\specs";
   FolderCreate(root);
   FolderCreate(folder);
   string stamp = IntegerToString((int)TimeGMT()) + "_" + IntegerToString((int)GetTickCount());
   string path = folder + "\\symbol_spec_" + SafeName(_Symbol) + "_" + stamp + ".csv";
   int handle = FileOpen(path, FILE_CSV|FILE_WRITE|FILE_ANSI|FILE_SHARE_READ, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("MT4 Tick Lab: cannot write symbol specification. Error=", GetLastError());
      return;
   }

   FileWrite(handle, "key", "value");
   Put(handle, "schema_version", "1");
   Put(handle, "captured_utc", TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS));
   Put(handle, "broker_company", AccountInfoString(ACCOUNT_COMPANY));
   Put(handle, "broker_server", AccountInfoString(ACCOUNT_SERVER));
   Put(handle, "account_currency", AccountInfoString(ACCOUNT_CURRENCY));
   Put(handle, "account_leverage", IntegerToString(AccountLeverage()));
   Put(handle, "account_stopout_mode", IntegerToString(AccountStopoutMode()));
   Put(handle, "account_stopout_level", IntegerToString(AccountStopoutLevel()));
   Put(handle, "terminal_name", TerminalInfoString(TERMINAL_NAME));
   Put(handle, "terminal_id", LastPathPart(TerminalInfoString(TERMINAL_DATA_PATH)));
   Put(handle, "terminal_build", IntegerToString((int)TerminalInfoInteger(TERMINAL_BUILD)));
   Put(handle, "symbol", _Symbol);

   PutNumber(handle, "bid", MarketInfo(_Symbol, MODE_BID));
   PutNumber(handle, "ask", MarketInfo(_Symbol, MODE_ASK));
   PutNumber(handle, "point", MarketInfo(_Symbol, MODE_POINT));
   PutNumber(handle, "digits", MarketInfo(_Symbol, MODE_DIGITS));
   PutNumber(handle, "spread", MarketInfo(_Symbol, MODE_SPREAD));
   PutNumber(handle, "stop_level", MarketInfo(_Symbol, MODE_STOPLEVEL));
   PutNumber(handle, "contract_size", MarketInfo(_Symbol, MODE_LOTSIZE));
   PutNumber(handle, "tick_value", MarketInfo(_Symbol, MODE_TICKVALUE));
   PutNumber(handle, "tick_size", MarketInfo(_Symbol, MODE_TICKSIZE));
   PutNumber(handle, "swap_long", MarketInfo(_Symbol, MODE_SWAPLONG));
   PutNumber(handle, "swap_short", MarketInfo(_Symbol, MODE_SWAPSHORT));
   PutNumber(handle, "starting", MarketInfo(_Symbol, MODE_STARTING));
   PutNumber(handle, "expiration", MarketInfo(_Symbol, MODE_EXPIRATION));
   PutNumber(handle, "trade_allowed", MarketInfo(_Symbol, MODE_TRADEALLOWED));
   PutNumber(handle, "min_lot", MarketInfo(_Symbol, MODE_MINLOT));
   PutNumber(handle, "lot_step", MarketInfo(_Symbol, MODE_LOTSTEP));
   PutNumber(handle, "max_lot", MarketInfo(_Symbol, MODE_MAXLOT));
   PutNumber(handle, "swap_type", MarketInfo(_Symbol, MODE_SWAPTYPE));
   PutNumber(handle, "profit_calc_mode", MarketInfo(_Symbol, MODE_PROFITCALCMODE));
   PutNumber(handle, "margin_calc_mode", MarketInfo(_Symbol, MODE_MARGINCALCMODE));
   PutNumber(handle, "margin_initial", MarketInfo(_Symbol, MODE_MARGININIT));
   PutNumber(handle, "margin_maintenance", MarketInfo(_Symbol, MODE_MARGINMAINTENANCE));
   PutNumber(handle, "margin_hedged", MarketInfo(_Symbol, MODE_MARGINHEDGED));
   PutNumber(handle, "margin_required", MarketInfo(_Symbol, MODE_MARGINREQUIRED));
   PutNumber(handle, "freeze_level", MarketInfo(_Symbol, MODE_FREEZELEVEL));
   PutNumber(handle, "closeby_allowed", MarketInfo(_Symbol, MODE_CLOSEBY_ALLOWED));

   FileFlush(handle);
   FileClose(handle);
   Print("MT4 Tick Lab: symbol specification saved to MQL4\\Files\\", path);
}
