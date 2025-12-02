# iLab Projekt Gipfeli
Dieses repository ist Teil des **Projekt Gipfeli** vom **iLab Kanti Glarus**. Mehr Infos zum iLab unter 
[iLab Kanti Glarus](https://www.kanti-glarus.ch).

Unsere Vision ist, einen Gipfeli-Delivery Service innerhalb unseres Schulhauses zu haben. Über die Telegram-App sollen Lernende direkt ihre Snacks nach Wahl bestellen und bezahlen können. 
Unser Roboterhund SPOT geht dann in die Mensa und kauft die Produkte, welcher er anschliessend zum gewünschten Schulzimmer bringt.  

> Modern educational project to develop an autonomous delivery service using an autonomous robot dog.

[![Status](https://img.shields.io/badge/status-ALPHA-orange)](#) [![License](https://img.shields.io/badge/license-GPLv3-blue)](COPYING) [![Build](https://img.shields.io/badge/build-passing-brightgreen)](#)

---

## Table of Contents
<!-- - [About](#about)  
- [Features](#features)  
- [Quick Start](#quick-start)  
- [Configuration](#configuration)  
- [Usage](#usage)  
- [Development](#development)  
- [Contributing](#contributing)   -->
- [Setup](#setup)
- [License](#license)  
- [Contact](#contact)

---
<!-- 
## About
Short overview and goals.  
Placeholder: expand with motivation, audience, and scope.

## Features
- Minimal, modern README template
- Placeholder-driven configuration
- Clear quick-start instructions

## Quick Start
Clone, install, run:
```bash
git clone <repo-url> && cd <repo-directory>
# install
<package-manager> install
# run
<package-manager> run start
```

## Configuration
Use environment variables or a config file. Example .env placeholders:
```
APP_NAME="MyApp"
API_URL="https://api.example.com"
API_KEY="__REPLACE_ME__"
```

## Usage
Provide short examples or command snippets:
```bash
# basic usage
<package-manager> run serve --port 3000

# example API call
curl -H "Authorization: Bearer $API_KEY" "$API_URL/endpoint"
```

## Development
- Code style: Placeholder (e.g., Prettier, Black)
- Tests: Placeholder (e.g., Jest, pytest)
- Recommended workflow:
    1. Create a feature branch
    2. Commit with clear messages
    3. Open a PR and request review -->

<!-- ## Contributing
Please read CONTRIBUTING.md (placeholder). Use issues and PR templates. Keep changes small and documented.
 -->
## Setup

### 1. Install uv
```bash
pip install uv
```

Oder folge der offiziellen Anleitung: https://docs.astral.sh/uv/getting-started/installation/

### 2. Virtuelle Umgebung erstellen und aktivieren

```bash
# im Projekt-Root
uv venv .venv

# aktivieren unter Linux/macOS
source .venv/bin/activate

# aktivieren unter Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
```

### 3. Abhängigkeiten installieren 

Dieses Projekt verwendet eine `pyproject.toml`. Installiere alle Abhängigkeiten mit:

```bash
uv sync
```

### 4. Projekt starten

```bash
# Example TODO
uv run python -m gipfeli
```

### 5. Notes on using the spot-sdk examples
Remove the following line from `view_map.py` to enable mouse controls. 
*At least on MacBook Air M4.*

```python
renderWindow.Start()
```
 

## License
Dieses Projekt steht unter der GNU General Public License Version 3 (GPLv3).  
Der vollständige Lizenztext befindet sich in der Datei `COPYING`.  

## Contact
Maintainer: Christopher Golling, cgolling@ethz.ch
Repository:

