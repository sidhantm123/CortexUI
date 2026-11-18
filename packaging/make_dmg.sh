#!/bin/bash
# Make dist/CortexUI-<version>-macOS-<AppleSilicon|Intel>.dmg from dist/CortexUI.app
# (after `pyinstaller --clean --noconfirm cortexui.spec`). Opening the .dmg shows the app next to
# an Applications shortcut: drag one onto the other to install.
set -euo pipefail
cd "$(dirname "$0")/.."

APP=dist/CortexUI.app
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' version.py)
case "$(lipo -archs "$APP/Contents/MacOS/CortexUI")" in
    arm64) ARCH=AppleSilicon ;;
    x86_64) ARCH=Intel ;;
    *) echo "unexpected architecture: $(lipo -archs "$APP/Contents/MacOS/CortexUI")" >&2; exit 1 ;;
esac
OUT="dist/CortexUI-$VERSION-macOS-$ARCH.dmg"

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/CortexUI.app"  # keeps the bundle's symlinks and signatures
ln -s /Applications "$STAGE/Applications"

rm -f "$OUT"
# hdiutil now and then fails with "Resource busy" (seen on CI runners): try again
for attempt in 1 2 3; do
    if hdiutil create -volname "CortexUI $VERSION" -srcfolder "$STAGE" -format UDZO -ov "$OUT"; then
        echo "$OUT"
        exit 0
    fi
    sleep 5
done
exit 1
