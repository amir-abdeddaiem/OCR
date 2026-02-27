import argparse
import json
import os
import sys
from typing import List, Optional

# Forcer UTF-8 sur stdout/stderr (Windows CP1252 ne gère pas les caractères spéciaux)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None  # type: ignore[assignment]

from extractor import extraire_donnees_environnementales

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


def _make_output_path(input_path: str, suffix: str = "_ocr.txt") -> str:
    """Génère automatiquement le chemin de sortie."""
    base, _ = os.path.splitext(input_path)
    return base + suffix


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

    # Convertir en LAB pour rehausser le contraste sans altérer les couleurs
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_chan = clahe.apply(l_chan)
    enhanced = cv2.merge([l_chan, a_chan, b_chan])
    enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    # Léger débruitage qui préserve les bords du texte
    denoised = cv2.fastNlMeansDenoisingColored(enhanced_bgr, None, 8, 8, 7, 21)
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


def _get_available_ocr_languages() -> List[str]:
    """Retourne la liste des tags de langues OCR disponibles sur Windows."""
    if not _windows_ocr_available():
        return []
    try:
        from winrt.windows.media.ocr import OcrEngine
        langs = OcrEngine.available_recognizer_languages
        return [l.language_tag for l in langs] if langs else []
    except Exception:
        return []


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

    async def _get_bitmap():
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        return await decoder.get_software_bitmap_async()

    async def _ocr_single(bitmap, language_tag: Optional[str]) -> str:
        engine = None
        if language_tag:
            try:
                engine = OcrEngine.try_create_from_language(Language(language_tag))
            except Exception:
                engine = None
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return ""
        result = await engine.recognize_async(bitmap)
        return (result.text or "").strip()

    async def _run() -> str:
        bitmap = await _get_bitmap()

        # Si une langue est spécifiée, OCR simple
        if lang:
            return await _ocr_single(bitmap, lang)

        # Mode bilingual : détecter toutes les langues disponibles et
        # faire une passe par langue, puis fusionner
        available = _get_available_ocr_languages()
        if len(available) <= 1:
            return await _ocr_single(bitmap, available[0] if available else None)

        # Passe multi-langue : FR d'abord (données principales),
        # puis les autres langues pour compléter
        results: dict[str, str] = {}
        # Prioriser fr > en > ar > reste
        priority = []
        for pref in ["fr", "en", "ar"]:
            for tag in available:
                if tag.startswith(pref) and tag not in priority:
                    priority.append(tag)
        for tag in available:
            if tag not in priority:
                priority.append(tag)

        for tag in priority:
            try:
                bitmap_copy = await _get_bitmap()  # Bitmap frais pour chaque passe
                text = await _ocr_single(bitmap_copy, tag)
                if text:
                    results[tag] = text
            except Exception:
                pass

        if not results:
            return await _ocr_single(bitmap, None)

        # Fusionner intelligemment :
        # - FR (ou EN) = texte principal avec données chiffrées
        # - AR = ajouter UNIQUEMENT les lignes contenant de l'arabe
        #   (le reste est une ré-OCR dégradée du français)
        import re as _re
        _arabic_re = _re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')

        if len(results) == 1:
            return list(results.values())[0]

        # Prendre FR comme base, sinon EN, sinon premier disponible
        base_tag = None
        for pref in ["fr", "en"]:
            for tag in priority:
                if tag.startswith(pref) and tag in results:
                    base_tag = tag
                    break
            if base_tag:
                break
        if not base_tag:
            base_tag = list(results.keys())[0]

        base_text = results[base_tag]
        base_lines_lower = set(
            l.strip().lower() for l in base_text.split("\n") if l.strip()
        )

        extra_lines: List[str] = []
        for tag, text in results.items():
            if tag == base_tag:
                continue
            for line in text.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                # Pour la passe arabe : n'ajouter que les lignes
                # qui contiennent effectivement de l'arabe
                if tag.startswith("ar"):
                    if _arabic_re.search(stripped) and stripped.lower() not in base_lines_lower:
                        extra_lines.append(stripped)
                        base_lines_lower.add(stripped.lower())
                # Pour les autres langues : ignorer (doublon du FR)

        combined = base_text
        if extra_lines:
            combined += "\n" + "\n".join(extra_lines)
        return combined

    try:
        return asyncio.run(_run())
    except RuntimeError:
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
        description="OCR intelligent : extrait le texte ou analyse les données carbone d'une facture.",
        epilog=(
            "Exemples:\n"
            "  python main.py -i facture.pdf                    (OCR simple)\n"
            "  python main.py -i facture.pdf --carbon            (extraction carbone)\n"
            "  python main.py -i facture.jpg --carbon -o res.json\n"
            "  python main.py -i scan.webp --max-pages 3"
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
    parser.add_argument(
        "--carbon",
        action="store_true",
        help="Mode carbone : extrait les données environnementales et calcule les émissions CO₂.",
    )
    args = parser.parse_args(argv)

    # --- Sortie automatique si non spécifiée ---
    if args.output:
        output_path = args.output
    elif args.carbon:
        output_path = _make_output_path(args.input, "_carbone.json")
    else:
        output_path = _make_output_path(args.input, "_ocr.txt")

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
        available_langs = _get_available_ocr_languages()
        if lang:
            print(f"[INFO] Moteur OCR: Windows OCR (langue: {lang})")
        elif len(available_langs) > 1:
            print(f"[INFO] Moteur OCR: Windows OCR MULTILINGUE ({', '.join(available_langs)})")
        else:
            detected = _detect_language_windows()
            if detected:
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

    # --- Mode Carbone ---
    if args.carbon:
        print("\n[INFO] Analyse environnementale en cours...")
        resultat = extraire_donnees_environnementales(text)

        print("\n" + "=" * 60)
        print("       ANALYSE BILAN CARBONE")
        print("=" * 60 + "\n")
        print(resultat.resume)

        # Sauvegarder le JSON structuré
        json_data = resultat.to_dict()
        json_data["texte_ocr_brut"] = text
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\nDonnées JSON sauvegardées dans: {output_path}")
        return 0

    # --- Mode OCR classique ---
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