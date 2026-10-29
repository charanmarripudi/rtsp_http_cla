#!/bin/bash
# ─────────────────────────────────────────────────
#  Generate a browser-trusted SSL certificate.
#  Uses mkcert (preferred) — zero browser warnings.
#  Falls back to plain openssl if mkcert is absent.
#  Run ONCE (or after IP changes): ./gen_cert.sh
# ─────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_DIR="$SCRIPT_DIR/certs"
MY_IP=$(ipconfig getifaddr en0)

mkdir -p "$CERT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "    Generating SSL certificate"
echo "  IP : $MY_IP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Try mkcert first (trusted by Chrome/Firefox with no warnings) ──────────
if command -v mkcert &>/dev/null; then

    echo "  Using mkcert (browser-trusted) ✓"

    # Install local CA into system/browser trust stores (only needed once)
    mkcert -install

    # Generate cert for the local IP + localhost
    mkcert \
        -cert-file "$CERT_DIR/cert.pem" \
        -key-file  "$CERT_DIR/key.pem" \
        "$MY_IP" "localhost" "127.0.0.1"

    echo ""
    echo "  Certificate trusted by Chrome, Safari, Firefox — NO warnings."
    echo "  Other devices on the local network:"
    echo "    1. Copy  $(mkcert -CAROOT)/rootCA.pem  to the other device"
    echo "    2. Install it as a trusted CA (Settings → Certificates)"
    echo "    3. Then  https://$MY_IP:8445  works warning-free everywhere."

else

    # ── Fallback: plain openssl ────────────────────────────────────────────
    echo "  mkcert not found — using openssl (will show browser warning)."
    echo "  To install mkcert:  brew install mkcert"
    echo ""

    openssl req -x509 -newkey rsa:2048 -nodes \
      -keyout "$CERT_DIR/key.pem" \
      -out    "$CERT_DIR/cert.pem" \
      -days   825 \
      -subj   "/CN=$MY_IP" \
      -addext "subjectAltName=IP:$MY_IP,IP:127.0.0.1,DNS:localhost"

    echo ""
    echo "  ⚠  Self-signed cert — Chrome BLOCKS this in iframes."
    echo "     Run  brew install mkcert  then re-run  ./gen_cert.sh"
fi

echo ""
echo "  cert : $CERT_DIR/cert.pem"
echo "  key  : $CERT_DIR/key.pem"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"




