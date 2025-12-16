# Documentation

## Generating PDFs

Install [pandoc](https://pandoc.org/installing.html) and a LaTeX distribution (e.g., MacTeX, TeX Live), then run:

```bash
cd docs
pandoc ./map_viewer.md -o ./map_viewer.pdf --pdf-engine=xelatex
pandoc ./spot-controller.md -o ./spot-controller.pdf --pdf-engine=xelatex
pandoc ./telegram-bot.md -o ./telegram-bot.pdf --pdf-engine=xelatex
```
