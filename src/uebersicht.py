"""Übersicht über alle laufenden Bewerbungen: was ist fällig, was wartet noch."""

from datetime import datetime
from typing import Dict, List, Optional

from src.anrufen import ERNEUT_TAGE, NACH_MAIL_TAGE
from src.config import AppConfig
from src.database import BewerbungsDB
from src.nachfassen import NACHFASS_TAGE

OHNE_ANTWORT_TAGE = 42  # danach gilt eine Bewerbung ohne Antwort als erledigt …
RUHE_TAGE = 14  # … wenn so lange nichts mehr passiert ist (Nachfassen, Anruf)


def schliesse_ohne_antwort(config: AppConfig) -> Optional[str]:
    """Räumt eingeschlafene Bewerbungen ab. Rückgabe: Meldung oder None."""
    db = BewerbungsDB(config.db_path)
    geschlossen = db.schliesse_ohne_antwort(OHNE_ANTWORT_TAGE, RUHE_TAGE)
    if not geschlossen:
        return None
    return f"👻 Seit über {OHNE_ANTWORT_TAGE} Tagen ohne Antwort – abgeschlossen:\n" + (
        "\n".join(f"   • {b['unternehmen']} – {b['position']}" for b in geschlossen)
    )


def _zeile(b: Dict) -> str:
    tage = (datetime.now() - datetime.fromisoformat(b["datum_gesendet"])).days
    infos = [f"vor {tage} Tagen", "Portal" if not b["empfaenger"] else "Mail"]
    if b["telefon"]:
        infos.append(f"☎️ {b['telefon']}")
    zeile = f"   • {b['unternehmen']} – {b['position']} ({', '.join(infos)})"
    return zeile + (f"\n     📝 {b['notiz']}" if b["notiz"] else "")


def uebersicht(config: AppConfig) -> str:
    db = BewerbungsDB(config.db_path)
    alle = db.get_all_bewerbungen()
    anrufen = db.anruf_kandidaten(NACH_MAIL_TAGE, NACHFASS_TAGE, ERNEUT_TAGE)
    nachfassen = db.nachfass_kandidaten(NACHFASS_TAGE)
    faellig = {b["id"] for b in anrufen + nachfassen}
    gruppen: List[tuple] = [
        ("🎉 Einladungen", [b for b in alle if b["status"] == "einladung"]),
        ("📞 Anrufen fällig", anrufen),
        ("📧 Nachfassen fällig", nachfassen),
        (
            "⏳ Wartet auf Antwort",
            [b for b in alle if b["status"] == "gesendet" and b["id"] not in faellig],
        ),
    ]

    zeilen = ["📋 Deine Bewerbungen"]
    for titel, bewerbungen in gruppen:
        if bewerbungen:
            zeilen.append(f"\n{titel} ({len(bewerbungen)})")
            zeilen += [_zeile(b) for b in bewerbungen]
    absagen = sum(b["status"] == "absage" for b in alle)
    if absagen:
        zeilen.append(f"\n📭 Absagen bisher: {absagen}")
    geghosted = sum(b["status"] == "geghosted" for b in alle)
    if geghosted:
        zeilen.append(f"👻 Ohne Antwort abgeschlossen: {geghosted}")
    if len(zeilen) == 1:
        zeilen.append("\nNoch keine Bewerbungen gespeichert.")
    elif faellig:
        zeilen.append(
            "\n👉 Fälliges erledigen: /postfach (Bot) bzw. nachfassen/anrufen"
        )
    return "\n".join(zeilen)


STATUS_SYMBOL = {
    "gesendet": "⏳",
    "einladung": "🎉",
    "absage": "📭",
    "geghosted": "👻",
}


def alle_bewerbungen(config: AppConfig) -> str:
    """Komplette Liste aller Bewerbungen, neueste zuerst."""
    alle = BewerbungsDB(config.db_path).get_all_bewerbungen()
    if not alle:
        return "📚 Noch keine Bewerbungen gespeichert."
    zeilen = [f"📚 Alle Bewerbungen ({len(alle)})"]
    for b in alle:
        datum = datetime.fromisoformat(b["datum_gesendet"]).strftime("%d.%m.%y")
        symbol = STATUS_SYMBOL.get(b["status"], "•")
        kanal = "Portal" if not b["empfaenger"] else "Mail"
        zeilen.append(
            f"{symbol} {datum}  {b['unternehmen']} – {b['position'] or '?'}"
            f" ({b['status']}, {kanal})"
        )
        if b["notiz"]:
            zeilen.append(f"     📝 {b['notiz']}")
    zeilen.append("\n" + "  ".join(f"{s} {n}" for n, s in STATUS_SYMBOL.items()))
    return "\n".join(zeilen)
