import { NextRequest, NextResponse } from "next/server";
import { writeFile, readFile, unlink, mkdir } from "fs/promises";
import { join } from "path";
import { randomUUID } from "crypto";
import { execFile } from "child_process";
import { existsSync } from "fs";

const PYTHON = process.env.PYTHON_PATH || "python";
// Resolve the project root (one level up from frontend/)
const PROJECT_ROOT = join(process.cwd(), "..");

export async function POST(req: NextRequest) {
  let tmpInputPath = "";
  let tmpOutputPath = "";

  try {
    const formData = await req.formData();
    const file = formData.get("file") as File | null;

    if (!file) {
      return NextResponse.json({ error: "Aucun fichier fourni." }, { status: 400 });
    }

    // Validate extension
    const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
    const allowed = new Set([
      ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff",
      ".webp", ".jp2", ".pbm", ".pgm", ".ppm", ".sr", ".ras", ".pdf",
    ]);
    if (!allowed.has(ext)) {
      return NextResponse.json(
        { error: `Extension '${ext}' non supportée.` },
        { status: 400 },
      );
    }

    // Write uploaded file to a temp location
    const tmpDir = join(PROJECT_ROOT, "tmp_uploads");
    if (!existsSync(tmpDir)) {
      await mkdir(tmpDir, { recursive: true });
    }

    const id = randomUUID();
    tmpInputPath = join(tmpDir, `${id}${ext}`);
    tmpOutputPath = join(tmpDir, `${id}_carbone.json`);

    const bytes = Buffer.from(await file.arrayBuffer());
    await writeFile(tmpInputPath, bytes);

    // Run main.py --carbon
    const mainScript = join(PROJECT_ROOT, "main.py");
    const result = await new Promise<string>((resolve, reject) => {
      execFile(
        PYTHON,
        [mainScript, "-i", tmpInputPath, "--carbon", "-o", tmpOutputPath],
        { cwd: PROJECT_ROOT, timeout: 120_000, maxBuffer: 10 * 1024 * 1024 },
        (err, stdout, stderr) => {
          if (err) {
            reject(new Error(stderr || err.message));
          } else {
            resolve(stdout);
          }
        },
      );
    });

    // Read the output JSON
    const jsonContent = await readFile(tmpOutputPath, "utf-8");
    const data = JSON.parse(jsonContent);

    // Add the original filename from the upload
    data.filename = file.name;

    return NextResponse.json(data);
  } catch (err: any) {
    console.error("[/api/ocr] Error:", err);
    return NextResponse.json(
      { error: err?.message || "Erreur interne du serveur." },
      { status: 500 },
    );
  } finally {
    // Clean up temp files
    for (const p of [tmpInputPath, tmpOutputPath]) {
      if (p) {
        try {
          await unlink(p);
        } catch {
          /* ignore */
        }
      }
    }
  }
}
