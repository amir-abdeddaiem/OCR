import argparse
import os
import sys
from typing import List, Optional

import cv2
import numpy as np

try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None  # type: ignore[assignment]

# Extensions d'images supportées par OpenCV
_SUPPORTED_IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff",
    ".webp", ".jp2", ".pbm", ".pgm", ".ppm", ".sr", ".ras",
}
# Extensions supplémentaires supportées (PDF)
_SUPPORTED_EXTS = _SUPPORTED_IMAGE_EXTS | {".pdf"}


def _is_pdf(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".pdf"


def _is_supported(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in _SUPPORTED_EXTS


def _make_output_path(input_path: str) -> str:
    """Génère automatiquement le chemin de sortie: input.pdf -> input_ocr.txt"""
    base, _ = os.path.splitext(input_path)
    return base + "_ocr.txt"


def _load_images_from_pdf(pdf_path: str, max_pages: Optional[int]) -> List[np.ndarray]:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "Entrée PDF détectée, mais PyMuPDF n'est pas installé.\n"
            "Installez-le avec: pip install pymupdf\n"
            f"Détail: {exc}"
        )

    doc = fitz.open(pdf_path)
    images: List[np.ndarray] = []
    page_count = len(doc)
    if page_count == 0:
        return images

    pages_to_read = page_count
    if max_pages is not None:
        pages_to_read = min(pages_to_read, max_pages)

    # Zoom élevé pour améliorer l'OCR (~300 DPI)
    mat = fitz.Matrix(3, 3)
    for i in range(pages_to_read):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif pix.n == 3:
            # pixmap est en RGB; OpenCV attend BGR
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        else:
            # Cas rare, on force vers BGR
            img = img[:, :, :3]
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        images.append(img)
    return images


def _load_images(input_path: str, max_pages: Optional[int]) -> List[np.ndarray]:
    if not os.path.exists(input_path):
        raise SystemExit(f"Erreur: fichier introuvable: {input_path}")

    if not _is_supported(input_path):
        ext = os.path.splitext(input_path)[1].lower()
        supported = ", ".join(sorted(_SUPPORTED_EXTS))
        raise SystemExit(
            f"Erreur: extension '{ext}' non supportée.\n"
            f"Extensions supportées: {supported}"
        )

    if _is_pdf(input_path):
        images = _load_images_from_pdf(input_path, max_pages=max_pages)
        if not images:
            raise SystemExit("Erreur: PDF vide ou pages non lisibles.")
        return images

    image = cv2.imread(input_path)
    if image is None:
        raise SystemExit("Erreur: image non lisible (format non supporté ou fichier corrompu).")
    return [image]


def _preprocess_light(image_bgr: np.ndarray) -> np.ndarray:
    """Pré-traitement LÉGER : garde les détails pour Windows OCR."""
    # Si l'image est petite, on agrandit
    h, w = image_bgr.shape[:2]
    if max(h, w) < 1200:
        scale = max(2, 1200 // max(h, w))
        image_bgr = cv2.resize(
            image_bgr, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC
        )

    # Léger débruitage qui préserve les bords du texte
    denoised = cv2.fastNlMeansDenoisingColored(image_bgr, None, 10, 10, 7, 21)
    return denoised


def _preprocess_heavy(image_bgr: np.ndarray) -> np.ndarray:
    """Pré-traitement FORT : binarisation pour PaddleOCR (moteur IA)."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    h, w = gray.shape[:2]
    if max(h, w) < 1200:
        scale = max(2, 1200 // max(h, w))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # Débruitage
    blur = cv2.GaussianBlur(gray, (3, 3), 0)

    # Binarisation adaptative (meilleure que Otsu pour texte sur fond variable)
    th = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 8
    )

    # Si fond sombre, on inverse
    if np.mean(th) < 127:
        th = cv2.bitwise_not(th)

    return cv2.cvtColor(th, cv2.COLOR_GRAY2BGR)


def _paddle_is_usable() -> bool:
    if PaddleOCR is None:
        return False
    try:
        import paddle  # noqa: F401
    except Exception:
        return False
    return True


def _create_paddle_ocr(lang: str):
    if PaddleOCR is None:
        raise RuntimeError("PaddleOCR non installé")
    # API a évolué: use_angle_cls est déprécié au profit de use_textline_orientation
    try:
        return PaddleOCR(use_textline_orientation=True, lang=lang)
    except TypeError:
        return PaddleOCR(use_angle_cls=True, lang=lang)


def _windows_ocr_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.ocr  # noqa: F401
        import winrt.windows.graphics.imaging  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
        import winrt.windows.globalization  # noqa: F401
    except Exception:
        return False
    return True


def _detect_language_windows() -> Optional[str]:
    """Détecte la langue principale du système Windows pour l'OCR."""
    if not _windows_ocr_available():
        return None
    try:
        from winrt.windows.media.ocr import OcrEngine
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine and engine.recognizer_language:
            tag = engine.recognizer_language.language_tag
            # ex: "fr-FR" -> "fr"
            return tag.split("-")[0] if tag else None
    except Exception:
        pass
    return None


def _windows_ocr_image_text(image_bgr: np.ndarray, lang: Optional[str]) -> str:
    if not _windows_ocr_available():
        raise RuntimeError(
            "OCR Windows indisponible. Installez les wheels WinRT avec: "
            "pip install winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging "
            "winrt-Windows.Storage.Streams winrt-Windows.Globalization"
        )

    import asyncio

    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    ok, buf = cv2.imencode(".png", image_bgr)
    if not ok:
        return ""
    png_bytes = buf.tobytes()

    async def _run() -> str:
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()

        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        software_bitmap = await decoder.get_software_bitmap_async()

        engine = None
        # Si une langue est spécifiée, essayer de l'utiliser
        if lang:
            try:
                engine = OcrEngine.try_create_from_language(Language(lang))
            except Exception:
                engine = None

        # Sinon, ou si ça a échoué, utiliser les langues du système
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Impossible d'initialiser l'OCR Windows (langues système manquantes ?)")

        result = await engine.recognize_async(software_bitmap)
        return (result.text or "").strip()

    try:
        return asyncio.run(_run())
    except RuntimeError:
        # Fallback si une boucle asyncio existe déjà (rare en script)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()


def _extract_text_from_paddle_result(result) -> str:
    # Formats rencontrés selon versions:
    # - pages:  [ [ [box, (text, score)], ... ], [ ... ], ... ]
    # - 1 page: [ [box, (text, score)], ... ]
    if result is None:
        return ""

    def is_word(item) -> bool:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return False
        meta = item[1]
        if not isinstance(meta, (list, tuple)) or len(meta) < 1:
            return False
        return isinstance(meta[0], str)

    def is_page(item) -> bool:
        return isinstance(item, list) and (len(item) == 0 or is_word(item[0]))

    pages: List[list]
    if isinstance(result, list) and len(result) > 0 and is_page(result[0]):
        # Liste de pages
        pages = result  # type: ignore[assignment]
    elif isinstance(result, list) and (len(result) == 0 or is_word(result[0])):
        # Une seule page
        pages = [result]
    else:
        pages = []

    lines: List[str] = []
    for page in pages:
        for word in page:
            if not is_word(word):
                continue
            text = word[1][0]
            if text.strip():
                lines.append(text.strip())

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="OCR (image ou PDF) -> texte. Supporte: PDF, JPG, PNG, WEBP, BMP, TIFF, etc.",
        epilog=(
            "Exemples:\n"
            "  python main.py -i cv.pdf                  (auto-détecte la langue, toutes les pages)\n"
            "  python main.py -i photo.jpg                (image simple)\n"
            "  python main.py -i doc.pdf -l en -o out.txt (anglais, sortie personnalisée)\n"
            "  python main.py -i scan.webp --max-pages 3  (limiter les pages)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Chemin vers une image (png/jpg/webp/bmp/tiff/...) ou un PDF.",
    )
    parser.add_argument(
        "--lang",
        "-l",
        default=None,
        help="Langue OCR (ex: fr, en, ar). Par défaut: auto-détection.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Chemin fichier .txt de sortie. Par défaut: <nom_du_fichier>_ocr.txt.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Nombre max de pages (PDF). Par défaut: toutes les pages.",
    )
    parser.add_argument(
        "--debug-images",
        action="store_true",
        help="Sauvegarde les images pré-traitées (utile pour diagnostiquer l'OCR).",
    )
    args = parser.parse_args(argv)

    # --- Sortie automatique si non spécifiée ---
    output_path = args.output if args.output else _make_output_path(args.input)

    # --- Charger toutes les pages ---
    images = _load_images(args.input, max_pages=args.max_pages)
    total_pages = len(images)
    print(f"\n[INFO] Fichier: {args.input}")
    print(f"[INFO] Pages à traiter: {total_pages}")

    # --- Choix du moteur OCR ---
    backend: str
    ocr = None
    lang = args.lang  # None = auto-détection

    if _paddle_is_usable():
        try:
            paddle_lang = lang if lang else "fr"  # PaddleOCR nécessite une langue
            ocr = _create_paddle_ocr(paddle_lang)
            backend = "paddle"
            print(f"[INFO] Moteur OCR: PaddleOCR (langue: {paddle_lang})")
        except Exception:
            ocr = None
            backend = "windows"
    else:
        backend = "windows"

    if backend == "windows":
        if not _windows_ocr_available():
            raise SystemExit(
                "PaddleOCR nécessite 'paddlepaddle' (non disponible pour ton Python actuel), "
                "et l'OCR Windows n'est pas disponible.\n"
                "Option 1 (recommandé): installer Python 3.10/3.11 puis: pip install paddlepaddle paddleocr pymupdf opencv-python\n"
                "Option 2: installer les wheels WinRT: pip install winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Globalization"
            )
        detected = _detect_language_windows()
        if lang:
            print(f"[INFO] Moteur OCR: Windows OCR (langue: {lang})")
        elif detected:
            print(f"[INFO] Moteur OCR: Windows OCR (langue auto-détectée: {detected})")
        else:
            print("[INFO] Moteur OCR: Windows OCR (langue du système)")

    # --- Extraction du texte ---
    extracted_pages: List[str] = []
    for idx, img in enumerate(images, start=1):
        print(f"[INFO] Traitement page {idx}/{total_pages}...", end=" ", flush=True)

        if backend == "paddle":
            cleaned = _preprocess_heavy(img)
            if args.debug_images:
                cv2.imwrite(f"cleaned_{idx}.png", cleaned)
            result = ocr.ocr(cleaned, cls=True)
            extracted_pages.append(_extract_text_from_paddle_result(result))
        else:
            # Windows OCR : essayer d'abord avec l'image légèrement nettoyée
            cleaned = _preprocess_light(img)
            if args.debug_images:
                cv2.imwrite(f"cleaned_{idx}.png", cleaned)
            extracted_pages.append(_windows_ocr_image_text(cleaned, lang))
        print("OK")

    text = "\n\n".join([t for t in extracted_pages if t.strip()]).strip()

    print("\n===== TEXTE EXTRAIT =====\n")
    print(text)

    # --- Sauvegarde automatique ---
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.write("\n")
    print(f"\nTexte sauvegardé dans: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())