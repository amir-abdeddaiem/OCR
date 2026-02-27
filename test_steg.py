"""Test STEG bill extraction."""
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

from extractor import extraire_donnees_environnementales

text = """Societe Tunisienne de l Electricite et du Gaz STEG Facture ref 218077560
Facture N Depannage 71978966 District KRAM FACTURE INTERMEDIAIRE
2022-08-05 : 2022-06-03 :
CONSOMMATION & SERVICES
Quantite (1) 400 15441 15441 2 7 27091100026667 ECLAIRAGE
19 9.800 0.700 Redevances Fixes ECLAIRAGE
80.200 Total Electricite
Gaz
19 85.624 0.556 154 4310 4310 2 5 SG14040520S607 GAZ-NATUR
1.500 0.150 2 5 SG14040520S607 GAZ-NATUR
87.124 Total Gaz
0 Total Services
167.324 Total Consommation & Services
200.862 MONTANT TOTAL
777.000 Montant a payer SEPT CENTS SOIXANTE-DIX-SEPT DINARS ZERO MLLIMES TTC
2022-08-29  2022-10-04"""

r = extraire_donnees_environnementales(text)
d = r.to_dict()

print("=" * 50)
print("RESULTATS EXTRACTION STEG")
print("=" * 50)
print(f"Type     : {d['type_facture']}")
print(f"Fourn.   : {d['fournisseur']}")
print(f"Periode  : {d['periode']}")
print()
for x in d['donnees']:
    u = x['unite'] or ''
    print(f"  {x['champ']}: {x['valeur']} {u}  (confiance: {x['confiance']})")
print()
print(f"CO2      : {d['emission_co2_kg']} kg")
print(f"Facteur  : {d['facteur_emission_utilise']}")
print(f"Types    : {d.get('types_energie', [])}")
print(f"Ref      : {d.get('reference_facture')}")
print(f"Client   : {d.get('reference_client')}")
print(f"Score    : {d.get('score_global')}")
print(f"Alertes  : {d.get('alertes', [])}")
if d.get('detail_co2'):
    print("\n─── Détail CO₂ ───")
    for c in d['detail_co2']:
        print(f"  {c['type']:15s} : {c['consommation']} {c['unite']} × {c['facteur']} = {c['co2_kg']} kg")
