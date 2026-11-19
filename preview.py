#!/usr/bin/env python
"""Preview: Erstellt Lebenslauf + Anschreiben OHNE Versand, Nachfragen und DB-Eintrag."""

import sys

from src.main import BewerbungsAgent, frage_stellendaten, ordnername
from src.stellen_info import erkenne_stelle
from src.stellen_link import lies_anzeige


def preview(stelle_file: str, unternehmen: str = "", position: str = ""):
    agent = BewerbungsAgent()
    stellen_text = lies_anzeige(stelle_file)
    if not (unternehmen and position):
        info = frage_stellendaten(
            erkenne_stelle(stellen_text, agent.client), mit_email=False
        )
        if not info:
            sys.exit(1)
        unternehmen, position = info.unternehmen, info.position

    print(f"\n🚀 Preview für {position} bei {unternehmen}\n")
    ordner = agent.config.preview_ordner / ordnername(unternehmen)
    unterlagen = agent.erstelle_unterlagen(stellen_text, unternehmen, position, ordner)
    # <base>: damit das Foto (relativer Pfad) auch im Browser gefunden wird
    basis = agent.config.html_template.parent.resolve().as_uri() + "/"
    html_vorschau = unterlagen["resume"]["html"].replace(
        "<head>", f'<head>\n<base href="{basis}">', 1
    )
    (ordner / "Lebenslauf.html").write_text(html_vorschau, encoding="utf-8")

    print(f"\n🎯 Berufsrichtung: {unterlagen['resume']['berufsrichtung']}")
    print(
        f"🧩 Projekte: {', '.join(p['name'] for p in unterlagen['resume']['projekte'])}"
    )
    print(f"\n✅ Preview gespeichert in: {ordner}")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 4):
        print(
            "Nutzung: python preview.py <stellenanzeige.txt|link> [<unternehmen> <position>]"
        )
        sys.exit(1)
    preview(*sys.argv[1:])
