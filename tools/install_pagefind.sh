#!/usr/bin/env bash
# Download the standard pagefind binary for the current platform.
# Chinese text is jieba-segmented at render time, so we don't need the
# pagefind_extended (CJK n-gram) variant. Idempotent.
set -euo pipefail

VERSION="${PAGEFIND_VERSION:-v1.5.2}"
BIN="${BIN:-bin}"
mkdir -p "$BIN"

if [ -x "$BIN/pagefind" ]; then
  echo "$BIN/pagefind already present"
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

asset="pagefind-${VERSION}-${triple}.tar.gz"
url="https://github.com/CloudCannon/pagefind/releases/download/${VERSION}/${asset}"
echo "downloading $url"
curl -sSL "$url" -o /tmp/pagefind.tar.gz
tar -xzf /tmp/pagefind.tar.gz -C "$BIN"
rm -f /tmp/pagefind.tar.gz
chmod +x "$BIN/pagefind"
echo "installed: $($BIN/pagefind --version)"
