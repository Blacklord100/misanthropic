#!/usr/bin/env bash
# Finish the Developer ID signing pipeline once the certificate is downloaded.
#
#   bash packaging/finish-signing.sh [path/to/developerID_application.cer]
#
# Prereq (one-time, done once ever):
#   1. A CSR was generated at ~/Developer/misanthropic-signing/ — upload
#      CertificateSigningRequest.certSigningRequest at
#      https://developer.apple.com/account/resources/certificates/add
#      choosing type "Developer ID Application" (Account Holder only).
#   2. Download the resulting developerID_application.cer (default: ~/Downloads).
#
# This script then does the rest: imports the cert + private key into the login
# keychain, builds the app signed with hardened runtime, notarizes, staples,
# and packages the DMG. Safe to re-run; each step is idempotent.
set -euo pipefail

cd "$(dirname "$0")/.."

KEY="$HOME/Developer/misanthropic-signing/developer_id.key"
PROFILE="misanthropic-notary"

# --- locate the downloaded certificate -------------------------------------
CER="${1:-}"
if [ -z "$CER" ]; then
  CER="$(ls -t "$HOME"/Downloads/developerID_application*.cer 2>/dev/null | head -1 || true)"
fi
[ -n "$CER" ] && [ -f "$CER" ] || {
  echo "error: no Developer ID certificate found." >&2
  echo "  Upload ~/Developer/misanthropic-signing/CertificateSigningRequest.certSigningRequest" >&2
  echo "  at https://developer.apple.com/account/resources/certificates/add (type: Developer ID" >&2
  echo "  Application), download the .cer, then re-run this script." >&2
  exit 1
}

SUBJECT="$(openssl x509 -inform der -in "$CER" -noout -subject 2>/dev/null || openssl x509 -in "$CER" -noout -subject)"
case "$SUBJECT" in
  *"Developer ID Application"*) ;;
  *)
    echo "error: $CER is not a 'Developer ID Application' certificate:" >&2
    echo "  $SUBJECT" >&2
    echo "  (An 'Apple Development' cert cannot sign apps for distribution.)" >&2
    exit 1
    ;;
esac

# --- import key + cert into the login keychain ------------------------------
IDENTITY="$(echo "$SUBJECT" | sed -n 's/.*CN=\([^,]*\).*/\1/p')"
if ! security find-identity -v -p codesigning | grep -q "$IDENTITY"; then
  echo "==> Importing private key + certificate into login keychain"
  [ -f "$KEY" ] || { echo "error: private key $KEY not found (it pairs with the CSR)" >&2; exit 1; }
  security import "$KEY" -k ~/Library/Keychains/login.keychain-db -T /usr/bin/codesign 2>/dev/null || true
  security import "$CER" -k ~/Library/Keychains/login.keychain-db -T /usr/bin/codesign
fi
security find-identity -v -p codesigning | grep "$IDENTITY" || {
  echo "error: identity did not become valid after import (missing Apple intermediate CA?)" >&2
  echo "  Install 'Developer ID - G2' from https://www.apple.com/certificateauthority/ and re-run." >&2
  exit 1
}
echo "==> Signing identity ready: $IDENTITY"

# --- build signed ------------------------------------------------------------
SIGN_IDENTITY="$IDENTITY" bash packaging/build.sh
APP="dist/Misanthropic.app"

# --- notarize + staple -------------------------------------------------------
if ! xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1; then
  echo "==> One-time notary credential setup (needs an app-specific password"
  echo "    from https://account.apple.com -> Sign-In and Security):"
  TEAM_ID="$(echo "$SUBJECT" | sed -n 's/.*OU=\([^,]*\).*/\1/p')"
  xcrun notarytool store-credentials "$PROFILE" \
    --apple-id "mightymithu@hotmail.com" --team-id "$TEAM_ID"
fi

echo "==> Notarizing (this can take a few minutes)"
ditto -c -k --keepParent "$APP" dist/Misanthropic-notarize.zip
xcrun notarytool submit dist/Misanthropic-notarize.zip \
  --keychain-profile "$PROFILE" --wait
rm -f dist/Misanthropic-notarize.zip

echo "==> Stapling notarization ticket"
xcrun stapler staple "$APP"

# --- package -----------------------------------------------------------------
bash packaging/make_dmg.sh
echo "==> Done. The DMG in dist/ opens on any Mac with a plain double-click."
