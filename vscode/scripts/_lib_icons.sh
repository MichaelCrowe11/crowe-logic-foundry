#!/usr/bin/env bash
# Shared icon-rendering helpers for the Crowe Logic VS Code rebrand scripts.
# Source this file: `source "$(dirname "$0")/_lib_icons.sh"`
#
# Provides:
#   render_png  <svg> <out.png> <size>
#   render_iconset_dir  <svg> <out_dir>          # writes icon_{16..1024}x{...}.png + @2x variants
#   build_icns  <svg> <out.icns> [tmpdir]
#   build_ico   <svg> <out.ico>  [tmpdir]
#   build_png   <svg> <out.png>  <size>
#
# Returns non-zero (without exiting) when no rasterizer is available so callers
# can decide whether to fall back to pre-staged assets.

set -u

render_png() {
  local svg="$1" out="$2" size="$3"
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w "$size" -h "$size" "$svg" -o "$out"
  elif command -v magick >/dev/null 2>&1; then
    magick -background none -density 1200 "$svg" -resize "${size}x${size}" "$out"
  elif command -v convert >/dev/null 2>&1; then
    convert -background none -density 1200 "$svg" -resize "${size}x${size}" "$out"
  else
    return 2
  fi
}

render_iconset_dir() {
  local svg="$1" dir="$2"
  mkdir -p "$dir"
  local sz half
  for sz in 16 32 64 128 256 512 1024; do
    render_png "$svg" "$dir/icon_${sz}x${sz}.png" "$sz" || return $?
    if (( sz > 16 )); then
      half=$((sz / 2))
      cp "$dir/icon_${sz}x${sz}.png" "$dir/icon_${half}x${half}@2x.png"
    fi
  done
}

build_icns() {
  local svg="$1" out="$2" tmp="${3:-$(mktemp -d)}"
  local iconset="$tmp/CroweLogic.iconset"
  render_iconset_dir "$svg" "$iconset" || return $?
  if ! command -v iconutil >/dev/null 2>&1; then
    echo "iconutil not available (macOS-only)" >&2
    return 3
  fi
  iconutil -c icns "$iconset" -o "$out"
}

build_png() {
  local svg="$1" out="$2" size="$3"
  render_png "$svg" "$out" "$size"
}

build_ico() {
  local svg="$1" out="$2" tmp="${3:-$(mktemp -d)}"
  if ! command -v magick >/dev/null 2>&1 && ! command -v convert >/dev/null 2>&1; then
    echo "imagemagick required to build .ico" >&2
    return 3
  fi
  local sz
  local -a sizes=(16 32 48 64 128 256)
  local -a pngs=()
  for sz in "${sizes[@]}"; do
    render_png "$svg" "$tmp/ico_${sz}.png" "$sz" || return $?
    pngs+=("$tmp/ico_${sz}.png")
  done
  if command -v magick >/dev/null 2>&1; then
    magick "${pngs[@]}" "$out"
  else
    convert "${pngs[@]}" "$out"
  fi
}
