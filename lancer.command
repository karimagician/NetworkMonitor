#!/usr/bin/env bash
# macOS : double-cliquez sur ce fichier pour lancer l'application.
# (Si macOS refuse : clic droit > Ouvrir, une seule fois.)
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
  echo "[ERREUR] Python 3.8+ est introuvable."
  echo "Installez-le depuis https://www.python.org/downloads/ puis relancez ce fichier."
  read -r -p "Appuyez sur Entree pour fermer..." _
  exit 1
}

if ! "$PY" -c 'import tkinter' >/dev/null 2>&1; then
  echo "[ERREUR] Le module Tkinter est absent de cet interpreteur Python."
  echo "Reinstallez Python depuis python.org (Tkinter y est inclus)."
  read -r -p "Appuyez sur Entree pour fermer..." _
  exit 1
fi

exec "$PY" network_monitor.py "$@"
