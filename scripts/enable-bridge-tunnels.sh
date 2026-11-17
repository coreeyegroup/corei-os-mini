#!/usr/bin/env bash
# Bridge nodes in tunnel mode (DEC-75): let a Windows PC or locked-down VPS open an SSH tunnel OUT to this server so
# COREI reaches its bridge on 127.0.0.1:<port>, with COREI managing the allowed keys itself (no hand-edited
# authorized_keys). Run ONCE, as root:  sudo bash scripts/enable-bridge-tunnels.sh
#
# What it sets up:
#   * a system user "corei-tunnel" with no shell, no password and no home login;
#   * /var/lib/corei-os-mini/tunnel/authorized_keys, written by COREI (owner: the repository user) when a node enrols:
#     one line per node, each allowed ONLY to listen on 127.0.0.1:<its port> (restrict, port-forwarding, permitlisten,
#     command /bin/false: no shell, no other forwarding);
#   * sshd reads those keys through a small root-owned command, only for that user (drop-in
#     /etc/ssh/sshd_config.d/60-corei-tunnel.conf). The configuration is tested with "sshd -t" before sshd reloads;
#     if the test fails nothing is changed.
#   * COREI_TUNNEL_KEYS in /etc/corei-os-mini/workstation.env, then the backend restarts.
# Your own SSH access is not touched.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
cd "$(dirname "$0")/.."
APP_USER=${SUDO_USER:-$(stat -c %U .)}
USER_NAME=corei-tunnel
DIR=/var/lib/corei-os-mini/tunnel
KEYS=$DIR/authorized_keys
CMD=/usr/local/libexec/corei-tunnel-keys
DROPIN=/etc/ssh/sshd_config.d/60-corei-tunnel.conf
ENV=/etc/corei-os-mini/workstation.env

if ! grep -Eqs '^\s*Include\s+/etc/ssh/sshd_config.d/\*\.conf' /etc/ssh/sshd_config; then
    echo 'sshd_config does not include /etc/ssh/sshd_config.d/*.conf; add that line at the top of /etc/ssh/sshd_config first.' >&2
    exit 1
fi
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$USER_NAME"
usermod -p '*' "$USER_NAME"  # no password at all ('*'), not 'locked' ('!'): sshd may refuse key logins for a locked account

install -d -m 0755 -o root -g root /var/lib/corei-os-mini
install -d -m 0755 -o "$APP_USER" -g "$APP_USER" "$DIR"
[[ -f $KEYS ]] || install -m 0644 -o "$APP_USER" -g "$APP_USER" /dev/null "$KEYS"

install -d -m 0755 /usr/local/libexec
cat > "$CMD" <<'SCRIPT'
#!/bin/sh
# sshd AuthorizedKeysCommand for the COREI tunnel user only: prints the keys COREI manages.
[ "$1" = "corei-tunnel" ] && [ -f /var/lib/corei-os-mini/tunnel/authorized_keys ] && grep -E '^restrict,port-forwarding,permitlisten="127\.0\.0\.1:[0-9]+",command="/bin/false" ' /var/lib/corei-os-mini/tunnel/authorized_keys
exit 0
SCRIPT
chown root:root "$CMD"; chmod 0755 "$CMD"

TMP=$(mktemp)
cat > "$TMP" <<CONF
# COREI bridge tunnels (scripts/enable-bridge-tunnels.sh): reverse port forwarding to 127.0.0.1 only, nothing else.
Match User $USER_NAME
    AuthorizedKeysFile none
    AuthorizedKeysCommand $CMD %u
    AuthorizedKeysCommandUser nobody
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding remote
    GatewayPorts no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitTunnel no
    ForceCommand /bin/false
CONF
OLD=""
[[ -f $DROPIN ]] && OLD=$(cat "$DROPIN")
install -m 0644 "$TMP" "$DROPIN"; rm -f "$TMP"
if ! sshd -t; then
    if [[ -n $OLD ]]; then printf '%s\n' "$OLD" > "$DROPIN"; else rm -f "$DROPIN"; fi
    echo 'sshd rejected the configuration; nothing changed.' >&2
    exit 1
fi
systemctl reload ssh 2>/dev/null || systemctl reload sshd

install -d -m 0750 /etc/corei-os-mini
touch "$ENV"
if grep -q '^COREI_TUNNEL_KEYS=' "$ENV"; then
    sed -i "s|^COREI_TUNNEL_KEYS=.*|COREI_TUNNEL_KEYS=$KEYS|" "$ENV"
else
    echo "COREI_TUNNEL_KEYS=$KEYS" >> "$ENV"
fi
grep -q '^COREI_TUNNEL_USER=' "$ENV" || echo "COREI_TUNNEL_USER=$USER_NAME" >> "$ENV"
systemctl restart corei-os-mini-workstation 2>/dev/null || true
echo "Done. Tunnel-mode bridge nodes now connect as $USER_NAME@$(hostname -f 2>/dev/null || hostname); COREI writes their keys to $KEYS."
echo 'Existing tunnels that use another user keep working.'
