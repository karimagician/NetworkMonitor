#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supervision Reseau - Switch Monitor Pro (v2.0)
=============================================

Superviseur d'equipements reseau (switchs, routeurs, AP, serveurs) base sur le
ping systeme. Aucune dependance externe, aucun droit administrateur requis.

Correctifs v2.0 par rapport a la v1 :
  1. Chemins ancres sur le dossier du script (Path(__file__).parent).
  2. Multiplateforme : Windows / Linux / macOS (ping + alerte sonore adaptes).
  3. Pings paralleles (ThreadPoolExecutor) + verrou anti-double-comptage.
  4. Validation stricte des cibles (ipaddress / hostname) -> pas d'injection
     d'arguments dans la commande ping.
  5. Journalisation via logging + RotatingFileHandler (5 Mo x 3).
  6. Source unique de verite pour la configuration par defaut.
  7. Decodage robuste de la sortie ping (codepage OEM sous Windows).
  8. Regex de latence independante de la langue de l'OS.
  9. Correctif du bug Tk sur les couleurs de tags du Treeview.
 10. Intervalle et seuil pris en compte a chaud, sans redemarrage.
 11. Nettoyage complet de l'etat a la suppression d'un equipement.
 12. Deduplication des IP au chargement de la configuration.

Usage :
    python network_monitor.py              # interface graphique
    python network_monitor.py --selftest   # autotest en console (sans GUI)
    python network_monitor.py --init-config
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import platform
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# --- Tkinter est importe de facon tolerante : l'autotest fonctionne sans GUI ---
try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - environnement sans Tk
    tk = ttk = messagebox = simpledialog = None  # type: ignore[assignment]
    TK_AVAILABLE = False

# --- Alerte sonore : winsound n'existe que sous Windows ---
try:
    import winsound
except ImportError:
    winsound = None  # type: ignore[assignment]

APP_NAME = "Supervision Reseau - Switch Monitor Pro"
VERSION = "2.0"

# ---------------------------------------------------------------------------
# Chemins : TOUJOURS relatifs au fichier, jamais au repertoire courant (fix #1)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "historique_reseau.log"

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_MACOS = SYSTEM == "Darwin"

# ---------------------------------------------------------------------------
# Source UNIQUE de verite pour la configuration par defaut (fix #6)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    "interval": 5,
    "threshold": 3,
    "timeout_ms": 1000,
    "max_workers": 10,
    "sound_alert": True,
    "popup_on_top": True,
    "switches": [
        {"name": "Switch Coeur (Baie Principale)", "ip": "192.168.1.1"},
        {"name": "Switch Bureaux R+1", "ip": "192.168.1.2"},
        {"name": "Switch Atelier", "ip": "192.168.1.254"},
    ],
}

# ---------------------------------------------------------------------------
# Journalisation avec rotation (fix #5)
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("network_monitor")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s",
                                            datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(_handler)


# ---------------------------------------------------------------------------
# Validation des cibles (fix #4) : bloque "-t", "-l 65500", chaines vides, etc.
# ---------------------------------------------------------------------------
HOSTNAME_RE = re.compile(
    r"^(?!-)[A-Za-z0-9_-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9_-]{1,63}(?<!-))*\.?$"
)


class InvalidTarget(ValueError):
    """Cible reseau refusee (syntaxe invalide ou tentative d'injection)."""


def validate_target(raw: str) -> str:
    """Retourne la cible nettoyee ou leve InvalidTarget."""
    target = (raw or "").strip()
    if not target:
        raise InvalidTarget("Adresse vide.")
    if len(target) > 253:
        raise InvalidTarget("Adresse trop longue.")
    if target.startswith("-"):
        raise InvalidTarget("Une adresse ne peut pas commencer par '-' "
                            "(option de commande interdite).")
    if any(c in target for c in ' \t"\'|&;$`<>()\n\r'):
        raise InvalidTarget("Caractere interdit dans l'adresse.")
    try:
        return str(ipaddress.ip_address(target))
    except ValueError:
        pass
    if HOSTNAME_RE.match(target):
        return target
    raise InvalidTarget(f"'{target}' n'est ni une adresse IP ni un nom d'hote valide.")


# ---------------------------------------------------------------------------
# Ping multiplateforme (fix #2, #7, #8)
# ---------------------------------------------------------------------------
# Capture la latence quelle que soit la langue : temps=1ms, time<1 ms, tiempo=2 ms,
# Zeit=3ms, tempo=4 ms... On cherche simplement "=<nombre> ms" ou "<<nombre> ms".
LATENCY_RE = re.compile(r"[=<]\s*([0-9]+(?:[.,][0-9]+)?)\s*ms", re.IGNORECASE)
UNREACHABLE_MARKERS = (
    "unreachable", "inaccessible", "unreachable", "nicht erreichbar",
    "inalcanzable", "irraggiungibile", "expire", "expired", "timed out",
    "depasse", "100% packet loss", "100% perte",
)


def _ping_command(host: str, timeout_ms: int) -> list[str]:
    if IS_WINDOWS:
        return ["ping", "-n", "1", "-w", str(timeout_ms), host]
    if IS_MACOS:
        # macOS : -W en millisecondes, -t en secondes (deadline global)
        secs = max(1, round(timeout_ms / 1000))
        return ["ping", "-n", "-c", "1", "-W", str(timeout_ms), "-t", str(secs), host]
    # Linux / BSD : -W en secondes
    secs = max(1, round(timeout_ms / 1000))
    return ["ping", "-n", "-c", "1", "-W", str(secs), host]


def _subprocess_kwargs() -> dict:
    kwargs: dict = {}
    if IS_WINDOWS:
        # Pas de fenetre console qui clignote
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # La console Windows parle OEM (cp850/cp437), pas UTF-8 (fix #7)
        kwargs["encoding"] = "oem" if sys.version_info >= (3, 6) else "cp850"
    else:
        kwargs["encoding"] = "utf-8"
    kwargs["errors"] = "replace"
    return kwargs


def ping_host(host: str, timeout_ms: int = 1000) -> tuple[bool, str]:
    """Ping unique. Retourne (joignable, latence_affichable)."""
    try:
        target = validate_target(host)
    except InvalidTarget as exc:
        logger.warning("Cible refusee (%s) : %s", host, exc)
        return False, "invalide"

    # Marge de securite : le timeout processus depasse legerement celui du ping
    hard_timeout = max(2.0, (timeout_ms / 1000.0) + 2.0)
    try:
        result = subprocess.run(
            _ping_command(target, timeout_ms),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=hard_timeout,
            **_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, "-"
    except (OSError, ValueError) as exc:
        logger.error("Echec d'execution du ping vers %s : %s", target, exc)
        return False, "-"

    output = (result.stdout or "") + (result.returncode and "" or "")
    upper = output.upper()
    lower = output.lower()

    # Sous Windows, ping peut renvoyer 0 avec "Destination inaccessible" :
    # on exige la presence de TTL= (garde-fou conserve de la v1).
    reachable = result.returncode == 0
    if reachable and IS_WINDOWS:
        reachable = "TTL=" in upper
    if reachable and any(m in lower for m in ("unreachable", "inaccessible")):
        reachable = False

    if not reachable:
        return False, "-"

    match = LATENCY_RE.search(output)
    if match:
        return True, f"{match.group(1).replace(',', '.')} ms"
    return True, "< 1 ms"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """Charge config.json, complete les cles manquantes, deduplique (fix #12)."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # copie profonde
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in config:
                    if key in data:
                        config[key] = data[key]
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("config.json illisible (%s) - valeurs par defaut utilisees.", exc)

    # Normalisation + deduplication des equipements
    clean: list[dict] = []
    seen: set[str] = set()
    for entry in config.get("switches") or []:
        if not isinstance(entry, dict):
            continue
        try:
            ip = validate_target(str(entry.get("ip", "")))
        except InvalidTarget as exc:
            logger.warning("Equipement ignore : %s", exc)
            continue
        if ip in seen:
            logger.warning("Doublon ignore pour l'adresse %s", ip)
            continue
        seen.add(ip)
        name = str(entry.get("name") or ip).strip() or ip
        clean.append({"name": name, "ip": ip})
    config["switches"] = clean

    config["interval"] = _clamp_int(config.get("interval"), 1, 3600, 5)
    config["threshold"] = _clamp_int(config.get("threshold"), 1, 20, 3)
    config["timeout_ms"] = _clamp_int(config.get("timeout_ms"), 200, 10000, 1000)
    config["max_workers"] = _clamp_int(config.get("max_workers"), 1, 64, 10)
    config["sound_alert"] = bool(config.get("sound_alert", True))
    config["popup_on_top"] = bool(config.get("popup_on_top", True))
    return config


def _clamp_int(value, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def save_config(config: dict) -> None:
    try:
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(config, indent=4, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_FILE)  # ecriture atomique
    except OSError as exc:
        logger.error("Impossible d'enregistrer config.json : %s", exc)


# ---------------------------------------------------------------------------
# Application graphique
# ---------------------------------------------------------------------------
class NetworkMonitorApp:
    COLUMNS = ("name", "ip", "status", "latency", "failures", "last_check")
    HEADINGS = {
        "name": "Nom de l'equipement",
        "ip": "Adresse IP / Hote",
        "status": "Statut",
        "latency": "Latence",
        "failures": "Echecs consecutifs",
        "last_check": "Dernier test",
    }

    def __init__(self, root) -> None:
        self.root = root
        self.config = load_config()
        self.switches: list[dict] = self.config["switches"]
        self.ping_interval: int = self.config["interval"]
        self.fail_threshold: int = self.config["threshold"]
        self.timeout_ms: int = self.config["timeout_ms"]

        self.is_running = False
        self.failure_counts: dict[str, int] = {}
        self.switch_status: dict[str, str] = {}

        # fix #3 : un seul cycle de test a la fois + etat protege
        self._cycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=self.config["max_workers"], thread_name_prefix="ping"
        )
        self._monitor_thread: threading.Thread | None = None

        self.root.title(f"{APP_NAME} v{VERSION}")
        self.root.geometry("920x640")
        self.root.minsize(780, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._setup_ui()
        self._populate_tree()
        self.log_message(f"{APP_NAME} v{VERSION} pret ({SYSTEM}). "
                         f"{len(self.switches)} equipement(s) charge(s).")

    # ---------------------------- Interface ----------------------------
    def _setup_ui(self) -> None:
        style = ttk.Style()
        self._fix_treeview_tag_colors(style)

        top = tk.LabelFrame(self.root, text="Controle et parametres", padx=10, pady=8)
        top.pack(fill="x", padx=10, pady=(8, 4))

        self.btn_start = tk.Button(top, text="\u25b6 Demarrer le monitoring",
                                   bg="#28a745", fg="white",
                                   font=("Arial", 10, "bold"), padx=10,
                                   command=self.start_monitoring)
        self.btn_start.pack(side="left", padx=4)

        self.btn_stop = tk.Button(top, text="\u23f9 Arreter", bg="#dc3545", fg="white",
                                  font=("Arial", 10, "bold"), padx=10,
                                  state="disabled", command=self.stop_monitoring)
        self.btn_stop.pack(side="left", padx=4)

        self.btn_test = tk.Button(top, text="\u26a1 Tester maintenant",
                                  command=self.test_now)
        self.btn_test.pack(side="left", padx=14)

        tk.Label(top, text="Intervalle (s) :").pack(side="left", padx=(16, 2))
        self.var_interval = tk.IntVar(value=self.ping_interval)
        self.spin_interval = ttk.Spinbox(top, from_=1, to=3600, width=6,
                                        textvariable=self.var_interval,
                                        command=self._apply_settings)
        self.spin_interval.pack(side="left")

        tk.Label(top, text="Alerte apres X echecs :").pack(side="left", padx=(14, 2))
        self.var_threshold = tk.IntVar(value=self.fail_threshold)
        self.spin_threshold = ttk.Spinbox(top, from_=1, to=20, width=5,
                                         textvariable=self.var_threshold,
                                         command=self._apply_settings)
        self.spin_threshold.pack(side="left")

        # fix #10 : prise en compte a chaud (frappe clavier + perte de focus)
        for widget in (self.spin_interval, self.spin_threshold):
            widget.bind("<KeyRelease>", lambda _e: self._apply_settings())
            widget.bind("<FocusOut>", lambda _e: self._apply_settings())

        self.var_sound = tk.BooleanVar(value=self.config["sound_alert"])
        tk.Checkbutton(top, text="Son", variable=self.var_sound,
                       command=self._apply_settings).pack(side="left", padx=(14, 0))

        manage = tk.Frame(self.root)
        manage.pack(fill="x", padx=10, pady=2)
        tk.Button(manage, text="\u2795 Ajouter un equipement",
                  command=self.add_switch).pack(side="left", padx=2)
        tk.Button(manage, text="\U0001f5d1 Supprimer la selection",
                  command=self.remove_switch).pack(side="left", padx=5)
        tk.Button(manage, text="\U0001f4c1 Ouvrir le dossier des logs",
                  command=self.open_logs).pack(side="right", padx=2)

        tree_frame = tk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.tree = ttk.Treeview(tree_frame, columns=self.COLUMNS,
                                 show="headings", height=12)
        widths = {"name": 210, "ip": 150, "status": 130, "latency": 90,
                  "failures": 120, "last_check": 130}
        anchors = {"name": "w"}
        for col in self.COLUMNS:
            self.tree.heading(col, text=self.HEADINGS[col])
            self.tree.column(col, width=widths[col], anchor=anchors.get(col, "center"))
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.tree.tag_configure("online", background="#d4edda", foreground="#155724")
        self.tree.tag_configure("warning", background="#fff3cd", foreground="#856404")
        self.tree.tag_configure("offline", background="#f8d7da", foreground="#721c24")
        self.tree.tag_configure("pending", background="#e2e3e5", foreground="#383d41")

        log_frame = tk.LabelFrame(self.root, text="Journal des evenements et alertes",
                                  padx=10, pady=5)
        log_frame.pack(fill="both", expand=False, padx=10, pady=(5, 8))
        self.log_text = tk.Text(log_frame, height=8, state="disabled", bg="#1e1e1e",
                                fg="#00ff66", insertbackground="#00ff66",
                                font=("Consolas" if IS_WINDOWS else "Monospace", 9))
        self.log_text.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Pret.")
        tk.Label(self.root, textvariable=self.status_var, anchor="w",
                 relief="sunken", bd=1).pack(fill="x", side="bottom")

    @staticmethod
    def _fix_treeview_tag_colors(style) -> None:
        """fix #9 : Tk >= 8.6.9 ignore les couleurs de tags du Treeview."""
        def fixed_map(option):
            return [elm for elm in style.map("Treeview", query_opt=option)
                    if elm[:2] != ("!disabled", "!selected")]
        try:
            style.map("Treeview",
                      foreground=fixed_map("foreground"),
                      background=fixed_map("background"))
        except tk.TclError:  # pragma: no cover
            pass

    def _populate_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        with self._state_lock:
            self.failure_counts.clear()
            self.switch_status.clear()
            for switch in self.switches:
                ip = switch["ip"]
                self.failure_counts[ip] = 0
                self.switch_status[ip] = "UNKNOWN"
        for switch in self.switches:
            ip = switch["ip"]
            if not self.tree.exists(ip):
                self.tree.insert("", "end", iid=ip,
                                 values=(switch["name"], ip, "EN ATTENTE", "-", "0", "-"),
                                 tags=("pending",))

    # ---------------------------- Parametres ----------------------------
    def _apply_settings(self) -> None:
        try:
            interval = _clamp_int(self.spin_interval.get(), 1, 3600, self.ping_interval)
            threshold = _clamp_int(self.spin_threshold.get(), 1, 20, self.fail_threshold)
        except tk.TclError:
            return
        changed = (interval != self.ping_interval or threshold != self.fail_threshold
                   or bool(self.var_sound.get()) != self.config["sound_alert"])
        self.ping_interval = interval
        self.fail_threshold = threshold
        self.config.update({
            "interval": interval,
            "threshold": threshold,
            "sound_alert": bool(self.var_sound.get()),
            "switches": self.switches,
        })
        if changed:
            save_config(self.config)
            self.status_var.set(f"Parametres appliques : intervalle {interval}s, "
                                f"seuil {threshold} echec(s).")

    # ---------------------------- Equipements ----------------------------
    def add_switch(self) -> None:
        name = simpledialog.askstring("Nouvel equipement",
                                      "Nom (ex : Switch R+2) :", parent=self.root)
        if not name:
            return
        raw_ip = simpledialog.askstring("Nouvel equipement",
                                        f"Adresse IP ou nom d'hote pour '{name}' :",
                                        parent=self.root)
        if not raw_ip:
            return
        try:
            ip = validate_target(raw_ip)
        except InvalidTarget as exc:
            messagebox.showerror("Adresse invalide", str(exc))
            return
        if any(s["ip"] == ip for s in self.switches):
            messagebox.showerror("Erreur", "Cette adresse est deja dans la liste.")
            return

        self.switches.append({"name": name.strip(), "ip": ip})
        with self._state_lock:
            self.failure_counts[ip] = 0
            self.switch_status[ip] = "UNKNOWN"
        self.tree.insert("", "end", iid=ip,
                         values=(name.strip(), ip, "EN ATTENTE", "-", "0", "-"),
                         tags=("pending",))
        self._apply_settings()
        save_config(self.config)
        self.log_message(f"Ajout de l'equipement : {name.strip()} ({ip})")

    def remove_switch(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Selectionnez au moins une ligne a supprimer.")
            return
        for ip in selected:
            self.switches[:] = [s for s in self.switches if s["ip"] != ip]
            if self.tree.exists(ip):
                self.tree.delete(ip)
            with self._state_lock:  # fix #11 : pas de fuite d'etat
                self.failure_counts.pop(ip, None)
                self.switch_status.pop(ip, None)
        self.config["switches"] = self.switches
        save_config(self.config)
        self.log_message(f"{len(selected)} equipement(s) supprime(s).")

    def open_logs(self) -> None:
        try:
            if IS_WINDOWS:
                subprocess.Popen(["explorer", str(LOG_DIR)])
            elif IS_MACOS:
                subprocess.Popen(["open", str(LOG_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(LOG_DIR)])
        except OSError:
            messagebox.showinfo("Journaux", f"Fichier de log :\n{LOG_FILE}")

    # ---------------------------- Journal ----------------------------
    def log_message(self, message: str, is_alert: bool = False) -> None:
        """Toujours appele depuis le thread principal Tk."""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        (logger.warning if is_alert else logger.info)(message)
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self.status_var.set(message)
        if is_alert:
            self._raise_alert()

    def _raise_alert(self) -> None:
        if self.config.get("sound_alert", True):
            if winsound is not None:
                threading.Thread(target=lambda: winsound.Beep(1200, 600),
                                 daemon=True).start()
            else:
                try:
                    self.root.bell()  # equivalent portable
                except tk.TclError:
                    pass
        if self.config.get("popup_on_top", True):
            try:
                self.root.attributes("-topmost", True)
                self.root.after(2500,
                                lambda: self.root.attributes("-topmost", False))
            except tk.TclError:
                pass

    # ---------------------------- Monitoring ----------------------------
    def start_monitoring(self) -> None:
        self._apply_settings()
        if not self.switches:
            messagebox.showwarning("Attention", "Aucun equipement a surveiller.")
            return
        if self.is_running:
            return
        self.is_running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.log_message("=== DEMARRAGE DU MONITORING RESEAU ===")
        self._monitor_thread = threading.Thread(target=self._monitor_loop,
                                                name="monitor", daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.log_message("=== ARRET DU MONITORING ===")

    def test_now(self) -> None:
        if self._cycle_lock.locked():
            self.status_var.set("Un cycle de test est deja en cours...")
            return
        threading.Thread(target=self._run_cycle, name="manual-cycle",
                         daemon=True).start()

    def _monitor_loop(self) -> None:
        while self.is_running:
            self._run_cycle()
            # sommeil fractionne : arret reactif
            for _ in range(max(1, self.ping_interval) * 10):
                if not self.is_running:
                    return
                time.sleep(0.1)

    def _run_cycle(self) -> None:
        """fix #3 : verrou non bloquant -> jamais deux cycles simultanes."""
        if not self._cycle_lock.acquire(blocking=False):
            return
        try:
            targets = [dict(s) for s in self.switches]
            if not targets:
                return
            timeout = self.timeout_ms
            results = list(self._executor.map(
                lambda s: (s, *ping_host(s["ip"], timeout)), targets))
            for switch, is_up, latency in results:
                self._process_result(switch, is_up, latency)
        except Exception as exc:  # ne jamais tuer le thread de monitoring
            logger.exception("Erreur pendant le cycle de test : %s", exc)
        finally:
            self._cycle_lock.release()

    def _process_result(self, switch: dict, is_up: bool, latency: str) -> None:
        ip, name = switch["ip"], switch["name"]
        now = datetime.now().strftime("%H:%M:%S")
        threshold = self.fail_threshold

        with self._state_lock:
            if ip not in self.failure_counts:
                return  # equipement supprime entre-temps
            previous = self.switch_status.get(ip, "UNKNOWN")
            if is_up:
                self.failure_counts[ip] = 0
                self.switch_status[ip] = "ONLINE"
                fails, new_status = 0, "ONLINE"
            else:
                self.failure_counts[ip] += 1
                fails = self.failure_counts[ip]
                new_status = "OFFLINE" if fails >= threshold else "UNSTABLE"
                self.switch_status[ip] = new_status

        if is_up:
            self._ui(self._update_row, ip, name, "\U0001f7e2 EN LIGNE", latency, 0, now, "online")
            if previous == "OFFLINE":
                self._ui(self.log_message,
                         f"RETABLI : {name} ({ip}) repond de nouveau ({latency}).")
        elif new_status == "OFFLINE":
            self._ui(self._update_row, ip, name, "\U0001f534 HORS LIGNE", "-", fails, now, "offline")
            if previous != "OFFLINE":
                self._ui(self.log_message,
                         f"ALERTE COUPURE : {name} ({ip}) ne repond plus "
                         f"apres {fails} essai(s) !", True)
        else:
            self._ui(self._update_row, ip, name, "\U0001f7e0 INSTABLE", "-", fails, now, "warning")

    def _ui(self, func, *args) -> None:
        """Marshalling systematique vers le thread Tk."""
        try:
            self.root.after(0, func, *args)
        except RuntimeError:  # pragma: no cover - fenetre detruite
            pass

    def _update_row(self, ip, name, status, latency, fails, last_check, tag) -> None:
        if self.tree.exists(ip):
            self.tree.item(ip, values=(name, ip, status, latency, fails, last_check),
                           tags=(tag,))

    # ---------------------------- Fermeture ----------------------------
    def on_close(self) -> None:
        self.is_running = False
        self._apply_settings()
        save_config(self.config)
        self._executor.shutdown(wait=False)
        logger.info("Fermeture de l'application.")
        self.root.destroy()


# ---------------------------------------------------------------------------
# Autotest console (aucun affichage requis)
# ---------------------------------------------------------------------------
def selftest() -> int:
    print(f"== Autotest {APP_NAME} v{VERSION} ==")
    print(f"Plateforme      : {SYSTEM} / Python {platform.python_version()}")
    print(f"Dossier de base : {BASE_DIR}")
    print(f"Tkinter present : {'oui' if TK_AVAILABLE else 'NON'}")
    failures = 0

    print("\n-- Validation des cibles --")
    for value, should_pass in [("192.168.1.1", True), ("8.8.8.8", True),
                               ("::1", True), ("switch-core.lan", True),
                               ("-t", False), ("-l 65500", False),
                               ("1.2.3.4; rm -rf /", False), ("", False),
                               ("$(whoami)", False)]:
        try:
            validate_target(value)
            ok = should_pass
        except InvalidTarget:
            ok = not should_pass
        failures += 0 if ok else 1
        print(f"  [{'OK ' if ok else 'KO '}] {value!r:24} attendu={'accepte' if should_pass else 'refuse'}")

    print("\n-- Configuration --")
    cfg = load_config()
    ok = all(k in cfg for k in DEFAULT_CONFIG)
    failures += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'KO '}] cles: {sorted(cfg)}")
    print(f"  interval={cfg['interval']} threshold={cfg['threshold']} "
          f"timeout_ms={cfg['timeout_ms']} workers={cfg['max_workers']} "
          f"equipements={len(cfg['switches'])}")

    print("\n-- Ping reel --")
    for host in ("127.0.0.1", "192.0.2.1"):  # 192.0.2.0/24 = reseau de test, injoignable
        start = time.perf_counter()
        up, latency = ping_host(host, cfg["timeout_ms"])
        elapsed = time.perf_counter() - start
        print(f"  {host:12} joignable={up!s:5} latence={latency:9} "
              f"({elapsed:.2f}s)")
    ok, _ = ping_host("127.0.0.1", cfg["timeout_ms"])
    failures += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'KO '}] la boucle locale doit repondre")

    print("\n-- Journalisation --")
    logger.info("Autotest : ecriture de journal.")
    for handler in logger.handlers:
        handler.flush()
    ok = LOG_FILE.exists() and LOG_FILE.stat().st_size > 0
    failures += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'KO '}] {LOG_FILE} "
          f"({LOG_FILE.stat().st_size if LOG_FILE.exists() else 0} octets, rotation 5 Mo x 3)")

    print(f"\nResultat : {'TOUS LES TESTS PASSENT' if failures == 0 else str(failures) + ' ECHEC(S)'}")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} v{VERSION}")
    parser.add_argument("--selftest", action="store_true",
                        help="lance les tests internes en console (sans interface)")
    parser.add_argument("--init-config", action="store_true",
                        help="ecrit un config.json par defaut puis quitte")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} v{VERSION}")
    args = parser.parse_args(argv)

    if args.init_config:
        save_config(load_config())
        print(f"Configuration ecrite : {CONFIG_FILE}")
        return 0
    if args.selftest:
        return selftest()

    if not TK_AVAILABLE:
        print("ERREUR : Tkinter n'est pas disponible dans cet interpreteur Python.\n"
              "  - Windows/macOS : reinstallez Python depuis python.org "
              "(Tkinter est inclus).\n"
              "  - Debian/Ubuntu : sudo apt install python3-tk\n"
              "  - Fedora        : sudo dnf install python3-tkinter", file=sys.stderr)
        return 2

    root = tk.Tk()
    app = NetworkMonitorApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":  # fix #12 : plus d'effet de bord a l'import
    sys.exit(main())
