#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
VIDEO="${VIDEO:-${1:-}}"
export PYTHONPATH="$ROOT/src:$ROOT"

echo '== Session =='
printf 'XDG_SESSION_TYPE=%s\n' "${XDG_SESSION_TYPE:-}"
printf 'XDG_CURRENT_DESKTOP=%s\n' "${XDG_CURRENT_DESKTOP:-}"
printf 'WAYLAND_DISPLAY=%s\n' "${WAYLAND_DISPLAY:-}"
printf 'DBUS_SESSION_BUS_ADDRESS=%s\n' "${DBUS_SESSION_BUS_ADDRESS:-}"

case "${XDG_CURRENT_DESKTOP:-}${XDG_SESSION_DESKTOP:-}" in
  *KDE*|*kde*|*Plasma*|*plasma*) ;;
  *) echo 'ERROR: run this script inside a KDE Plasma session.' >&2; exit 2 ;;
esac

for cmd in plasma-apply-wallpaperimage qdbus6 qdbus mpvpaper; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '%-32s %s\n' "$cmd" "$(command -v "$cmd")"
  else
    printf '%-32s MISSING\n' "$cmd"
  fi
done

echo '== Capability probe =='
"$PYTHON" - <<'PY'
import json
from platform_adapters.backends.linux.capabilities import probe_capabilities
print(json.dumps(probe_capabilities(), ensure_ascii=False, indent=2))
PY

echo '== Portal introspection =='
if command -v gdbus >/dev/null 2>&1; then
  gdbus introspect --session \
    --dest org.freedesktop.portal.Desktop \
    --object-path /org/freedesktop/portal/desktop \
    | grep -A8 -B2 GlobalShortcuts || true
else
  echo 'gdbus unavailable; use qdbus6 org.freedesktop.portal.Desktop /org/freedesktop/portal/desktop'
fi

echo '== Portal hotkey consent smoke =='
"$PYTHON" - <<'PY'
import time
from platform_adapters.backends.linux.portal_hotkeys import PortalGlobalShortcuts
hits=[]
portal=PortalGlobalShortcuts(startup_timeout=8)
ok=portal.start({'next':'Ctrl+Alt+n'}, lambda action: hits.append(action))
print('session_created=', ok, 'error=', portal.last_error)
if ok:
    print('Approve the compositor dialog, then press Ctrl+Alt+N within 20 seconds.')
    deadline=time.time()+20
    while time.time()<deadline and not hits:
        time.sleep(0.2)
    print('activations=', hits)
portal.stop()
raise SystemExit(0 if ok else 3)
PY

if [[ -n "$VIDEO" ]]; then
  echo '== Video start/stop smoke =='
  [[ -f "$VIDEO" ]] || { echo "Video not found: $VIDEO" >&2; exit 4; }
  VIDEO="$VIDEO" "$PYTHON" - <<'PY'
import os, time
from platform_adapters.backends.linux.video import start_video_wallpaper, stop_video_wallpaper, is_video_wallpaper_running
ok, message = start_video_wallpaper(os.environ['VIDEO'], muted=True, volume=0)
print('start=', ok, message)
if ok:
    time.sleep(10)
    print('running=', is_video_wallpaper_running())
stop_video_wallpaper()
print('stopped=', not is_video_wallpaper_running())
raise SystemExit(0 if ok else 5)
PY
else
  echo 'SKIP video smoke: pass a file path or set VIDEO=/path/to/video.mp4'
fi

echo 'KDE dynamic check completed.'
