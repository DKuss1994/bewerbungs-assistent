import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional


@contextmanager
def verbinde(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Verbindung zur SQLite-Datei: committet bei Erfolg, rollt bei Fehler zurück
    und schließt sie immer. (`with sqlite3.connect()` allein schließt nicht –
    bei vielen Abfragen gehen sonst die Dateihandles aus.)"""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# Spalten, die nachträglich zu "bewerbungen" hinzugekommen sind (Migration)
NEUE_SPALTEN = {
    "berufsrichtung": "TEXT",
    "projekte": "TEXT",  # Komma-Liste der Projekt-IDs aus projekte.json
    "skills_gefordert": "TEXT",
    "empfaenger": "TEXT",
    "kanal": "TEXT",  # "email" oder "portal"
    "nachgefasst": "TEXT",  # Datum der Nachfass-Mail oder "nein"
    "telefon": "TEXT",
    "ansprechpartner": "TEXT",
    "link": "TEXT",  # Link zur Anzeige, falls von einer Webseite geholt
    "anruf": "TEXT",  # Datum des Anrufs oder "nein"
    "anruf_erinnert": "TEXT",  # Datum der letzten Anruf-Erinnerung
    "notiz": "TEXT",  # z.B. Ergebnis des Anrufs
}

# Rechtsform-Zusätze, die beim Firmen-Abgleich ignoriert werden
RECHTSFORMEN = r"\b(gmbh|ag|kg|se|ug|mbh|co|inc|ltd|e\.?v)\b"


def normalisiere_firma(name: str) -> str:
    name = re.sub(RECHTSFORMEN, " ", name.lower())
    return re.sub(r"[^a-z0-9äöüß]+", " ", name).strip()


def _normalisiere(text: Optional[str]) -> str:
    return " ".join((text or "").lower().split())


class BewerbungsDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_tables()

    def _init_tables(self):
        with verbinde(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bewerbungen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    datum_gesendet TEXT NOT NULL,
                    unternehmen TEXT NOT NULL,
                    branche TEXT,
                    position TEXT,
                    tech_stack TEXT,
                    wochentag_gesendet TEXT,
                    monat_gesendet INTEGER,
                    agent_self_score INTEGER,
                    status TEXT DEFAULT 'gesendet',
                    is_reapply INTEGER DEFAULT 0,
                    previous_id INTEGER NULL,
                    cooldown_until TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rueckmeldungen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bewerbung_id INTEGER NOT NULL,
                    datum_rueckmeldung TEXT NOT NULL,
                    typ TEXT NOT NULL,
                    dauer_tage INTEGER,
                    grund_kategorie TEXT,
                    hr_feedback_score INTEGER,
                    hr_professionalitaet INTEGER,
                    FOREIGN KEY(bewerbung_id) REFERENCES bewerbungen(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skills_timeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    datum_erlernt TEXT NOT NULL,
                    level TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lern_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kategorie TEXT NOT NULL,
                    ausloeser TEXT,
                    aenderung TEXT NOT NULL,
                    aktiv INTEGER DEFAULT 1,
                    erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP,
                    angewendet_bei INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS zustand (
                    schluessel TEXT PRIMARY KEY,
                    wert TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS verarbeitete_mails (
                    message_id TEXT PRIMARY KEY,
                    bewerbung_id INTEGER,
                    datum TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            vorhandene = {r[1] for r in conn.execute("PRAGMA table_info(bewerbungen)")}
            for spalte, typ in NEUE_SPALTEN.items():
                if spalte not in vorhandene:
                    conn.execute(f"ALTER TABLE bewerbungen ADD COLUMN {spalte} {typ}")
            conn.commit()

    def insert_bewerbung(
        self,
        unternehmen: str,
        branche: str = "Unbekannt",
        position: str = "",
        tech_stack: str = "",
        agent_score: int = 50,
        berufsrichtung: str = "",
        projekte: Optional[List[str]] = None,
        skills_gefordert: Optional[List[str]] = None,
        empfaenger: str = "",
        kanal: str = "email",
        previous_id: Optional[int] = None,
        telefon: str = "",
        ansprechpartner: str = "",
        link: str = "",
    ) -> int:
        """previous_id: frühere Bewerbung bei derselben Firma (Re-Bewerbung)."""
        now = datetime.now()
        with verbinde(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO bewerbungen (
                    datum_gesendet, unternehmen, branche, position, tech_stack,
                    wochentag_gesendet, monat_gesendet, agent_self_score, 
                    is_reapply, previous_id, cooldown_until,
                    berufsrichtung, projekte, skills_gefordert, empfaenger, kanal,
                    telefon, ansprechpartner, link
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    now.isoformat(),
                    unternehmen,
                    branche,
                    position,
                    tech_stack,
                    now.strftime("%A"),
                    now.month,
                    agent_score,
                    int(previous_id is not None),
                    previous_id,
                    (now + timedelta(days=180)).isoformat(),
                    berufsrichtung,
                    ",".join(projekte or []),
                    ", ".join(skills_gefordert or []),
                    empfaenger,
                    kanal,
                    telefon,
                    ansprechpartner,
                    link,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_all_bewerbungen(self) -> List[Dict]:
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM bewerbungen ORDER BY id DESC")
            return [dict(row) for row in cursor.fetchall()]

    def update_status(self, bewerbung_id: int, neuer_status: str):
        with verbinde(self.db_path) as conn:
            conn.execute(
                "UPDATE bewerbungen SET status = ? WHERE id = ?",
                (neuer_status, bewerbung_id),
            )
            conn.commit()

    def hat_doppelte_bewerbung(
        self, unternehmen: str, position: str, tage: int = 30
    ) -> bool:
        """Gleiche Firma + Position in den letzten `tage` Tagen? Rechtsform,
        Groß-/Kleinschreibung und Leerzeichen spielen keine Rolle."""
        grenze = (datetime.now() - timedelta(days=tage)).isoformat()
        firma, stelle = normalisiere_firma(unternehmen), _normalisiere(position)
        return any(
            normalisiere_firma(b["unternehmen"]) == firma
            and _normalisiere(b["position"]) == stelle
            and b["datum_gesendet"] > grenze
            for b in self.get_all_bewerbungen()
        )

    def letzte_bewerbung_bei(self, unternehmen: str) -> Optional[Dict]:
        """Neueste Bewerbung bei dieser Firma (Rechtsform egal), sonst None."""
        firma = normalisiere_firma(unternehmen)
        for b in self.get_all_bewerbungen():  # neueste zuerst
            if firma and normalisiere_firma(b["unternehmen"]) == firma:
                return b
        return None

    def finde_bewerbung(self, *texte: str) -> Optional[Dict]:
        """Findet die neueste Bewerbung, deren Firmenname in einem der Texte steht
        (z.B. Absender, Betreff, Mailtext)."""
        gesamt = normalisiere_firma(" ".join(texte))
        for b in self.get_all_bewerbungen():
            firma = normalisiere_firma(b["unternehmen"])
            if firma and re.search(rf"\b{re.escape(firma)}\b", gesamt):
                return b
        return None

    def add_rueckmeldung(
        self, bewerbung_id: int, typ: str, grund: Optional[str] = None
    ) -> None:
        """Speichert eine Rückmeldung (absage/einladung) und setzt den Status."""
        now = datetime.now()
        with verbinde(self.db_path) as conn:
            row = conn.execute(
                "SELECT datum_gesendet FROM bewerbungen WHERE id = ?", (bewerbung_id,)
            ).fetchone()
            dauer = (now - datetime.fromisoformat(row[0])).days if row else None
            conn.execute(
                """
                INSERT INTO rueckmeldungen
                    (bewerbung_id, datum_rueckmeldung, typ, dauer_tage, grund_kategorie)
                VALUES (?, ?, ?, ?, ?)
            """,
                (bewerbung_id, now.isoformat(), typ, dauer, grund),
            )
            conn.execute(
                "UPDATE bewerbungen SET status = ? WHERE id = ?", (typ, bewerbung_id)
            )
            conn.commit()

    def mail_verarbeitet(self, message_id: str) -> bool:
        with verbinde(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM verarbeitete_mails WHERE message_id = ?", (message_id,)
            ).fetchone()
            return row is not None

    def markiere_mail(self, message_id: str, bewerbung_id: Optional[int]) -> None:
        with verbinde(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO verarbeitete_mails (message_id, bewerbung_id) "
                "VALUES (?, ?)",
                (message_id, bewerbung_id),
            )
            conn.commit()

    # ---------- NACHFASSEN ----------
    def nachfass_kandidaten(self, tage: int) -> List[Dict]:
        """Per E-Mail gesendet, seit `tage` Tagen keine Antwort, noch nicht nachgefasst."""
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM bewerbungen
                WHERE status = 'gesendet' AND kanal = 'email'
                AND COALESCE(empfaenger, '') != '' AND nachgefasst IS NULL
                AND datum_gesendet <= ?
                ORDER BY datum_gesendet
            """,
                ((datetime.now() - timedelta(days=tage)).isoformat(),),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- ANRUFEN ----------
    def anruf_kandidaten(
        self, nach_mail_tage: int, ohne_mail_tage: int, erneut_tage: int
    ) -> List[Dict]:
        """Noch keine Antwort und noch nicht angerufen: `nach_mail_tage` nach der
        Nachfass-Mail bzw. – ohne Adresse (Portal) – `ohne_mail_tage` nach dem
        Bewerben. Wer bei "später" war, kommt nach `erneut_tage` wieder."""
        jetzt = datetime.now()
        with verbinde(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM bewerbungen
                WHERE status = 'gesendet' AND anruf IS NULL
                AND (anruf_erinnert IS NULL OR anruf_erinnert <= ?)
                AND (
                    (nachgefasst LIKE '____-__-__' AND nachgefasst <= ?)
                    OR (COALESCE(empfaenger, '') = '' AND datum_gesendet <= ?)
                )
                ORDER BY datum_gesendet
            """,
                (
                    (jetzt - timedelta(days=erneut_tage)).date().isoformat(),
                    (jetzt - timedelta(days=nach_mail_tage)).date().isoformat(),
                    (jetzt - timedelta(days=ohne_mail_tage)).isoformat(),
                ),
            ).fetchall()
            return [dict(r) for r in rows]

    def schliesse_ohne_antwort(self, tage: int, ruhe_tage: int) -> List[Dict]:
        """Setzt Bewerbungen auf "geghosted", die seit `tage` Tagen ohne Antwort
        sind und bei denen seit `ruhe_tage` nichts mehr passiert ist (Nachfass-
        Mail, Anruf). Kommt später doch eine Antwort, setzt das Postfach den
        Status wieder. Rückgabe: die geschlossenen Bewerbungen."""
        jetzt = datetime.now()
        ruhe = (jetzt - timedelta(days=ruhe_tage)).date().isoformat()
        geschlossen = []
        for b in self.get_all_bewerbungen():
            if (
                b["status"] != "gesendet"
                or b["datum_gesendet"] > (jetzt - timedelta(days=tage)).isoformat()
            ):
                continue
            aktivitaeten = [b["datum_gesendet"][:10]] + [
                w for w in (b["nachgefasst"], b["anruf"]) if w and w[0].isdigit()
            ]
            if max(aktivitaeten) <= ruhe:
                self.update_status(b["id"], "geghosted")
                geschlossen.append(b)
        return geschlossen

    def setze_feld(self, bewerbung_id: int, spalte: str, wert: str) -> None:
        """Setzt eine der nachgerüsteten Spalten (z.B. anruf, notiz)."""
        if spalte not in NEUE_SPALTEN:
            raise ValueError(f"Unbekannte Spalte: {spalte}")
        with verbinde(self.db_path) as conn:
            conn.execute(
                f"UPDATE bewerbungen SET {spalte} = ? WHERE id = ?",
                (wert, bewerbung_id),
            )
            conn.commit()

    def markiere_nachgefasst(self, bewerbung_id: int, wert: str) -> None:
        with verbinde(self.db_path) as conn:
            conn.execute(
                "UPDATE bewerbungen SET nachgefasst = ? WHERE id = ?",
                (wert, bewerbung_id),
            )
            conn.commit()

    # ---------- ZUSTAND (z.B. wann zuletzt das Postfach geprüft wurde) ----------
    def lies_zustand(self, schluessel: str) -> Optional[str]:
        with verbinde(self.db_path) as conn:
            row = conn.execute(
                "SELECT wert FROM zustand WHERE schluessel = ?", (schluessel,)
            ).fetchone()
            return row[0] if row else None

    def setze_zustand(self, schluessel: str, wert: str) -> None:
        with verbinde(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO zustand (schluessel, wert) VALUES (?, ?)",
                (schluessel, wert),
            )
            conn.commit()
