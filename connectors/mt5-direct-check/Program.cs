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
var inspect = args.Length == 2 && args[0] == "--inspect";
if (args.Length != 1 && !inspect)
{
    Console.Error.WriteLine("Usage: ServerCatalog [--inspect] <servers.dat>");
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
            foreach (var address in access?.Addresses ?? []) if (address is not null) addresses.Add((access!.AccessRec?.ServerName ?? "Access point", address.Address));
        // Newer files carry extended access records whose shape the SDK does not
        // document. Read any host:port they contain instead of rejecting the file.
        foreach (var accessEx in server.AccessesEx ?? [])
        {
            var found = new List<(string Name, string Address)>();
            FindEndpoints(accessEx, "Access point", found, 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
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

partial class Program
{
    [GeneratedRegex(@"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]{0,252}|[0-9A-Fa-f:]+:[0-9A-Fa-f:]+):\d{1,5}$")]
    private static partial Regex Endpoint();

    // A bare host: IPv4, IPv6 or a dotted hostname (no port).
    [GeneratedRegex(@"^([0-9]{1,3}(\.[0-9]{1,3}){3}|[0-9A-Fa-f]*:[0-9A-Fa-f:.]+|[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)$")]
    private static partial Regex Host();
}
