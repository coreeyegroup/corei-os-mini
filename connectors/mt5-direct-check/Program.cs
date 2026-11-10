using System.Security.Cryptography;
using System.Text.Json;
using mtapi.mt5;

// Local qualification tool. No login, connection, subscription or order calls.
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
        return new { server_name = name, access_points = endpoints };

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
