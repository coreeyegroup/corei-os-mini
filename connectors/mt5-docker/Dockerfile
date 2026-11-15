# COREI MT5 container: the official MetaTrader 5 terminal under WineHQ's Wine on Debian (where Wine installs cleanly),
# a virtual desktop you can view in a browser (KasmVNC, published on 127.0.0.1 only), and COREI's terminal bridge
# (TLS + pinned certificate + token). One container per MT5 account. Built and started by scripts/use-mt5-docker.sh.
FROM ghcr.io/linuxserver/baseimage-kasmvnc:debianbookworm

ENV TITLE="COREI MT5" \
    WINEPREFIX=/config/.wine \
    WINEDEBUG=-all \
    WINEDLLOVERRIDES="mscoree=;mshtml="

# Which Wine: "latest" = WineHQ's newest stable; a version such as "10.0" or "9.0" = that WineHQ stable release;
# "debian" = Debian's own Wine. scripts/mt5-docker-wine-doctor.sh picks the one that runs on the server.
ARG WINE=latest
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg2 wget wmctrl; \
    dpkg --add-architecture i386; \
    if [ "$WINE" = debian ]; then \
      apt-get update; \
      apt-get install -y --install-recommends wine wine64 wine32:i386; \
    else \
      mkdir -pm755 /etc/apt/keyrings; \
      wget -qO /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key; \
      wget -qNP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources; \
      apt-get update; \
      if [ "$WINE" = latest ]; then \
        apt-get install -y --install-recommends winehq-stable; \
      else \
        V=$(apt-cache madison wine-stable | awk -v v="$WINE" 'index($3, v ".") == 1 {print $3; exit}'); \
        test -n "$V"; \
        apt-get install -y --install-recommends "winehq-stable=$V" "wine-stable=$V" "wine-stable-amd64=$V" "wine-stable-i386:i386=$V"; \
      fi; \
    fi; \
    wine --version; \
    apt-get clean; rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY connectors/mt5-docker/start.sh /corei/start.sh
COPY connectors/mt5-bridge/corei_mt5_bridge.py /corei/corei_mt5_bridge.py
COPY connectors/mt5-docker/root/ /
RUN chmod 755 /corei/start.sh

EXPOSE 3000 9443
VOLUME /config
