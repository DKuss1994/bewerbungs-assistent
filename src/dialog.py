"""Wie der Agent mit dir spricht: im Terminal – später auch per Chat-Bot.

Der Ablauf (main.py, kompetenz_frage.py) fragt nur über einen Dialog,
nie direkt per input()/print(). Ein Chat-Bot muss dieselben drei Methoden
anbieten, dann läuft die ganze Bewerbung auch über ihn."""

import subprocess
from pathlib import Path
from typing import List, Optional


class Dialog:
    def zeige(self, text: str) -> None:
        """Meldung an dich."""
        raise NotImplementedError

    def frage(self, text: str, optionen: Optional[List[str]] = None) -> Optional[str]:
        """Frage an dich. "" = übernehmen/überspringen, None = Abbruch.
        optionen: erlaubte Antworten (ein Bot kann daraus Knöpfe machen)."""
        raise NotImplementedError

    def zeige_dateien(self, pfade: List[Path]) -> None:
        """PDFs (und Ordner) zum Ansehen öffnen bzw. schicken."""
        raise NotImplementedError


class TerminalDialog(Dialog):
    def zeige(self, text: str) -> None:
        print(text)

    def frage(self, text: str, optionen: Optional[List[str]] = None) -> Optional[str]:
        try:
            return input(text).strip()
        except EOFError:
            return None

    def zeige_dateien(self, pfade: List[Path]) -> None:
        for pfad in pfade:
            subprocess.Popen(
                ["xdg-open", str(pfad)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
