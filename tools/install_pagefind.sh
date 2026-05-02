#!/usr/bin/env bash
# Download the pagefind_extended binary (CJK-capable) for the current platform.
# Idempotent: skips download if bin/pagefind_extended is already present.
set -euo pipefail

VERSION="${PAGEFIND_VERSION:-v1.5.2}"
BIN="${BIN:-bin}"
mkdir -p "$BIN"

if [ -x "$BIN/pagefind_extended" ]; then
  echo "$BIN/pagefind_extended already present"
  exit 0
fi

uname_s="$(uname -s)"
uname_m="$(uname -m)"
case "$uname_s-$uname_m" in
  Darwin-arm64)   triple="aarch64-apple-darwin" ;;
  Darwin-x86_64)  triple="x86_64-apple-darwin" ;;
  Linux-x86_64)   triple="x86_64-unknown-linux-musl" ;;
  Linux-aarch64)  triple="aarch64-unknown-linux-musl" ;;
  *) echo "unsupported platform: $uname_s-$uname_m" >&2; exit 1 ;;
esac

asset="pagefind_extended-${VERSION}-${triple}.tar.gz"
url="https://github.com/CloudCannon/pagefind/releases/download/${VERSION}/${asset}"
echo "downloading $url"
curl -sSL "$url" -o /tmp/pagefind.tar.gz
tar -xzf /tmp/pagefind.tar.gz -C "$BIN"
rm -f /tmp/pagefind.tar.gz
chmod +x "$BIN/pagefind_extended"
echo "installed: $($BIN/pagefind_extended --version)"
