from typing import List

from src.dialog import Dialog
from src.kompetenzen import KompetenzenDB

GUELTIGE_LEVEL = ["ja", "teilweise", "nein", "lernend"]
KURZFORMEN = {"j": "ja", "t": "teilweise", "n": "nein", "l": "lernend"}


class KompetenzFrage:
    def __init__(self, db: KompetenzenDB):
        self.db = db

    def frage_nach(self, skills: List[str], dialog: Dialog):
        """Fragt nach unbekannten Skills und merkt sich die Antworten."""
        if not skills:
            return

        dialog.zeige(
            "\n" + "=" * 60 + "\n"
            f"❓ {len(skills)} neue Kompetenz(en) gefunden – bitte beantworte:\n"
            + "=" * 60
            + "\nOptionen: ja | teilweise | nein | lernend  (leer = nein)\n"
        )

        for skill in skills:
            while True:
                antwort = dialog.frage(f"   {skill}: ", optionen=GUELTIGE_LEVEL)
                if antwort is None:
                    return  # Abbruch: Rest bleibt unbekannt und wird später gefragt
                antwort = antwort.lower() or "nein"
                antwort = KURZFORMEN.get(antwort, antwort)

                if antwort in GUELTIGE_LEVEL:
                    kontext = ""
                    if antwort in ("teilweise", "ja"):
                        kontext = dialog.frage(
                            "      Kontext (optional, leer oder - = überspringen): "
                        )
                        kontext = "" if kontext in (None, "-") else kontext
                    self.db.add_kompetenz(
                        skill, antwort, quelle="selbstauskunft", kontext=kontext
                    )
                    dialog.zeige(f"      ✅ Gespeichert: {skill} = {antwort}\n")
                    break
                dialog.zeige(
                    "      ⚠️ Ungültige Eingabe. Bitte ja/teilweise/nein/lernend eingeben."
                )
