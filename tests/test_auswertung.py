from src.analytics import Analytics
from src.database import BewerbungsDB
from src.gap_check import GapCheck


def test_passung_aus_gap():
    gap = {
        "gefordert": ["A", "B", "C", "D"],
        "bekannt": ["A", "B", "C"],
        "teilweise": ["D"],
    }
    assert GapCheck.passung(gap) == 88  # (3 + 0,5) / 4 – "teilweise" zählt halb
    assert GapCheck.passung({"gefordert": [], "bekannt": [], "teilweise": []}) == 50


def test_report_zeigt_erfolg_nach_passung(config):
    db = BewerbungsDB(config.db_path)
    db.add_rueckmeldung(db.insert_bewerbung("A GmbH", agent_score=90), "einladung")
    db.add_rueckmeldung(db.insert_bewerbung("B GmbH", agent_score=30), "absage")

    bericht = Analytics(config).bericht()

    assert "hoch (ab 75 %): 1 Einladung(en), 0 Absage(n)" in bericht
    assert "niedrig (unter 50 %): 0 Einladung(en), 1 Absage(n)" in bericht
