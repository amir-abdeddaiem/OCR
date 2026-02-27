"""
Module d'extraction intelligente de données environnementales à partir de factures.

Analyse le texte OCR d'une facture (électricité, gaz, eau, transport, carburant...)
et extrait UNIQUEMENT les données pertinentes pour le calcul du bilan carbone / ESG.
"""

import re
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


# ─────────────────────────────────────────────────────────────────────────────
# Facteurs d'émission CO₂ (kg CO₂ par unité) — sources: ADEME, IEA, ANME
# ─────────────────────────────────────────────────────────────────────────────
EMISSION_FACTORS: Dict[str, Dict[str, Any]] = {
    "electricite": {
        "facteur_kg_co2_par_kwh": 0.475,   # Tunisie (mix énergétique ~gaz naturel)
        "source": "ANME / IEA 2024 — mix électrique Tunisie",
        "unite": "kWh",
    },
    "gaz_naturel": {
        "facteur_kg_co2_par_kwh": 0.205,
        "facteur_kg_co2_par_m3": 2.0,       # ~1 m³ gaz naturel ≈ 10 kWh ≈ 2 kg CO₂
        "source": "ADEME Base Carbone 2024",
        "unite": "kWh ou m³",
    },
    "eau": {
        "facteur_kg_co2_par_m3": 0.344,
        "source": "Water UK / ADEME 2024 — traitement + distribution",
        "unite": "m³",
    },
    "essence": {
        "facteur_kg_co2_par_litre": 2.31,
        "source": "ADEME Base Carbone 2024",
        "unite": "litres",
    },
    "diesel": {
        "facteur_kg_co2_par_litre": 2.68,
        "source": "ADEME Base Carbone 2024",
        "unite": "litres",
    },
    "gpl": {
        "facteur_kg_co2_par_litre": 1.66,
        "source": "ADEME Base Carbone 2024",
        "unite": "litres",
    },
}

# Mots-cles pour identifier le type de facture (FR + EN + AR)
_TYPE_KEYWORDS = {
    "electricite": [
        "steg", "électricité", "electricite", "electricity", "kwh", "kilowatt",
        "compteur électrique", "consommation electrique", "tarif electricite",
        "puissance souscrite", "heures pleines", "heures creuses",
        # Arabe
        "\u0643\u0647\u0631\u0628\u0627\u0621",           # كهرباء
        "\u0627\u0633\u062a\u0647\u0644\u0627\u0643 \u0627\u0644\u0643\u0647\u0631\u0628\u0627\u0621",  # استهلاك الكهرباء
        "\u0641\u0627\u062a\u0648\u0631\u0629 \u0643\u0647\u0631\u0628\u0627\u0621",  # فاتورة كهرباء
        "\u0639\u062f\u0627\u062f \u0643\u0647\u0631\u0628\u0627\u0626\u064a",  # عداد كهربائي
        "\u0643\u064a\u0644\u0648\u0648\u0627\u0637",    # كيلوواط
        "\u0637\u0627\u0642\u0629",                        # طاقة
    ],
    "gaz_naturel": [
        "steg gaz", "gaz naturel", "natural gas", "m³ gaz", "thermie",
        "consommation gaz", "compteur gaz",
        # Arabe
        "\u063a\u0627\u0632",                               # غاز
        "\u063a\u0627\u0632 \u0637\u0628\u064a\u0639\u064a",  # غاز طبيعي
        "\u0627\u0633\u062a\u0647\u0644\u0627\u0643 \u0627\u0644\u063a\u0627\u0632",  # استهلاك الغاز
        "\u0641\u0627\u062a\u0648\u0631\u0629 \u063a\u0627\u0632",  # فاتورة غاز
    ],
    "eau": [
        "sonede", "eau potable", "water", "consommation eau", "m³ eau",
        "assainissement", "compteur eau",
        # Arabe
        "\u0645\u0627\u0621",                               # ماء
        "\u0645\u064a\u0627\u0647",                         # مياه
        "\u0627\u0633\u062a\u0647\u0644\u0627\u0643 \u0627\u0644\u0645\u0627\u0621",  # استهلاك الماء
        "\u0641\u0627\u062a\u0648\u0631\u0629 \u0645\u0627\u0621",  # فاتورة ماء
        "\u0635\u0631\u0641 \u0635\u062d\u064a",           # صرف صحي
    ],
    "essence": [
        "essence", "gasoline", "sans plomb", "sp95", "sp98",
        # Arabe
        "\u0628\u0646\u0632\u064a\u0646",                  # بنزين
        "\u0648\u0642\u0648\u062f",                         # وقود
    ],
    "diesel": [
        "diesel", "gasoil", "gazole",
        # Arabe
        "\u062f\u064a\u0632\u0644",                         # ديزل
        "\u0645\u0627\u0632\u0648\u062a",                   # مازوت
    ],
    "gpl": [
        "gpl", "lpg", "butane", "propane",
        # Arabe
        "\u063a\u0627\u0632 \u0645\u0633\u0627\u0644",    # غاز مسال
        "\u0628\u0648\u062a\u0627\u0646",                   # بوتان
        "\u0628\u0631\u0648\u0628\u0627\u0646",             # بروبان
    ],
}


@dataclass
class DonneeEnvironnementale:
    """Structure d'une donnée extraite pertinente pour le bilan carbone."""
    champ: str                        # Ex: "Énergie consommée"
    valeur: Optional[str] = None      # Ex: "450"
    unite: Optional[str] = None       # Ex: "kWh"
    confiance: float = 0.0            # 0.0 à 1.0


@dataclass
class ResultatExtraction:
    """Résultat complet de l'extraction environnementale."""
    type_facture: str = "inconnu"
    fournisseur: Optional[str] = None
    periode: Optional[str] = None
    donnees: List[DonneeEnvironnementale] = field(default_factory=list)
    emission_co2_kg: Optional[float] = None
    facteur_emission_utilise: Optional[str] = None
    source_facteur: Optional[str] = None
    resume: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Arrondir
        if d.get("emission_co2_kg") is not None:
            d["emission_co2_kg"] = round(d["emission_co2_kg"], 3)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ─────────────────────────────────────────────────────────────────────────────
# Fonctions d'extraction par patterns
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalise le texte pour la recherche."""
    return text.lower().replace("\n", " ").replace("\r", " ")


def _detect_type(text_lower: str) -> str:
    """Détecte le type de facture à partir du texte."""
    scores: Dict[str, int] = {}
    for type_name, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[type_name] = score

    if not scores:
        return "inconnu"
    return max(scores, key=scores.get)  # type: ignore[arg-type]


def _detect_fournisseur(text_lower: str) -> Optional[str]:
    """Detecte le fournisseur."""
    fournisseurs = {
        "STEG": ["steg",
                  "société tunisienne de l'électricité et du gaz",
                  "societe tunisienne de l electricite",
                  "société tunisienne du gaz",
                  "societe tunisienne du gaz",
                  "tunisienne du gaz",
                  "tunisienne de l'électricité",
                  "tunisienne de l electricite",
                  "\u0627\u0644\u0634\u0631\u0643\u0629 \u0627\u0644\u062a\u0648\u0646\u0633\u064a\u0629 \u0644\u0644\u0643\u0647\u0631\u0628\u0627\u0621",  # الشركة التونسية للكهرباء
                  "\u0627\u0644\u0634\u0631\u0643\u0629 \u0627\u0644\u062a\u0648\u0646\u0633\u064a\u0629 \u0644\u0644\u0643\u0647\u0631\u0628\u0627\u0621 \u0648\u0627\u0644\u063a\u0627\u0632",  # الشركة التونسية للكهرباء والغاز
                 ],
        "SONEDE": ["sonede",
                    "société nationale d'exploitation et de distribution des eaux",
                    "\u0627\u0644\u0634\u0631\u0643\u0629 \u0627\u0644\u0648\u0637\u0646\u064a\u0629 \u0644\u0627\u0633\u062a\u063a\u0644\u0627\u0644 \u0648\u062a\u0648\u0632\u064a\u0639 \u0627\u0644\u0645\u064a\u0627\u0647",  # الشركة الوطنية لاستغلال وتوزيع المياه
                   ],
        "EDF": ["edf", "électricité de france"],
        "Engie": ["engie"],
        "TotalEnergies": ["totalenergies", "total energies"],
        "Shell": ["shell"],
    }
    for name, keywords in fournisseurs.items():
        if any(kw in text_lower for kw in keywords):
            return name
    return None


def _extract_numbers_with_unit(text: str, patterns: List[str]) -> List[DonneeEnvironnementale]:
    """Extrait des nombres associés à des patterns (regex)."""
    results = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            groups = match.groups()
            if groups:
                # Nettoyer le nombre: remplacer virgule par point, enlever espaces
                raw_val = groups[0].replace(" ", "").replace(",", ".")
                unite = groups[1] if len(groups) > 1 else ""
                results.append((raw_val, unite, match.group(0)))
    return results


def _extract_consumption(text: str, text_lower: str) -> List[DonneeEnvironnementale]:
    """Extrait les données de consommation d'énergie."""
    donnees: List[DonneeEnvironnementale] = []

    # ── kWh (électricité / gaz) ──
    kwh_patterns = [
        r'([\d\s.,]+)\s*(kwh)',
        r'consommation[:\s]*(?:de\s+)?([\d\s.,]+)\s*(kwh)',
        r'\u00e9nergie[:\s]*(?:consomm\u00e9e)?[:\s]*([\d\s.,]+)\s*(kwh)',
        r'energie[:\s]*(?:consommee)?[:\s]*([\d\s.,]+)\s*(kwh)',
        r'total[:\s]*([\d\s.,]+)\s*(kwh)',
        # STEG : "Quantit\u00e9 (1)  400" -> only capture 1 standalone integer
        r'quantit[\u00e9e]\s*(?:\(\d\))?\s+(\d+)',
        # Arabe : استهلاك ... كو.س  / طاقة ... كو.س
        r'\u0627\u0633\u062a\u0647\u0644\u0627\u0643[:\s]*([\d\s.,]+)\s*(kwh|\u0643\.?\u0648\.?\u0633|\u0643\u064a\u0644\u0648\u0648\u0627\u0637)',
        r'\u0637\u0627\u0642\u0629[:\s]*([\d\s.,]+)\s*(kwh|\u0643\.?\u0648\.?\u0633)',
        r'([\d\s.,]+)\s*(\u0643\.?\u0648\.?\u0633|\u0643\u064a\u0644\u0648\u0648\u0627\u0637)',
    ]
    seen_kwh = set()
    for pat in kwh_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            try:
                fv = float(val)
                # Filtrer les valeurs absurdes (r\u00e9sidentiel : 1 - 100 000 kWh)
                if fv < 1 or fv > 100000:
                    continue
            except ValueError:
                continue
            if val not in seen_kwh:
                seen_kwh.add(val)
                donnees.append(DonneeEnvironnementale(
                    champ="Énergie consommée",
                    valeur=val, unite="kWh", confiance=0.85
                ))

    # ── m³ (gaz / eau) ──
    m3_patterns = [
        r'([\d\s.,]+)\s*(m[\u00b33]|m\s*cube)',
        r'consommation[:\s]*(?:de\s+)?([\d\s.,]+)\s*(m[\u00b33])',
        r'volume[:\s]*([\d\s.,]+)\s*(m[\u00b33])',
        # Arabe : متر مكعب / م³
        r'([\d\s.,]+)\s*(\u0645\u062a\u0631\s*\u0645\u0643\u0639\u0628|\u0645[\u00b33])',
        r'\u0627\u0633\u062a\u0647\u0644\u0627\u0643[:\s]*([\d\s.,]+)\s*(\u0645[\u00b33]|m[\u00b33])',
    ]
    seen_m3 = set()
    for pat in m3_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            try:
                float(val)
            except ValueError:
                continue
            if val not in seen_m3:
                seen_m3.add(val)
                label = "Volume consommé (eau)" if "eau" in text_lower or "sonede" in text_lower else "Volume consommé (gaz)"
                donnees.append(DonneeEnvironnementale(
                    champ=label, valeur=val, unite="m³", confiance=0.80
                ))

    # ── STEG gaz : extraction contextuelle m³ ──
    # Les factures STEG combinent électricité et gaz dans un tableau.
    # La quantité gaz (m³) apparaît dans la section Gaz sans unité explicite.
    if 'gaz' in text_lower and not seen_m3:
        # Chercher entre "Total Electricité" et "Total Gaz"
        gaz_section = re.search(
            r'(?:total\s*electricit[\u00e9e]|\u0645\u062c\u0645\u0648\u0639\s*\u0627\u0644\u0643\u0647\u0631\u0628\u0627\u0621)(.*?)(?:total\s*gaz|\u0645\u062c\u0645\u0648\u0639\s*\u0627\u0644\u063a\u0627\u0632)',
            text, re.IGNORECASE | re.DOTALL
        )
        if gaz_section:
            gaz_text = gaz_section.group(1)
            # Chercher des entiers PURS (pas partie d'un décimal)
            # (?<!\.) exclut les digits après un point (ex: 85 de 85.624)
            # (?!\.\d) exclut les digits suivis d'un point-décimal
            candidates = re.findall(r'(?<![.\d])(\d{2,5})(?!\.\d)', gaz_text)
            for c in candidates:
                cv = int(c)
                # Min 50 (exclut TVA 13/19, nb mois, puissance)
                # Max 50000, exclure années 2000-2100
                if 50 <= cv <= 50000 and not (2000 <= cv <= 2100):
                    if str(cv) not in seen_m3:
                        seen_m3.add(str(cv))
                        donnees.append(DonneeEnvironnementale(
                            champ="Volume consommé (gaz)", valeur=str(cv),
                            unite="m³", confiance=0.70
                        ))
                        break

    # ── Litres (carburant) ──
    litre_patterns = [
        r'([\d\s.,]+)\s*(litres?|l\b)',
        r'quantit[\u00e9e][:\s]*([\d\s.,]+)\s*(litres?|l\b)',
        r'volume[:\s]*([\d\s.,]+)\s*(litres?|l\b)',
        # Arabe : لتر
        r'([\d\s.,]+)\s*(\u0644\u062a\u0631)',
        r'\u0643\u0645\u064a\u0629[:\s]*([\d\s.,]+)\s*(\u0644\u062a\u0631|litres?)',
    ]
    seen_l = set()
    for pat in litre_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            try:
                fv = float(val)
                if fv < 0.1 or fv > 100000:
                    continue
            except ValueError:
                continue
            if val not in seen_l:
                seen_l.add(val)
                donnees.append(DonneeEnvironnementale(
                    champ="Volume carburant", valeur=val, unite="litres", confiance=0.75
                ))

    return donnees


def _extract_period(text: str) -> Optional[str]:
    """Extrait la période de facturation."""
    period_patterns = [
        # "du 01/01/2025 au 31/01/2025"
        r'(?:du|from|p\u00e9riode|periode)\s*[:\s]?\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\s*(?:au|to|[-\u2013])\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})',
        # Arabe : من ... إلى ...  (min ... ila ...)
        r'(?:\u0645\u0646)\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\s*(?:\u0625\u0644\u0649|\u0627\u0644\u0649)\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})',
        # STEG : format ISO "YYYY-MM-DD : \u00e0/\u0625\u0644\u0649 YYYY-MM-DD" ou "YYYY-MM-DD ... YYYY-MM-DD"
        r'(\d{4}-\d{2}-\d{2})\s*[:\s]*(?:\u0625\u0644[\u064a\u0649]|\u00e0|au|to)?\s*[:\s]*(\d{4}-\d{2}-\d{2})',
        # "Janvier 2025" / "January 2025" / mois arabes
        r'((?:janvier|f\u00e9vrier|mars|avril|mai|juin|juillet|ao\u00fbt|septembre|octobre|novembre|d\u00e9cembre|january|february|march|april|may|june|july|august|september|october|november|december|\u062c\u0627\u0646\u0641\u064a|\u0641\u064a\u0641\u0631\u064a|\u0645\u0627\u0631\u0633|\u0623\u0641\u0631\u064a\u0644|\u0645\u0627\u064a|\u062c\u0648\u0627\u0646|\u062c\u0648\u064a\u0644\u064a\u0629|\u0623\u0648\u062a|\u0633\u0628\u062a\u0645\u0628\u0631|\u0623\u0643\u062a\u0648\u0628\u0631|\u0646\u0648\u0641\u0645\u0628\u0631|\u062f\u064a\u0633\u0645\u0628\u0631)\s+\d{4})',
        # "01/2025 - 02/2025"
        r'(\d{1,2}[/\-]\d{4})\s*[-\u2013\u00e0]\s*(\d{1,2}[/\-]\d{4})',
        # Trimestre
        r'((?:T[1-4]|Q[1-4]|trimestre\s*\d)\s*\d{4})',
    ]
    for pat in period_patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            groups = [g for g in match.groups() if g]
            if len(groups) > 1:
                # Trier chronologiquement (corrige l'inversion RTL arabe)
                sorted_dates = sorted(groups[:2])
                return f"{sorted_dates[0]} au {sorted_dates[1]}"
            return groups[0]
    return None


def _extract_amounts(text: str) -> List[DonneeEnvironnementale]:
    """Extrait les montants monétaires liés à l'énergie."""
    donnees: List[DonneeEnvironnementale] = []

    amount_patterns = [
        # STEG : "595.000  Montant \u00e0 payer" (montant AVANT le libell\u00e9)
        r'(\d[\d.,]+)\s+montant\s*(?:\u00e0|a)\s*payer',
        # STEG : "Montant \u00e0 payer  595.000"
        r'montant\s*(?:\u00e0|a)\s*payer[:\s]*(\d[\d\s.,]*)',
        # STEG : "MONTANT TOTAL  139.006"
        r'montant\s*total[:\s]*(\d[\d\s.,]*)',
        # STEG : "595.000  Montant" (bulletin de versement)
        r'(\d[\d.,]+)\s+montant\b',
        # "Total TTC: 125,50 DT" ou "Total: 125.50 \u20ac"
        r'total\s*(?:ttc|ht)?[:\s]*([\d\s.,]+)\s*(dt|tnd|\u20ac|eur|dinars?|euros?|\$|usd)',
        # "Montant: 125,50 DT"
        r'montant[:\s]*([\d\s.,]+)\s*(dt|tnd|\u20ac|eur|dinars?|euros?|\$|usd)',
        # "Net \u00e0 payer: 125,50 DT"
        r'net\s*(?:\u00e0|a)\s*payer[:\s]*([\d\s.,]+)\s*(dt|tnd|\u20ac|eur|dinars?|euros?)',
        # Montant avec devise avant le nombre
        r'(dt|tnd|\u20ac)\s*([\d\s.,]+)',
        # Arabe : المبلغ / المجموع / الصافي
        r'\u0627\u0644\u0645\u0628\u0644\u063a[:\s]*([\d\s.,]+)\s*(\u062f\.?\u062a|dt|tnd|\u062f\u064a\u0646\u0627\u0631)',
        r'\u0627\u0644\u0645\u062c\u0645\u0648\u0639[:\s]*([\d\s.,]+)\s*(\u062f\.?\u062a|dt|tnd|\u062f\u064a\u0646\u0627\u0631)',
        r'\u0627\u0644\u0635\u0627\u0641\u064a[:\s]*([\d\s.,]+)\s*(\u062f\.?\u062a|dt|tnd|\u062f\u064a\u0646\u0627\u0631)',
    ]
    seen = set()
    for pat in amount_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            groups = match.groups()
            val = groups[0].replace(" ", "").replace(",", ".")
            try:
                float(val)
            except ValueError:
                # Peut-être la devise est en premier
                if len(groups) > 1:
                    val = groups[1].replace(" ", "").replace(",", ".")
                    try:
                        float(val)
                    except ValueError:
                        continue
                else:
                    continue
            if val not in seen:
                seen.add(val)
                devise = "DT"
                for g in groups:
                    gl = g.lower().strip()
                    if gl in ("\u20ac", "eur", "euros", "euro"):
                        devise = "EUR"
                    elif gl in ("$", "usd"):
                        devise = "USD"
                    elif gl in ("dt", "tnd", "dinar", "dinars",
                                "\u062f.\u062a", "\u062f\u062a",  # د.ت / دت
                                "\u062f\u064a\u0646\u0627\u0631"):  # دينار
                        devise = "DT"
                donnees.append(DonneeEnvironnementale(
                    champ="Montant facture", valeur=val, unite=devise, confiance=0.70
                ))
    return donnees


def _extract_co2_direct(text: str) -> List[DonneeEnvironnementale]:
    """Extrait les émissions CO₂ directement mentionnées dans la facture."""
    donnees: List[DonneeEnvironnementale] = []
    co2_patterns = [
        r'(?:co2|co₂|carbone|émissions?|emissions?)[:\s]*([\d\s.,]+)\s*(kg|tonnes?|t\b|g\b)',
        r'([\d\s.,]+)\s*(kg|tonnes?|t)\s*(?:de\s+)?(?:co2|co₂|carbone)',
        r'empreinte\s*carbone[:\s]*([\d\s.,]+)\s*(kg|tonnes?|t)',
    ]
    for pat in co2_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            unite = match.group(2).lower()
            if unite in ("t", "tonnes", "tonne"):
                unite = "tonnes CO₂"
            else:
                unite = "kg CO₂"
            try:
                float(val)
            except ValueError:
                continue
            donnees.append(DonneeEnvironnementale(
                champ="Émissions CO₂ (déclarées)", valeur=val, unite=unite, confiance=0.90
            ))
    return donnees


def _extract_energy_type(text_lower: str, facture_type: str) -> Optional[str]:
    """Détermine le type précis d'énergie."""
    types = {
        "electricite": "Électricité (réseau)",
        "gaz_naturel": "Gaz naturel",
        "eau": "Eau potable (+ assainissement)",
        "essence": "Essence (carburant)",
        "diesel": "Diesel / Gasoil",
        "gpl": "GPL (Gaz de Pétrole Liquéfié)",
    }
    if facture_type in types:
        return types[facture_type]

    # Détection fine
    if "renouvelable" in text_lower or "solaire" in text_lower or "éolien" in text_lower:
        return "Énergie renouvelable"
    return None


def _calculate_co2(donnees: List[DonneeEnvironnementale], facture_type: str) -> tuple:
    """
    Calcule les émissions CO₂ à partir des données extraites et du facteur d'émission.
    Retourne (emission_kg, facteur_str, source).
    """
    if facture_type not in EMISSION_FACTORS:
        return None, None, None

    factors = EMISSION_FACTORS[facture_type]

    for d in donnees:
        try:
            val = float(d.valeur) if d.valeur else 0
        except (ValueError, TypeError):
            continue

        if val <= 0:
            continue

        if d.unite == "kWh" and "facteur_kg_co2_par_kwh" in factors:
            f = factors["facteur_kg_co2_par_kwh"]
            emission = val * f
            return emission, f"{f} kg CO₂/kWh", factors["source"]

        if d.unite == "m³" and "facteur_kg_co2_par_m3" in factors:
            f = factors["facteur_kg_co2_par_m3"]
            emission = val * f
            return emission, f"{f} kg CO₂/m³", factors["source"]

        if d.unite == "litres" and "facteur_kg_co2_par_litre" in factors:
            f = factors["facteur_kg_co2_par_litre"]
            emission = val * f
            return emission, f"{f} kg CO₂/litre", factors["source"]

    return None, None, None


# ─────────────────────────────────────────────────────────────────────────────
# Fonction principale d'extraction
# ─────────────────────────────────────────────────────────────────────────────

def extraire_donnees_environnementales(texte_ocr: str) -> ResultatExtraction:
    """
    Analyse le texte OCR d'une facture et extrait les données environnementales
    pertinentes pour le calcul du bilan carbone.

    Args:
        texte_ocr: Le texte brut extrait par OCR.

    Returns:
        ResultatExtraction contenant les données structurées.
    """
    if not texte_ocr or not texte_ocr.strip():
        return ResultatExtraction(resume="Aucun texte à analyser.")

    text_lower = _normalize(texte_ocr)
    result = ResultatExtraction()

    # 1. Identifier le type de facture
    result.type_facture = _detect_type(text_lower)

    # 2. Identifier le fournisseur
    result.fournisseur = _detect_fournisseur(text_lower)

    # 3. Extraire la période
    result.periode = _extract_period(texte_ocr)

    # 4. Extraire le type d'énergie
    energy_type = _extract_energy_type(text_lower, result.type_facture)
    if energy_type:
        result.donnees.append(DonneeEnvironnementale(
            champ="Type d'énergie", valeur=energy_type, confiance=0.90
        ))

    # 5. Extraire les consommations
    result.donnees.extend(_extract_consumption(texte_ocr, text_lower))

    # 6. Extraire les montants
    result.donnees.extend(_extract_amounts(texte_ocr))

    # 7. Extraire les émissions CO₂ déclarées
    result.donnees.extend(_extract_co2_direct(texte_ocr))

    # 8. Calculer les émissions CO₂ si non déclarées
    has_declared_co2 = any("CO₂" in d.champ for d in result.donnees)
    emission, facteur_str, source = _calculate_co2(result.donnees, result.type_facture)

    if emission is not None:
        result.emission_co2_kg = emission
        result.facteur_emission_utilise = facteur_str
        result.source_facteur = source

        if not has_declared_co2:
            result.donnees.append(DonneeEnvironnementale(
                champ="Émissions CO₂ (calculées)",
                valeur=str(round(emission, 3)),
                unite="kg CO₂",
                confiance=0.75,
            ))

    # 9. Générer le résumé
    result.resume = _generer_resume(result)

    return result


def _generer_resume(r: ResultatExtraction) -> str:
    """Génère un résumé lisible des données extraites."""
    lines = []

    type_labels = {
        "electricite": "[ELEC] Facture d'electricite",
        "gaz_naturel": "[GAZ] Facture de gaz naturel",
        "eau": "[EAU] Facture d'eau",
        "essence": "[CARB] Facture de carburant (essence)",
        "diesel": "[CARB] Facture de carburant (diesel)",
        "gpl": "[GPL] Facture de GPL",
        "inconnu": "[?] Type de facture non identifie",
    }
    lines.append(f"{type_labels.get(r.type_facture, r.type_facture)}")

    if r.fournisseur:
        lines.append(f"Fournisseur : {r.fournisseur}")
    if r.periode:
        lines.append(f"Periode : {r.periode}")

    # Données clés
    for d in r.donnees:
        if d.valeur:
            u = f" {d.unite}" if d.unite else ""
            lines.append(f"  • {d.champ} : {d.valeur}{u}")

    if r.emission_co2_kg is not None:
        lines.append(f"\nEmissions CO2 estimees : {r.emission_co2_kg:.3f} kg CO2")
        lines.append(f"   ({r.emission_co2_kg / 1000:.4f} tonnes CO2)")
        if r.facteur_emission_utilise:
            lines.append(f"   Facteur utilise : {r.facteur_emission_utilise}")
        if r.source_facteur:
            lines.append(f"   Source : {r.source_facteur}")
    else:
        lines.append("\nImpossible de calculer les emissions CO2 (donnees insuffisantes).")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python extractor.py <fichier_texte_ocr>")
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        texte = f.read()
    res = extraire_donnees_environnementales(texte)
    print(res.resume)
    print("\n─── JSON ───")
    print(res.to_json())
