#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${OUT:-$ROOT/SCREENSHOTS/real}"
mkdir -p "$OUT"

capture() {
  local name="$1" prompt="$2"
  echo
  echo "Prepare: $prompt"
  echo 'Capturing in 8 seconds...'
  sleep 8
  if command -v spectacle >/dev/null 2>&1; then
    spectacle -b -n -o "$OUT/$name.png"
  elif command -v gnome-screenshot >/dev/null 2>&1; then
    gnome-screenshot -f "$OUT/$name.png"
  elif command -v grim >/dev/null 2>&1; then
    grim "$OUT/$name.png"
  else
    echo 'No screenshot command found (spectacle/gnome-screenshot/grim).' >&2
    exit 2
  fi
  echo "Saved $OUT/$name.png"
}

capture 01-preferences-mpv 'Open ShangBackground → Preferences → mpv/video settings.'
capture 02-language-before 'Select Chinese and show the language dropdown.'
capture 03-language-after 'Switch to English and keep the same page visible; verify labels re-render.'
capture 04-kde-video-wayland 'On KDE Wayland, start a video wallpaper and leave desktop icons visible.'
capture 05-kde-video-x11 'Log into KDE X11, start the same video wallpaper and leave desktop icons visible.'
capture 06-build-feature-size 'Open Build GUI, select features, and show the size/runtime hint.'
capture 07-kde-tray-autostart 'Show the KDE tray menu plus single-instance/autostart settings.'

echo 'All requested screenshots captured.'
