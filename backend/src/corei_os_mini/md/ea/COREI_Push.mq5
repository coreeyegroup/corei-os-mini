//+------------------------------------------------------------------+
//| COREI_Push.mq5 — COREI-OS-MINI terminal push (read-only)          |
//|                                                                   |
//| Sends what this MT5 terminal sees to COREI over HTTPS:            |
//|   - the login, server, company and terminal build (once, and when |
//|     COREI asks),                                                  |
//|   - the broker's symbol list with each symbol's rules,            |
//|   - every tick of the symbols COREI subscribes to,                |
//|   - historical bars when COREI asks (backfill).                   |
//| It never places, changes or closes orders.                        |
//|                                                                   |
//| Setup:                                                            |
//|  1. Tools > Options > Expert Advisors > tick "Allow WebRequest for|
//|     listed URL" and add your COREI address, e.g.                  |
//|     https://corei-os-mini.coreeye.in                              |
//|  2. Compile this file in MetaEditor (F7), attach COREI_Push to any|
//|     chart, fill in AccountCode and PushToken, tick "Allow Algo    |
//|     Trading" is NOT needed (nothing is traded).                   |
//|  3. Keep the terminal logged in to that account and running.      |
//+------------------------------------------------------------------+
#property copyright   "COREI-OS-MINI"
#property version     "1.00"
#property description "Sends this terminal's quotes, bars and symbol list to COREI over HTTPS. Read-only: never trades."

input string CoreiUrl    = "https://corei-os-mini.coreeye.in"; // COREI address (also allow it under Tools > Options > Expert Advisors)
input string AccountCode = "";                                  // COREI account code, e.g. METAQUOTES_DEMO_5056977914
input string PushToken   = "";                                  // Push token from COREI (Setup > the account > Routes > EA push > Issue token)
input int    PushEveryMs = 250;                                 // Send interval in milliseconds (100 or more)
input bool   SendSymbolList = true;                             // Send the broker's symbol list on start

#define EA_VERSION   "1.00"
#define MAX_PENDING  60000      // lines kept while COREI cannot be reached
#define MAX_PER_POST 4000       // lines per request
#define MAX_BODY     3000000    // bytes per request

string g_endpoint;
string g_subs[];        // subscribed symbols
long   g_last_msc[];    // last tick time (ms) sent, per subscribed symbol
string g_lines[];       // lines waiting to be sent
long   g_seq      = 0;
uint   g_last_hb  = 0;
int    g_pause    = 0;  // timer ticks to wait after a failed send
int    g_failures = 0;
string g_last_error = "";

//--- text helpers -----------------------------------------------------
string Esc(const string s)
  {
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\r", " ");
   StringReplace(r, "\n", " ");
   StringReplace(r, "\t", " ");
   return r;
  }

string Q(const string s) { return "\"" + Esc(s) + "\""; }

// ISO 8601 without zone, e.g. 2026-11-15T07:30:01.250 (server time or UTC, as the field says)
string Iso(const datetime t, const int ms)
  {
   MqlDateTime d;
   TimeToStruct(t, d);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d.%03d", d.year, d.mon, d.day, d.hour, d.min, d.sec, ms);
  }

string Num(const double v, const int digits) { return DoubleToString(v, digits); }

void Add(const string line)
  {
   int n = ArraySize(g_lines);
   if(n >= MAX_PENDING)
     {
      // COREI unreachable for a long time: keep the newest lines (the oldest are re-read by backfill).
      ArrayRemove(g_lines, 0, n - MAX_PENDING + 1);
      n = ArraySize(g_lines);
     }
   ArrayResize(g_lines, n + 1, 1024);
   g_lines[n] = line;
  }

//--- what COREI is told -------------------------------------------------
void Hello()
  {
   datetime utc = TimeGMT();
   string line = "{\"t\":\"connected\""
                 + ",\"login\":" + Q(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)))
                 + ",\"host\":" + Q(AccountInfoString(ACCOUNT_SERVER))
                 + ",\"port\":0"
                 + ",\"company\":" + Q(AccountInfoString(ACCOUNT_COMPANY))
                 + ",\"client_build\":" + IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))
                 + ",\"server_time\":" + Q(Iso(TimeTradeServer(), 0))
                 + ",\"utc_now\":" + Q(Iso(utc, 0))
                 + ",\"ea_version\":" + Q(EA_VERSION)
                 + "}";
   Add(line);
   Subscribed();
  }

void Subscribed()
  {
   string list = "";
   for(int i = 0; i < ArraySize(g_subs); i++)
      list += (i > 0 ? "," : "") + Q(g_subs[i]);
   Add("{\"t\":\"subscribed\",\"symbols\":[" + list + "]}");
  }

string SymbolJson(const string name)
  {
   return "{\"Name\":" + Q(name)
          + ",\"Description\":" + Q(SymbolInfoString(name, SYMBOL_DESCRIPTION))
          + ",\"Path\":" + Q(SymbolInfoString(name, SYMBOL_PATH))
          + ",\"Digits\":" + IntegerToString(SymbolInfoInteger(name, SYMBOL_DIGITS))
          + ",\"Point\":" + Num(SymbolInfoDouble(name, SYMBOL_POINT), 10)
          + ",\"TradeTickSize\":" + Num(SymbolInfoDouble(name, SYMBOL_TRADE_TICK_SIZE), 10)
          + ",\"ContractSize\":" + Num(SymbolInfoDouble(name, SYMBOL_TRADE_CONTRACT_SIZE), 4)
          + ",\"VolumeMin\":" + Num(SymbolInfoDouble(name, SYMBOL_VOLUME_MIN), 4)
          + ",\"VolumeMax\":" + Num(SymbolInfoDouble(name, SYMBOL_VOLUME_MAX), 4)
          + ",\"VolumeStep\":" + Num(SymbolInfoDouble(name, SYMBOL_VOLUME_STEP), 4)
          + ",\"StopsLevel\":" + IntegerToString(SymbolInfoInteger(name, SYMBOL_TRADE_STOPS_LEVEL))
          + ",\"FreezeLevel\":" + IntegerToString(SymbolInfoInteger(name, SYMBOL_TRADE_FREEZE_LEVEL))
          + ",\"TradeMode\":" + IntegerToString(SymbolInfoInteger(name, SYMBOL_TRADE_MODE))
          + ",\"ExecutionMode\":" + IntegerToString(SymbolInfoInteger(name, SYMBOL_TRADE_EXEMODE))
          + ",\"FillingMode\":" + IntegerToString(SymbolInfoInteger(name, SYMBOL_FILLING_MODE))
          + ",\"CurrencyBase\":" + Q(SymbolInfoString(name, SYMBOL_CURRENCY_BASE))
          + ",\"CurrencyProfit\":" + Q(SymbolInfoString(name, SYMBOL_CURRENCY_PROFIT))
          + ",\"MarginCurrency\":" + Q(SymbolInfoString(name, SYMBOL_CURRENCY_MARGIN))
          + "}";
  }

void SymbolList()
  {
   int total = SymbolsTotal(false);
   string chunk = "";
   int inChunk = 0;
   for(int i = 0; i < total; i++)
     {
      string name = SymbolName(i, false);
      if(name == "")
         continue;
      chunk += (inChunk > 0 ? "," : "") + SymbolJson(name);
      inChunk++;
      if(inChunk == 200)
        {
         Add("{\"t\":\"catalog\",\"symbols\":[" + chunk + "]}");
         chunk = "";
         inChunk = 0;
        }
     }
   if(inChunk > 0)
      Add("{\"t\":\"catalog\",\"symbols\":[" + chunk + "]}");
   PrintFormat("COREI_Push: symbol list queued (%d symbols)", total);
  }

//--- subscriptions -------------------------------------------------------
int Find(const string s)
  {
   for(int i = 0; i < ArraySize(g_subs); i++)
      if(g_subs[i] == s)
         return i;
   return -1;
  }

void Subscribe(const string csv)
  {
   string names[];
   int n = StringSplit(csv, ',', names);
   for(int i = 0; i < n; i++)
     {
      string s = names[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "" || Find(s) >= 0)
         continue;
      if(!SymbolSelect(s, true))
        {
         Add("{\"t\":\"error\",\"msg\":" + Q("Symbol " + s + " is not offered by this server") + "}");
         continue;
        }
      int k = ArraySize(g_subs);
      ArrayResize(g_subs, k + 1);
      ArrayResize(g_last_msc, k + 1);
      g_subs[k] = s;
      g_last_msc[k] = 0;
     }
   Subscribed();
  }

void Unsubscribe(const string csv)
  {
   string names[];
   int n = StringSplit(csv, ',', names);
   for(int i = 0; i < n; i++)
     {
      int k = Find(names[i]);
      if(k < 0)
         continue;
      ArrayRemove(g_subs, k, 1);
      ArrayRemove(g_last_msc, k, 1);
     }
   Subscribed();
  }

//--- ticks ---------------------------------------------------------------
void Collect()
  {
   datetime utc = TimeGMT();
   string rx = Iso(utc, (int)((GetMicrosecondCount() / 1000) % 1000));
   for(int i = 0; i < ArraySize(g_subs); i++)
     {
      string s = g_subs[i];
      int digits = (int)SymbolInfoInteger(s, SYMBOL_DIGITS);
      if(g_last_msc[i] == 0)
        {
         // First pass: the current price only, then every tick after it.
         MqlTick t;
         if(SymbolInfoTick(s, t) && t.time_msc > 0)
           {
            g_last_msc[i] = t.time_msc;
            Add(Quote(s, t, digits, rx));
           }
         continue;
        }
      MqlTick ticks[];
      int got = CopyTicks(s, ticks, COPY_TICKS_ALL, (ulong)(g_last_msc[i] + 1), 2000);
      for(int j = 0; j < got; j++)
        {
         if(ticks[j].time_msc <= g_last_msc[i])
            continue;
         g_last_msc[i] = ticks[j].time_msc;
         Add(Quote(s, ticks[j], digits, rx));
        }
     }
  }

string Quote(const string s, const MqlTick &t, const int digits, const string rx)
  {
   g_seq++;
   return "{\"t\":\"q\",\"s\":" + Q(s)
          + ",\"st\":" + Q(Iso((datetime)(t.time_msc / 1000), (int)(t.time_msc % 1000)))
          + ",\"rx\":" + Q(rx)
          + ",\"b\":" + Num(t.bid, digits)
          + ",\"a\":" + Num(t.ask, digits)
          + ",\"l\":" + Num(t.last, digits)
          + ",\"v\":" + Num(t.volume_real > 0 ? t.volume_real : (double)t.volume, 2)
          + ",\"n\":" + IntegerToString(g_seq) + "}";
  }

//--- history (backfill) ----------------------------------------------------
ENUM_TIMEFRAMES Tf(const string s)
  {
   if(s == "M1")  return PERIOD_M1;
   if(s == "M5")  return PERIOD_M5;
   if(s == "M15") return PERIOD_M15;
   if(s == "M30") return PERIOD_M30;
   if(s == "H1")  return PERIOD_H1;
   if(s == "H4")  return PERIOD_H4;
   if(s == "D1")  return PERIOD_D1;
   if(s == "W1")  return PERIOD_W1;
   if(s == "MN1") return PERIOD_MN1;
   return PERIOD_CURRENT;
  }

datetime FromIso(const string s)
  {
   string t = s;
   StringReplace(t, "-", ".");
   StringReplace(t, "T", " ");
   return StringToTime(t);
  }

void History(const string id, const string symbol, const string tf, const string from, const string to)
  {
   ENUM_TIMEFRAMES p = Tf(tf);
   if(p == PERIOD_CURRENT)
     {
      Add("{\"t\":\"history_error\",\"id\":" + Q(id) + ",\"msg\":" + Q("Unknown timeframe " + tf) + "}");
      return;
     }
   SymbolSelect(symbol, true);
   MqlRates r[];
   int got = CopyRates(symbol, p, FromIso(from), FromIso(to), r);
   if(got < 0)
     {
      Add("{\"t\":\"history_error\",\"id\":" + Q(id) + ",\"msg\":" + Q("CopyRates failed for " + symbol + " " + tf + " (error " + IntegerToString(GetLastError()) + "); the terminal may still be loading history") + "}");
      return;
     }
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   string bars = "";
   for(int i = 0; i < got; i++)
      bars += (i > 0 ? "," : "") + "{\"t\":" + Q(Iso(r[i].time, 0)) + ",\"o\":" + Num(r[i].open, digits) + ",\"h\":" + Num(r[i].high, digits)
              + ",\"l\":" + Num(r[i].low, digits) + ",\"c\":" + Num(r[i].close, digits) + ",\"sp\":" + IntegerToString(r[i].spread)
              + ",\"v\":" + IntegerToString(r[i].tick_volume) + "}";
   Add("{\"t\":\"bars\",\"id\":" + Q(id) + ",\"bars\":[" + bars + "]}");
  }

//--- commands from COREI (one per line, plain text) -----------------------
void Commands(const string text)
  {
   string lines[];
   int n = StringSplit(text, '\n', lines);
   for(int i = 0; i < n; i++)
     {
      string line = lines[i];
      StringTrimRight(line);
      if(line == "")
         continue;
      string w[];
      int k = StringSplit(line, ' ', w);
      if(k >= 2 && w[0] == "subscribe")
         Subscribe(w[1]);
      else if(k >= 2 && w[0] == "unsubscribe")
         Unsubscribe(w[1]);
      else if(k >= 6 && w[0] == "history")
         History(w[1], w[2], w[3], w[4], w[5]);
      else if(k >= 1 && w[0] == "hello")
         Hello();
      else if(k >= 1 && w[0] == "catalog")
         SymbolList();
     }
  }

//--- sending ----------------------------------------------------------------
int Post(const string body, string &reply)
  {
   char data[];
   char result[];
   string replyHeaders;
   int len = StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(len > 0)
      ArrayResize(data, len - 1);   // without the terminating zero
   string headers = "Content-Type: application/json\r\n" + "Authorization: Bearer " + PushToken + "\r\n" + "X-COREI-Account: " + AccountCode + "\r\n";
   ResetLastError();
   int code = WebRequest("POST", g_endpoint, headers, 8000, data, result, replyHeaders);
   reply = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(code == -1)
     {
      int err = GetLastError();
      reply = (err == 4014) ? "WebRequest is not allowed for " + CoreiUrl + ": add it under Tools > Options > Expert Advisors"
                            : "Cannot reach COREI (error " + IntegerToString(err) + ")";
     }
   return code;
  }

void Flush()
  {
   while(ArraySize(g_lines) > 0)
     {
      int count = 0;
      int size = 12;
      string body = "{\"lines\":[";
      while(count < ArraySize(g_lines) && count < MAX_PER_POST)
        {
         int add = StringLen(g_lines[count]) + 1;
         if(count > 0 && size + add > MAX_BODY)
            break;
         body += (count > 0 ? "," : "") + g_lines[count];
         size += add;
         count++;
        }
      body += "]}";
      string reply;
      int code = Post(body, reply);
      if(code == 200)
        {
         ArrayRemove(g_lines, 0, count);
         if(g_failures > 0)
            PrintFormat("COREI_Push: COREI reachable again after %d failed sends", g_failures);
         g_failures = 0;
         g_last_error = "";
         Commands(reply);
         continue;
        }
      g_failures++;
      g_pause = MathMin(40, g_failures * 4);   // back off up to ~10 s at 250 ms
      string why = "HTTP " + IntegerToString(code) + ": " + StringSubstr(reply, 0, 300);
      if(why != g_last_error)
        {
         Print("COREI_Push: send failed — ", why);
         g_last_error = why;
        }
      if(code == 401 || code == 409)
         ArrayResize(g_lines, 0);   // wrong token or account: nothing will be accepted; do not pile up
      return;
     }
  }

//--- events -------------------------------------------------------------------
int OnInit()
  {
   if(AccountCode == "" || PushToken == "")
     {
      Alert("COREI_Push: fill in AccountCode and PushToken (from COREI).");
      return INIT_PARAMETERS_INCORRECT;
     }
   string base = CoreiUrl;
   while(StringLen(base) > 0 && StringSubstr(base, StringLen(base) - 1) == "/")
      base = StringSubstr(base, 0, StringLen(base) - 1);
   g_endpoint = base + "/api/md/push";
   if(AccountInfoInteger(ACCOUNT_LOGIN) == 0)
      Print("COREI_Push: the terminal is not logged in yet; it will send once it is.");
   Hello();
   if(SendSymbolList)
      SymbolList();
   EventSetMillisecondTimer(MathMax(PushEveryMs, 100));
   PrintFormat("COREI_Push %s: sending to %s as %s", EA_VERSION, g_endpoint, AccountCode);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Add("{\"t\":\"disconnected\"}");
   Flush();
  }

void OnTimer()
  {
   if(g_pause > 0)
     {
      g_pause--;
      return;
     }
   if(AccountInfoInteger(ACCOUNT_LOGIN) == 0)
      return;
   Collect();
   if(ArraySize(g_lines) == 0 && GetTickCount() - g_last_hb > 5000)
     {
      Add("{\"t\":\"hb\"}");
      g_last_hb = GetTickCount();
     }
   Flush();
  }

void OnTick() { }
//+------------------------------------------------------------------+
