"""Run the synthetic set through the app's own sniff/extract/classify/structure/rules code and compare with ground truth."""
import hashlib, json, re, sys
from pathlib import Path
from app.ingestion.sniff import sniff
from app.ingestion.parsers.extract import extract
from app.ingestion.classify.classifier import classify
from app.ingestion.structure import parse_structure
from app.engine.packs.registry import load_rule_pack
from app.engine.rules.evaluator import evaluate_pack

class NoOcr:
    available = False
    def recognize(self, png): raise RuntimeError
root = Path(sys.argv[1]); man = json.loads((root / "ground-truth/manifest.json").read_text())
packs = [load_rule_pack(p, allow_unreviewed=True) for p in ("core_contract", "insurance")]
seen = {}; problems = []

def run(entry, folder):
    f = root / folder / entry["file"]
    h = hashlib.sha256(f.read_bytes()).hexdigest()
    dup = seen.get(h); seen.setdefault(h, entry["id"])
    with f.open("rb") as fh: s = sniff(fh, f.name)
    ex = extract(s.kind, f, NoOcr())
    methods = sorted({p.method for p in ex.pages})
    gt = (root / entry["ground_truth_text"]).read_text() if "ground_truth_text" in entry else ""
    layer = "\n".join(p.text for p in ex.pages)
    text = layer if "text" in methods else gt   # scanned pages: use ground truth as a perfect-OCR stand-in
    if "text" in methods and "none" in methods: text = layer + "\n" + gt.split("\f")[-1]
    if ex.password_protected:  # the pipeline stops here and asks for the password
        return dict(kind=s.kind, pages=0, methods=[], pw=True, dup_of=dup, doc_type=None, sens="normal", flags=[], chars=0)
    cl = classify(text)
    doc = parse_structure(text, cl.doc_type)
    flags = sorted({f"{fl.rule_id}" for p in packs for fl in evaluate_pack(p, doc)})
    return dict(kind=s.kind, pages=len(ex.pages), methods=methods, pw=ex.password_protected, dup_of=dup,
                doc_type=cl.doc_type, sens=cl.sensitivity, flags=flags, chars=len(layer))

print("=== JUDGMENTS (text layer as extracted; scanned -> OCR stand-in)")
for j in man["judgments"]:
    r = run(j, "Judgements-synthetic")
    if j.get("duplicate_of"):
        ok = r["dup_of"] == j["duplicate_of"]; print(f"{j['id']:8} dup_of={r['dup_of']} {'OK' if ok else 'FAIL'}"); continue
    note = []
    if j["format"] == "docx":
        layer = "\n".join(p.text for p in extract("docx", root/"Judgements-synthetic"/j["file"], NoOcr()).pages)
        found = len(re.findall(r"(?m)^\d+\.\s", layer)); note.append(f"numbers_in_text={found}/{j['numbered_paragraphs']}")
    if not r["pw"] and r["doc_type"] != j.get("expected_doc_type"): note.append(f"TYPE {r['doc_type']}!=judgment")
    if r["sens"] != j["expected_sensitivity"]: note.append(f"SENS {r['sens']}!={j['expected_sensitivity']}")
    print(f"{j['id']:8} {r['kind']:5} pages={r['pages']:2} methods={','.join(r['methods']):9} pw={r['pw']!s:5} cls={r['doc_type']}/{r['sens']} {' '.join(note)}")

print("\n=== CLIENT DOCS")
for c in man["client_documents"]:
    r = run(c, "Client-documents-OCR")
    if c.get("duplicate_of"):
        print(f"{c['id']:4} dup_of={r['dup_of']} {'OK' if r['dup_of']==c['duplicate_of'] else 'FAIL'}"); continue
    e = c["expected"]; exp = sorted(x.split("(")[0] for x in e["flags"])
    miss = [x for x in exp if x not in r["flags"]]; extra = [x for x in r["flags"] if x not in exp]
    bad = [x for x in e.get("not_flags", []) if x in r["flags"]]
    st = []
    if e["doc_type"] != r["doc_type"]: st.append(f"type {r['doc_type']}!={e['doc_type']}")
    if e["sensitivity"] != r["sens"]: st.append(f"sens {r['sens']}!={e['sensitivity']}")
    if miss: st.append(f"missing {miss}")
    if extra: st.append(f"extra {extra}")
    if bad: st.append(f"UNWANTED {bad}")
    print(f"{c['id']:4} {r['kind']:5} pages={r['pages']} methods={','.join(r['methods']):9} pw={r['pw']!s:5} text_layer_chars={r['chars']:5} | {'OK' if not st else '; '.join(st)}")
