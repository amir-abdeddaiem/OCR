"""
Extracteur intelligent v2 — Données environnementales & Bilan Carbone
=====================================================================

Architecture :
  1. Pré-analyse → découpe le texte en ZONES (entête, consommation, montants, taxes, pied)
  2. Détection fournisseur → sélectionne une STRATEGIE spécifique (STEG, SONEDE, …)
  3. Extraction structurée → la stratégie extrait chaque champ avec contexte
  4. Validation croisée → cohérence consommation × tarif ≈ montant
  5. Scoring dynamique → confiance ajustée selon nombre de confirmations
  6. Calcul CO₂ combiné → électricité + gaz séparément si facture mixte

API publique inchangée :
  - extraire_donnees_environnementales(texte_ocr) → ResultatExtraction
  - ResultatExtraction.to_dict() / .to_json()
"""

from __future__ import annotations

import re
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from collections import Counter


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES & FACTEURS D'ÉMISSION
# ═══════════════════════════════════════════════════════════════════════════════

EMISSION_FACTORS: Dict[str, Dict[str, Any]] = {
    "electricite": {
        "facteur_kg_co2_par_kwh": 0.475,
        "source": "ANME / IEA 2024 — mix électrique Tunisie",
        "unite": "kWh",
    },
    "gaz_naturel": {
        "facteur_kg_co2_par_m3": 2.0,
        "facteur_kg_co2_par_kwh": 0.205,
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

# Mots-clés par type (FR + EN + AR)
_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "electricite": [
        "steg", "électricité", "electricite", "electricity", "kwh", "kilowatt",
        "compteur électrique", "consommation electrique", "tarif electricite",
        "puissance souscrite", "heures pleines", "heures creuses",
        "كهرباء", "استهلاك الكهرباء", "فاتورة كهرباء", "عداد كهربائي",
        "كيلوواط", "طاقة",
    ],
    "gaz_naturel": [
        "gaz naturel", "natural gas", "m³ gaz", "thermie",
        "consommation gaz", "compteur gaz", "total gaz",
        "غاز", "غاز طبيعي", "استهلاك الغاز", "فاتورة غاز",
    ],
    "eau": [
        "eau", "sonede", "eau potable", "water", "consommation eau", "m³ eau",
        "assainissement", "compteur eau", "facture eau", "tarif eau",
        "ماء", "مياه", "استهلاك الماء", "فاتورة ماء", "صرف صحي",
    ],
    "essence": [
        "essence", "gasoline", "sans plomb", "sp95", "sp98",
        "بنزين", "وقود",
    ],
    "diesel": [
        "diesel", "gasoil", "gazole",
        "ديزل", "مازوت",
    ],
    "gpl": [
        "gpl", "lpg", "butane", "propane",
        "غاز مسال", "بوتان", "بروبان",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
#  STRUCTURES DE DONNÉES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DonneeEnvironnementale:
    """Structure d'une donnée extraite pertinente pour le bilan carbone."""
    champ: str
    valeur: Optional[str] = None
    unite: Optional[str] = None
    confiance: float = 0.0


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

    # ── Nouveaux champs v2 ──
    reference_facture: Optional[str] = None
    reference_client: Optional[str] = None
    adresse: Optional[str] = None
    types_energie: List[str] = field(default_factory=list)
    detail_co2: List[Dict[str, Any]] = field(default_factory=list)
    score_global: float = 0.0
    alertes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("emission_co2_kg") is not None:
            d["emission_co2_kg"] = round(d["emission_co2_kg"], 3)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYSE DE ZONES (TEXT STRUCTURE)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TextZones:
    """Découpe du texte OCR en zones sémantiques."""
    texte_complet: str
    texte_lower: str
    entete: str = ""
    consommation: str = ""
    electricite: str = ""
    gaz: str = ""
    montants: str = ""
    taxes: str = ""
    pied: str = ""


def _normalize(text: str) -> str:
    return text.lower().replace("\n", " ").replace("\r", " ")


def _parse_zones_steg(text: str, text_lower: str) -> TextZones:
    """Découpe spécifique STEG — sépare électricité / gaz / montants."""
    zones = TextZones(texte_complet=text, texte_lower=text_lower)

    m_conso = re.search(r'consommation', text, re.IGNORECASE)
    if m_conso:
        zones.entete = text[:m_conso.start()]
        rest = text[m_conso.start():]
    else:
        zones.entete = text[:300]
        rest = text

    zones.consommation = rest

    # Section Electricité
    elec_match = re.search(
        r'(electricit[ée].*?)(?=total\s*gaz|redevances?\s*fixes.*?gaz|\bgaz\b\s+redevance)',
        rest, re.IGNORECASE | re.DOTALL
    )
    if elec_match:
        zones.electricite = elec_match.group(1)

    # Section Gaz (entre "Total Electricité" et "Total Gaz")
    gaz_match = re.search(
        r'(?:total\s*electricit[ée]|مجموع\s*الكهرباء)(.*?)(?:total\s*gaz|مجموع\s*الغاز|total\s*services)',
        rest, re.IGNORECASE | re.DOTALL
    )
    if gaz_match:
        zones.gaz = gaz_match.group(1)

    # Section Montants
    montant_match = re.search(
        r'(montant\s*total.*?)(?:bulletin|versement|fermer|$)',
        rest, re.IGNORECASE | re.DOTALL
    )
    if montant_match:
        zones.montants = montant_match.group(1)

    # Pied de page
    pied_match = re.search(r'(bulletin\s*de\s*versement.*)', rest, re.IGNORECASE | re.DOTALL)
    if pied_match:
        zones.pied = pied_match.group(1)

    # Taxes
    taxes_match = re.search(
        r'((?:contribution|taxe|tva|fte|redevance).*?)(?:montant\s*total|bulletin|$)',
        rest, re.IGNORECASE | re.DOTALL
    )
    if taxes_match:
        zones.taxes = taxes_match.group(1)

    return zones


def _parse_zones_sonede(text: str, text_lower: str) -> TextZones:
    zones = TextZones(texte_complet=text, texte_lower=text_lower)
    m_conso = re.search(r'consommation', text, re.IGNORECASE)
    if m_conso:
        zones.entete = text[:m_conso.start()]
    else:
        zones.entete = text[:300]
    zones.consommation = text
    montant_match = re.search(r'((?:montant|total|net).*)', text, re.IGNORECASE | re.DOTALL)
    if montant_match:
        zones.montants = montant_match.group(1)
    return zones


def _parse_zones_generic(text: str, text_lower: str) -> TextZones:
    zones = TextZones(texte_complet=text, texte_lower=text_lower)
    zones.entete = text[:min(500, len(text))]
    zones.consommation = text
    zones.montants = text
    return zones


# ═══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIES D'EXTRACTION PAR FOURNISSEUR
# ═══════════════════════════════════════════════════════════════════════════════

class ExtractionStrategy(ABC):
    """Classe de base pour les stratégies d'extraction."""

    def __init__(self, zones: TextZones):
        self.zones = zones
        self.text = zones.texte_complet
        self.text_lower = zones.texte_lower

    @abstractmethod
    def extract_consumption(self) -> List[DonneeEnvironnementale]: ...

    @abstractmethod
    def extract_amounts(self) -> List[DonneeEnvironnementale]: ...

    def extract_period(self) -> Optional[str]:
        return _extract_period_generic(self.text)

    def extract_reference(self) -> Tuple[Optional[str], Optional[str]]:
        return _extract_references_generic(self.text, self.text_lower)

    def extract_address(self) -> Optional[str]:
        return _extract_address_generic(self.text)

    def detect_energy_types(self) -> List[str]:
        return _detect_all_types(self.text_lower)


# ─────────────────────────────────────────────────────────────────────────────
#  STEG Strategy
# ─────────────────────────────────────────────────────────────────────────────

class STEGStrategy(ExtractionStrategy):
    """Extraction spécialisée pour les factures STEG (Tunisie).

    Layout STEG typique :
      - En-tête : Référence, N° Dépannage, District, "FACTURE SUR RELEVE"
      - Dates : "YYYY-MM-DD : إلى  YYYY-MM-DD : من"  (RTL)
      - Tableau : Electricité Quantité(1) <val>  |  Index Nouveau Ancien  |  HT(4)
      - Sous-total "Total Electricité" puis section Gaz avec même layout
      - Montant Total → libellé en toutes lettres → "Montant à payer"
      - Bulletin de versement avec montant final + référence CCP
    """

    def extract_consumption(self) -> List[DonneeEnvironnementale]:
        donnees: List[DonneeEnvironnementale] = []

        # ── 1. Électricité (kWh) — cross-validation multi-source ──
        elec_kwh = self._extract_steg_electricity()
        if elec_kwh is not None:
            donnees.append(DonneeEnvironnementale(
                champ="Énergie consommée (électricité)",
                valeur=str(elec_kwh), unite="kWh", confiance=0.90
            ))

        # ── 2. Gaz (m³) — extraction contextuelle ──
        gaz_m3 = self._extract_steg_gas()
        if gaz_m3 is not None:
            donnees.append(DonneeEnvironnementale(
                champ="Volume consommé (gaz)",
                valeur=str(gaz_m3), unite="m³", confiance=0.85
            ))

        return donnees

    def _extract_steg_electricity(self) -> Optional[int]:
        """Cross-validation multi-source pour la quantité d'électricité."""
        candidates: List[Tuple[str, int, float]] = []

        # Source 1 : "Quantité (1) 483" ou "éQuantité  483"
        for pat in [
            r'quantit[ée]\s*(?:\(\d\))?\s+(\d+)',
            r'[ée]quantit[ée]?\s+(\d+)',
        ]:
            for m in re.finditer(pat, self.text, re.IGNORECASE):
                val = int(m.group(1))
                if 1 <= val <= 100_000:
                    candidates.append(("quantite_label", val, 0.90))

        # Source 2 : "<N> kWh" explicite
        for m in re.finditer(r'(\d+)\s*kwh', self.text, re.IGNORECASE):
            val = int(m.group(1))
            if 1 <= val <= 100_000:
                candidates.append(("kwh_unit", val, 0.85))

        # Source 3 : Différence d'index
        idx_match = re.search(r'(\d{4,7})\s+(\d{4,7})', self.zones.electricite or self.text)
        if idx_match:
            a, b = int(idx_match.group(1)), int(idx_match.group(2))
            diff = abs(a - b)
            if 1 <= diff <= 100_000:
                candidates.append(("index_diff", diff, 0.80))

        if not candidates:
            return None

        # Cross-validation : majorité vote
        values = [c[1] for c in candidates]
        counts = Counter(values)
        best_val, best_count = counts.most_common(1)[0]
        return best_val

    def _extract_steg_gas(self) -> Optional[int]:
        """Extraction contextuelle gaz entre sections STEG.

        Validation stricte :
        - Zone gaz doit contenir un indicateur de consommation gaz (GAZ-NATUR, Redevances, m³…)
        - Plage résidentielle : 10–2000 m³ par période
        - Exclure les valeurs identiques à la référence facture
        """
        # Récupérer la ref pour éviter les faux positifs
        ref_numbers = set()
        ref_match = re.search(r'[Rr][ée]f[ée]rence\s*:\s*([\d\s]+\d)', self.text)
        if ref_match:
            for part in re.findall(r'\d+', ref_match.group(1)):
                ref_numbers.add(int(part))

        # Vérifier qu'il y a un VRAI indicateur de consommation gaz
        has_gaz_conso = bool(re.search(
            r'gaz[\s-]*natur|sg\d{6,}|\bgaz\b[^a-z]*\d{2,5}\s+\d{3,7}\s+\d{3,7}|'
            r'gaz\b[^a-z]*redevance|غاز\s*طبيعي',
            self.text, re.IGNORECASE
        ))
        if not has_gaz_conso:
            return None

        # Stratégie 1 : zone gaz pré-découpée
        if self.zones.gaz:
            result = self._find_gas_quantity_in_zone(self.zones.gaz, ref_numbers)
            if result is not None:
                return result

        # Stratégie 2 : recherche contextuelle globale
        gaz_section = re.search(
            r'(?:total\s*electricit[ée]|مجموع\s*الكهرباء)'
            r'(.*?)'
            r'(?:total\s*gaz|مجموع\s*الغاز)',
            self.text, re.IGNORECASE | re.DOTALL
        )
        if gaz_section:
            result = self._find_gas_quantity_in_zone(gaz_section.group(1), ref_numbers)
            if result is not None:
                return result

        return None

    def _find_gas_quantity_in_zone(self, zone_text: str, exclude: set = None) -> Optional[int]:
        """Cherche une quantité gaz dans une zone, avec exclusion de faux positifs."""
        if exclude is None:
            exclude = set()
        candidates = re.findall(r'(?<![.\d])(\d{2,5})(?!\.\d)', zone_text)
        for c in candidates:
            cv = int(c)
            # Plage résidentielle gaz : 10–2000 m³ (excl. années, refs)
            if 50 <= cv <= 2000 and not (2000 <= cv <= 2100) and cv not in exclude:
                return cv
        return None

    def extract_amounts(self) -> List[DonneeEnvironnementale]:
        """STEG : extraction hiérarchisée.

        Priorité : Montant à payer > Bulletin > MONTANT TOTAL > Sous-totaux
        """
        donnees: List[DonneeEnvironnementale] = []
        seen: set = set()

        # ── 1. Montant à payer ──
        val = self._extract_montant_a_payer()
        if val is not None:
            key = f"{val:.3f}"
            seen.add(key)
            donnees.append(DonneeEnvironnementale(
                champ="Montant à payer", valeur=key, unite="DT", confiance=0.95
            ))

        # ── 2. Bulletin de versement ──
        val2 = self._extract_bulletin_versement()
        if val2 is not None:
            key = f"{val2:.3f}"
            if key not in seen:
                seen.add(key)
                donnees.append(DonneeEnvironnementale(
                    champ="Montant bulletin", valeur=key, unite="DT", confiance=0.90
                ))

        # ── 3. MONTANT TOTAL ──
        val3 = self._extract_montant_total()
        if val3 is not None:
            key = f"{val3:.3f}"
            if key not in seen:
                seen.add(key)
                donnees.append(DonneeEnvironnementale(
                    champ="Montant total HT", valeur=key, unite="DT", confiance=0.80
                ))

        # ── 4. Sous-totaux ──
        for label_re, champ in [
            (r'total\s*electricit[ée]', "Sous-total électricité"),
            (r'total\s*gaz', "Sous-total gaz"),
            (r'total\s*services', "Sous-total services"),
        ]:
            m = re.search(label_re + r'[:\s]*([\d.,]+)', self.text, re.IGNORECASE)
            if m:
                try:
                    fv = float(m.group(1).replace(",", "."))
                    key = f"{fv:.3f}"
                    if key not in seen and fv > 0.5:
                        seen.add(key)
                        donnees.append(DonneeEnvironnementale(
                            champ=champ, valeur=key, unite="DT", confiance=0.70
                        ))
                except ValueError:
                    pass

        # ── Cross-validation ──
        if val and val2 and abs(val - val2) < 0.01:
            for d in donnees:
                if d.champ == "Montant à payer":
                    d.confiance = min(1.0, d.confiance + 0.05)

        return donnees

    def _extract_montant_a_payer(self) -> Optional[float]:
        patterns = [
            # "Montant à payer  595.000"
            r'montant\s*(?:à|a)\s*payer[:\s]*([\d]+[.,]\d{3})',
            # "595.000  Montant à payer" (direct)
            r'([\d]+[.,]\d{3})\s*(?:,?\s*montant\s*(?:à|a)\s*payer)',
            # STEG : "595.000  QUINZE DINARS ... Montant à payer" (amount in words between)
            r'([\d]+[.,]\d{3})\s+[A-ZÀ-Ÿ\s.,\-]+(?:dinars?|millimes?)[A-ZÀ-Ÿ\s.,\-]*montant\s*(?:à|a)\s*payer',
            # Plus souple
            r'montant\s*(?:à|a)\s*payer[:\s]*([\d.,]+)',
            r'([\d]+[.,]\d{3})\s+montant\s*(?:à|a)\s*payer',
        ]
        for pat in patterns:
            m = re.search(pat, self.text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except ValueError:
                    continue
        return None

    def _extract_bulletin_versement(self) -> Optional[float]:
        pied = self.zones.pied or self.text
        patterns = [
            r'bulletin\s*de\s*versement.*?(\d+[.,]\d{3})\s*montant',
            r'bulletin\s*de\s*versement.*?montant[:\s]*([\d.,]+)',
            r'(\d+[.,]\d{3})\s+montant\b',
        ]
        for pat in patterns:
            m = re.search(pat, pied, re.IGNORECASE | re.DOTALL)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except ValueError:
                    continue
        return None

    def _extract_montant_total(self) -> Optional[float]:
        patterns = [
            r'montant\s*total[:\s]*([\d]+[.,]\d+)',
            r'([\d]+[.,]\d+)\s*montant\s*total',
        ]
        for pat in patterns:
            m = re.search(pat, self.text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except ValueError:
                    continue
        return None

    def extract_period(self) -> Optional[str]:
        """STEG : dates ISO inversées (RTL)."""
        dates = re.findall(r'(\d{4}-\d{2}-\d{2})', self.text)
        if len(dates) >= 2:
            unique = sorted(set(dates))
            if len(unique) >= 2:
                return f"{unique[0]} au {unique[1]}"
            s = sorted(dates[:2])
            return f"{s[0]} au {s[1]}"
        return _extract_period_generic(self.text)

    def extract_reference(self) -> Tuple[Optional[str], Optional[str]]:
        """STEG : 'Référence : 07283 740 0', District, Poste."""
        ref_facture = None
        ref_client = None

        m = re.search(r'[Rr][ée]f[ée]rence\s*:\s*([\d\s]+\d)', self.text)
        if m:
            ref_facture = re.sub(r'\s+', '', m.group(1).strip())

        m = re.search(r'(?:d[ée]pannage|district)\s*[:\s]*(\d+)', self.text, re.IGNORECASE)
        if m:
            ref_client = m.group(1).strip()
        elif not ref_client:
            m = re.search(r'poste\s*(\d+)', self.text, re.IGNORECASE)
            if m:
                ref_client = f"Poste {m.group(1)}"

        return ref_facture, ref_client


# ─────────────────────────────────────────────────────────────────────────────
#  SONEDE Strategy
# ─────────────────────────────────────────────────────────────────────────────

class SONEDEStrategy(ExtractionStrategy):
    """Extraction spécialisée SONEDE (eau, Tunisie)."""

    def extract_consumption(self) -> List[DonneeEnvironnementale]:
        donnees: List[DonneeEnvironnementale] = []

        for m in re.finditer(r'(\d[\d\s.,]*)\s*(m[³3]|m\s*cube)', self.text, re.IGNORECASE):
            val = m.group(1).replace(" ", "").replace(",", ".")
            try:
                fv = float(val)
                if 0.1 <= fv <= 100_000:
                    donnees.append(DonneeEnvironnementale(
                        champ="Volume consommé (eau)", valeur=val, unite="m³", confiance=0.85
                    ))
                    break
            except ValueError:
                continue

        if not donnees:
            m = re.search(r'consommation[:\s]*(\d+)', self.text, re.IGNORECASE)
            if m:
                val = m.group(1)
                if 1 <= int(val) <= 100_000:
                    donnees.append(DonneeEnvironnementale(
                        champ="Volume consommé (eau)", valeur=val, unite="m³", confiance=0.70
                    ))

        if not donnees:
            idx = re.search(
                r'(\d{3,8})\s+(\d{3,8})\s+(?:ancien|consommation)',
                self.text, re.IGNORECASE
            )
            if idx:
                diff = abs(int(idx.group(1)) - int(idx.group(2)))
                if 1 <= diff <= 100_000:
                    donnees.append(DonneeEnvironnementale(
                        champ="Volume consommé (eau)", valeur=str(diff), unite="m³", confiance=0.75
                    ))

        return donnees

    def extract_amounts(self) -> List[DonneeEnvironnementale]:
        return _extract_amounts_generic(self.text)


# ─────────────────────────────────────────────────────────────────────────────
#  Generic Strategy
# ─────────────────────────────────────────────────────────────────────────────

class GenericStrategy(ExtractionStrategy):
    """Extraction générique pour tout type de facture."""

    def extract_consumption(self) -> List[DonneeEnvironnementale]:
        return _extract_consumption_generic(self.text, self.text_lower)

    def extract_amounts(self) -> List[DonneeEnvironnementale]:
        return _extract_amounts_generic(self.text)


# ═══════════════════════════════════════════════════════════════════════════════
#  FONCTIONS D'EXTRACTION GÉNÉRIQUE (shared)
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_all_types(text_lower: str) -> List[str]:
    """Détecte TOUS les types d'énergie présents (pas seulement le principal)."""
    scores: Dict[str, int] = {}
    for type_name, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[type_name] = score

    if not scores:
        return ["inconnu"]

    sorted_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    max_score = sorted_types[0][1]
    threshold = max(1, max_score * 0.3)
    return [t for t, s in sorted_types if s >= threshold]


def _detect_fournisseur(text_lower: str) -> Optional[str]:
    """Détecte le fournisseur avec scoring pondéré."""
    fournisseurs = {
        "STEG": {
            "keywords": [
                "steg",
                "société tunisienne de l'électricité et du gaz",
                "societe tunisienne de l electricite",
                "société tunisienne du gaz",
                "societe tunisienne du gaz",
                "tunisienne du gaz",
                "tunisienne de l'électricité",
                "tunisienne de l electricite",
                "الشركة التونسية للكهرباء",
                "الشركة التونسية للكهرباء والغاز",
                "للكهرباء والغاز",
                "للهرباء",
            ],
            "weight": 1.0,
        },
        "SONEDE": {
            "keywords": [
                "eau",
                "sonede",
                "société nationale d'exploitation et de distribution des eaux",
                "societe nationale d exploitation",
                "distribution des eaux",
                "الشركة الوطنية لاستغلال وتوزيع المياه",
                "توزيع المياه",
            ],
            "weight": 1.0,
        },
        "EDF": {
            "keywords": ["edf", "électricité de france", "electricite de france"],
            "weight": 1.0,
        },
        "Engie": {
            "keywords": ["engie"],
            "weight": 1.0,
        },
        "TotalEnergies": {
            "keywords": ["totalenergies", "total energies"],
            "weight": 1.0,
        },
        "Shell": {
            "keywords": ["shell energy", "shell"],
            "weight": 0.8,
        },
    }
    best_name = None
    best_score = 0.0
    for name, config in fournisseurs.items():
        score = sum(config["weight"] for kw in config["keywords"] if kw in text_lower)
        if score > best_score:
            best_score = score
            best_name = name
    return best_name if best_score > 0 else None


def _extract_period_generic(text: str) -> Optional[str]:
    period_patterns = [
        r'(?:du|from|période|periode)\s*[:\s]?\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\s*(?:au|to|[-–])\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})',
        r'(?:من)\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\s*(?:إلى|الى)\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})',
        r'(\d{4}-\d{2}-\d{2})\s*[:\s]*(?:إل[يى]|à|au|to)?\s*[:\s]*(\d{4}-\d{2}-\d{2})',
        r'((?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|january|february|march|april|may|june|july|august|september|october|november|december|جانفي|فيفري|مارس|أفريل|ماي|جوان|جويلية|أوت|سبتمبر|أكتوبر|نوفمبر|ديسمبر)\s+\d{4})',
        r'(\d{1,2}[/\-]\d{4})\s*[-–à]\s*(\d{1,2}[/\-]\d{4})',
        r'((?:T[1-4]|Q[1-4]|trimestre\s*\d)\s*\d{4})',
    ]
    for pat in period_patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            groups = [g for g in match.groups() if g]
            if len(groups) > 1:
                sorted_dates = sorted(groups[:2])
                return f"{sorted_dates[0]} au {sorted_dates[1]}"
            return groups[0]
    return None


def _extract_references_generic(text: str, text_lower: str) -> Tuple[Optional[str], Optional[str]]:
    ref_facture = None
    ref_client = None

    for pat in [
        r'(?:r[ée]f[ée]rence|n[°o\.]\s*facture|invoice\s*(?:no?|#)|رقم\s*الفاتورة)\s*[:\s]*([\w\d/\-]+)',
        r'(?:facture\s*n[°o])\s*[:\s]*([\w\d/\-]+)',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            ref_facture = m.group(1).strip()
            break

    for pat in [
        r'(?:r[ée]f[ée]rence\s*client|n[°o\.]\s*client|n[°o\.]\s*abonn[ée]|n[°o\.]\s*compteur|customer\s*(?:no?|#|ref)|رقم\s*العميل|رقم\s*المشترك)\s*[:\s]*([\w\d/\-]+)',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            ref_client = m.group(1).strip()
            break

    return ref_facture, ref_client


def _extract_address_generic(text: str) -> Optional[str]:
    for pat in [
        r'(?:adresse|address|عنوان)\s*[:\s]*(.{10,80}?)(?:\n|$)',
        r'((?:نهج|شارع|حي|طريق)\s+.{5,60})',
        r'((?:rue|avenue|boulevard|bd|impasse)\s+.{5,60})',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            addr = re.sub(r'\s+', ' ', m.group(0 if '(' not in pat[:5] else 1).strip())
            if len(addr) > 10:
                return addr
    return None


def _extract_consumption_generic(text: str, text_lower: str) -> List[DonneeEnvironnementale]:
    donnees: List[DonneeEnvironnementale] = []

    # ── kWh ──
    kwh_patterns = [
        r'([\d\s.,]+)\s*(kwh)',
        r'consommation[:\s]*(?:de\s+)?([\d\s.,]+)\s*(kwh)',
        r'[ée]nergie[:\s]*(?:consomm[ée]e)?[:\s]*([\d\s.,]+)\s*(kwh)',
        r'total[:\s]*([\d\s.,]+)\s*(kwh)',
        r'quantit[ée]\s*(?:\(\d\))?\s+(\d+)',
        r'استهلاك[:\s]*([\d\s.,]+)\s*(kwh|ك\.?و\.?س|كيلوواط)',
        r'طاقة[:\s]*([\d\s.,]+)\s*(kwh|ك\.?و\.?س)',
        r'([\d\s.,]+)\s*(ك\.?و\.?س|كيلوواط)',
    ]
    seen_kwh: set = set()
    for pat in kwh_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            try:
                fv = float(val)
                if fv < 1 or fv > 100_000:
                    continue
            except ValueError:
                continue
            if val not in seen_kwh:
                seen_kwh.add(val)
                donnees.append(DonneeEnvironnementale(
                    champ="Énergie consommée", valeur=val, unite="kWh", confiance=0.80
                ))

    # ── m³ ──
    m3_patterns = [
        r'([\d\s.,]+)\s*(m[³3]|m\s*cube)',
        r'consommation[:\s]*(?:de\s+)?([\d\s.,]+)\s*(m[³3])',
        r'volume[:\s]*([\d\s.,]+)\s*(m[³3])',
        r'([\d\s.,]+)\s*(متر\s*مكعب|م[³3])',
        r'استهلاك[:\s]*([\d\s.,]+)\s*(م[³3]|m[³3])',
    ]
    seen_m3: set = set()
    for pat in m3_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            try:
                float(val)
            except ValueError:
                continue
            if val not in seen_m3:
                seen_m3.add(val)
                label = "Volume consommé (eau)" if ("eau" in text_lower or "sonede" in text_lower) else "Volume consommé (gaz)"
                donnees.append(DonneeEnvironnementale(
                    champ=label, valeur=val, unite="m³", confiance=0.80
                ))

    # ── Litres ──
    litre_patterns = [
        r'([\d\s.,]+)\s*(litres?|l\b)',
        r'quantit[ée][:\s]*([\d\s.,]+)\s*(litres?|l\b)',
        r'volume[:\s]*([\d\s.,]+)\s*(litres?|l\b)',
        r'([\d\s.,]+)\s*(لتر)',
        r'كمية[:\s]*([\d\s.,]+)\s*(لتر|litres?)',
    ]
    seen_l: set = set()
    for pat in litre_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            try:
                fv = float(val)
                if fv < 0.1 or fv > 100_000:
                    continue
            except ValueError:
                continue
            if val not in seen_l:
                seen_l.add(val)
                donnees.append(DonneeEnvironnementale(
                    champ="Volume carburant", valeur=val, unite="litres", confiance=0.75
                ))

    return donnees


def _extract_amounts_generic(text: str) -> List[DonneeEnvironnementale]:
    donnees: List[DonneeEnvironnementale] = []

    amount_patterns = [
        (r'montant\s*(?:à|a)\s*payer[:\s]*([\d\s.,]+)', "Montant à payer", 0.90),
        (r'([\d]+[.,]\d{3})\s+montant\s*(?:à|a)\s*payer', "Montant à payer", 0.90),
        (r'net\s*(?:à|a)\s*payer[:\s]*([\d\s.,]+)\s*(dt|tnd|€|eur|dinars?|euros?)', "Net à payer", 0.85),
        (r'total\s*ttc[:\s]*([\d\s.,]+)\s*(dt|tnd|€|eur|dinars?|euros?|\$|usd)', "Total TTC", 0.85),
        (r'montant\s*total[:\s]*([\d\s.,]*\d)', "Montant total", 0.80),
        (r'montant[:\s]*([\d\s.,]+)\s*(dt|tnd|€|eur|dinars?|euros?|\$|usd)', "Montant facture", 0.75),
        (r'total[:\s]*([\d\s.,]+)\s*(dt|tnd|€|eur|dinars?|euros?|\$|usd)', "Total", 0.70),
        (r'المبلغ[:\s]*([\d\s.,]+)\s*(د\.?ت|dt|tnd|دينار)', "Montant (المبلغ)", 0.80),
        (r'المجموع[:\s]*([\d\s.,]+)\s*(د\.?ت|dt|tnd|دينار)', "Total (المجموع)", 0.75),
        (r'الصافي[:\s]*([\d\s.,]+)\s*(د\.?ت|dt|tnd|دينار)', "Net (الصافي)", 0.85),
    ]

    seen: set = set()
    for pat, champ, conf in amount_patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            val = match.group(1).replace(" ", "").replace(",", ".")
            try:
                fval = float(val)
                if fval < 0.01:
                    continue
            except ValueError:
                continue
            if val not in seen:
                seen.add(val)
                devise = _detect_devise(match.groups())
                donnees.append(DonneeEnvironnementale(
                    champ=champ, valeur=val, unite=devise, confiance=conf
                ))
    return donnees


def _detect_devise(groups: tuple) -> str:
    for g in groups:
        if not g:
            continue
        gl = g.lower().strip()
        if gl in ("€", "eur", "euros", "euro"):
            return "EUR"
        if gl in ("$", "usd"):
            return "USD"
        if gl in ("dt", "tnd", "dinar", "dinars", "د.ت", "دت", "دينار"):
            return "DT"
    return "DT"


def _extract_co2_direct(text: str) -> List[DonneeEnvironnementale]:
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
            unite = "tonnes CO₂" if unite in ("t", "tonnes", "tonne") else "kg CO₂"
            try:
                float(val)
            except ValueError:
                continue
            donnees.append(DonneeEnvironnementale(
                champ="Émissions CO₂ (déclarées)", valeur=val, unite=unite, confiance=0.90
            ))
    return donnees


# ═══════════════════════════════════════════════════════════════════════════════
#  CALCUL CO₂ COMBINÉ
# ═══════════════════════════════════════════════════════════════════════════════

def _calculate_co2_combined(
    donnees: List[DonneeEnvironnementale],
    types_energie: List[str],
) -> Tuple[Optional[float], List[Dict[str, Any]], Optional[str], Optional[str]]:
    """Calcule CO₂ pour CHAQUE type d'énergie séparément."""
    total_co2 = 0.0
    detail: List[Dict[str, Any]] = []

    for d in donnees:
        try:
            val = float(d.valeur) if d.valeur else 0
        except (ValueError, TypeError):
            continue
        if val <= 0:
            continue

        # Électricité (kWh)
        if d.unite == "kWh" and "electricite" in types_energie:
            f = EMISSION_FACTORS["electricite"]
            co2 = val * f["facteur_kg_co2_par_kwh"]
            detail.append({
                "type": "electricite", "consommation": val, "unite": "kWh",
                "facteur": f["facteur_kg_co2_par_kwh"],
                "co2_kg": round(co2, 3), "source": f["source"],
            })
            total_co2 += co2

        # Gaz (m³)
        elif d.unite == "m³" and "gaz" in d.champ.lower():
            f = EMISSION_FACTORS["gaz_naturel"]
            co2 = val * f["facteur_kg_co2_par_m3"]
            detail.append({
                "type": "gaz_naturel", "consommation": val, "unite": "m³",
                "facteur": f["facteur_kg_co2_par_m3"],
                "co2_kg": round(co2, 3), "source": f["source"],
            })
            total_co2 += co2

        # Eau (m³)
        elif d.unite == "m³" and "eau" in d.champ.lower():
            f = EMISSION_FACTORS["eau"]
            co2 = val * f["facteur_kg_co2_par_m3"]
            detail.append({
                "type": "eau", "consommation": val, "unite": "m³",
                "facteur": f["facteur_kg_co2_par_m3"],
                "co2_kg": round(co2, 3), "source": f["source"],
            })
            total_co2 += co2

        # Carburant (litres)
        elif d.unite == "litres":
            carb_type = "essence"
            for t in types_energie:
                if t in ("essence", "diesel", "gpl"):
                    carb_type = t
                    break
            f = EMISSION_FACTORS[carb_type]
            co2 = val * f["facteur_kg_co2_par_litre"]
            detail.append({
                "type": carb_type, "consommation": val, "unite": "litres",
                "facteur": f["facteur_kg_co2_par_litre"],
                "co2_kg": round(co2, 3), "source": f["source"],
            })
            total_co2 += co2

    if total_co2 == 0:
        return None, [], None, None

    sources = list({d["source"] for d in detail})
    facteurs = " + ".join(f'{d["facteur"]} kg CO₂/{d["unite"]}' for d in detail)
    return round(total_co2, 3), detail, facteurs, " ; ".join(sources)


# ═══════════════════════════════════════════════════════════════════════════════
#  VALIDATION CROISÉE & SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def _cross_validate(result: ResultatExtraction) -> List[str]:
    """Valide la cohérence entre données extraites."""
    alertes: List[str] = []

    conso_kwh = None
    montant_payer = None
    montant_total = None

    for d in result.donnees:
        try:
            val = float(d.valeur) if d.valeur else 0
        except (ValueError, TypeError):
            continue
        if d.unite == "kWh" and val > 0:
            conso_kwh = val
        elif "payer" in d.champ.lower() and val > 0:
            montant_payer = val
        elif d.champ.lower() in ("montant total", "montant total ht", "total ttc") and d.unite in ("DT", "EUR", "USD") and val > 0:
            montant_total = val

    # Use best available montant for validation
    best_montant = montant_payer or montant_total

    # Montant à payer < 50% du montant total ?
    if montant_payer and montant_total:
        if montant_payer < montant_total * 0.5:
            alertes.append(
                f"⚠ Montant à payer ({montant_payer}) < 50% du montant total ({montant_total})"
            )

    # Tarif estimé hors plage
    if conso_kwh and best_montant:
        tarif = best_montant / conso_kwh
        if tarif < 0.05 or tarif > 2.0:
            alertes.append(
                f"⚠ Tarif estimé ({tarif:.3f} DT/kWh) hors plage [0.05-2.0]"
            )

    # CO₂ aberrant
    if result.emission_co2_kg is not None:
        if result.emission_co2_kg > 50_000:
            alertes.append(f"⚠ Émissions CO₂ ({result.emission_co2_kg:.0f} kg) anormalement élevées")
        elif result.emission_co2_kg < 0.1 and conso_kwh and conso_kwh > 10:
            alertes.append(f"⚠ Émissions CO₂ trop basses pour {conso_kwh} kWh")

    # Période cohérente
    if result.periode:
        dates = re.findall(r'(\d{4})-(\d{2})-(\d{2})', result.periode)
        if len(dates) >= 2:
            try:
                d1 = datetime(int(dates[0][0]), int(dates[0][1]), int(dates[0][2]))
                d2 = datetime(int(dates[1][0]), int(dates[1][1]), int(dates[1][2]))
                delta = (d2 - d1).days
                if delta < 0:
                    alertes.append("⚠ Période inversée")
                elif delta > 365:
                    alertes.append(f"⚠ Période longue ({delta} jours)")
                elif delta < 7:
                    alertes.append(f"⚠ Période courte ({delta} jours)")
            except ValueError:
                pass

    return alertes


def _calculate_global_score(result: ResultatExtraction) -> float:
    """Score de confiance global 0-1."""
    score = 0.0
    weights = {
        "type": 0.10, "fournisseur": 0.10, "periode": 0.10,
        "conso": 0.25, "montant": 0.15, "co2": 0.15,
        "ref": 0.05, "clean": 0.10,
    }

    if result.type_facture != "inconnu":
        score += weights["type"]
    if result.fournisseur:
        score += weights["fournisseur"]
    if result.periode:
        score += weights["periode"]
    if any(d.unite in ("kWh", "m³", "litres") for d in result.donnees):
        conso_conf = max(
            (d.confiance for d in result.donnees if d.unite in ("kWh", "m³", "litres")),
            default=0
        )
        score += weights["conso"] * conso_conf
    if any("montant" in d.champ.lower() or "payer" in d.champ.lower() for d in result.donnees):
        score += weights["montant"]
    if result.emission_co2_kg is not None:
        score += weights["co2"]
    if result.reference_facture or result.reference_client:
        score += weights["ref"]
    if not result.alertes:
        score += weights["clean"]

    return round(min(1.0, score), 2)


# ═══════════════════════════════════════════════════════════════════════════════
#  FONCTION PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

def extraire_donnees_environnementales(texte_ocr: str) -> ResultatExtraction:
    """
    Pipeline v2 :
      1. Normaliser → 2. Fournisseur → 3. Zones → 4. Stratégie
      5. CO₂ combiné → 6. Cross-validation → 7. Score → 8. Résumé
    """
    if not texte_ocr or not texte_ocr.strip():
        return ResultatExtraction(resume="Aucun texte à analyser.")

    text_lower = _normalize(texte_ocr)
    result = ResultatExtraction()

    # 1. Fournisseur
    result.fournisseur = _detect_fournisseur(text_lower)

    # 2. Types d'énergie (TOUS)
    result.types_energie = _detect_all_types(text_lower)
    result.type_facture = result.types_energie[0] if result.types_energie else "inconnu"

    # 3. Zones + Stratégie
    if result.fournisseur == "STEG":
        zones = _parse_zones_steg(texte_ocr, text_lower)
        strategy: ExtractionStrategy = STEGStrategy(zones)
    elif result.fournisseur == "SONEDE":
        zones = _parse_zones_sonede(texte_ocr, text_lower)
        strategy = SONEDEStrategy(zones)
    else:
        zones = _parse_zones_generic(texte_ocr, text_lower)
        strategy = GenericStrategy(zones)

    # 4. Extraction structurée
    result.periode = strategy.extract_period()

    ref_f, ref_c = strategy.extract_reference()
    result.reference_facture = ref_f
    result.reference_client = ref_c

    result.adresse = strategy.extract_address()

    energy_labels = _get_energy_labels(result.types_energie)
    if energy_labels:
        result.donnees.append(DonneeEnvironnementale(
            champ="Type d'énergie", valeur=energy_labels, confiance=0.90
        ))

    result.donnees.extend(strategy.extract_consumption())
    result.donnees.extend(strategy.extract_amounts())
    result.donnees.extend(_extract_co2_direct(texte_ocr))

    # 5. CO₂ combiné
    has_declared = any("déclar" in d.champ.lower() for d in result.donnees)
    total_co2, detail, facteur_str, source = _calculate_co2_combined(
        result.donnees, result.types_energie
    )

    if total_co2 is not None:
        result.emission_co2_kg = total_co2
        result.facteur_emission_utilise = facteur_str
        result.source_facteur = source
        result.detail_co2 = detail

        if not has_declared:
            for d_info in detail:
                result.donnees.append(DonneeEnvironnementale(
                    champ=f"Émissions CO₂ ({d_info['type']})",
                    valeur=str(d_info["co2_kg"]),
                    unite="kg CO₂",
                    confiance=0.75,
                ))

    # 6. Cross-validation
    result.alertes = _cross_validate(result)

    # 7. Score global
    result.score_global = _calculate_global_score(result)

    # 8. Résumé
    result.resume = _generer_resume(result)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS & RÉSUMÉ
# ═══════════════════════════════════════════════════════════════════════════════

def _get_energy_labels(types: List[str]) -> str:
    labels = {
        "electricite": "Électricité (réseau)",
        "gaz_naturel": "Gaz naturel",
        "eau": "Eau potable (+ assainissement)",
        "essence": "Essence (carburant)",
        "diesel": "Diesel / Gasoil",
        "gpl": "GPL",
        "inconnu": "Non identifié",
    }
    parts = [labels.get(t, t) for t in types if t != "inconnu"]
    return " + ".join(parts) if parts else "Non identifié"


def _generer_resume(r: ResultatExtraction) -> str:
    lines: List[str] = []

    type_labels = {
        "electricite": "[ELEC]", "gaz_naturel": "[GAZ]",
        "eau": "[EAU]", "essence": "[CARB]", "diesel": "[CARB]",
        "gpl": "[GPL]", "inconnu": "[?]",
    }

    tags = [type_labels.get(t, f"[{t.upper()}]") for t in r.types_energie]
    lines.append(f"{' '.join(tags)} Facture {_get_energy_labels(r.types_energie)}")

    if r.fournisseur:
        lines.append(f"Fournisseur : {r.fournisseur}")
    if r.periode:
        lines.append(f"Periode : {r.periode}")
    if r.reference_facture:
        lines.append(f"Reference : {r.reference_facture}")
    if r.reference_client:
        lines.append(f"Client/District : {r.reference_client}")
    if r.adresse:
        lines.append(f"Adresse : {r.adresse}")

    lines.append("")

    for d in r.donnees:
        if d.valeur:
            u = f" {d.unite}" if d.unite else ""
            conf = f" [{d.confiance*100:.0f}%]"
            lines.append(f"  • {d.champ} : {d.valeur}{u}{conf}")

    if r.detail_co2:
        lines.append("")
        lines.append("─── Bilan CO₂ détaillé ───")
        for info in r.detail_co2:
            lines.append(
                f"  {info['type']:15s} : {info['consommation']:>8} {info['unite']:5s} "
                f"× {info['facteur']} = {info['co2_kg']:.3f} kg CO₂"
            )
        if r.emission_co2_kg is not None:
            lines.append(
                f"  {'TOTAL':15s} : {r.emission_co2_kg:.3f} kg CO₂ "
                f"({r.emission_co2_kg / 1000:.4f} tonnes)"
            )
    elif r.emission_co2_kg is not None:
        lines.append(f"\nEmissions CO₂ : {r.emission_co2_kg:.3f} kg ({r.emission_co2_kg / 1000:.4f} tonnes)")
    else:
        lines.append("\nImpossible de calculer les emissions CO₂ (donnees insuffisantes).")

    if r.facteur_emission_utilise:
        lines.append(f"   Facteur(s) : {r.facteur_emission_utilise}")
    if r.source_facteur:
        lines.append(f"   Source(s) : {r.source_facteur}")

    if r.alertes:
        lines.append("")
        lines.append("─── Alertes ───")
        for a in r.alertes:
            lines.append(f"  {a}")

    lines.append(f"\nScore de confiance global : {r.score_global*100:.0f}%")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

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
