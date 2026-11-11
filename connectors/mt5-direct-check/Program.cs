using System.Collections;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Security.Cryptography;
using System.Text.Json;
using mtapi.mt5;

// Reads a broker's MT5 servers.dat. No login, connection, subscription or order calls.
// For each server it returns the name, every scalar field the SDK exposes on the
// server records (company, time zone, and whatever else the file holds), and the
// named access points. Fields are read by reflection so nothing the file carries is
// dropped because of an assumed field name.
// --inspect prints counts and the field structure of the access records (with sample
// values) so an unknown newer servers.dat layout can be decoded exactly. servers.dat
// holds public broker server lists only; no credentials are read or printed.
// --search <broker> asks the broker directory (the SDK's Broker.Search / SearchMQ, the
// same lookup as MT5 "Open an Account") and prints the result in the same shape.
// --describe-search prints the SDK's search method signatures and result shapes.
// --describe-api prints the SDK's MT5API surface (no login).
// --session logs in ONCE with the request read from stdin (the password never appears
// on the command line), reads account facts, server time and symbol specifications,
// disconnects and prints them. It never sends, changes or cancels orders.
// --stream logs in once (request on stdin's first line), then writes one JSON line per
// price update, heartbeats and history answers to stdout, and takes subscribe /
// unsubscribe / history / stop commands as further stdin lines. Read-only as well.
var inspect = args.Length == 2 && args[0] == "--inspect";
if (args.Length == 2 && args[0] == "--search") return Search(args[1]);
if (args.Length == 1 && args[0] == "--describe-search") return DescribeSearch();
if (args.Length == 1 && args[0] == "--describe-api") return DescribeApi();
if (args.Length == 1 && args[0] == "--session") return Session();
if (args.Length == 1 && args[0] == "--stream") return Stream();
if (args.Length != 1 && !inspect)
{
    Console.Error.WriteLine("Usage: ServerCatalog [--inspect] <servers.dat> | --search <broker> | --describe-search");
    return 2;
}
if (inspect) return Inspect(args[1]);

try
{
    using var file = File.OpenRead(args[0]);
    // A terminal's servers.dat can hold every broker the terminal has searched for.
    if (file.Length < 32 || file.Length > 32L * 1024 * 1024)
        throw new InvalidDataException();
    var bytes = new byte[checked((int)file.Length)];
    file.ReadExactly(bytes);
    var servers = MT5API.LoadServersDat(bytes);
    if (servers is null || servers.Length == 0 || servers.Length > 100_000)
        throw new InvalidDataException();
    int skipped = 0, extendedUnreadable = 0, total = 0;
    var rows = new List<object>(servers.Length);
    foreach (var server in servers)
    {
        var name = server?.ServerInfoEx?.ServerName ?? server?.ServerInfo?.ServerName;
        if (server is null || string.IsNullOrWhiteSpace(name)) { skipped++; continue; }
        var addresses = new List<(string Name, string Address)>();
        foreach (var access in server.Accesses ?? [])
            foreach (var address in access?.Addresses ?? []) if (address is not null) addresses.Add((string.IsNullOrWhiteSpace(access!.AccessRec?.ServerName) ? name : access!.AccessRec!.ServerName!, address.Address));
        // Newer files carry extended access records whose shape the SDK does not
        // document. Read any host:port they contain instead of rejecting the file.
        foreach (var accessEx in server.AccessesEx ?? [])
        {
            var found = new List<(string Name, string Address)>();
            FindEndpoints(accessEx, name, found, 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
            if (found.Count == 0) extendedUnreadable++;
            addresses.AddRange(found);
        }
        var primary = server.ServerInfoEx?.Address ?? server.ServerInfo?.Address;
        if (addresses.Count == 0 && !string.IsNullOrWhiteSpace(primary)) addresses.Add(("Primary", primary));
        var endpoints = addresses.Where(a => !string.IsNullOrWhiteSpace(a.Address))
            .Select(a => (Name: a.Name.Trim(), Address: a.Address.Trim())).DistinctBy(a => a.Address.ToLowerInvariant())
            .Select(a => new { name = a.Name, address = a.Address }).ToArray();
        total += endpoints.Length;
        if (total > 1_000_000) throw new InvalidDataException();
        rows.Add(new { server_name = name.Trim(), details = Describe(server.ServerInfoEx, server.ServerInfo, server), access_points = endpoints });
    }
    if (rows.Count == 0) throw new InvalidDataException();
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        status = "PARSED_NOT_CONNECTED",
        source_sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(),
        sdk_version = typeof(MT5API).Assembly.GetName().Version?.ToString(),
        server_count = rows.Count,
        access_point_count = total,
        skipped_servers = skipped,
        unreadable_extended_records = extendedUnreadable,
        execution_available = false,
        servers = rows
    }));
    return 0;
}
catch (Exception)
{
    // Do not print vendor exceptions or raw uploaded content.
    Console.Error.WriteLine("SERVER_CATALOG_READ_FAILED: file unreadable, invalid, unsupported, or SDK unavailable.");
    return 1;
}

// Public scalar fields and properties of the given records, first source wins on a
// name clash. Nested objects and arrays are skipped (access points are read above).
static Dictionary<string, object> Describe(params object?[] sources)
{
    var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
    foreach (var source in sources)
    {
        if (source is null) continue;
        var type = source.GetType();
        var members = type.GetFields(BindingFlags.Public | BindingFlags.Instance)
            .Select(f => (f.Name, Read: (Func<object?>)(() => f.GetValue(source))))
            .Concat(type.GetProperties(BindingFlags.Public | BindingFlags.Instance)
                .Where(p => p.CanRead && p.GetIndexParameters().Length == 0)
                .Select(p => (p.Name, Read: (Func<object?>)(() => p.GetValue(source)))));
        foreach (var (memberName, read) in members)
        {
            if (result.Count >= 64 || result.ContainsKey(memberName)) continue;
            object? value;
            try { value = read(); } catch (Exception) { continue; }
            var scalar = Scalar(value);
            if (scalar is not null) result[memberName] = scalar;
        }
    }
    return result;
}

static object? Scalar(object? value) => value switch
{
    string s when !string.IsNullOrWhiteSpace(s) => s.Trim().Length <= 500 ? s.Trim() : s.Trim()[..500],
    bool b => b,
    Enum e => e.ToString(),
    sbyte or byte or short or ushort or int or uint or long => Convert.ToInt64(value),
    ulong u => u <= long.MaxValue ? (long)u : u.ToString(),
    float f when float.IsFinite(f) => (double)f,
    double d when double.IsFinite(d) => d,
    _ => null,
};

// Depth-limited walk over an unknown record collecting "host:port" strings. A string
// member called ServerName or Name on the same record labels what is found under it.
static void FindEndpoints(object? value, string label, List<(string Name, string Address)> found, int depth, HashSet<object> seen)
{
    if (value is null || depth > 5 || found.Count > 4096) return;
    if (value is string text)
    {
        if (Endpoint().IsMatch(text.Trim())) found.Add((label, text.Trim()));
        return;
    }
    var type = value.GetType();
    if (type.IsPrimitive || type.IsEnum || value is decimal || value is byte[] || !seen.Add(value)) return;
    if (value is IEnumerable items)
    {
        foreach (var item in items) FindEndpoints(item, label, found, depth + 1, seen);
        return;
    }
    var members = type.GetFields(BindingFlags.Public | BindingFlags.Instance)
        .Select(f => (f.Name, Read: (Func<object?>)(() => f.GetValue(value))))
        .Concat(type.GetProperties(BindingFlags.Public | BindingFlags.Instance)
            .Where(p => p.CanRead && p.GetIndexParameters().Length == 0)
            .Select(p => (p.Name, Read: (Func<object?>)(() => p.GetValue(value)))))
        .ToArray();
    var own = label;
    foreach (var (memberName, read) in members)
    {
        if (memberName is not ("ServerName" or "Name")) continue;
        try { if (read() is string s && !string.IsNullOrWhiteSpace(s) && !Endpoint().IsMatch(s.Trim())) own = s.Trim(); } catch (Exception) { }
    }
    // Records that keep host and port in separate members (e.g. Address + Port).
    string? host = null; long? port = null; int hosts = 0, ports = 0;
    foreach (var (memberName, read) in members)
    {
        object? v; try { v = read(); } catch (Exception) { continue; }
        var lower = memberName.ToLowerInvariant();
        if (lower.Contains("port") && v is not null && !(v is string) && long.TryParse(Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture), out var p) && p is >= 1 and <= 65535)
        { port = p; ports++; continue; }
        if (!(lower.Contains("addr") || lower.Contains("host") || lower.Contains("ip") || lower.Contains("server"))) continue;
        var candidate = v switch
        {
            string hostText when Host().IsMatch(hostText.Trim()) => hostText.Trim(),
            byte[] { Length: 4 or 16 } raw => new System.Net.IPAddress(raw).ToString(),
            System.Net.IPAddress ip => ip.ToString(),
            _ => null,
        };
        if (candidate is not null) { host = candidate; hosts++; }
    }
    if (hosts == 1 && ports == 1 && host is not null)
        found.Add((own, host.Contains(':') ? $"[{host}]:{port}" : $"{host}:{port}"));
    foreach (var (_, read) in members)
    {
        object? child;
        try { child = read(); } catch (Exception) { continue; }
        FindEndpoints(child, own, found, depth + 1, seen);
    }
}


// ---------- --inspect ----------

static int Inspect(string path)
{
    try
    {
        var bytes = File.ReadAllBytes(path);
        var servers = MT5API.LoadServersDat(bytes) ?? [];
        int withAccesses = 0, withEx = 0, fromAccesses = 0, fromEx = 0, exRecords = 0, exUnread = 0, none = 0;
        var noEndpoint = new List<string>();
        object? firstEx = null;
        var exSamples = new List<object>();
        foreach (var server in servers)
        {
            var name = server?.ServerInfoEx?.ServerName ?? server?.ServerInfo?.ServerName ?? "(no name)";
            int a = 0, e = 0;
            foreach (var access in server?.Accesses ?? []) foreach (var address in access?.Addresses ?? []) if (!string.IsNullOrWhiteSpace(address?.Address)) a++;
            foreach (var accessEx in server?.AccessesEx ?? [])
            {
                exRecords++;
                firstEx ??= accessEx;
                var found = new List<(string Name, string Address)>();
                FindEndpoints(accessEx, "Access point", found, 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
                if (found.Count == 0) { exUnread++; if (exSamples.Count < 3) exSamples.Add(new { server = name, record = Dump(accessEx, 0) }); }
                e += found.Count;
            }
            if (a > 0) withAccesses++;
            if (server?.AccessesEx is { Length: > 0 }) withEx++;
            fromAccesses += a; fromEx += e;
            if (a + e == 0) { none++; if (noEndpoint.Count < 25) noEndpoint.Add(name); }
        }
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            sdk_version = typeof(MT5API).Assembly.GetName().Version?.ToString(),
            file_bytes = bytes.Length,
            server_count = servers.Length,
            servers_with_accesses = withAccesses,
            servers_with_accesses_ex = withEx,
            endpoints_from_accesses = fromAccesses,
            endpoints_from_accesses_ex = fromEx,
            accesses_ex_records = exRecords,
            accesses_ex_records_unread = exUnread,
            servers_without_any_endpoint = none,
            examples_without_endpoint = noEndpoint,
            server_record_shape = Shape(servers.FirstOrDefault()?.GetType(), 0),
            accesses_ex_shape = Shape(firstEx?.GetType(), 0),
            unread_accesses_ex_samples = exSamples,
        }, new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine("INSPECT_FAILED: " + ex.GetType().Name);
        return 1;
    }
}

static object? Shape(Type? type, int depth)
{
    if (type is null) return null;
    if (depth > 4 || type.IsPrimitive || type.IsEnum || type == typeof(string) || type == typeof(decimal)) return type.Name;
    var element = type.IsArray ? type.GetElementType() : null;
    if (element is not null) return new Dictionary<string, object?> { ["array_of"] = Shape(element, depth + 1) };
    var fields = type.GetFields(BindingFlags.Public | BindingFlags.Instance).Select(f => (f.Name, Type: f.FieldType));
    var props = type.GetProperties(BindingFlags.Public | BindingFlags.Instance).Where(p => p.GetIndexParameters().Length == 0).Select(p => (p.Name, Type: p.PropertyType));
    return new Dictionary<string, object?> { ["type"] = type.FullName, ["members"] = fields.Concat(props).ToDictionary(m => m.Name, m => Shape(m.Type, depth + 1)) };
}

static object? Dump(object? value, int depth)
{
    switch (value)
    {
        case null: return null;
        case string s: return s.Length > 200 ? s[..200] : s;
        case byte[] b: return "bytes[" + b.Length + "] " + Convert.ToHexString(b.AsSpan(0, Math.Min(b.Length, 48)));
        case Enum e: return e.ToString();
    }
    var type = value.GetType();
    if (type.IsPrimitive || value is decimal) return value;
    if (depth > 4) return type.Name;
    if (value is IEnumerable items) return items.Cast<object?>().Take(5).Select(i => Dump(i, depth + 1)).ToArray();
    var result = new Dictionary<string, object?>();
    foreach (var f in type.GetFields(BindingFlags.Public | BindingFlags.Instance)) { try { result[f.Name] = Dump(f.GetValue(value), depth + 1); } catch (Exception) { } }
    foreach (var p in type.GetProperties(BindingFlags.Public | BindingFlags.Instance).Where(p => p.GetIndexParameters().Length == 0)) { try { result[p.Name] = Dump(p.GetValue(value), depth + 1); } catch (Exception) { } }
    return result;
}


// ---------- --search: online broker directory ----------

static Type? BrokerType() => typeof(MT5API).Assembly.GetType("mtapi.mt5.Broker");

static int DescribeSearch()
{
    var broker = BrokerType();
    if (broker is null) { Console.WriteLine("{\"broker_type\":null}"); return 1; }
    var methods = broker.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance | BindingFlags.DeclaredOnly)
        .Select(m => new
        {
            name = m.Name,
            is_static = m.IsStatic,
            parameters = m.GetParameters().Select(p => $"{p.ParameterType.Name} {p.Name}{(p.HasDefaultValue ? " = " + p.DefaultValue : "")}").ToArray(),
            returns = Shape(Unwrap(m.ReturnType), 0),
        }).ToArray();
    var constructors = broker.GetConstructors().Select(c => string.Join(", ", c.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name))).ToArray();
    Console.WriteLine(JsonSerializer.Serialize(new { sdk_version = typeof(MT5API).Assembly.GetName().Version?.ToString(), broker_type = broker.FullName, constructors, methods },
        new JsonSerializerOptions { WriteIndented = true }));
    return 0;
}

static Type Unwrap(Type t) => t.IsGenericType && t.GetGenericTypeDefinition() == typeof(Task<>) ? t.GetGenericArguments()[0] : t;

static int Search(string term)
{
    term = term.Trim();
    if (!SearchTerm().IsMatch(term)) { Console.Error.WriteLine("SEARCH_TERM_INVALID"); return 2; }
    var broker = BrokerType();
    if (broker is null) { Console.Error.WriteLine("SEARCH_NOT_SUPPORTED_BY_SDK"); return 4; }
    var servers = new Dictionary<string, (string? Company, Dictionary<string, object> Details, List<(string Name, string Address)> Points)>(StringComparer.OrdinalIgnoreCase);
    var errors = new List<string>();
    int calls = 0;
    // Both directories are asked and merged: SearchMQ (MetaQuotes) and Search.
    foreach (var name in new[] { "SearchMQ", "Search", "SearchMQAsync", "SearchAsync" })
    {
        if (name.EndsWith("Async") && calls > 0) break;  // the sync versions already answered
        foreach (var method in broker.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance).Where(m => m.Name == name))
        {
            using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(40));
            var arguments = Arguments(method.GetParameters(), term, cancel.Token);
            if (arguments is null) continue;
            try
            {
                object? target = null;
                if (!method.IsStatic && (target = TryCreate(broker)) is null) continue;
                var result = method.Invoke(target, arguments);
                if (result is Task task)
                {
                    if (!task.Wait(TimeSpan.FromSeconds(45))) throw new TimeoutException();
                    result = task.GetType().GetProperty("Result")?.GetValue(task);
                }
                calls++;
                Collect(result, null, servers, 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
                break;  // one working overload per method name
            }
            catch (Exception ex)
            {
                var inner = ex is TargetInvocationException { InnerException: { } i } ? i : ex;
                if (inner is AggregateException { InnerException: { } a }) inner = a;
                errors.Add($"{name}: {inner.GetType().Name}: {Truncate(inner.Message, 200)}");
            }
        }
    }
    foreach (var e in errors) Console.Error.WriteLine("SEARCH_CALL_FAILED " + e);
    if (calls == 0) { Console.Error.WriteLine("SEARCH_UNAVAILABLE"); return 5; }
    var rows = servers.Where(s => s.Value.Points.Count > 0 || s.Value.Company is not null).Select(s =>
    {
        var points = s.Value.Points.Where(a => Endpoint().IsMatch(a.Address.Trim())).DistinctBy(a => a.Address.Trim().ToLowerInvariant())
            .Select(a => new { name = a.Name, address = a.Address.Trim() }).ToArray();
        var details = s.Value.Details.Where(d => !(d.Value is string v && string.Equals(v, s.Key, StringComparison.OrdinalIgnoreCase)))
            .ToDictionary(d => d.Key, d => d.Value);
        if (s.Value.Company is not null && !details.ContainsKey("CompanyName")) details["CompanyName"] = s.Value.Company;
        return new { server_name = s.Key, details, access_points = points };
    }).Take(100_000).ToArray();
    if (rows.Length == 0) { Console.Error.WriteLine("SEARCH_NO_RESULTS"); return 3; }
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        status = "SEARCHED_NOT_CONNECTED",
        source = "broker-directory",
        query = term,
        sdk_version = typeof(MT5API).Assembly.GetName().Version?.ToString(),
        server_count = rows.Length,
        access_point_count = rows.Sum(r => r.access_points.Length),
        skipped_servers = 0,
        unreadable_extended_records = 0,
        execution_available = false,
        servers = rows
    }));
    return 0;
}

static string Truncate(string s, int n) => s.Length <= n ? s : s[..n];

static object? TryCreate(Type type)
{
    try { return type.GetConstructor(Type.EmptyTypes) is null ? null : Activator.CreateInstance(type); } catch (Exception) { return null; }
}

// Fill a search method's parameters: the first string is the broker name; anything
// else must have a default or be an obvious limit, flag or cancellation token.
static object?[]? Arguments(ParameterInfo[] parameters, string term, CancellationToken token)
{
    var values = new object?[parameters.Length];
    var termUsed = false;
    for (var i = 0; i < parameters.Length; i++)
    {
        var p = parameters[i]; var t = p.ParameterType; var n = (p.Name ?? "").ToLowerInvariant();
        if (t == typeof(string) && !termUsed) { values[i] = term; termUsed = true; }
        else if (t == typeof(CancellationToken)) values[i] = token;
        else if (p.HasDefaultValue) values[i] = p.DefaultValue;
        else if (t == typeof(bool)) values[i] = false;
        else if (t == typeof(TimeSpan)) values[i] = TimeSpan.FromSeconds(30);
        else if ((t == typeof(int) || t == typeof(long)) && n.Contains("timeout")) values[i] = Convert.ChangeType(n.Contains("sec") ? 30 : 30_000, t);
        else if ((t == typeof(int) || t == typeof(long)) && (n.Contains("limit") || n.Contains("count") || n.Contains("max"))) values[i] = Convert.ChangeType(1000, t);
        else return null;
    }
    return termUsed ? values : null;
}

static List<(string Name, object? Value)> Members(object value)
{
    var type = value.GetType();
    var list = new List<(string, object?)>();
    foreach (var f in type.GetFields(BindingFlags.Public | BindingFlags.Instance)) { try { list.Add((f.Name, f.GetValue(value))); } catch (Exception) { } }
    foreach (var p in type.GetProperties(BindingFlags.Public | BindingFlags.Instance).Where(p => p.CanRead && p.GetIndexParameters().Length == 0)) { try { list.Add((p.Name, p.GetValue(value))); } catch (Exception) { } }
    return list;
}

// Walk the search result: a record with a name and access members is a server; a
// company/companyName string on an enclosing record names its company.
static void Collect(object? value, string? company, Dictionary<string, (string? Company, Dictionary<string, object> Details, List<(string Name, string Address)> Points)> servers, int depth, HashSet<object> seen)
{
    if (value is null || depth > 8 || servers.Count > 100_000 || value is string) return;
    var type = value.GetType();
    if (type.IsPrimitive || type.IsEnum || value is decimal || value is byte[] || !seen.Add(value)) return;
    if (value is IEnumerable items) { foreach (var item in items) Collect(item, company, servers, depth + 1, seen); return; }
    var members = Members(value);
    foreach (var (n, v) in members)
        if (v is string s && !string.IsNullOrWhiteSpace(s) && n.ToLowerInvariant() is "company" or "companyname" or "company_name") company = s.Trim();
    var infoEx = members.FirstOrDefault(m => m.Name == "ServerInfoEx").Value;
    var info = members.FirstOrDefault(m => m.Name == "ServerInfo").Value;
    string? serverName = (infoEx is not null ? Members(infoEx).FirstOrDefault(m => m.Name == "ServerName").Value as string : null)
        ?? (info is not null ? Members(info).FirstOrDefault(m => m.Name == "ServerName").Value as string : null);
    var access = members.Where(m => m.Name.ToLowerInvariant().Contains("access")).ToList();
    serverName ??= access.Count > 0 ? members.FirstOrDefault(m => m.Name.ToLowerInvariant() is "name" or "servername" or "server" && m.Value is string s2 && !Endpoint().IsMatch(s2.Trim())).Value as string : null;
    if (!string.IsNullOrWhiteSpace(serverName) && access.Count > 0)
    {
        serverName = serverName.Trim();
        var found = new List<(string Name, string Address)>();
        foreach (var (_, v) in access) FindEndpoints(v, serverName, found, 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
        if (found.Count == 0)
            foreach (var src in new[] { infoEx, info })
                if (src is not null && Members(src).FirstOrDefault(m => m.Name == "Address").Value is string addr && Endpoint().IsMatch(addr.Trim())) found.Add(("Primary", addr.Trim()));
        if (!servers.TryGetValue(serverName, out var entry))
            servers[serverName] = entry = (company, Describe(infoEx, info, value), new List<(string, string)>());
        entry.Points.AddRange(found);
        if (entry.Company is null && company is not null) servers[serverName] = (company, entry.Details, entry.Points);
        return;
    }
    foreach (var (_, v) in members) Collect(v, company, servers, depth + 1, seen);
}


// ---------- --describe-api: the SDK's connection surface (for the account adapter) ----------

static object TypeSurface(Type t, int depth, HashSet<Type> seen)
{
    if (!seen.Add(t)) return new { type = t.FullName, seen = true };
    const BindingFlags flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
    string P(ParameterInfo p) => $"{p.ParameterType.Name} {p.Name}{(p.HasDefaultValue ? " = " + (p.DefaultValue ?? "null") : "")}";
    var nested = new Dictionary<string, object>();
    if (depth > 0)
        foreach (var related in t.GetProperties(flags).Select(p => p.PropertyType)
                     .Concat(t.GetMethods(flags).Select(m => Unwrap(m.ReturnType)))
                     .Select(x => x.IsArray ? x.GetElementType()! : x.IsGenericType ? x.GetGenericArguments().Last() : x)
                     .Where(x => x.Assembly == typeof(MT5API).Assembly && !x.IsEnum).Distinct().Take(40))
            nested[related.FullName ?? related.Name] = TypeSurface(related, depth - 1, seen);
    return new
    {
        type = t.FullName,
        constructors = t.GetConstructors().Select(c => string.Join(", ", c.GetParameters().Select(P))).ToArray(),
        properties = t.GetProperties(flags).Select(p => $"{p.PropertyType.Name} {p.Name}{(p.CanWrite ? " {get;set;}" : "")}").ToArray(),
        fields = t.GetFields(flags).Select(f => $"{f.FieldType.Name} {f.Name}").ToArray(),
        methods = t.GetMethods(flags).Where(m => !m.IsSpecialName).Select(m => $"{(m.IsStatic ? "static " : "")}{m.ReturnType.Name} {m.Name}({string.Join(", ", m.GetParameters().Select(P))})").Distinct().ToArray(),
        events = t.GetEvents(flags).Select(e => $"{e.EventHandlerType?.Name} {e.Name}").ToArray(),
        nested,
    };
}

static int DescribeApi()
{
    var asm = typeof(MT5API).Assembly;
    var enums = asm.GetExportedTypes().Where(t => t.IsEnum && (t.Name.Contains("Margin") || t.Name.Contains("Fill") || t.Name.Contains("Order")
        || t.Name.Contains("Trade") || t.Name.Contains("Account") || t.Name.Contains("Execution") || t.Name.Contains("Symbol")))
        .ToDictionary(t => t.FullName!, t => Enum.GetNames(t));
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        sdk_version = asm.GetName().Version?.ToString(),
        api = TypeSurface(typeof(MT5API), 2, new HashSet<Type>()),
        exported_types = asm.GetExportedTypes().Select(t => t.FullName).OrderBy(n => n).ToArray(),
        enums,
    }, new JsonSerializerOptions { WriteIndented = true }));
    return 0;
}

// ---------- --session: one read-only login (request on stdin) ----------

// Name fragments of SDK methods the session must never call: it reads, it does not trade.
static bool Forbidden(string name) => new[] { "Order", "Close", "Modify", "Cancel", "Send", "Trade", "Position", "Subscribe" }
    .Any(f => name.Contains(f, StringComparison.OrdinalIgnoreCase));

static object? ArgFor(ParameterInfo p, SessionRequest r, SessionHost h)
{
    var n = (p.Name ?? "").ToLowerInvariant();
    var t = Nullable.GetUnderlyingType(p.ParameterType) ?? p.ParameterType;
    if (t == typeof(string) && n.Contains("pass")) return r.password;
    if (t == typeof(string) && (n.Contains("host") || n.Contains("server") || n.Contains("addr"))) return h.host;
    if ((t == typeof(int) || t == typeof(ushort) || t == typeof(uint)) && n.Contains("port")) return Convert.ChangeType(h.port, t);
    if ((t == typeof(ulong) || t == typeof(long) || t == typeof(uint) || t == typeof(int)) && (n.Contains("user") || n.Contains("login") || n.Contains("account")))
        return Convert.ChangeType(r.login, t);
    if (p.HasDefaultValue) return p.DefaultValue;
    throw new InvalidOperationException("unmapped " + p.ParameterType.Name + " " + p.Name);
}

static object Construct(SessionRequest r, SessionHost h)
{
    foreach (var c in typeof(MT5API).GetConstructors().OrderByDescending(c => c.GetParameters().Count(p => !p.HasDefaultValue)))
    {
        object?[] values;
        try { values = c.GetParameters().Select(p => ArgFor(p, r, h)).ToArray(); } catch (InvalidOperationException) { continue; }
        if (values.Length == 0) continue;
        return c.Invoke(values);
    }
    var api = Activator.CreateInstance(typeof(MT5API))!;
    foreach (var (names, value) in new (string[], object)[] { (new[] { "User", "Login" }, r.login), (new[] { "Password" }, r.password),
                 (new[] { "Host", "Server" }, h.host), (new[] { "Port" }, h.port) })
    {
        var prop = names.Select(n => typeof(MT5API).GetProperty(n)).FirstOrDefault(pr => pr is { CanWrite: true })
            ?? throw new InvalidOperationException("SDK has no settable " + names[0]);
        prop.SetValue(api, Convert.ChangeType(value, Nullable.GetUnderlyingType(prop.PropertyType) ?? prop.PropertyType));
    }
    return api;
}

static void Call(object target, string name, int timeoutSeconds)
{
    var m = target.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance)
        .Where(x => x.Name == name || x.Name == name + "Async").OrderBy(x => x.GetParameters().Count(p => !p.HasDefaultValue))
        .FirstOrDefault() ?? throw new InvalidOperationException("SDK has no " + name);
    var args = m.GetParameters().Select(p => p.ParameterType == typeof(int) && (p.Name ?? "").Contains("time", StringComparison.OrdinalIgnoreCase)
        ? (object?)(timeoutSeconds * 1000) : p.HasDefaultValue ? p.DefaultValue : p.ParameterType.IsValueType ? Activator.CreateInstance(p.ParameterType) : null).ToArray();
    var work = Task.Run(() => { var res = m.Invoke(target, args); if (res is Task t) t.GetAwaiter().GetResult(); });
    if (!work.Wait(TimeSpan.FromSeconds(timeoutSeconds))) throw new TimeoutException(name + " did not finish in " + timeoutSeconds + " s");
}

static string Reason(Exception e)
{
    while (e is TargetInvocationException or AggregateException && e.InnerException is not null) e = e.InnerException!;
    return Truncate(e.GetType().Name + ": " + e.Message, 300);
}

// Scalars of an object plus scalars one level down (Account, Group, ...), names prefixed.
static Dictionary<string, object> Facts(object source, int depth = 1)
{
    var result = Describe(source);
    if (depth <= 0) return result;
    foreach (var p in source.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance)
                 .Where(p => p.CanRead && p.GetIndexParameters().Length == 0 && !p.PropertyType.IsPrimitive && p.PropertyType != typeof(string)
                             && !p.PropertyType.IsEnum && !typeof(IEnumerable).IsAssignableFrom(p.PropertyType) && p.PropertyType.IsClass))
    {
        object? v; try { v = p.GetValue(source); } catch (Exception) { continue; }
        if (v is null || v.GetType().Assembly != typeof(MT5API).Assembly) continue;
        foreach (var kv in Describe(v)) result.TryAdd(p.Name + "." + kv.Key, kv.Value);
    }
    return result;
}

static IEnumerable<string> SymbolNames(object symbols)
{
    foreach (var name in new[] { "Names", "SymbolNames", "Infos", "Sessions" })
    {
        var p = symbols.GetType().GetProperty(name);
        var value = p?.GetValue(symbols);
        if (value is IDictionary d) return d.Keys.Cast<object>().Select(k => k.ToString()!).ToArray();
        if (value is IEnumerable<string> list) return list.ToArray();
    }
    var m = symbols.GetType().GetMethods().FirstOrDefault(x => x.GetParameters().Length == 0 && x.Name.Contains("Names"));
    return m?.Invoke(symbols, null) is IEnumerable<string> names ? names.ToArray() : Array.Empty<string>();
}

static Dictionary<string, object> SymbolFacts(object symbols, string symbol)
{
    var result = new Dictionary<string, object>();
    foreach (var m in symbols.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance)
                 .Where(m => m.GetParameters() is [{ ParameterType: var t }] && t == typeof(string) && !Forbidden(m.Name)
                             && m.ReturnType != typeof(void) && m.ReturnType != typeof(bool) && !m.ReturnType.IsPrimitive && m.ReturnType != typeof(string)
                             && (m.Name.StartsWith("Get") || m.Name.Contains("Info") || m.Name.Contains("Group") || m.Name.Contains("Exist")) ))
    {
        object? v; try { v = m.Invoke(symbols, new object[] { symbol }); } catch (Exception) { continue; }
        if (v is null) continue;
        var prefix = m.Name.StartsWith("Get") ? m.Name[3..] : m.Name;
        foreach (var kv in Facts(v)) result.TryAdd(prefix + "." + kv.Key, kv.Value);
    }
    return result;
}

static int Session()
{
    SessionRequest? r;
    try { r = JsonSerializer.Deserialize<SessionRequest>(Console.In.ReadToEnd()); }
    catch (JsonException) { r = null; }
    if (r is null || r.login == 0 || string.IsNullOrEmpty(r.password) || r.hosts is not { Length: > 0 })
    {
        Console.Error.WriteLine("SESSION_REQUEST_INVALID");
        return 2;
    }
    var timeout = Math.Clamp(r.timeout_seconds, 5, 60);
    var attempts = new List<object>();
    foreach (var h in r.hosts.Take(12))
    {
        if (!Host().IsMatch(h.host) || h.port is < 1 or > 65535) { attempts.Add(new { h.host, h.port, ok = false, error = "invalid address" }); continue; }
        object api;
        var watch = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            api = Construct(r, h);
            Call(api, "Connect", timeout);
        }
        catch (Exception e)
        {
            attempts.Add(new { h.host, h.port, ok = false, ms = watch.ElapsedMilliseconds, error = Reason(e).Replace(r.password, "***") });
            continue;
        }
        var connectMs = watch.ElapsedMilliseconds;
        attempts.Add(new { h.host, h.port, ok = true, ms = connectMs });
        var utcNow = DateTime.UtcNow;
        var account = Facts(api);
        account.Remove("Password");
        object? serverTime = null;
        try { serverTime = (api.GetType().GetProperty("ServerTime")?.GetValue(api) as DateTime?)?.ToString("yyyy-MM-ddTHH:mm:ss"); } catch (Exception) { }
        var symbols = new Dictionary<string, object>();
        var available = 0;
        string? symbolError = null;
        try
        {
            var holder = api.GetType().GetProperty("Symbols")?.GetValue(api);
            if (holder is not null)
            {
                var names = SymbolNames(holder).ToArray();
                available = names.Length;
                var wanted = r.symbols is { Length: > 0 } ? r.symbols.Where(s => names.Contains(s, StringComparer.OrdinalIgnoreCase) || names.Length == 0)
                    : names.Take(Math.Clamp(r.max_symbols, 0, 5000));
                foreach (var sym in wanted) symbols[sym] = SymbolFacts(holder, sym);
            }
            else symbolError = "SDK has no Symbols property";
        }
        catch (Exception e) { symbolError = Reason(e); }
        try { Call(api, "Disconnect", 10); } catch (Exception) { }
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            connected = true, h.host, h.port, connect_ms = connectMs, attempts,
            utc_now = utcNow.ToString("yyyy-MM-ddTHH:mm:ss"), server_time = serverTime,
            account, symbols_available = available, symbols, symbol_error = symbolError,
            sdk_version = typeof(MT5API).Assembly.GetName().Version?.ToString(),
        }));
        return 0;
    }
    Console.WriteLine(JsonSerializer.Serialize(new { connected = false, attempts }));
    return 0;
}


// ---------- --stream: live prices for the market-data collector ----------

static void Emit(object message)
{
    var line = JsonSerializer.Serialize(message);
    lock (StreamOut) { Console.Out.WriteLine(line); Console.Out.Flush(); }
}

static string Utc(DateTime t) => (t.Kind == DateTimeKind.Unspecified ? DateTime.SpecifyKind(t, DateTimeKind.Utc) : t.ToUniversalTime()).ToString("yyyy-MM-ddTHH:mm:ss.fffZ");

static object? Member(object o, params string[] names)
{
    var t = o.GetType();
    foreach (var n in names)
    {
        var p = t.GetProperty(n, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
        if (p is not null && p.GetIndexParameters().Length == 0) { try { return p.GetValue(o); } catch (Exception) { } }
        var f = t.GetField(n, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
        if (f is not null) { try { return f.GetValue(o); } catch (Exception) { } }
    }
    return null;
}

static double? Num(object? v) => v switch { null => null, double d => d, float f => f, decimal m => (double)m, IConvertible c when v is not string and not bool and not DateTime => c.ToDouble(null), _ => null };

static void OnQuote(object quote)
{
    var received = DateTime.UtcNow;
    var symbol = Member(quote, "Symbol", "Name") as string;
    if (symbol is null) return;
    var time = Member(quote, "Time", "Datetime", "DateTime");
    Emit(new
    {
        t = "q", s = symbol, n = Interlocked.Increment(ref StreamSeq),
        b = Num(Member(quote, "Bid")), a = Num(Member(quote, "Ask")), l = Num(Member(quote, "Last")),
        v = Num(Member(quote, "Volume", "Vol")),
        st = time is DateTime dt ? DateTime.SpecifyKind(dt, DateTimeKind.Unspecified).ToString("yyyy-MM-ddTHH:mm:ss.fff") : null,
        rx = Utc(received),
    });
}

// Attach OnQuote to the SDK's quote event, whatever its delegate type: the event whose
// handler's last argument carries Bid and Ask.
static string AttachQuotes(object api)
{
    foreach (var e in api.GetType().GetEvents(BindingFlags.Public | BindingFlags.Instance))
    {
        var invoke = e.EventHandlerType?.GetMethod("Invoke");
        var ps = invoke?.GetParameters();
        if (ps is null || ps.Length == 0) continue;
        var arg = ps[^1].ParameterType;
        if (arg.GetProperty("Bid") is null && arg.GetField("Bid") is null) continue;
        var parameters = ps.Select(p => System.Linq.Expressions.Expression.Parameter(p.ParameterType, p.Name)).ToArray();
        Action<object> sink = OnQuote;
        var call = System.Linq.Expressions.Expression.Invoke(System.Linq.Expressions.Expression.Constant(sink),
            System.Linq.Expressions.Expression.Convert(parameters[^1], typeof(object)));
        var handler = System.Linq.Expressions.Expression.Lambda(e.EventHandlerType!, call, parameters).Compile();
        e.AddEventHandler(api, handler);
        return e.Name;
    }
    throw new InvalidOperationException("SDK has no quote event with Bid/Ask");
}

static void AttachDisconnect(object api, Action onDisconnect)
{
    foreach (var e in api.GetType().GetEvents(BindingFlags.Public | BindingFlags.Instance).Where(e => e.Name.Contains("Disconnect", StringComparison.OrdinalIgnoreCase)))
    {
        var ps = e.EventHandlerType!.GetMethod("Invoke")!.GetParameters();
        var parameters = ps.Select(p => System.Linq.Expressions.Expression.Parameter(p.ParameterType, p.Name)).ToArray();
        var body = System.Linq.Expressions.Expression.Invoke(System.Linq.Expressions.Expression.Constant(onDisconnect));
        e.AddEventHandler(api, System.Linq.Expressions.Expression.Lambda(e.EventHandlerType!, body, parameters).Compile());
    }
}

static bool? IsConnected(object api)
{
    foreach (var n in new[] { "Connected", "IsConnected" })
        if (api.GetType().GetProperty(n)?.GetValue(api) is bool b) return b;
    return null;
}

static void SubscribeSymbols(object api, IEnumerable<string> symbols, bool subscribe)
{
    var name = subscribe ? "Subscribe" : "Unsubscribe";
    var methods = api.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance).Where(m => m.Name == name).ToArray();
    var single = methods.FirstOrDefault(m => m.GetParameters() is [{ ParameterType: var t }] && t == typeof(string));
    var many = methods.FirstOrDefault(m => m.GetParameters() is [{ ParameterType: var t }] && t == typeof(string[]));
    var list = symbols.ToArray();
    if (list.Length == 0) return;
    if (many is not null) { many.Invoke(api, new object[] { list }); return; }
    if (single is null) throw new InvalidOperationException("SDK has no " + name + "(string)");
    foreach (var s in list) single.Invoke(api, new object[] { s });
}

// History bars through whichever history method the SDK offers: (symbol, timeframe, from, to)
// or (symbol, timeframe, from, count). Timeframe as the SDK's enum (M1/PERIOD_M1/…) or minutes.
static List<object> History(object api, string symbol, string frame, DateTime from, DateTime to)
{
    var minutes = FrameMinutes[Array.IndexOf(Frames, frame)];
    var candidates = api.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance)
        .Where(m => m.Name.Contains("History", StringComparison.OrdinalIgnoreCase) && m.Name.Contains("Quote", StringComparison.OrdinalIgnoreCase)
                    && m.ReturnType != typeof(void) && m.GetParameters().Any(p => p.ParameterType == typeof(string)))
        .OrderByDescending(m => m.GetParameters().Count(p => p.ParameterType == typeof(DateTime))).ToArray();
    Exception? last = null;
    foreach (var m in candidates)
    {
        try
        {
            var args = m.GetParameters().Select(p =>
            {
                var n = (p.Name ?? "").ToLowerInvariant();
                var t = Nullable.GetUnderlyingType(p.ParameterType) ?? p.ParameterType;
                if (t == typeof(string)) return (object?)symbol;
                if (t.IsEnum)
                {
                    var name = Enum.GetNames(t).FirstOrDefault(x => x.Equals(frame, StringComparison.OrdinalIgnoreCase) || x.EndsWith("_" + frame, StringComparison.OrdinalIgnoreCase))
                        ?? Enum.GetNames(t).FirstOrDefault(x => Convert.ToInt64(Enum.Parse(t, x)) == minutes);
                    if (name is null) throw new InvalidOperationException("timeframe " + frame + " not in " + t.Name);
                    return Enum.Parse(t, name);
                }
                if (t == typeof(DateTime)) return n.Contains("to") || n.Contains("end") ? to : from;
                if ((t == typeof(int) || t == typeof(ushort) || t == typeof(short)) && (n.Contains("time") || n.Contains("frame") || n.Contains("period")))
                    return Convert.ChangeType(minutes, t);
                if (t == typeof(int) || t == typeof(ushort)) return Convert.ChangeType(Math.Min(65000, (int)Math.Ceiling((to - from).TotalMinutes / minutes) + 1), t);
                if (p.HasDefaultValue) return p.DefaultValue;
                throw new InvalidOperationException("unmapped " + t.Name + " " + p.Name);
            }).ToArray();
            var result = m.Invoke(api, args);
            if (result is Task task) { task.Wait(TimeSpan.FromSeconds(60)); result = task.GetType().GetProperty("Result")?.GetValue(task); }
            if (result is not IEnumerable items) continue;
            var bars = new List<object>();
            foreach (var bar in items)
            {
                if (bar is null) continue;
                var time = Member(bar, "Time", "OpenTime", "Datetime");
                if (time is not DateTime t0) continue;
                bars.Add(new
                {
                    t = DateTime.SpecifyKind(t0, DateTimeKind.Unspecified).ToString("yyyy-MM-ddTHH:mm:ss"),
                    o = Num(Member(bar, "OpenPrice", "Open")), h = Num(Member(bar, "HighPrice", "High")),
                    l = Num(Member(bar, "LowPrice", "Low")), c = Num(Member(bar, "ClosePrice", "Close")),
                    v = Num(Member(bar, "Volume", "TickVolume", "RealVolume")),
                    sp = Num(Member(bar, "Spread")),
                });
            }
            return bars;
        }
        catch (Exception e) { last = e; }
    }
    throw last ?? new InvalidOperationException("SDK has no quote history method");
}

static int Stream()
{
    SessionRequest? r;
    try { r = JsonSerializer.Deserialize<SessionRequest>(Console.In.ReadLine() ?? ""); } catch (JsonException) { r = null; }
    if (r is null || r.login == 0 || string.IsNullOrEmpty(r.password) || r.hosts is not { Length: > 0 })
    {
        Console.Error.WriteLine("STREAM_REQUEST_INVALID");
        return 2;
    }
    var timeout = Math.Clamp(r.timeout_seconds, 5, 60);
    object? api = null;
    SessionHost? used = null;
    var attempts = new List<object>();
    foreach (var h in r.hosts.Take(12))
    {
        if (!Host().IsMatch(h.host) || h.port is < 1 or > 65535) { attempts.Add(new { h.host, h.port, ok = false, error = "invalid address" }); continue; }
        var watch = System.Diagnostics.Stopwatch.StartNew();
        try { api = Construct(r, h); AttachQuotes(api); Call(api, "Connect", timeout); used = h; attempts.Add(new { h.host, h.port, ok = true, ms = watch.ElapsedMilliseconds }); break; }
        catch (Exception e) { attempts.Add(new { h.host, h.port, ok = false, ms = watch.ElapsedMilliseconds, error = Reason(e).Replace(r.password, "***") }); api = null; }
    }
    if (api is null || used is null) { Emit(new { t = "failed", attempts }); return 3; }
    var lost = new ManualResetEventSlim(false);
    AttachDisconnect(api, () => lost.Set());
    string? serverTime = null;
    try { serverTime = (api.GetType().GetProperty("ServerTime")?.GetValue(api) as DateTime?)?.ToString("yyyy-MM-ddTHH:mm:ss"); } catch (Exception) { }
    Emit(new { t = "connected", used.host, used.port, attempts, server_time = serverTime, utc_now = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss"),
               sdk_version = typeof(MT5API).Assembly.GetName().Version?.ToString() });
    var subscribed = new HashSet<string>(StringComparer.Ordinal);
    try { SubscribeSymbols(api, r.symbols ?? Array.Empty<string>(), true); foreach (var s in r.symbols ?? Array.Empty<string>()) subscribed.Add(s); Emit(new { t = "subscribed", symbols = subscribed.ToArray() }); }
    catch (Exception e) { Emit(new { t = "error", msg = "subscribe: " + Reason(e) }); }

    var stop = false;
    var commands = new Thread(() =>
    {
        string? line;
        while (!stop && (line = Console.In.ReadLine()) is not null)
        {
            try
            {
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                var cmd = root.GetProperty("cmd").GetString();
                string[] Symbols() => root.TryGetProperty("symbols", out var s) ? s.EnumerateArray().Select(x => x.GetString()!).Where(x => !string.IsNullOrEmpty(x)).ToArray() : Array.Empty<string>();
                if (cmd == "stop") { stop = true; lost.Set(); break; }
                if (cmd == "subscribe") { var add = Symbols().Where(x => !subscribed.Contains(x)).ToArray(); SubscribeSymbols(api, add, true); foreach (var x in add) subscribed.Add(x); Emit(new { t = "subscribed", symbols = subscribed.ToArray() }); }
                else if (cmd == "unsubscribe") { var del = Symbols().Where(subscribed.Contains).ToArray(); try { SubscribeSymbols(api, del, false); } catch (Exception) { } foreach (var x in del) subscribed.Remove(x); Emit(new { t = "subscribed", symbols = subscribed.ToArray() }); }
                else if (cmd == "history")
                {
                    var id = root.GetProperty("id").GetString();
                    var symbol = root.GetProperty("symbol").GetString()!;
                    var frame = root.GetProperty("timeframe").GetString()!;
                    if (Array.IndexOf(Frames, frame) < 0) { Emit(new { t = "history_error", id, msg = "unknown timeframe" }); continue; }
                    var from = DateTime.Parse(root.GetProperty("from").GetString()!, null, System.Globalization.DateTimeStyles.RoundtripKind);
                    var to = DateTime.Parse(root.GetProperty("to").GetString()!, null, System.Globalization.DateTimeStyles.RoundtripKind);
                    try { Emit(new { t = "bars", id, symbol, timeframe = frame, bars = History(api, symbol, frame, from, to) }); }
                    catch (Exception e) { Emit(new { t = "history_error", id, msg = Reason(e) }); }
                }
            }
            catch (Exception e) { Emit(new { t = "error", msg = "command: " + Reason(e) }); }
        }
        stop = true; lost.Set();
    }) { IsBackground = true };
    commands.Start();
    var beat = Math.Clamp(r.heartbeat_seconds, 1, 60);
    while (!stop)
    {
        if (lost.Wait(TimeSpan.FromSeconds(beat))) break;
        if (IsConnected(api) == false) break;
        string? st = null;
        try { st = (api.GetType().GetProperty("ServerTime")?.GetValue(api) as DateTime?)?.ToString("yyyy-MM-ddTHH:mm:ss"); } catch (Exception) { }
        Emit(new { t = "hb", rx = Utc(DateTime.UtcNow), server_time = st, subscribed = subscribed.Count });
    }
    try { Call(api, "Disconnect", 10); } catch (Exception) { }
    Emit(new { t = stop ? "stopped" : "disconnected" });
    return stop ? 0 : 4;
}


partial class Program
{
    static readonly object StreamOut = new();
    static long StreamSeq;
    static readonly string[] Frames = { "M1", "M5", "M15", "M30", "H1", "H4", "D1" };
    static readonly int[] FrameMinutes = { 1, 5, 15, 30, 60, 240, 1440 };

    [GeneratedRegex(@"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]{0,252}|[0-9A-Fa-f:]+:[0-9A-Fa-f:]+):\d{1,5}$")]
    private static partial Regex Endpoint();

    // A bare host: IPv4, IPv6 or a dotted hostname (no port).
    [GeneratedRegex(@"^([0-9]{1,3}(\.[0-9]{1,3}){3}|[0-9A-Fa-f]*:[0-9A-Fa-f:.]+|[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)$")]
    private static partial Regex Host();

    // Broker name as typed in MT5's search: letters, digits, space, dot, dash, underscore.
    [GeneratedRegex(@"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,63}$")]
    private static partial Regex SearchTerm();
}

sealed record SessionHost(string host, int port);
sealed record SessionRequest(ulong login, string password, SessionHost[] hosts, string[]? symbols, int max_symbols = 2000, int timeout_seconds = 20, int heartbeat_seconds = 5);
