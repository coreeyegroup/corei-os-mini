// COREI OS MINI terminal readiness checker v0.6.2
// Local diagnostics only. No network access, credentials, DLLs or order functions.
#property strict
#property version "0.62"
#property description "Local terminal readiness check only. Not connected to COREI VPS."
input string InstanceLabel = "mt5-demo-01";
input long ExpectedLogin = 0; // Set your broker account number; 0 means unspecified.
input string ExpectedServer = ""; // Exact broker server name; not the COREI domain.

void ReportReadiness()
{
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   string server = AccountInfoString(ACCOUNT_SERVER);
   bool connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   bool identity_configured = ExpectedLogin > 0 && StringLen(ExpectedServer) > 0;
   bool identity_matches = identity_configured && login == ExpectedLogin && server == ExpectedServer;
   string identity = "NOT_CONFIGURED";
   if(identity_configured) identity = identity_matches ? "MATCH" : "MISMATCH";
   MqlTick tick;
   bool has_quote = SymbolInfoTick(_Symbol, tick);
   string quote_time = has_quote ? TimeToString(tick.time, TIME_DATE|TIME_SECONDS) : "UNAVAILABLE";
   PrintFormat("COREI_OS_MINI LOCAL_CHECK instance=%s platform=MT5 build=%d connected=%s login=%I64d server=%s identity=%s symbol=%s quote_time=%s VPS=NOT_CONNECTED",
               InstanceLabel, (int)TerminalInfoInteger(TERMINAL_BUILD), connected ? "YES" : "NO",
               login, server, identity, _Symbol, quote_time);
   // A cached quote timestamp is NOT proof of fresh data or VPS verification.
}

int OnInit()
{
   if(StringLen(InstanceLabel) < 1 || StringLen(InstanceLabel) > 120)
      return INIT_PARAMETERS_INCORRECT;
   if(!EventSetTimer(30))
   {
      PrintFormat("COREI_OS_MINI timer failed: %d", GetLastError());
      return INIT_FAILED;
   }
   ReportReadiness();
   return INIT_SUCCEEDED;
}
void OnTimer() { ReportReadiness(); }
void OnDeinit(const int reason)
{
   EventKillTimer();
   PrintFormat("COREI_OS_MINI local checker stopped, reason=%d", reason);
}
