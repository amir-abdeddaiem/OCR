import { NextRequest, NextResponse } from "next/server";
import { writeFile, unlink, readFile } from "fs/promises";
import { join, resolve } from "path";
import { execFile } from "child_process";
import { randomUUID } from "crypto";
import { existsSync } from "fs";

// Résoudre le chemin de la racine du projet (parent de frontend/)
function findProjectRoot(): string {
  // Essayer process.cwd()/.. (dev: cwd = frontend/)
  let root = resolve(process.cwd(), "..");
  if (existsSync(join(root, "main.py"))) return root;
  // Essayer process.cwd() directement
  if (existsSync(join(process.cwd(), "main.py"))) return process.cwd();
  // Fallback absolu
  return "D:\\testOCR";
}

const PROJECT_ROOT = findProjectRoot();
const PYTHON_EXE = join(PROJECT_ROOT, ".venv", "Scripts", "python.exe");
const OCR_SCRIPT = join(PROJECT_ROOT, "main.py");

export async function POST(req: NextRequest) {
  let tmpInput = "";
  let tmpOutput = "";

  try {
    const formData = await req.formData();
    const file = formData.get("file") as File | null;

    if (!file) {
      return NextResponse.json({ error: "Aucun fichier envoyé." }, { status: 400 });
    }

    // Sauvegarder le fichier uploadé dans un dossier temporaire
    const bytes = await file.arrayBuffer();
    const buffer = Buffer.from(bytes);
    const ext = file.name.split(".").pop() || "pdf";
    const id = randomUUID();
    tmpInput = join(PROJECT_ROOT, `tmp_${id}.${ext}`);
    tmpOutput = join(PROJECT_ROOT, `tmp_${id}_carbone.json`);

    await writeFile(tmpInput, buffer);

    // Lancer le script OCR en mode --carbon → sortie JSON structurée
    const result = await new Promise<string>((resolve, reject) => {
      execFile(
        PYTHON_EXE,
        [OCR_SCRIPT, "-i", tmpInput, "-o", tmpOutput, "--carbon"],
        { timeout: 120_000, maxBuffer: 10 * 1024 * 1024, cwd: PROJECT_ROOT, env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
        async (error, stdout, stderr) => {
          if (error) {
            console.error("[OCR stderr]", stderr);
            console.error("[OCR stdout]", stdout);
            reject(new Error(`OCR échoué: ${stderr || error.message}`));
            return;
          }
          try {
            const data = await readFile(tmpOutput, "utf-8");
            resolve(data.trim());
          } catch (readErr) {
            console.error("[OCR] Cannot read output file:", tmpOutput, readErr);
            reject(new Error("Impossible de lire le résultat."));
          }
        }
      );
    });

    const parsed = JSON.parse(result);
    return NextResponse.json({ ...parsed, filename: file.name });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Erreur inconnue";
    return NextResponse.json({ error: message }, { status: 500 });
  } finally {
    // Nettoyage des fichiers temporaires
    try { await unlink(tmpInput); } catch { /* ignore */ }
    try { await unlink(tmpOutput); } catch { /* ignore */ }
  }
}
