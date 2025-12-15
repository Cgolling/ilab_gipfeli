# iLab Projekt Gipfeli

[![Status](https://img.shields.io/badge/status-ALPHA-orange)](#) [![License](https://img.shields.io/badge/license-GPLv3-blue)](COPYING)

Dieses Repository ist Teil des **Projekt Gipfeli** vom **iLab Kanti Glarus**.
Mehr Infos zum iLab unter [iLab Kanti Glarus](https://www.kanti-glarus.ch).

---

## Über das Projekt

Unsere Vision ist ein **Gipfeli-Delivery Service** innerhalb unseres Schulhauses. Über die Telegram-App können Lernende ihre Snacks bestellen. Unser Roboterhund **SPOT** (von Boston Dynamics) holt die Produkte in der Mensa und liefert sie vor das gewünschte Schulzimmer.

**Was dieses Projekt macht:**
- Telegram-Bot für Bestellungen
- Steuerung des SPOT-Roboters via GraphNav
- Navigation zu vordefinierten Standorten (Aula, Turnhalle, etc.)

---

## Inhaltsverzeichnis

- [Voraussetzungen](#voraussetzungen)
- [Schnellstart](#schnellstart)
- [Detaillierte Einrichtung](#detaillierte-einrichtung)
  - [Was ist UV?](#was-ist-uv)
  - [Repository klonen](#repository-klonen)
  - [UV installieren](#uv-installieren)
  - [Projekt einrichten](#projekt-einrichten)
  - [Umgebungsvariablen konfigurieren](#umgebungsvariablen-konfigurieren)
  - [Telegram Bot erstellen](#telegram-bot-erstellen)
  - [Bot starten](#bot-starten)
- [Projektstruktur](#projektstruktur)
- [Bot-Befehle](#bot-befehle)
- [Troubleshooting](#troubleshooting)
- [Entwicklung](#entwicklung)
- [Lizenz](#lizenz)
- [Kontakt](#kontakt)

---

## Voraussetzungen

Bevor du startest, stelle sicher dass du folgendes hast:

- **Python 3.13** oder neuer
- **Git** installiert
- **Telegram Account** (für den Bot)
- (Optional) Zugang zum **SPOT Roboter** und dessen Credentials

---

## Schnellstart

Für Ungeduldige - hier die Kurzversion:

```bash
# 1. Repository klonen (mit Submodule!)
git clone --recurse-submodules https://github.com/DEIN-USERNAME/ilab_gipfeli.git
cd ilab_gipfeli

# 2. UV installieren (falls noch nicht vorhanden)
pip install uv

# 3. Virtuelle Umgebung erstellen und Dependencies installieren
uv venv .venv
source .venv/bin/activate   # Linux/macOS
# .\.venv\Scripts\Activate.ps1   # Windows PowerShell
uv sync

# 4. Umgebungsvariablen konfigurieren
cp .env.example .env
# Dann .env bearbeiten und Werte eintragen

# 5. Bot starten
uv run python -m src.telegram.bot
```

> **Hinweis:** Falls etwas nicht klappt, lies die [detaillierte Einrichtung](#detaillierte-einrichtung) unten.

---

## Detaillierte Einrichtung

### Was ist UV?

**UV** ist ein moderner Python Package Manager - eine schnellere Alternative zu `pip`.

Vorteile von UV:
- **Schneller**: Installiert Pakete deutlich schneller als pip
- **Zuverlässiger**: Bessere Auflösung von Dependencies
- **Einfacher**: Kombiniert `pip`, `venv` und `pip-tools` in einem Tool

Du kannst dir UV wie einen "besseren pip" vorstellen.

### Repository klonen

Dieses Projekt verwendet ein **Git Submodule** für das Boston Dynamics SPOT SDK. Das bedeutet, es gibt ein Git-Repository innerhalb unseres Repositories.

**Wichtig:** Verwende `--recurse-submodules` beim Klonen!

```bash
git clone --recurse-submodules https://github.com/DEIN-USERNAME/ilab_gipfeli.git
cd ilab_gipfeli
```

Falls du das Repository bereits ohne `--recurse-submodules` geklont hast:

```bash
cd ilab_gipfeli
git submodule init
git submodule update
```

> **Was ist ein Git Submodule?**
> Ein Submodule ist ein Verweis auf ein anderes Git-Repository. In unserem Fall verweisen wir auf das offizielle [spot-sdk](https://github.com/boston-dynamics/spot-sdk) von Boston Dynamics. So bleiben wir immer auf dem neuesten Stand, ohne den Code zu kopieren.

### UV installieren

**Option 1: Via pip (empfohlen für Anfänger)**
```bash
pip install uv
```

**Option 2: Standalone Installation**

Für macOS/Linux:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Für Windows (PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Mehr Infos: [UV Dokumentation](https://docs.astral.sh/uv/getting-started/installation/)

### Projekt einrichten

**1. Virtuelle Umgebung erstellen:**

```bash
uv venv .venv
```

> **Was ist eine virtuelle Umgebung?**
> Eine isolierte Python-Installation nur für dieses Projekt. So vermeiden wir Konflikte mit anderen Python-Projekten auf deinem Computer.

**2. Virtuelle Umgebung aktivieren:**

Linux/macOS:
```bash
source .venv/bin/activate
```

Windows (PowerShell):
```powershell
.\.venv\Scripts\Activate.ps1
```

Windows (CMD):
```cmd
.\.venv\Scripts\activate.bat
```

Du erkennst eine aktive Umgebung am `(.venv)` vor deinem Terminal-Prompt.

**3. Dependencies installieren:**

```bash
uv sync
```

Dieser Befehl liest die `pyproject.toml` und installiert alle benötigten Pakete.

### Umgebungsvariablen konfigurieren

Das Projekt benötigt einige geheime Werte (Passwörter, Tokens). Diese speichern wir in einer `.env` Datei, die **niemals** in Git committet wird.

**1. Vorlage kopieren:**
```bash
cp .env.example .env
```

**2. `.env` Datei bearbeiten:**

Öffne `.env` in einem Texteditor und fülle die Werte aus:

```env
# SPOT Roboter Credentials (vom iLab Team)
BOSDYN_CLIENT_USERNAME=dein_username
BOSDYN_CLIENT_PASSWORD=dein_password
SPOT_HOSTNAME=192.168.80.3

# Telegram Bot Token (siehe nächster Abschnitt)
TELEGRAM_BOT_TOKEN=dein_telegram_token
```

### Telegram Bot erstellen

Um den Telegram-Bot zu nutzen, brauchst du einen **Bot Token** von Telegram.

**Schritt-für-Schritt Anleitung:**

1. Öffne Telegram und suche nach **@BotFather**
2. Starte einen Chat mit BotFather
3. Sende den Befehl `/newbot`
4. Wähle einen **Namen** für deinen Bot (z.B. "Gipfeli Test Bot")
5. Wähle einen **Username** (muss auf `bot` enden, z.B. `gipfeli_test_bot`)
6. BotFather gibt dir einen **Token** - kopiere diesen!
7. Füge den Token in deine `.env` Datei ein:
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   ```

> **Wichtig:** Teile deinen Bot-Token niemals öffentlich! Jeder mit dem Token kann deinen Bot steuern.

### Bot starten

Wenn alles eingerichtet ist:

```bash
uv run python -m src.telegram.bot
```

Du solltest sehen:
```
INFO - Application started
```

Öffne nun Telegram, suche deinen Bot und sende `/start`!

---

## Projektstruktur

```
ilab_gipfeli/
├── src/                      # Quellcode
│   ├── spot/                 # SPOT Roboter Steuerung
│   │   └── spot_controller.py
│   └── telegram/             # Telegram Bot
│       └── bot.py
├── spot-sdk/                 # Boston Dynamics SDK (Git Submodule)
├── maps/                     # Navigationskarten für SPOT
│   └── map_catacombs_01/
├── .env.example              # Vorlage für Umgebungsvariablen
├── .env                      # Deine lokalen Secrets (nicht in Git!)
├── pyproject.toml            # Projekt-Konfiguration und Dependencies
├── uv.lock                   # Gesperrte Dependency-Versionen
└── README.md                 # Diese Datei
```

---

## Bot-Befehle

| Befehl | Beschreibung |
|--------|--------------|
| `/start` | Begrüssung und Übersicht |
| `/help` | Liste aller Befehle |
| `/connect` | Verbindung zu SPOT herstellen |
| `/disconnect` | Verbindung trennen und Lease freigeben |
| `/forceconnect` | Lease erzwingen (falls blockiert) |
| `/status` | Roboter-Status anzeigen (Batterie, etc.) |
| `/goto` | SPOT zu einem Standort navigieren |

**Verfügbare Standorte für `/goto`:**
- Aula
- Triangle
- Hauswart
- Turnhalle

---

## Troubleshooting

### "Command not found: uv"
UV ist nicht installiert oder nicht im PATH. Versuche:
```bash
pip install uv
```

### "No module named 'src'"
Die virtuelle Umgebung ist nicht aktiviert. Führe aus:
```bash
source .venv/bin/activate  # Linux/macOS
```

### "TELEGRAM_BOT_TOKEN not set"
Die `.env` Datei fehlt oder ist nicht korrekt. Prüfe:
1. Existiert `.env` im Projektordner?
2. Ist `TELEGRAM_BOT_TOKEN=...` darin gesetzt?

### "spot-sdk Ordner ist leer"
Git Submodule wurden nicht initialisiert:
```bash
git submodule init
git submodule update
```

### SPOT SDK view_map.py Problem auf Mac
Falls du die SPOT SDK Beispiele nutzt und `view_map.py` auf einem Mac nicht funktioniert, entferne folgende Zeile:
```python
renderWindow.Start()
```

### "Lease already claimed"
Der SPOT Roboter wird bereits von einem anderen Gerät (z.B. Tablet) gesteuert. Lösungen:
1. Verwende `/forceconnect` um die Lease zu übernehmen
2. Oder trenne die andere Verbindung zuerst (z.B. am Tablet)

---

## Entwicklung

### Code-Style

Wir verwenden Python-Standards:
- **Formatierung**: Halte dich an PEP 8
- **Docstrings**: Für alle öffentlichen Funktionen
- **Type Hints**: Wo sinnvoll

### Änderungen beitragen

1. Erstelle einen neuen Branch: `git checkout -b feature/mein-feature`
2. Mache deine Änderungen
3. Committe mit aussagekräftiger Nachricht
4. Erstelle einen Pull Request

---

## Lizenz

Dieses Projekt steht unter der **GNU General Public License Version 3 (GPLv3)**.
Der vollständige Lizenztext befindet sich in der Datei [COPYING](COPYING).

---

## Kontakt

**Maintainer:** Christopher Golling
**E-Mail:** cgolling@ethz.ch
**Repository:** [GitHub](https://github.com/DEIN-USERNAME/ilab_gipfeli)
