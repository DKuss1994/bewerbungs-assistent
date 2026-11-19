"""Lebenslauf-Generator – deterministisch, nur echte Daten, lernt aus Rückmeldungen."""

import base64
import io
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from src.berufsrichtung import Berufsrichtung
from src.config import AppConfig
from src.erfolg import ErfolgsTracker
from src.kompetenzen import KompetenzenDB, finde_skills, kommt_vor

MAX_PROJEKTE = 3
MIN_PROJEKTE = 2
MAX_SKILLS_PRO_KATEGORIE = 6


MAX_BILD_PIXEL = 1000  # reicht für ein Passfoto in 300 dpi


def bilder_fuer_pdf(html: str, basis: Path) -> str:
    """Bettet lokale Fotos als verkleinertes Baseline-JPEG ein.

    WeasyPrint übernimmt JPEGs unverändert – ein großes, progressives Foto
    (z.B. direkt vom Handy) zeigen manche PDF-Viewer und Bewerberportale
    dann nicht an."""
    try:
        from PIL import Image
    except ImportError:
        return html

    def ersetze(m: re.Match) -> str:
        pfad = basis / m.group(2)
        if not pfad.is_file():
            return m.group(0)
        try:
            with Image.open(pfad) as bild:
                if bild.format not in ("JPEG", "PNG"):
                    return m.group(0)
                bild = bild.convert("RGB")
                bild.thumbnail((MAX_BILD_PIXEL, MAX_BILD_PIXEL))
                puffer = io.BytesIO()
                bild.save(puffer, "JPEG", quality=90, progressive=False)
        except OSError:
            return m.group(0)
        daten = base64.b64encode(puffer.getvalue()).decode("ascii")
        return f"{m.group(1)}data:image/jpeg;base64,{daten}{m.group(3)}"

    # Nur relative Pfade (keine http:, data: usw.)
    return re.sub(r'(<img[^>]*?\ssrc=")(?![a-z]+:)([^"]+)(")', ersetze, html)


def skill_name(eintrag) -> str:
    return eintrag["name"] if isinstance(eintrag, dict) else eintrag


def skill_keywords(eintrag) -> List[str]:
    """Keywords eines Profil-Skills: explizit angegeben oder das erste Wort
    ("Java 17+" → "java", "Scrum-Grundlagen" → "scrum")."""
    if isinstance(eintrag, dict):
        return eintrag.get("keywords", [])
    erstes_wort = re.split(r"[\s\-(]", eintrag.strip())[0]
    return [erstes_wort.lower().rstrip("+")]


class ResumeBuilder:
    def __init__(self, config: AppConfig):
        self.config = config
        self.html_template = self._load_json_or_text(config.html_template)
        self.projekte = self._load_json_or_text(config.data_dir / "projekte.json")
        self.profiltexte = self._load_json_or_text(config.data_dir / "profiltexte.json")
        self.berufsrichtung = Berufsrichtung()
        self.kompetenzen = KompetenzenDB(config.db_path)
        self.erfolg = ErfolgsTracker(config.db_path)

    @staticmethod
    def _load_json_or_text(pfad: Path):
        with open(pfad, "r", encoding="utf-8") as f:
            return json.load(f) if pfad.suffix == ".json" else f.read()

    # ---------- MARKER IM HTML ----------
    def _extract_section(self, html: str, section: str) -> str:
        pattern = rf"<!-- START_{section} -->(.*?)<!-- END_{section} -->"
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            raise ValueError(f"❌ START_{section} oder END_{section} nicht gefunden!")
        return match.group(1).strip()

    def _replace_section(self, html: str, section: str, new_content: str) -> str:
        # Prüft, dass der Marker existiert (sonst würde still nichts ersetzt)
        self._extract_section(html, section)
        pattern = rf"(<!-- START_{section} -->).*?(<!-- END_{section} -->)"
        return re.sub(
            pattern,
            lambda m: f"{m.group(1)}\n{new_content}\n{m.group(2)}",
            html,
            flags=re.DOTALL,
        )

    # ---------- HAUPTFUNKTION ----------
    def anpassen_fuer_stelle(self, stellen_text: str) -> Dict:
        """Passt Lebenslauf an die Stelle an – nur aus Profil, Projekten und Kompetenz-DB."""
        richtung = self.berufsrichtung.erkenne(stellen_text)
        profil = self.profiltexte.get(richtung, self.profiltexte["softwareentwicklung"])

        skills_html = self._baue_skills_html(stellen_text)
        projekte = self.waehle_projekte(stellen_text)
        projekte_html = self._baue_projekte_html(projekte)

        html = self.html_template
        html = self._replace_section(html, "SKILLS", skills_html)
        html = self._replace_section(html, "PROJECTS", projekte_html)
        html = self._replace_section(html, "SUBTITLE", profil["subtitle"])
        html = self._replace_section(
            html, "PROFIL", self._fuelle_platzhalter(profil["profil"])
        )
        html = self._replace_section(
            html,
            "KERNKOMPETENZEN",
            "\n".join(
                f'<div style="margin-bottom:3px;">{self._fuelle_platzhalter(kk)}</div>'
                for kk in profil["kernkompetenzen"]
            ),
        )

        return {
            "html": html,
            "skills": skills_html,
            "projekte": projekte,
            "berufsrichtung": richtung,
        }

    def _fuelle_platzhalter(self, text: str) -> str:
        """{jahre_erfahrung} in profiltexte.json → aktuelle Berufsjahre."""
        jahre = self.config.jahre_erfahrung
        if jahre is None:
            return text
        return text.replace("{jahre_erfahrung}", str(jahre))

    # ---------- SKILLS ----------
    def _baue_skills_html(self, stellen_text: str) -> str:
        """Profil-Skills nach Relevanz sortiert + passende Skills aus der Kompetenz-DB."""
        text_lower = stellen_text.lower()

        def passt(eintrag) -> bool:
            return any(kommt_vor(kw, text_lower) for kw in skill_keywords(eintrag))

        kategorien = []
        for name, eintraege in self.config.profil["skills"].items():
            # Stabile Sortierung: passende Skills zuerst, sonst Profil-Reihenfolge
            sortiert = sorted(eintraege, key=lambda e: not passt(e))
            treffer = sum(passt(e) for e in eintraege)
            anteil = treffer / len(eintraege) if eintraege else 0
            kategorien.append(
                (treffer, anteil, name, [skill_name(e) for e in sortiert])
            )

        zusatz = self._zusatz_skills_aus_db(stellen_text)
        if zusatz:
            kategorien.append((len(zusatz), 1.0, "Weitere Kenntnisse", zusatz))

        # Meiste Treffer zuerst; bei Gleichstand die Kategorie, die am
        # stärksten zur Stelle passt (2 von 3 Skills schlägt 2 von 8)
        kategorien.sort(key=lambda k: (-k[0], -k[1]))
        return "\n".join(
            '<div class="skill-category">\n'
            f"<strong>{name}</strong>\n"
            f"<div>{', '.join(skills[:MAX_SKILLS_PRO_KATEGORIE])}</div>\n"
            "</div>"
            for _, _, name, skills in kategorien
        )

    def _zusatz_skills_aus_db(self, stellen_text: str) -> List[str]:
        """Skills, die die Stelle fordert, die du laut Kompetenz-DB kannst,
        die aber noch nicht im Profil stehen (z.B. nach dem Gap-Check gelernt)."""
        profil_text = " ".join(
            skill_name(e).lower()
            for eintraege in self.config.profil["skills"].values()
            for e in eintraege
        )
        profil_skills = set(finde_skills(profil_text))

        zusatz = []
        for skill in finde_skills(stellen_text):
            if skill in profil_skills:
                continue
            level = self.kompetenzen.hat_kompetenz(skill)
            if level == "ja":
                zusatz.append(skill)
            elif level == "teilweise":
                zusatz.append(f"{skill} (Grundlagen)")
        return zusatz

    # ---------- PROJEKTE ----------
    def waehle_projekte(self, stellen_text: str) -> List[Dict]:
        """Wählt 2–3 Projekte: Tag-Treffer in der Stelle + Bonus aus früheren Erfolgen."""
        text_lower = stellen_text.lower()
        bonus = self.erfolg.projekt_bonus()

        bewertet = []
        for i, p in enumerate(self.projekte):
            treffer = sum(kommt_vor(tag, text_lower) for tag in p.get("tags", []))
            score = treffer + bonus.get(p["id"], 0.0)
            # -i: bei Gleichstand gewinnt die Reihenfolge in projekte.json
            bewertet.append((score, treffer, -i, p))
        bewertet.sort(key=lambda x: (x[0], x[2]), reverse=True)

        gewaehlt = [p for score, treffer, _, p in bewertet if treffer > 0][
            :MAX_PROJEKTE
        ]
        for _, _, _, p in bewertet:
            if len(gewaehlt) >= MIN_PROJEKTE:
                break
            if p not in gewaehlt:
                gewaehlt.append(p)
        return gewaehlt

    def _baue_projekte_html(self, projekte: List[Dict]) -> str:
        teile = []
        for p in projekte:
            link_html = ""
            if p.get("link"):
                link_text = p.get("link_text") or "Link öffnen"
                link_html = (
                    f'<br><span style="font-size:7pt;">'
                    f'🔗 <a href="{p["link"]}" '
                    f'style="color:inherit;text-decoration:underline;">'
                    f"{link_text}</a></span>"
                )
            teile.append(
                f'<div class="project">\n'
                f'<strong>{p["name"]} ({p["tech"]})</strong>\n'
                f'<div>{p["beschreibung"]}{link_html}</div>\n'
                f"</div>"
            )
        return "\n".join(teile)

    # ---------- PDF ----------
    def speichere_pdf(
        self, html_inhalt: str, ausgabe_pfad: Path, base_url: Optional[Path] = None
    ) -> int:
        """Schreibt das PDF und gibt die Seitenzahl zurück."""
        try:
            from weasyprint import HTML
        except ImportError:
            raise ImportError("❌ WeasyPrint nicht installiert: pip install weasyprint")
        # base_url: damit relative Pfade (z.B. Foto) im HTML gefunden werden
        basis = base_url or self.config.html_template.parent
        html_inhalt = bilder_fuer_pdf(html_inhalt, basis)
        dokument = HTML(string=html_inhalt, base_url=str(basis)).render()
        dokument.write_pdf(ausgabe_pfad)
        return len(dokument.pages)
