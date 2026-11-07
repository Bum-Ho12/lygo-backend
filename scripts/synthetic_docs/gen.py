"""Generate the synthetic judgments + OCR client documents with ground truth. Usage: gen.py OUT_DIR"""
import io
import json
import math
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import pymupdf
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

sys.path.insert(0, str(Path(__file__).parent))
from content_judgments import FOOT, JUDGMENTS  # noqa: E402
from content_client import CLIENTS  # noqa: E402

OUT = Path(sys.argv[1])
J_DIR, C_DIR, GT_DIR = OUT / "Judgements-synthetic", OUT / "Client-documents-OCR", OUT / "ground-truth"
for d in (J_DIR, C_DIR, GT_DIR / "text"):
    d.mkdir(parents=True, exist_ok=True)

RNG = np.random.default_rng(20261107)
random.seed(20261107)
FONT_DIR = Path("/usr/share/fonts/truetype")
SERIF = str(FONT_DIR / "liberation/LiberationSerif-Regular.ttf")
SERIF_B = str(FONT_DIR / "liberation/LiberationSerif-Bold.ttf")
SANS = str(FONT_DIR / "liberation/LiberationSans-Regular.ttf")
SANS_B = str(FONT_DIR / "liberation/LiberationSans-Bold.ttf")
HAND = str(FONT_DIR / "msttcorefonts/comic.ttf")

# ------------------------------------------------------------------ text (ground truth)


def plain_text(blocks):
    lines, n = [], 0
    for k, t in blocks:
        if k == "n":
            n += 1
            lines.append(f"{n}. {t}")
        elif k == "tbl":
            lines.extend(" | ".join(r) for r in t)
        elif k == "sig":
            lines.append("____________________")
            lines.append(t)
        elif k == "pb":
            lines.append("\f")
        else:
            lines.append(t)
    return "\n".join(lines).strip() + "\n"


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")

# ------------------------------------------------------------------ PDF (reportlab)


ST = dict(
    c=ParagraphStyle("c", fontName="Times-Bold", fontSize=12, alignment=TA_CENTER, leading=15, spaceAfter=2),
    cn=ParagraphStyle("cn", fontName="Times-Roman", fontSize=11.5, alignment=TA_CENTER, leading=14, spaceAfter=2),
    t=ParagraphStyle("t", fontName="Times-Bold", fontSize=14, alignment=TA_CENTER, leading=18, spaceBefore=10, spaceAfter=10),
    s=ParagraphStyle("s", fontName="Times-Bold", fontSize=12, leading=15, spaceBefore=8, spaceAfter=4),
    p=ParagraphStyle("p", fontName="Times-Roman", fontSize=11.5, leading=15, alignment=TA_JUSTIFY, spaceAfter=6),
    n=ParagraphStyle("n", fontName="Times-Roman", fontSize=11.5, leading=15, alignment=TA_JUSTIFY, spaceAfter=6, leftIndent=10 * mm, firstLineIndent=-10 * mm),
    r=ParagraphStyle("r", fontName="Times-Bold", fontSize=11.5, alignment=TA_RIGHT, leading=14),
)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillGray(0.35)
    canvas.drawCentredString(A4[0] / 2, 10 * mm, FOOT)
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(blocks, path, title):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                            title=title, author="Lygo QA (synthetic)", subject="Synthetic test document")
    story, n = [], 0
    for k, t in blocks:
        if k == "n":
            n += 1
            story.append(Paragraph(f"{n}.&nbsp;&nbsp;&nbsp;{esc(t)}", ST["n"]))
        elif k == "tbl":
            tb = Table([[Paragraph(esc(c), ST["p"]) for c in r] for r in t], hAlign="LEFT")
            tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story += [tb, Spacer(1, 8)]
        elif k == "sig":
            story += [Spacer(1, 26), Paragraph("____________________________", ST["p"]), Paragraph(esc(t), ST["p"])]
        elif k == "pb":
            story.append(PageBreak())
        else:
            story.append(Paragraph(esc(t), ST[k]))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)

# ------------------------------------------------------------------ DOCX


def render_docx(blocks, path, numbering, title):
    d = Document()
    d.core_properties.title = title
    d.core_properties.author = "Lygo QA (synthetic)"
    d.core_properties.comments = FOOT
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Times New Roman", Pt(12)
    d.sections[0].footer.paragraphs[0].text = FOOT
    n = 0
    for k, t in blocks:
        if k == "n":
            n += 1
            if numbering == "auto":  # Word list numbering: the number is NOT in the paragraph text
                p = d.add_paragraph(t, style="List Number")
            else:
                p = d.add_paragraph(f"{n}.\t{t}")
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        elif k in ("c", "cn", "t"):
            p = d.add_paragraph()
            run = p.add_run(t)
            run.bold = k != "cn"
            if k == "t":
                run.font.size = Pt(14)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif k == "s":
            d.add_paragraph().add_run(t).bold = True
        elif k == "r":
            p = d.add_paragraph()
            p.add_run(t).bold = True
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        else:
            d.add_paragraph(t)
    d.save(str(path))

# ------------------------------------------------------------------ scanning simulation


def rasterize(pdf_path, dpi):
    with pymupdf.open(pdf_path) as doc:
        return [Image.open(io.BytesIO(p.get_pixmap(dpi=dpi).tobytes("png"))).convert("RGB") for p in doc]


def stain(img):
    w, h = img.size
    layer = Image.new("L", img.size, 0)
    dr = ImageDraw.Draw(layer)
    cx, cy, r = int(w * 0.72), int(h * 0.30), int(w * 0.11)
    for i in range(6):
        dr.ellipse([cx - r + i * 3, cy - r + i * 2, cx + r - i * 2, cy + r - i * 3], outline=90 - i * 12, width=max(2, w // 300))
    layer = layer.filter(ImageFilter.GaussianBlur(w / 400))
    tint = Image.new("RGB", img.size, (150, 105, 60))
    return Image.composite(tint, img, layer.point(lambda v: int(v * 0.6)))


def stamp(img, text, color=(40, 60, 170)):
    w, h = img.size
    s = int(w * 0.26)
    st = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(st)
    lw = max(3, s // 60)
    dr.ellipse([lw, lw, s - lw, s - lw], outline=color + (200,), width=lw)
    dr.ellipse([s * 0.12, s * 0.12, s * 0.88, s * 0.88], outline=color + (200,), width=max(2, lw // 2))
    f = ImageFont.truetype(SANS_B, max(12, s // 11))
    words = text.split()
    mid = len(words) // 2 or 1
    for i, line in enumerate([" ".join(words[:mid]), " ".join(words[mid:])]):
        tw = dr.textlength(line, font=f)
        dr.text(((s - tw) / 2, s * 0.36 + i * s * 0.15), line, fill=color + (210,), font=f)
    st = st.rotate(random.uniform(-25, 25), resample=Image.BICUBIC, expand=True)
    img = img.convert("RGBA")
    img.alpha_composite(st, (int(w * 0.58), int(h * 0.70)))
    return img.convert("RGB")


def signature(img, at=(0.14, 0.74)):
    w, h = img.size
    dr = ImageDraw.Draw(img)
    x0, y0 = w * at[0], h * at[1]
    pts = []
    for i in range(70):
        t = i / 69
        pts.append((x0 + t * w * 0.22 + math.sin(t * 19) * w * 0.012, y0 + math.sin(t * 11 + 1) * h * 0.012 + math.cos(t * 23) * h * 0.005))
    dr.line(pts, fill=(25, 35, 120), width=max(2, w // 450), joint="curve")
    return img


def degrade(img, cfg, rotate=0):
    g = img.convert("L")
    if cfg.get("photocopy"):
        g = g.point(lambda v: 255 if v > 175 else int(v * 0.55))
    if cfg.get("blur"):
        g = g.filter(ImageFilter.GaussianBlur(cfg["blur"]))
    a = np.asarray(g).astype(np.int16)
    a = a + RNG.normal(0, cfg.get("noise", 8), a.shape).astype(np.int16)
    if cfg.get("photocopy"):  # dark speckles + grey edge band
        sp = RNG.random(a.shape) < 0.0015
        a[sp] = 30
        a[:, : a.shape[1] // 40] -= 60
    g = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    g = g.rotate(cfg.get("skew", 0) * random.choice([-1, 1]), resample=Image.BICUBIC, expand=False, fillcolor=245)
    if rotate:
        g = g.rotate(rotate, expand=True, fillcolor=245)
    if cfg.get("binarize"):
        g = g.point(lambda v: 255 if v > 165 else 0).convert("1").convert("L")
    return g


def phone_photo(img, cfg):
    """Perspective tilt, uneven lighting and a desk background, like a client's phone picture."""
    img = img.convert("RGB")
    w, h = img.size
    bg = Image.new("RGB", (int(w * 1.18), int(h * 1.14)), (92, 74, 58))
    bg.paste(img, (int(w * 0.09), int(h * 0.06)))
    W, H = bg.size
    d = 0.045
    coeffs = _persp([(0, 0), (W, 0), (W, H), (0, H)],
                    [(W * d, H * d * 0.6), (W * (1 - d * 0.4), 0), (W, H * (1 - d * 0.3)), (W * d * 1.6, H)])
    bg = bg.transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(70, 56, 44))
    yy, xx = np.mgrid[0:H, 0:W]
    shade = 1.0 - 0.32 * np.clip((xx / W) * 0.7 + (yy / H) * 0.5 - 0.35, 0, 1)
    a = np.asarray(bg).astype(np.float32) * shade[..., None] * np.array([1.0, 0.97, 0.9])
    a += RNG.normal(0, cfg.get("noise", 8), a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))


def _persp(src, dst):
    m = []
    for (x, y), (u, v) in zip(dst, src):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    b = np.array(src).reshape(8)
    return np.linalg.solve(np.array(m), b).tolist()


def hand_line(dr, x, y, text, size, color=(20, 30, 110)):
    """Jittered per-word rendering with a casual font: a stand-in for handwriting."""
    f = ImageFont.truetype(HAND, size)
    for word in text.split(" "):
        dy = random.uniform(-size * 0.12, size * 0.12)
        for ch in word:
            fs = ImageFont.truetype(HAND, int(size * random.uniform(0.9, 1.1)))
            dr.text((x, y + dy + random.uniform(-1.5, 1.5)), ch, fill=color, font=fs)
            x += dr.textlength(ch, font=fs) * random.uniform(0.92, 1.05)
        x += dr.textlength(" ", font=f) * random.uniform(1.0, 1.6)
    return x


def page_canvas(dpi):
    w, h = int(8.27 * dpi), int(11.69 * dpi)
    return Image.new("RGB", (w, h), (252, 251, 247)), w, h


def draw_footer(dr, w, h, dpi):
    dr.text((w * 0.08, h - dpi * 0.45), FOOT, fill=(110, 110, 110), font=ImageFont.truetype(SANS, max(9, dpi // 12)))


def render_handwritten(pages, dpi):
    out = []
    for lines in pages:
        img, w, h = page_canvas(dpi)
        dr = ImageDraw.Draw(img)
        for i in range(28):  # ruled paper
            y = dpi * 1.0 + i * dpi * 0.36
            dr.line([(w * 0.06, y + dpi * 0.3), (w * 0.95, y + dpi * 0.3)], fill=(190, 205, 230), width=1)
        dr.line([(w * 0.12, 0), (w * 0.12, h)], fill=(230, 160, 160), width=2)
        for i, line in enumerate(lines):
            hand_line(dr, w * 0.14, dpi * 1.0 + i * dpi * 0.72 + dpi * 0.02, line, int(dpi * 0.24))
        draw_footer(dr, w, h, dpi)
        out.append(img)
    return out


def render_form(spec, dpi):
    img, w, h = page_canvas(dpi)
    dr = ImageDraw.Draw(img)
    fb, fr = ImageFont.truetype(SANS_B, int(dpi * 0.2)), ImageFont.truetype(SANS, int(dpi * 0.13))
    dr.text((w * 0.08, dpi * 0.6), spec["form_org"], fill=(0, 0, 0), font=fb)
    dr.text((w * 0.08, dpi * 0.95), spec["form_title"], fill=(0, 0, 0), font=ImageFont.truetype(SANS_B, int(dpi * 0.16)))
    y = dpi * 1.5
    for label, value in spec["fields"]:
        dr.rectangle([w * 0.07, y, w * 0.93, y + dpi * 0.62], outline=(0, 0, 0), width=2)
        dr.text((w * 0.085, y + dpi * 0.05), label, fill=(0, 0, 0), font=fr)
        hand_line(dr, w * 0.10, y + dpi * 0.24, value, int(dpi * 0.22))
        y += dpi * 0.72
    dr.text((w * 0.08, y + dpi * 0.3), "Declaration: I declare that the above is true and complete.", fill=(0, 0, 0), font=fr)
    signature(img, at=(0.12, (y + dpi * 0.75) / h))
    draw_footer(dr, w, h, dpi)
    return img


def images_to_pdf(images, path, dpi):
    rgb = [im.convert("RGB") for im in images]
    rgb[0].save(path, "PDF", save_all=True, append_images=rgb[1:], resolution=dpi, quality=75)


def save_image(img, path, fmt, cfg):
    if fmt == "jpg":
        img.convert("RGB").save(path, "JPEG", quality=cfg.get("jpeg", 70))
    elif fmt == "png":
        img.save(path, "PNG", optimize=True)
    elif fmt == "tiff":
        img.convert("1").save(path, "TIFF", compression="group4", dpi=(cfg["dpi"], cfg["dpi"]))


def jpeg_roundtrip(img, q):
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=q)
    return Image.open(io.BytesIO(b.getvalue())).convert("RGB")


def encrypt(path, pw):
    tmp = path.with_suffix(".tmp.pdf")
    with pymupdf.open(path) as doc:
        doc.save(tmp, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=pw, owner_pw=pw + "-owner")
    tmp.replace(path)

# ------------------------------------------------------------------ build


manifest = dict(
    generated_for="Lygo ingestion / OCR / RAG / tenancy testing",
    warning="All documents are synthetic. Parties, advocates, judges, insurers and case numbers are fictional. "
            "Statute section numbers cited as statute_in_corpus were checked against the 'Acts and Laws' folder.",
    citation_classes={
        "statute_in_corpus": "real provision present in 'Acts and Laws' -> fact-check should return SUPPORTED/resolved",
        "statute_not_in_corpus": "real Act not loaded -> NOT FOUND (not 'false')",
        "case_in_set": "fictional case that exists in this synthetic set -> resolves within the firm knowledge base",
        "case_external": "fictional case outside every corpus -> NOT FOUND",
        "fabricated": "cannot exist (e.g. section beyond the Act) -> citation guardrail MUST reject",
    },
    judgments=[], client_documents=[],
)
tmp = OUT / ".tmp"
tmp.mkdir(exist_ok=True)

for j in JUDGMENTS:
    text = plain_text(j["blocks"])
    (GT_DIR / "text" / f"{j['id']}.txt").write_text(text, encoding="utf-8")
    fmt = j["fmt"]
    ext = "docx" if fmt == "docx" else "pdf"
    path = J_DIR / f"{j['file']}.{ext}"
    if fmt == "docx":
        render_docx(j["blocks"], path, j.get("numbering", "typed"), j["file"])
    else:
        src = tmp / f"{j['id']}.pdf"
        render_pdf(j["blocks"], src, j["file"])
        if fmt == "scan_pdf":
            cfg = j["scan"]
            pages = [jpeg_roundtrip(degrade(p, cfg), cfg["jpeg"]) for p in rasterize(src, cfg["dpi"])]
            images_to_pdf(pages, path, cfg["dpi"])
        else:
            shutil.copyfile(src, path)
            if j.get("password"):
                encrypt(path, j["password"])
    n_paras = sum(1 for k, _ in j["blocks"] if k == "n")
    manifest["judgments"].append(dict(
        id=j["id"], file=path.name, format=fmt if fmt != "pdf" else ("pdf_password" if j.get("password") else "pdf_text"),
        docx_numbering=j.get("numbering") if fmt == "docx" else None, password=j.get("password"),
        kind=j["kind"], court_level=j["court_level"], neutral_citation=j["citation"], date=j["date"],
        practice_area=j["practice_area"], expected_doc_type="judgment", expected_sensitivity=j["sensitivity"], numbered_paragraphs=n_paras,
        ocr_required=fmt == "scan_pdf", version_of=j.get("version_of"), expected_changes=j.get("expected_changes"),
        citations=[dict(text=t, target=tg, cls=c) for t, tg, c in j["citations"]],
        amounts=j["amounts"], periods=j["deadlines"], ground_truth_text=f"ground-truth/text/{j['id']}.txt",
        suggested_scope="firm_kb",
    ))

# byte-identical duplicate, like the real "(1)" file
j01 = next(m for m in manifest["judgments"] if m["id"] == "J01")
dup = J_DIR / j01["file"].replace(".pdf", " (1).pdf")
shutil.copyfile(J_DIR / j01["file"], dup)
manifest["judgments"].append(dict(id="J01-dup", file=dup.name, duplicate_of="J01", note="byte-identical: dedup must catch it (same SHA-256)"))

for cd in CLIENTS:
    cfg = cd.get("scan", {})
    out = cd["out"]
    ext = {"scan_pdf": "pdf", "text_pdf": "pdf", "mixed_pdf": "pdf", "jpg": "jpg", "png": "png", "tiff": "tif"}[out]
    path = C_DIR / f"{cd['id']} - {cd['file']}.{ext}"
    if cd.get("kind") == "handwritten":
        text = "\n".join("\n".join(p) for p in cd["hand_pages"]) + "\n"
        pages = [jpeg_roundtrip(degrade(p, cfg), cfg["jpeg"]) for p in render_handwritten(cd["hand_pages"], cfg["dpi"])]
        images_to_pdf(pages, path, cfg["dpi"])
    elif cd.get("kind") == "form":
        text = "\n".join([cd["form_org"], cd["form_title"]] + [f"{a}: {b}" for a, b in cd["fields"]]) + "\n"
        img = phone_photo(render_form(cd, cfg["dpi"]), cfg)
        save_image(img, path, "jpg", cfg)
    else:
        text = plain_text(cd["blocks"])
        src = tmp / f"{cd['id']}.pdf"
        render_pdf(cd["blocks"], src, cd["file"])
        if out == "text_pdf":
            shutil.copyfile(src, path)
            if cd.get("password"):
                encrypt(path, cd["password"])
        else:
            pages = rasterize(src, cfg["dpi"])
            if cd.get("blank"):
                text = ""
            if cd.get("signature"):
                pages[-1] = signature(pages[-1])
            if cd.get("stamp"):
                pages[-1] = stamp(pages[-1], cd["stamp"])
            if cd.get("stain"):
                pages[0] = stain(pages[0])
            if out == "mixed_pdf":
                scanned = jpeg_roundtrip(degrade(pages[-1], cfg), cfg["jpeg"])
                with pymupdf.open(src) as s, pymupdf.open() as d:
                    d.insert_pdf(s, from_page=0, to_page=0)
                    b = io.BytesIO()
                    scanned.save(b, "JPEG", quality=cfg["jpeg"])
                    pg = d.new_page(width=s[1].rect.width, height=s[1].rect.height)
                    pg.insert_image(pg.rect, stream=b.getvalue())
                    d.save(path)
            elif out in ("jpg", "png", "tiff"):
                img = pages[0]
                img = phone_photo(img, cfg) if cfg.get("phone") else degrade(img, cfg)
                save_image(img, path, out, cfg)
            else:
                rot = cd.get("rotate_pages", {})
                pages = [jpeg_roundtrip(degrade(p, cfg, rotate=rot.get(i + 1, 0)), cfg["jpeg"]) for i, p in enumerate(pages)]
                images_to_pdf(pages, path, cfg["dpi"])
    (GT_DIR / "text" / f"{cd['id']}.txt").write_text(text, encoding="utf-8")
    e = cd["expect"]
    manifest["client_documents"].append(dict(
        id=cd["id"], file=path.name, client=cd["client"], matter=cd["matter"], delivered_as=out,
        scan_profile={k: v for k, v in cfg.items()} or None, password=cd.get("password"),
        language=cd.get("language", "en"), version_of=cd.get("version_of"), suggested_scope="client",
        expected=e, ocr_required=out not in ("text_pdf",), ground_truth_text=f"ground-truth/text/{cd['id']}.txt",
    ))

c10 = next(m for m in manifest["client_documents"] if m["id"] == "C10")
dup = C_DIR / c10["file"].replace("C10 - ", "C17 - ").replace("(scan)", "(scan) - re-uploaded copy")
shutil.copyfile(C_DIR / c10["file"], dup)
manifest["client_documents"].append(dict(id="C17", file=dup.name, duplicate_of="C10", note="byte-identical re-upload: dedup must catch it"))

shutil.rmtree(tmp)
(GT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"judgments: {len(manifest['judgments'])}  client docs: {len(manifest['client_documents'])}  -> {OUT}")
