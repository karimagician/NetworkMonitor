#!/usr/bin/env bash
# Linux : double-cliquez (ou ./lancer.sh) pour lancer l'application.
set -u
cd "$(dirname "$0")" || exit 1

find_python() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PY="$(find_python)" || {
  echo "[ERREUR] Python 3.8+ est introuvable. Installez-le, par exemple :"
  echo "  Debian/Ubuntu : sudo apt install python3 python3-tk"
  echo "  Fedora        : sudo dnf install python3 python3-tkinter"
  read -r -p "Appuyez sur Entree pour fermer..." _
  exit 1
}

if ! "$PY" -c 'import tkinter' >/dev/null 2>&1; then
  echo "[ERREUR] Le module Tkinter est absent."
  echo "  Debian/Ubuntu : sudo apt install python3-tk"
  echo "  Fedora        : sudo dnf install python3-tkinter"
  read -r -p "Appuyez sur Entree pour fermer..." _
  exit 1
fi

exec "$PY" network_monitor.py "$@"
