"use client";

import { useState, useRef, useCallback } from "react";

/* ── Types ── */
type Donnee = {
  champ: string;
  valeur: string | null;
  unite: string | null;
  confiance: number;
};

type CO2Detail = {
  type: string;
  consommation: number;
  unite: string;
  facteur: number;
  co2_kg: number;
  source: string;
};

type CarbonResult = {
  filename: string;
  type_facture: string;
  fournisseur: string | null;
  periode: string | null;
  donnees: Donnee[];
  emission_co2_kg: number | null;
  facteur_emission_utilise: string | null;
  source_facteur: string | null;
  resume: string;
  texte_ocr_brut: string;
  // v2 fields
  reference_facture: string | null;
  reference_client: string | null;
  adresse: string | null;
  types_energie: string[];
  detail_co2: CO2Detail[];
  score_global: number;
  alertes: string[];
};

/* ── Helpers ── */
const TYPE_LABELS: Record<string, string> = {
  electricite: "Electricite",
  gaz_naturel: "Gaz naturel",
  eau: "Eau",
  essence: "Essence",
  diesel: "Diesel",
  gpl: "GPL",
  inconnu: "Non identifie",
};

const TYPE_ICONS: Record<string, string> = {
  electricite: "⚡", gaz_naturel: "🔥", eau: "💧",
  essence: "⛽", diesel: "⛽", gpl: "🛢️", inconnu: "❓",
};

/** Detect if a string contains Arabic characters → apply RTL */
function hasArabic(text: string | null | undefined): boolean {
  if (!text) return false;
  return /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]/.test(text);
}

function dirFor(text: string | null | undefined): "rtl" | "ltr" {
  return hasArabic(text) ? "rtl" : "ltr";
}

const TYPE_COLORS: Record<string, string> = {
  electricite: "from-yellow-500 to-amber-600",
  gaz_naturel: "from-blue-500 to-cyan-600",
  eau: "from-sky-500 to-blue-600",
  essence: "from-red-500 to-orange-600",
  diesel: "from-gray-500 to-zinc-600",
  gpl: "from-purple-500 to-violet-600",
  inconnu: "from-slate-500 to-slate-600",
};

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 80 ? "bg-green-500" : pct >= 50 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-2 rounded-full bg-slate-700 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-400">{pct}%</span>
    </div>
  );
}

/* ── Helper: find a donnee by matching champ keywords ── */
function findDonnee(donnees: Donnee[], ...keywords: string[]): Donnee | undefined {
  const lowerKw = keywords.map((k) => k.toLowerCase());
  return donnees.find((d) => {
    const champ = (d.champ || "").toLowerCase();
    return lowerKw.some((kw) => champ.includes(kw));
  });
}

/* ── Resume Table component (v2) ── */
function ResumeTable({ result }: { result: CarbonResult }) {
  const conso = findDonnee(result.donnees, "consomm", "énergie consommée", "energie", "consumption", "استهلاك");
  const montant = findDonnee(result.donnees, "payer", "montant", "amount", "المبلغ");

  // Build energy type display with icons
  const typesDisplay = (result.types_energie || [result.type_facture])
    .map(t => `${TYPE_ICONS[t] || ""} ${TYPE_LABELS[t] || t}`.trim())
    .join(" + ") || "—";

  const rows: { label: string; value: string; highlight?: boolean; suffix?: string; small?: boolean }[] = [
    { label: "Type", value: typesDisplay },
    { label: "Fournisseur", value: result.fournisseur || "—" },
    { label: "Periode", value: result.periode || "—" },
    { label: "Reference", value: result.reference_facture || "—", small: true },
    { label: "Client", value: result.reference_client || "—", small: true },
    {
      label: "Consommation",
      value: conso?.valeur ? `${conso.valeur} ${conso.unite || ""}`.trim() : "—",
      highlight: !!conso?.valeur,
    },
    {
      label: "Montant",
      value: montant?.valeur ? `${montant.valeur} ${montant.unite || ""}`.trim() : "—",
    },
    {
      label: "CO₂ total",
      value: result.emission_co2_kg != null ? `${result.emission_co2_kg.toFixed(2)} kg` : "—",
      highlight: result.emission_co2_kg != null,
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold">Resume</h3>
        {result.score_global != null && (
          <span className={`text-xs font-semibold px-2 py-1 rounded-full ${
            result.score_global >= 0.8 ? "bg-emerald-500/20 text-emerald-400"
            : result.score_global >= 0.5 ? "bg-yellow-500/20 text-yellow-400"
            : "bg-red-500/20 text-red-400"
          }`}>
            Confiance {Math.round(result.score_global * 100)}%
          </span>
        )}
      </div>
      <div className="rounded-xl bg-slate-800/60 border border-slate-700 overflow-hidden">
        <table className="w-full text-sm">
          <tbody>
            {rows.filter(r => !r.small || r.value !== "—").map((row, i) => (
              <tr key={row.label} className="border-b border-slate-700/50 last:border-0">
                <td className="px-5 py-3 text-slate-400 font-medium w-44">{row.label}</td>
                <td
                  className={`px-5 py-3 ${row.highlight ? "text-emerald-400 font-bold" : "text-slate-200"}`}
                  dir={dirFor(row.value)}
                >
                  {row.value}
                  {row.suffix && <span className="text-xs text-slate-500 ml-2 font-normal">{row.suffix}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── CO₂ Detail Breakdown ── */
function CO2DetailTable({ detail }: { detail: CO2Detail[] }) {
  if (!detail || detail.length === 0) return null;
  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Detail CO₂ par energie</h3>
      <div className="rounded-xl bg-slate-800/60 border border-slate-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400">
              <th className="px-4 py-2 text-left">Energie</th>
              <th className="px-4 py-2 text-right">Conso.</th>
              <th className="px-4 py-2 text-right">Facteur</th>
              <th className="px-4 py-2 text-right font-bold text-emerald-400">CO₂</th>
            </tr>
          </thead>
          <tbody>
            {detail.map((d, i) => (
              <tr key={i} className="border-b border-slate-700/50 last:border-0">
                <td className="px-4 py-2">{TYPE_ICONS[d.type] || ""} {TYPE_LABELS[d.type] || d.type}</td>
                <td className="px-4 py-2 text-right">{d.consommation} {d.unite}</td>
                <td className="px-4 py-2 text-right text-slate-400">{d.facteur} kg/{d.unite}</td>
                <td className="px-4 py-2 text-right font-bold text-emerald-400">{d.co2_kg.toFixed(3)} kg</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500 mt-1">
        {detail.map(d => d.source).filter((v, i, a) => a.indexOf(v) === i).join(" | ")}
      </p>
    </div>
  );
}

/* ── Alertes ── */
function AlertesPanel({ alertes }: { alertes: string[] }) {
  if (!alertes || alertes.length === 0) return null;
  return (
    <div className="rounded-xl bg-amber-500/10 border border-amber-500/30 px-5 py-4">
      <p className="font-semibold text-amber-400 mb-2">Alertes de validation</p>
      <ul className="space-y-1 text-sm text-amber-300">
        {alertes.map((a, i) => <li key={i}>{a}</li>)}
      </ul>
    </div>
  );
}

/* ── Main ── */
export default function Home() {
  const [result, setResult] = useState<CarbonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const processFile = useCallback(async (file: File) => {
    setLoading(true);
    setError(null);
    setResult(null);
    setShowRaw(false);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/ocr", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Erreur lors du traitement.");
        return;
      }
      setResult(data);
    } catch {
      setError("Impossible de contacter le serveur.");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) processFile(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) processFile(file);
  };

  const handleDownloadJSON = () => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (result.filename || "resultat").replace(/\.[^.]+$/, "_carbone.json");
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white">
      <div className="max-w-4xl mx-auto px-4 py-10">
        {/* ── Header ── */}
        <div className="text-center mb-10">
          <h1 className="text-4xl font-bold mb-2">
            Bilan <span className="text-emerald-400">Carbone</span>
          </h1>
          <p className="text-slate-400 max-w-xl mx-auto">
            Deposez une facture (electricite, gaz, eau, carburant...) et obtenez
            une extraction intelligente des donnees environnementales + estimation
            des emissions CO2.
          </p>
        </div>

        {/* ── Upload Zone ── */}
        <div
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={`
            cursor-pointer rounded-2xl border-2 border-dashed p-10 text-center
            transition-all duration-200
            ${dragOver
              ? "border-emerald-400 bg-emerald-400/10 scale-[1.02]"
              : "border-slate-600 hover:border-emerald-400/60 hover:bg-slate-800/40"
            }
          `}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff"
            className="hidden"
            onChange={handleFileSelect}
          />
          {loading ? (
            <div className="flex flex-col items-center gap-3">
              <div className="h-10 w-10 rounded-full border-4 border-emerald-400 border-t-transparent animate-spin" />
              <p className="text-emerald-300 font-medium">Analyse de la facture...</p>
            </div>
          ) : (
            <>
              <div className="text-5xl mb-3">+</div>
              <p className="text-lg font-medium text-slate-300">
                Cliquez ou glissez une facture ici
              </p>
              <p className="text-sm text-slate-500 mt-1">PDF, JPG, PNG, WEBP, BMP, TIFF</p>
            </>
          )}
        </div>

        {/* ── Erreur ── */}
        {error && (
          <div className="mt-6 rounded-xl bg-red-500/10 border border-red-500/30 p-4 text-red-300">
            <p className="font-semibold">Erreur</p>
            <p className="text-sm mt-1">{error}</p>
          </div>
        )}

        {/* ── Resultats ── */}
        {result && (
          <div className="mt-8 space-y-6">
            {/* ─ En-tete ─ */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold">
                  Analyse : <span className="text-emerald-400">{result.filename}</span>
                </h2>
                {result.fournisseur && (
                  <p className="text-sm text-slate-400 mt-0.5" dir={dirFor(result.fournisseur)}>
                    Fournisseur : {result.fournisseur}
                    {result.periode && (
                      <span dir={dirFor(result.periode)}> | Periode : {result.periode}</span>
                    )}
                  </p>
                )}
              </div>
              <button
                onClick={handleDownloadJSON}
                className="px-4 py-2 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-500 transition cursor-pointer whitespace-nowrap"
              >
                Telecharger JSON
              </button>
            </div>

            {/* ─ Type de facture ─ */}
            <div className={`rounded-xl bg-gradient-to-r ${TYPE_COLORS[result.type_facture] || TYPE_COLORS.inconnu} p-[1px]`}>
              <div className="rounded-xl bg-slate-900/90 backdrop-blur px-5 py-4 flex items-center justify-between">
                <div>
                  <p className="text-sm text-slate-400">Type de facture</p>
                  <p className="text-lg font-semibold">
                    {TYPE_LABELS[result.type_facture] || result.type_facture}
                  </p>
                </div>
                {result.emission_co2_kg != null && (
                  <div className="text-right">
                    <p className="text-sm text-slate-400">Emissions CO2</p>
                    <p className="text-2xl font-bold text-emerald-400">
                      {result.emission_co2_kg.toFixed(2)}{" "}
                      <span className="text-base font-normal text-slate-400">kg</span>
                    </p>
                    <p className="text-xs text-slate-500">
                      {(result.emission_co2_kg / 1000).toFixed(4)} tonnes
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* ─ Alertes de validation ─ */}
            <AlertesPanel alertes={result.alertes} />

            {/* ─ Detail CO₂ par énergie ─ */}
            <CO2DetailTable detail={result.detail_co2} />

            {/* ─ Donnees extraites ─ */}
            {result.donnees.length > 0 && (
              <div>
                <h3 className="text-lg font-semibold mb-3">Donnees environnementales extraites</h3>
                <div className="grid gap-3">
                  {result.donnees.map((d, i) => (
                    <div
                      key={i}
                      className="rounded-lg bg-slate-800/70 border border-slate-700/50 px-4 py-3
                        flex flex-col sm:flex-row sm:items-center justify-between gap-2"
                    >
                      <div>
                        <p className="text-sm text-slate-400" dir={dirFor(d.champ)}>{d.champ}</p>
                        <p className="text-base font-medium" dir={dirFor(d.valeur)}>
                          {d.valeur ?? "—"}
                          {d.unite && <span className="text-slate-400 ml-1" dir={dirFor(d.unite)}>{d.unite}</span>}
                        </p>
                      </div>
                      <ConfidenceBar value={d.confiance} />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ─ Resume structuré ─ */}
            <ResumeTable result={result} />

            {/* ─ Texte OCR brut (toggle) ─ */}
            <div>
              <button
                onClick={() => setShowRaw(!showRaw)}
                className="text-sm text-slate-500 hover:text-slate-300 transition cursor-pointer"
              >
                {showRaw ? "Masquer" : "Afficher"} le texte OCR brut
              </button>
              {showRaw && result.texte_ocr_brut && (
                <div className="mt-2 rounded-xl bg-slate-800/60 border border-slate-700 p-4 max-h-64 overflow-y-auto">
                  <pre
                    className="whitespace-pre-wrap text-xs text-slate-400 font-mono"
                    dir={dirFor(result.texte_ocr_brut)}
                  >
                    {result.texte_ocr_brut}
                  </pre>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
