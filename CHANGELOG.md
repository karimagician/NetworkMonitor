# Journal des modifications

## v2.0 — Toutes les recommandations de l'audit appliquées

### Corrections bloquantes / fonctionnelles

1. **Chemins relatifs corrigés.** `config.json` et `logs/` sont désormais ancrés sur
   `Path(__file__).resolve().parent` et non plus sur le répertoire courant. Les
   lanceurs font en plus un `cd` explicite (`cd /d "%~dp0"` sous Windows, avec
   repli `pushd` pour les chemins réseau UNC). Les équipements ne « disparaissent »
   plus quand l'application est lancée depuis un raccourci.
2. **Multiplateforme.** `import winsound` est protégé par un `try/except` et
   remplacé par `root.bell()` hors Windows. La commande ping est adaptée :
   `-n 1 -w <ms>` (Windows), `-c 1 -W <ms> -t <s>` (macOS), `-c 1 -W <s>` (Linux).
3. **Dossier `logs/` livré dans le ZIP** grâce à `logs/.gitkeep` (un répertoire vide
   n'est jamais ajouté par `zipfile.write`).
4. **Race condition supprimée.** « Tester maintenant » et la boucle de monitoring
   partagent un `threading.Lock` non bloquant : jamais deux cycles simultanés, donc
   plus de double incrémentation du compteur d'échecs. L'état partagé est protégé
   par un second verrou.
5. **Pings parallèles.** `ThreadPoolExecutor` (10 workers par défaut, configurable) :
   le temps de cycle ne croît plus linéairement avec le nombre d'équipements.
6. **Intervalle et seuil à chaud.** Les `Spinbox` sont reliés à des `IntVar` avec
   `command` + `<KeyRelease>` + `<FocusOut>` ; toute modification est appliquée et
   sauvegardée immédiatement, sans redémarrage du monitoring.
7. **Collision d'`iid` du Treeview corrigée.** Les adresses en doublon sont éliminées
   au chargement de la configuration et `tree.exists()` est vérifié avant insertion :
   plus de `TclError` au démarrage sur un `config.json` édité à la main.
8. **Décodage robuste de la sortie ping.** Codec `oem` sous Windows (cp850/cp437) et
   `errors="replace"` : plus de `UnicodeDecodeError` sur les messages accentués.
9. **Latence indépendante de la langue.** La regex `[=<]\s*([0-9.,]+)\s*ms` fonctionne
   avec `temps=`, `time=`, `tiempo=`, `Zeit=`, `tempo=`… La valeur `< 1 ms` n'est plus
   renvoyée à tort sur un Windows non francophone.
10. **Plus de fuite d'état.** `remove_switch` purge `failure_counts` et `switch_status`.
11. **Journalisation avec rotation.** `logging` + `RotatingFileHandler`
    (5 Mo × 3 fichiers) remplace l'écriture manuelle non bornée.
12. **Aucun effet de bord à l'import.** Tout est encapsulé dans `main()` sous
    `if __name__ == "__main__"`, l'`input()` bloquant a été supprimé, et un timeout dur
    est appliqué au sous-processus ping.

### Sécurité

13. **Validation stricte des cibles.** `validate_target()` accepte uniquement une IPv4/IPv6
    valide (`ipaddress`) ou un nom d'hôte conforme. Sont refusés : chaîne vide, adresse
    commençant par `-` (injection d'arguments type `-t` ou `-l 65500`), caractères de shell
    (`; | & $ \` < > ' "`), longueur supérieure à 253.
14. **Configuration à source unique.** Le dictionnaire `DEFAULT_CONFIG` est l'unique
    référence ; `config.json` en est dérivé, avec bornage de chaque valeur numérique et
    écriture atomique (fichier temporaire puis `replace`).

### Améliorations d'interface

- Correctif du bug Tk ≥ 8.6.9 qui ignorait les couleurs de tags du `Treeview` (`fixed_map`).
- Barre de statut, état « EN ATTENTE » distinct, bouton « Ouvrir le dossier des logs ».
- Case à cocher pour désactiver l'alerte sonore.
- Fermeture propre : arrêt du thread, extinction de l'`executor`, sauvegarde de la config.
- Mode `--selftest` (validation, config, ping réel, journal) et `--init-config`,
  fonctionnels même sans Tkinter installé.
