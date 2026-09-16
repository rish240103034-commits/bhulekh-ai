"""Generate synthetic 'legacy' land record scans for demos/tests.

Produces English, Hindi and mixed documents with realistic degradation
(noise, blur, skew, faded ink, stains). Run: python samples/generate_samples.py
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "generated"
OUT.mkdir(exist_ok=True)

FONT_EN = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
FONT_HI_CANDIDATES = ["/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
                      "/usr/share/fonts/truetype/noto/NotoSerifDevanagari-Regular.ttf",
                      "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"]
FONT_HI = next((f for f in FONT_HI_CANDIDATES if Path(f).exists()), FONT_EN)

DOCS = {
    "ror_english_up.png": ("en", [
        "GOVERNMENT OF UTTAR PRADESH",
        "RECORD OF RIGHTS (KHATAUNI)  Fasli Year : 1430",
        "District : Lucknow        Tehsil : Sadar",
        "Village : Rampur          Khata No : 45",
        "Khasra No : 123/4",
        "Owner Name : Ram Prasad Verma",
        "Father's Name : Shyam Lal Verma",
        "Area : 2.50 ha",
        "Land Type : Irrigated Agricultural",
        "Ownership : Bhumidhar",
        "Mutation No : M-2019/117    Mutation Date : 12/03/2019",
    ]),
    "khasra_hindi_up.png": ("hi", [
        "उत्तर प्रदेश शासन",
        "खतौनी - अधिकार अभिलेख   फसली वर्ष : 1430",
        "जिला : लखनऊ        तहसील : सदर",
        "ग्राम : रामपुर       खाता संख्या : 48",
        "खसरा संख्या : 127",
        "खातेदार का नाम : सीता देवी",
        "पिता का नाम : रामलाल",
        "रकबा : 2.00 एकड़",
        "भूमि प्रकार : सिंचित",
        "नामांतरण संख्या : 2021/58   नामांतरण दिनांक : 05/08/2021",
    ]),
    "satbara_mixed_mh.png": ("mixed", [
        "MAHARASHTRA GOVERNMENT  -  7/12 UTARA (Record of Rights)",
        "District : Pune          Taluka : Haveli",
        "Village : Wagholi        Survey No : 56/2",
        "Khata No : 112",
        "Owner : Sunil Patil",
        "Total Area : 1.214 ha",
        "Land Class : Jirayat (Unirrigated)",
        "Ownership : Individual",
        "Registration No : PNE-4/2018/3341   Registration Date : 21-11-2018",
    ]),
    "mutation_mp_faded.png": ("en", [
        "MADHYA PRADESH  BHU-ABHILEKH",
        "MUTATION ORDER (Namantaran)",
        "District : Bhopal     Tehsil : Huzur",
        "Village : Kolar       Khasra No : 301",
        "Khata No : 9",
        "Owner Name : Mohan Lal Sharma",
        "Father's Name : Kishan Lal Sharma",
        "Area : 4.05 ha",
        "Land Type : Agricultural",
        "Mutation No : NAM/2020/77    Mutation Date : 30/06/2020",
    ]),
    "duplicate_of_ror.png": ("en", [
        "GOVERNMENT OF UTTAR PRADESH",
        "RECORD OF RIGHTS (KHATAUNI)  Fasli Year : 1430",
        "District : Lucknow        Tehsil : Sadar",
        "Village : Rampur          Khata No : 45",
        "Khasra No : 123/4",
        "Owner Name : Ram Prasad Verma",
        "Father's Name : Shyam Lal Verma",
        "Area : 2.50 ha",
        "Land Type : Irrigated Agricultural",
    ]),
}


def render(lines: list[str], lang: str) -> np.ndarray:
    W, H = 1654, 2339  # A4 @ 200 dpi
    img = Image.new("L", (W, H), 245)
    d = ImageDraw.Draw(img)
    fen, fhi = ImageFont.truetype(FONT_EN, 40), ImageFont.truetype(FONT_HI, 44)
    y = 160
    for i, line in enumerate(lines):
        f = fhi if any("ऀ" <= ch <= "ॿ" for ch in line) else fen
        if i == 0:
            f = ImageFont.truetype(f.path, f.size + 10)
        d.text((150, y), line, fill=20, font=f)
        y += 110 if i else 140
    # table-like rule lines and a seal
    d.rectangle([120, 130, W - 120, y + 40], outline=60, width=3)
    d.ellipse([W - 520, H - 520, W - 220, H - 220], outline=90, width=6)
    d.text((W - 470, H - 400), "SEAL", fill=90, font=fen)
    return np.array(img)


def degrade(gray: np.ndarray, level: str) -> np.ndarray:
    out = gray.astype(np.float32)
    rng = np.random.default_rng(42)
    if level in ("medium", "heavy"):
        out = out * 0.85 + 30  # faded ink / yellowed paper
        out += rng.normal(0, 12 if level == "medium" else 22, out.shape)
    if level == "heavy":
        out = cv2.GaussianBlur(out, (3, 3), 0)
        # stains
        for _ in range(6):
            cx, cy, r = rng.integers(0, out.shape[1]), rng.integers(0, out.shape[0]), rng.integers(60, 180)
            cv2.circle(out, (int(cx), int(cy)), int(r), float(rng.integers(120, 200)), -1)
    out = np.clip(out, 0, 255).astype(np.uint8)
    angle = {"clean": 0.0, "light": 0.4, "medium": -1.5, "heavy": 2.5}[level]
    h, w = out.shape
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(out, m, (w, h), borderValue=235)


# A ruled multi-parcel khasra sheet: several plots in one grid, which is how real
# khasra / khatauni records are actually laid out.
KHASRA_TABLE = {
    "header_left": ["जिला - कानपुर देहात", "तहसील - अकबरपुर", "ग्राम का नाम - बिल्हौर"],
    "header_right": ["वर्ष - 1987-88", "ग्राम - बिल्हौर", "परगना - अकबरपुर", "खाता संख्या - 0123"],
    "title": ["उत्तर प्रदेश शासन", "खसरा", "(भूमि अभिलेख)"],
    "columns": ["खसरा संख्या", "रकबा (हे.)", "भूमि का प्रकार", "फसल", "मालिक का नाम", "कब्जेदार का नाम", "टिप्पणी"],
    "rows": [
        ["143", "0.0800", "कृषि", "गेहूँ", "रामस्वरूप", "रामस्वरूप", ""],
        ["144", "0.1200", "कृषि", "धान", "गोपाल सिंह", "गोपाल सिंह", ""],
        ["145", "0.1500", "कृषि", "गेहूँ", "सीताराम", "सीताराम", ""],
        ["146", "0.0900", "बंजर", "-", "शिवकुमार", "शिवकुमार", ""],
        ["147", "0.1100", "कृषि", "अरहर", "रामकली देवी", "रामकली देवी", ""],
        ["148", "0.0600", "कृषि", "गेहूँ", "महेन्द्र पाल", "महेन्द्र पाल", ""],
    ],
    "footer": ["कुल रकबा - 0.6100 हे.", "अन्य विवरण - सिंचित : 0.35 हे.  असिंचित : 0.26 हे.",
               "तारीख - 12.08.1987"],
}


def render_table_form(spec: dict) -> np.ndarray:
    W, H = 2200, 1500
    img = Image.new("L", (W, H), 246)
    d = ImageDraw.Draw(img)
    f_hi = ImageFont.truetype(FONT_HI, 34)
    f_small = ImageFont.truetype(FONT_HI, 30)
    f_title = ImageFont.truetype(FONT_HI, 52)

    for i, line in enumerate(spec["header_left"]):
        d.text((70, 60 + i * 56), line, fill=20, font=f_hi)
    for i, line in enumerate(spec["header_right"]):
        d.text((1560, 60 + i * 56), line, fill=20, font=f_hi)
    for i, line in enumerate(spec["title"]):
        w = d.textlength(line, font=f_title if i == 1 else f_hi)
        d.text(((W - w) / 2, 55 + i * 62), line, fill=20, font=f_title if i == 1 else f_hi)

    cols = [70, 250, 470, 730, 930, 1330, 1760, 2130]
    top = 300
    header_h, row_h = 110, 84
    n_rows = len(spec["rows"])
    bottom = top + header_h + n_rows * row_h

    for x in cols:                                   # column rules
        d.line([(x, top), (x, bottom)], fill=25, width=4)
    d.line([(cols[0], top), (cols[-1], top)], fill=25, width=4)
    d.line([(cols[0], top + header_h), (cols[-1], top + header_h)], fill=25, width=4)
    for r in range(1, n_rows + 1):                   # row rules
        y = top + header_h + r * row_h
        d.line([(cols[0], y), (cols[-1], y)], fill=25, width=4)

    for c, label in enumerate(spec["columns"]):
        d.text((cols[c] + 14, top + 32), label, fill=20, font=f_small)
    for r, row in enumerate(spec["rows"]):
        y = top + header_h + r * row_h + 22
        for c, cell in enumerate(row):
            if cell:
                d.text((cols[c] + 18, y), cell, fill=20, font=f_hi)

    for i, line in enumerate(spec["footer"]):
        d.text((70, bottom + 50 + i * 58), line, fill=20, font=f_hi)
    return np.array(img)


if __name__ == "__main__":
    levels = ["light", "medium", "heavy", "medium", "light"]
    for (name, (lang, lines)), level in zip(DOCS.items(), levels):
        img = degrade(render(lines, lang), level)
        cv2.imwrite(str(OUT / name), img)
        print("wrote", OUT / name, level)
    # a 2-page PDF sample
    pages = [Image.fromarray(degrade(render(DOCS["ror_english_up.png"][1], "en"), "light")).convert("RGB"),
             Image.fromarray(degrade(render(DOCS["mutation_mp_faded.png"][1], "en"), "medium")).convert("RGB")]
    import io

    import img2pdf
    bufs = []
    for pg in pages:
        b = io.BytesIO(); pg.save(b, format="PNG"); bufs.append(b.getvalue())
    (OUT / "two_page_bundle.pdf").write_bytes(img2pdf.convert(bufs))
    print("wrote", OUT / "two_page_bundle.pdf")

    # Kept unskewed on purpose: this fixture is the regression test for the table
    # reader, while the noisy/rotated cases are covered by the other samples.
    table_img = degrade(render_table_form(KHASRA_TABLE), "clean")
    cv2.imwrite(str(OUT / "khasra_table_multi_parcel.png"), table_img)
    print("wrote", OUT / "khasra_table_multi_parcel.png")
