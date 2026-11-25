#!/bin/sh
# Genera un certificado autofirmado la primera vez que arranca el gateway.
# La clave vive en el volumen `gateway_tls`, nunca en el repositorio.
set -eu

TLS_DIR=/etc/nginx/tls
CRT="$TLS_DIR/nginx.crt"
KEY="$TLS_DIR/nginx.key"

mkdir -p "$TLS_DIR"

if [ ! -s "$CRT" ] || [ ! -s "$KEY" ]; then
    echo "[gateway] Generando certificado TLS autofirmado en $TLS_DIR"
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -keyout "$KEY" \
        -out "$CRT" \
        -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
    chmod 600 "$KEY"
fi

if [ "$#" -eq 0 ]; then
    set -- nginx -g 'daemon off;'
fi

exec "$@"
