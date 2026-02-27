import { neon } from "@neondatabase/serverless";

const DATABASE_URL =
  process.env.DATABASE_URL ||
  "postgresql://neondb_owner:npg_0bHQYW7zORgZ@ep-blue-night-ai8jukbo-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require";

const sql = neon(DATABASE_URL);

/* ── Initialise tables (idempotent) ── */
export async function initDB() {
  // Table principale : une ligne par facture traitée
  await sql`
    CREATE TABLE IF NOT EXISTS factures (
      id            SERIAL PRIMARY KEY,
      filename      TEXT,
      type_facture  TEXT,
      fournisseur   TEXT,
      periode       TEXT,
      reference_facture TEXT,
      reference_client  TEXT,
      adresse       TEXT,
      emission_co2_kg   DOUBLE PRECISION,
      score_global      DOUBLE PRECISION,
      created_at    TIMESTAMP DEFAULT NOW()
    )
  `;

  // Table des données environnementales : nom / valeur / unité
  await sql`
    CREATE TABLE IF NOT EXISTS donnees_environnementales (
      id          SERIAL PRIMARY KEY,
      facture_id  INTEGER REFERENCES factures(id) ON DELETE CASCADE,
      nom         TEXT NOT NULL,
      valeur      TEXT,
      unite       TEXT,
      confiance   DOUBLE PRECISION,
      created_at  TIMESTAMP DEFAULT NOW()
    )
  `;
}

/* ── Enregistrer un résultat complet ── */
export async function saveExtraction(data: {
  filename: string;
  type_facture: string;
  fournisseur: string | null;
  periode: string | null;
  reference_facture: string | null;
  reference_client: string | null;
  adresse: string | null;
  emission_co2_kg: number | null;
  score_global: number;
  donnees: { champ: string; valeur: string | null; unite: string | null; confiance: number }[];
}) {
  // 1. Insérer la facture
  const rows = await sql`
    INSERT INTO factures (filename, type_facture, fournisseur, periode,
                          reference_facture, reference_client, adresse,
                          emission_co2_kg, score_global)
    VALUES (${data.filename}, ${data.type_facture}, ${data.fournisseur},
            ${data.periode}, ${data.reference_facture}, ${data.reference_client},
            ${data.adresse}, ${data.emission_co2_kg}, ${data.score_global})
    RETURNING id
  `;

  const factureId = rows[0].id as number;

  // 2. Insérer chaque donnée environnementale
  for (const d of data.donnees) {
    await sql`
      INSERT INTO donnees_environnementales (facture_id, nom, valeur, unite, confiance)
      VALUES (${factureId}, ${d.champ}, ${d.valeur}, ${d.unite}, ${d.confiance})
    `;
  }

  return factureId;
}

export { sql };
