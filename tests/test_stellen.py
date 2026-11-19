import json

from src.database import BewerbungsDB
from src.kriterien import Kriterien, huerden, quereinstieg_hinweise, remote_anteil
from src.stellen import StellenDB, StellenSuche
from src.stellen_ba import BAJobsuche, Fund
from src.vorschlaege import Vorschlaege
from tests.conftest import FakeDialog

# ohne erkannte Skills ist die Passung 50 – so bleiben die Punkte vorhersagbar
FUELLTEXT = " Wir sind ein freundliches Team und freuen uns auf dich." * 5
TEXT_QUEREINSTIEG = "Quereinsteiger willkommen, gründliche Einarbeitung." + FUELLTEXT
TEXT_HUERDE = "Du bringst mehrjährige Berufserfahrung mit." + FUELLTEXT


def fund(ref, titel="IT-Support (m/w/d)", firma="Muster GmbH", km=10, **kwargs):
    return Fund(
        quelle="ba",
        ref=ref,
        titel=titel,
        firma=firma,
        ort="Musterstadt",
        km=km,
        link=f"https://example.org/{ref}",
        **kwargs,
    )


class FakeBA:
    def __init__(self, funde, texte, fehler_bei=()):
        self.funde, self.texte, self.fehler_bei = funde, texte, fehler_bei
        self.anfragen = []
        self.gelesen = []

    def suche(self, begriff, plz, umkreis, tage):
        self.anfragen.append((begriff, plz, umkreis, tage))
        if begriff in self.fehler_bei:
            raise ValueError("kaputt")
        return self.funde

    def anzeigentext(self, f):
        self.gelesen.append(f.ref)
        return self.texte.get(f.ref, "")


def kriterien(**kwargs):
    werte = dict(plz="12345", suchbegriffe=["IT-Support"], km_max=30)
    werte.update(kwargs)
    return Kriterien(**werte)


# ---------- KRITERIEN ----------
def test_kriterien_aus_datei_unbekanntes_wird_ignoriert(config):
    assert Kriterien.laden(config) is None
    (config.data_dir / "kriterien.json").write_text(
        json.dumps({"plz": "12345", "km_max": 20, "kommentar": "egal"})
    )
    k = Kriterien.laden(config)
    assert k.plz == "12345" and k.km_max == 20 and "Senior" in k.titel_nicht


def test_ort_und_remote_haengen_zusammen():
    k = kriterien(km_max=50, km_max_mit_remote=100, remote_ab_prozent=60)
    assert k.ort_passt(40, None)[0]
    assert not k.ort_passt(80, 40)[0]  # zu weit für so wenig Homeoffice
    assert k.ort_passt(80, 60)[0]
    assert not k.ort_passt(300, 80)[0]
    assert k.ort_passt(300, 100) == (True, "100 % remote")
    assert k.ort_passt(None, None) == (False, "Ort unbekannt")


def test_remote_anteil_aus_text():
    assert remote_anteil("Bis zu 60 % Homeoffice möglich") == 60
    assert remote_anteil("3 Tage pro Woche im Homeoffice") == 60
    assert remote_anteil("Die Stelle ist full remote.") == 100
    assert remote_anteil("Arbeitsort: Berlin, Büro mit Kantine") is None


def test_quereinstieg_und_huerden():
    assert quereinstieg_hinweise("Ideal für Berufseinsteiger, Junior-Rolle") == [
        "Berufseinstieg",
        "Junior",
    ]
    assert huerden("Mindestens 3 Jahre Berufserfahrung") == ["mehrere Jahre Erfahrung"]
    assert huerden("abgeschlossenes Studium oder vergleichbare Ausbildung") == []
    assert huerden("ein abgeschlossenes Studium der Informatik.") == [
        "Studium verlangt"
    ]


def test_titel_wort_nur_als_ganzes_wort():
    k = kriterien()
    assert k.titel_ausgeschlossen("Senior System Engineer") == "Senior"
    assert k.titel_ausgeschlossen("IT-Support Leitstelle") is None  # nicht „Leit“
    assert k.titel_ausgeschlossen("Teamleiter IT") == "Leiter"
    assert k.titel_ausgeschlossen("Lead Developer") == "Lead"
    assert (
        k.titel_ausgeschlossen("Leadgenerierung Support") is None
    )  # kurz → ganzes Wort


# ---------- SUCHE ----------
def test_lauf_sortiert_vor_und_bewertet(config):
    BewerbungsDB(config.db_path).insert_bewerbung("Alt AG", position="IT-Support")
    funde = [
        fund("gut", quereinstieg=True),
        fund("huerde", firma="Hürden GmbH", km=20),  # nicht nah
        fund("senior", titel="Senior Admin (m/w/d)"),
        fund("weit", km=80),
        fund("weit-remote", firma="Fern GmbH", km=80, homeoffice=True, remote=80),
        fund("mini", minijob=True),
        fund("schon", firma="Alt AG"),
    ]
    texte = {
        "gut": TEXT_QUEREINSTIEG,
        "huerde": TEXT_HUERDE,
        "weit-remote": TEXT_QUEREINSTIEG,
    }
    ba = FakeBA(funde, texte)
    zahlen = StellenSuche(config, kriterien(), ba, melde=print).lauf()

    assert zahlen == {"neu": 7, "vorgeschlagen": 2, "verworfen": 5}
    assert sorted(ba.gelesen) == ["gut", "huerde", "weit-remote"]
    db = StellenDB(config.db_path)
    gruende = {s["ref"]: s["gruende"] for s in db._alle()}
    assert gruende["senior"] == "Titel enthält „Senior“"
    assert gruende["mini"] == "Minijob" and gruende["schon"] == "schon beworben"
    assert "80 km" in gruende["weit"]
    assert "⚠️ mehrjährige Erfahrung" in gruende["huerde"]
    vorschlaege = db.vorschlaege(10)
    assert [s["ref"] for s in vorschlaege] == ["gut", "weit-remote"]
    assert "laut Firma für Quereinsteiger geeignet" in vorschlaege[0]["gruende"]
    assert ba.anfragen == [("IT-Support", "12345", 100, 28)]  # erster Lauf

    # zweiter Lauf: nichts wird doppelt geprüft, Suche nur noch eine Woche zurück
    zahlen = StellenSuche(config, kriterien(), ba).lauf()
    assert zahlen["neu"] == 0 and ba.anfragen[-1][3] == 7


def test_unlesbare_anzeige_kommt_beim_naechsten_lauf_wieder(config):
    ba = FakeBA([fund("leer")], {"leer": "zu kurz"})
    StellenSuche(config, kriterien(), ba).lauf()
    assert StellenDB(config.db_path).ist_leer()
    ba.texte["leer"] = TEXT_QUEREINSTIEG
    assert StellenSuche(config, kriterien(), ba).lauf()["vorgeschlagen"] == 1


def test_gleiche_stelle_unter_neuer_nummer_und_fehler_beim_suchen(config):
    meldungen = []
    ba = FakeBA(
        [fund("1"), fund("2", firma="Muster")],  # Rechtsform egal
        {"1": TEXT_QUEREINSTIEG, "2": TEXT_QUEREINSTIEG},
        fehler_bei=["Helpdesk"],
    )
    k = kriterien(suchbegriffe=["Helpdesk", "IT-Support"])
    StellenSuche(config, k, ba, melde=meldungen.append).lauf()
    assert "Helpdesk" in meldungen[0]
    ba.funde = [fund("3")]
    StellenSuche(config, k, ba, melde=meldungen.append).lauf()
    alle = {s["ref"]: s for s in StellenDB(config.db_path)._alle()}
    # schon im selben Lauf erkannt, nicht erst beim nächsten
    assert alle["2"]["gruende"] == "doppelt (andere Nummer)"
    assert alle["3"]["gruende"] == "doppelt (andere Nummer)"


def test_ba_suche_liest_eckdaten():
    antwort = {
        "ergebnisliste": [
            {
                "referenznummer": "10000-123-S",
                "stellenangebotsTitel": "IT-Support (m/w/d)",
                "firma": "Muster GmbH",
                "stellenlokationen": [{"adresse": {"ort": "Musterstadt"}}],
                "entfernung": "12",  # kommt von der BA als Text
                "homeofficeprozent": "60",
                "homeofficemoeglich": True,
                "quereinstiegGeeignet": True,
            }
        ]
    }
    parameter = {}
    ba = BAJobsuche(hole=lambda **p: parameter.update(p) or antwort)
    [f] = ba.suche("IT-Support", "12345", 50, 7)
    assert parameter["was"] == "IT-Support" and parameter["angebotsart"] == 1
    assert (
        f.km == 12
        and f.remote == 60
        and f.homeoffice
        and f.quereinstieg
        and not f.minijob
    )
    assert f.link.endswith("/jobdetail/10000-123-S")
    assert BAJobsuche(hole=lambda **p: {}).suche("x", "1", 1, 1) == []


# ---------- VORSCHLÄGE ----------
class FakeAgent:
    client = None

    def __init__(self, bewerbung_id):
        self.bewerbung_id = bewerbung_id
        self.aufrufe = []

    def bewerben(self, text, firma, position, email, link=None):
        self.aufrufe.append((firma, position, email, link))
        return self.bewerbung_id


def vorschlaege_mit(config, dialog, agent=None):
    (config.data_dir / "kriterien.json").write_text(json.dumps({"plz": "12345"}))
    funde = [fund("a", quereinstieg=True), fund("b", firma="Zweite GmbH")]
    texte = {"a": TEXT_QUEREINSTIEG, "b": TEXT_QUEREINSTIEG}
    suche = StellenSuche(config, kriterien(), FakeBA(funde, texte))
    return Vorschlaege(config, dialog, suche=suche, agent=agent)


def test_vorschlaege_bewerben_und_ignorieren(config):
    agent = FakeAgent(bewerbung_id=7)
    # a: bewerben → Firma, Position, E-Mail übernehmen, Portal; b: ignorieren
    dialog = FakeDialog("b", "", "", "", "i")
    assert vorschlaege_mit(config, dialog, agent).pruefe() == 1

    assert agent.aufrufe == [
        ("Muster GmbH", "IT-Support", None, "https://example.org/a")
    ]
    status = {s["ref"]: s for s in StellenDB(config.db_path)._alle()}
    assert status["a"]["status"] == "beworben" and status["a"]["bewerbung_id"] == 7
    assert status["b"]["status"] == "ignoriert"
    assert "2 Stellen-Vorschläge" in dialog.text


def test_vorschlaege_spaeter_und_abbruch(config):
    dialog = FakeDialog("s", "x")
    assert vorschlaege_mit(config, dialog, FakeAgent(None)).pruefe() == 0
    db = StellenDB(config.db_path)
    assert db.anzahl_vorschlaege() == 2  # beide kommen wieder

    agent = FakeAgent(None)  # Bewerbung abgebrochen
    vorschlaege_mit(config, FakeDialog("b", "", "", "", None), agent).pruefe()
    assert db._alle("WHERE status = 'abgebrochen'")[0]["ref"] == "a"


def test_ohne_kriterien_still_oder_mit_hinweis(config):
    dialog = FakeDialog()
    assert Vorschlaege(config, dialog).pruefe() == 0
    assert dialog.ausgaben == []
    Vorschlaege(config, dialog).pruefe(automatisch=False)
    assert "kriterien.json" in dialog.text
