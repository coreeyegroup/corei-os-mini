using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using mtapi.mt5;

// Reads a broker's MT5 servers.dat. No login, connection, subscription or order calls.
// For each server it returns the name, every scalar field the SDK exposes on the
// server records (company, time zone, and whatever else the file holds), and the
// named access points. Fields are read by reflection so nothing the file carries is
// dropped because of an assumed field name.
if (args.Length != 1)
{
    Console.Error.WriteLine("Usage: ServerCatalog <servers.dat>");
    return 2;
}

try
{
    using var file = File.OpenRead(args[0]);
    if (file.Length < 32 || file.Length > 1024 * 1024)
        throw new InvalidDataException();
    var bytes = new byte[checked((int)file.Length)];
    file.ReadExactly(bytes);
    var servers = MT5API.LoadServersDat(bytes);
    if (servers is null || servers.Length == 0)
        throw new InvalidDataException();
    var rows = servers.Select(server =>
    {
        var name = server.ServerInfoEx?.ServerName ?? server.ServerInfo?.ServerName;
        if (string.IsNullOrWhiteSpace(name)) throw new InvalidDataException();
        var addresses = new List<(string Name, string Address)>();
        foreach (var access in server.Accesses ?? [])
            foreach (var address in access.Addresses ?? []) addresses.Add((access.AccessRec?.ServerName ?? "Access point", address.Address));
        // This SDK exposes no readable endpoint on AddressRecEx. Reject rather
        // than silently returning an incomplete catalog for newer file formats.
        if (server.AccessesEx is { Length: > 0 })
            throw new InvalidDataException();
        var primary = server.ServerInfoEx?.Address ?? server.ServerInfo?.Address;
        if (addresses.Count == 0 && !string.IsNullOrWhiteSpace(primary)) addresses.Add(("Primary", primary));
        var endpoints = addresses.Where(a => !string.IsNullOrWhiteSpace(a.Address))
            .Distinct().Select(a => new { name = a.Name, address = a.Address }).ToArray();
        if (endpoints.Length == 0 || endpoints.Length > 8192) throw new InvalidDataException();
        return new { server_name = name, details = Describe(server.ServerInfoEx, server.ServerInfo, server), access_points = endpoints };

    }).ToArray();
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        status = "PARSED_NOT_CONNECTED",
        source_sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(),
        sdk_version = typeof(MT5API).Assembly.GetName().Version?.ToString(),
        server_count = rows.Length,
        execution_available = false,
        servers = rows
    }, new JsonSerializerOptions { WriteIndented = true }));
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
