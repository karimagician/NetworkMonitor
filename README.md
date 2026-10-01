# Supervision Réseau — Switch Monitor Pro v2.0

Superviseur d'équipements réseau (switchs, routeurs, points d'accès, serveurs) basé
sur le **ping système**. Aucune dépendance à installer, aucun droit administrateur.

---

## 🚀 Démarrage en un clic

| Système | Quoi faire |
|---|---|
| **Windows** | Double-cliquez sur **`lancer.bat`** |
| **macOS** | Double-cliquez sur **`lancer.command`** (la 1re fois : clic droit ▸ Ouvrir) |
| **Linux** | Double-cliquez sur **`lancer.sh`** (ou `./lancer.sh` dans un terminal) |

Seul prérequis : **Python 3.8 ou plus**, disponible sur [python.org/downloads](https://www.python.org/downloads/).
À l'installation sous Windows, cochez **« Add python.exe to PATH »**.
Si Python est absent, le lanceur affiche un message clair avec la procédure à suivre.

> Sous Windows le lanceur utilise `pythonw.exe` en priorité : **aucune fenêtre noire**
> ne reste ouverte derrière l'application.

### Vérifier que tout fonctionne (optionnel)

```bash
python network_monitor.py --selftest
```

L'autotest contrôle la validation des adresses, la configuration, un ping réel sur
`127.0.0.1` et l'écriture du journal — sans ouvrir l'interface.

---

## 🖥️ Utilisation

1. **▶ Démarrer le monitoring** lance la surveillance en continu.
2. **⚡ Tester maintenant** force un cycle immédiat.
3. **➕ Ajouter un équipement** demande un nom puis une IP / un nom d'hôte.
4. **Intervalle** et **seuil d'alerte** sont pris en compte **à chaud**, sans redémarrage.

### États affichés

| État | Signification |
|---|---|
| 🟢 EN LIGNE | L'équipement répond, la latence est affichée |
| 🟠 INSTABLE | 1 à (seuil − 1) échecs consécutifs — pas encore d'alerte |
| 🔴 HORS LIGNE | Seuil d'échecs atteint → alerte sonore + fenêtre au premier plan |

Le système anti-faux-positifs n'alerte qu'après **X pings échoués consécutifs**
(paramétrable), ce qui évite les fausses alarmes sur une micro-coupure.

---

## 📁 Contenu du projet

```
NetworkMonitor/
├── network_monitor.py   # application complète (une seule source)
├── config.json          # configuration (créée/mise à jour automatiquement)
├── lancer.bat           # lanceur Windows
├── lancer.command       # lanceur macOS
├── lancer.sh            # lanceur Linux
├── README.md            # ce fichier
├── CHANGELOG.md         # détail des 14 correctifs de la v2
└── logs/
    └── historique_reseau.log   # journal (rotation 5 Mo × 3)
```

## ⚙️ Configuration (`config.json`)

```json
{
    "interval": 5,          // secondes entre deux cycles (1–3600)
    "threshold": 3,         // échecs consécutifs avant alerte (1–20)
    "timeout_ms": 1000,     // délai d'attente d'un ping (200–10000)
    "max_workers": 10,      // pings menés en parallèle (1–64)
    "sound_alert": true,
    "popup_on_top": true,
    "switches": [
        { "name": "Switch Coeur (Baie Principale)", "ip": "192.168.1.1" }
    ]
}
```

Le fichier est écrit de façon **atomique** ; s'il est corrompu ou incomplet, les
valeurs par défaut sont réappliquées automatiquement et les doublons d'adresses
sont ignorés au chargement.

---

## 🔒 Sécurité

Toute adresse saisie est validée (`ipaddress` ou syntaxe de nom d'hôte) **avant**
d'atteindre la commande `ping`. Les entrées commençant par `-` (`-t`, `-l 65500`)
et les caractères de shell (`; | & $ \` < >`) sont refusés : ni injection de shell,
ni injection d'arguments possible.

## 📈 Capacité

Les pings sont exécutés **en parallèle** (10 threads par défaut) : un parc de
50 équipements est testé en ~2 s au lieu de ~50 s en séquentiel. Augmentez
`max_workers` pour de plus grands parcs.

---

## 🔧 Dépannage

| Symptôme | Solution |
|---|---|
| « Python n'a pas été trouvé » | Installez Python 3 en cochant « Add to PATH », puis relancez |
| « Tkinter n'est pas disponible » | Debian/Ubuntu : `sudo apt install python3-tk` · Fedora : `sudo dnf install python3-tkinter` |
| Tout apparaît HORS LIGNE | Le pare-feu ou le réseau bloque l'ICMP ; augmentez `timeout_ms` |
| Latence toujours « < 1 ms » | Normal en réseau local (ping inférieur à la milliseconde) |
| macOS refuse d'ouvrir le lanceur | Clic droit sur `lancer.command` ▸ **Ouvrir** (une seule fois) |

## 📦 Créer un .exe autonome (optionnel)

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name SwitchMonitor network_monitor.py
```

Copiez ensuite `config.json` et le dossier `logs/` à côté de l'exécutable produit.
