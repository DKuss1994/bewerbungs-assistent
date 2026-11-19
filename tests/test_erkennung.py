from src.berufsrichtung import Berufsrichtung
from src.gap_check import GapCheck
from src.kompetenzen import KompetenzenDB, finde_skills, kommt_vor


def test_kurze_keywords_nur_als_ganzes_wort():
    assert not kommt_vor("git", "digitalisierung von prozessen")
    assert kommt_vor("git", "erfahrung mit git und maven")
    assert not kommt_vor("js", "daten im json-format")
    assert kommt_vor("java", "java-entwickler (m/w/d)")
    assert not kommt_vor("java", "javascript-kenntnisse")


def test_lange_keywords_auch_in_zusammengesetzten_woertern():
    assert kommt_vor("linux", "linux-kenntnisse")
    assert kommt_vor("monitoring", "monitoringsysteme")


def test_finde_skills():
    skills = finde_skills("Wir suchen Java, Spring Boot und Docker. Digitalisierung!")
    assert {"Java", "Spring Boot", "Docker"} <= set(skills)
    assert "Git" not in skills


def test_berufsrichtung():
    br = Berufsrichtung()
    assert (
        br.erkenne("1st Level Support, Helpdesk, Ticketsystem, Windows Server")
        == "itsupport"
    )
    assert (
        br.erkenne("Backend Developer Java Spring Boot REST API")
        == "softwareentwicklung"
    )
    assert br.erkenne("Kaufmann im Einzelhandel") == "softwareentwicklung"


def test_gap_check(config):
    db = KompetenzenDB(config.db_path)
    db.add_kompetenz("Java", "ja", "test")
    db.add_kompetenz("Docker", "lernend", "test")
    db.add_kompetenz("AWS", "nein", "test")

    gap = GapCheck(db).finde_luecken("Java, Docker, AWS und Kubernetes")
    assert gap["bekannt"] == ["Java"]
    assert gap["teilweise"] == ["Docker"]
    assert gap["unbekannt"] == ["Kubernetes"]  # AWS = nein → nicht erneut fragen
