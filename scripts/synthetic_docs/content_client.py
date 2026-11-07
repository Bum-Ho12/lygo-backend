"""Synthetic client documents (the kind clients bring in on paper -> scanned -> OCR).

Extra block kinds:  ("tbl", [[...], ...]) table; ("sig", name) signature line; ("pb", "") page break.
`scan` controls degradation; `out` is the delivered format (scan_pdf | jpg | png | tiff | text_pdf | mixed_pdf).
`expect` is ground truth for the pipeline (doc type, flags, sensitivity, review queue).
No national ID numbers, KRA PINs or government ID documents are generated (see README).
"""

CLIENTS = []

CLEAN = dict(dpi=300, skew=0.4, noise=6, blur=0.0, jpeg=85)
OFFICE = dict(dpi=200, skew=1.5, noise=10, blur=0.4, jpeg=70)
POOR = dict(dpi=150, skew=2.8, noise=22, blur=1.0, jpeg=45, photocopy=True)
FAX = dict(dpi=140, skew=0.8, noise=4, blur=0.2, jpeg=90, binarize=True)

# C01 ---------------------------------------------------------------------------------
CLIENTS.append(dict(
    id="C01", file="Tausi Medical Cover - policy wording and schedule (scan)", out="scan_pdf", scan=CLEAN,
    client="Grace Akinyi Ouma", matter="Medical claim refusal",
    blocks=[
        ("c", "TAUSI GENERAL INSURANCE COMPANY LIMITED"), ("cn", "P.O. Box 00000-00100 Nairobi  |  claims@tausi-insurance.example"),
        ("t", "INDIVIDUAL MEDICAL COVER - POLICY WORDING"),
        ("s", "1. Definitions"),
        ("p", "In this policy 'the Insurer' means Tausi General Insurance Company Limited and 'the Insured' means the person named in the Policy Schedule."),
        ("s", "2. Cover"),
        ("p", "The Insurer will pay reasonable and customary inpatient and outpatient medical expenses incurred by the Insured during the period of insurance, up to the limits shown in the Policy Schedule."),
        ("s", "3. Exclusions"),
        ("p", "The Insurer shall not be liable for any claim arising from a pre-existing condition, meaning any illness or injury for which the Insured received treatment or advice in the 24 months before the start date, unless declared and accepted in writing."),
        ("p", "The Insurer shall not be liable for cosmetic treatment, experimental procedures or treatment outside Kenya unless pre-approved."),
        ("s", "4. Claims"),
        ("p", "The Insured must notify the Insurer of any hospital admission within 14 days of admission and submit original invoices, discharge summary and prescription records."),
        ("s", "5. Renewal and termination"),
        ("p", "This policy is annual. The Insurer may terminate it on 30 days' written notice for non-payment of premium."),
        ("pb", ""),
        ("t", "POLICY SCHEDULE"),
        ("tbl", [["Policy number", "TGI/MED/2025/118204"], ["Insured", "Grace Akinyi Ouma"], ["Period of insurance", "1 January 2025 to 31 December 2025"],
                 ["Inpatient limit", "KES 1,000,000"], ["Outpatient limit", "KES 100,000"], ["Annual premium", "KES 68,400"], ["Waiting period", "30 days (general), 12 months (chronic)"]]),
        ("p", "Issued at Nairobi on 20 December 2024."), ("sig", "For: Tausi General Insurance Company Limited"),
    ],
    expect=dict(doc_type="insurance_policy", sensitivity="sensitive",
                flags=["ins.exclusion.pre_existing", "ins.claim.notice_window(14)"], not_flags=["ins.missing.policy_schedule", "core.notice_period"],
                review_queue=False, pages=2),
))

# C02 ---------------------------------------------------------------------------------
CLIENTS.append(dict(
    id="C02", file="Salama Takaful fire policy - wording only, schedule missing (scan)", out="scan_pdf", scan=OFFICE,
    client="Amani Bakers Ltd", matter="Fire claim (see J01)",
    blocks=[
        ("c", "SALAMA TAKAFUL INSURANCE LIMITED"), ("t", "TAKAFUL FIRE AND ALLIED PERILS - POLICY WORDING"),
        ("s", "Clause 1. Operative clause"),
        ("p", "In consideration of the contribution paid, the Takaful operator will indemnify the Participant against loss of or damage to the property described, caused by fire, lightning or explosion."),
        ("s", "Clause 6. Claims conditions"),
        ("p", "6.1 The Participant shall take all reasonable steps to minimise the loss."),
        ("p", "6.2 Notice of claim must be given in writing within 7 days of the loss, failing which the operator may decline liability."),
        ("s", "Clause 9. Governing law"),
        ("p", "This policy is governed by the laws of Kenya."),
    ],
    expect=dict(doc_type="insurance_policy", sensitivity="normal",
                flags=["ins.claim.notice_window(7)", "ins.missing.policy_schedule"], review_queue=False, pages=1),
))

# C03 handwritten claim form, phone photo --------------------------------------------
CLIENTS.append(dict(
    id="C03", file="Motor claim form - handwritten (phone photo)", out="jpg", kind="form", scan=dict(phone=True, dpi=150, noise=10, jpeg=60),
    client="Baraka Haulage Ltd", matter="Goods-in-transit loss (see J10)",
    form_title="MOTOR / GOODS IN TRANSIT CLAIM FORM", form_org="MLINZI INSURANCE BROKERS LIMITED",
    fields=[("Name of insured", "Baraka Haulage Ltd"), ("Policy / cover note no.", "MIB/GIT/2025/0441"),
            ("Vehicle registration", "KDH 418Q"), ("Date and time of loss", "19/05/2025  about 4.30 am"),
            ("Place of loss", "Kericho - Kisumu road near Kipsitet"), ("Description of loss", "Truck overturned, cargo of cement 600 bags lost"),
            ("Estimated value of loss (KES)", "6,420,000"), ("Driver's name", "Samuel Kiprono"), ("Reported to police? (Y/N)", "Yes - OB 14/19/05/2025")],
    expect=dict(doc_type="claim_form", sensitivity="normal", flags=[], review_queue=True, pages=1,
                note="handwriting: expect low OCR confidence -> human review queue"),
))

# C04 repudiation letter with stamp + signature ---------------------------------------
CLIENTS.append(dict(
    id="C04", file="Letter declining claim - stamped and signed (scan)", out="scan_pdf", scan=OFFICE, stamp="RECEIVED 07 OCT 2024", signature=True,
    client="Amani Bakers Ltd", matter="Fire claim (see J01)",
    blocks=[
        ("c", "SALAMA TAKAFUL INSURANCE LIMITED"), ("r", "2nd October 2024"),
        ("p", "The Managing Director\nAmani Bakers Limited\nP.O. Box 00000-00500\nNAIROBI"),
        ("p", "Dear Madam,"),
        ("s", "RE: CLAIM NO. STI/CLM/2024/3318 - FIRE AT INDUSTRIAL AREA PREMISES ON 3RD AUGUST 2024"),
        ("p", "We refer to the above claim. Our records show that written notice of the loss was received on 12th August 2024, nine days after the loss. Clause 6.2 of the policy requires written notice within 7 days."),
        ("p", "In addition, our investigations indicate an electrical fault at the premises that was not disclosed at the time of the proposal."),
        ("p", "For these reasons we regret that we are unable to admit liability under the policy. This letter is written without prejudice to any other rights of the operator."),
        ("p", "Yours faithfully,"), ("sig", "Head of Claims"),
    ],
    expect=dict(doc_type="letter", sensitivity="normal", flags=[], review_queue=False, pages=1),
))

# C05 fax-quality assessor report (TIFF) ----------------------------------------------
CLIENTS.append(dict(
    id="C05", file="Loss assessor preliminary report (fax quality)", out="tiff", scan=FAX,
    client="Amani Bakers Ltd", matter="Fire claim (see J01)",
    blocks=[
        ("c", "MFANO LOSS ASSESSORS AND ADJUSTERS"), ("t", "PRELIMINARY LOSS REPORT"),
        ("tbl", [["Insured", "Amani Bakers Limited"], ["Date of loss", "3 August 2024"], ["Date instructed", "5 August 2024"], ["Cause", "Fire, oven room, suspected overheating"]]),
        ("p", "We attended the premises on 5th August 2024 at the request of the insurer. Two deck ovens, one spiral mixer and finished stock were destroyed. The electrical installation outside the oven room was intact."),
        ("tbl", [["Item", "Claimed (KES)", "Assessed (KES)"], ["Deck ovens x2", "8,400,000", "7,650,000"], ["Spiral mixer", "2,150,000", "1,980,500"],
                 ["Stock", "3,700,000", "3,350,000"], ["Total", "14,250,000", "12,980,500"]]),
    ],
    expect=dict(doc_type=None, sensitivity="normal", flags=[], review_queue=True, pages=1, note="100 dpi binarised fax: tables are hard for OCR"),
))

# C06 tenancy agreement, 3 pages, page 2 scanned sideways -----------------------------
CLIENTS.append(dict(
    id="C06", file="Lease of shop G14 Jamhuri Plaza (scan, page 2 sideways)", out="scan_pdf", scan=OFFICE, rotate_pages={2: 90},
    client="Nuru Pharmacy Ltd", matter="Lock-out and rent increase (see J08)",
    blocks=[
        ("t", "LEASE AGREEMENT"),
        ("p", "THIS AGREEMENT is made on 1st February 2018 BETWEEN Jamhuri Plaza Properties Limited (the Landlord) AND Nuru Pharmacy Limited (the Tenant)."),
        ("s", "1. Premises"), ("p", "The Landlord lets to the Tenant shop number G14 on the ground floor of Jamhuri Plaza, Moi Avenue, Nairobi."),
        ("s", "2. Rent"), ("p", "The rent is KES 85,000 per month payable in advance on or before the 5th day of each month. A deposit of KES 170,000 is payable on signing."),
        ("s", "3. Term and renewal"),
        ("p", "3.1 The term is 2 years from 1st February 2018. This lease shall renew automatically for successive terms of 2 years unless either party gives 90 days' written notice before the end of the current term."),
        ("pb", ""),
        ("s", "4. Tenant's obligations"),
        ("p", "4.1 The Tenant shall keep the premises clean and in good repair, fair wear and tear excepted."),
        ("p", "4.2 The Tenant shall indemnify the Landlord against all claims arising from the Tenant's use of the premises."),
        ("s", "5. Landlord's obligations"),
        ("p", "5.1 The Landlord shall keep the common areas lit, clean and secure and shall insure the building."),
        ("pb", ""),
        ("s", "6. Termination"),
        ("p", "Either party may terminate this lease on 90 days' written notice in accordance with the law governing controlled tenancies."),
        ("s", "7. Governing law"), ("p", "This agreement is governed by the laws of Kenya."),
        ("sig", "For the Landlord"), ("sig", "For the Tenant"),
    ],
    expect=dict(doc_type="tenancy_agreement", sensitivity="normal",
                flags=["core.auto_renewal", "core.notice_period(90)", "core.indemnity"], not_flags=["core.termination.missing", "core.governing_law.missing"],
                review_queue=False, pages=3, note="page 2 is rotated 90 degrees: tests auto-rotation / OSD before OCR"),
))

# C07 + C18 employment contract and amended version -----------------------------------
EMP = [
    ("t", "EMPLOYMENT CONTRACT"),
    ("p", "This employment contract is made between Pwani Logistics Limited (the Employer) and Brian Ochieng Otieno (the Employee)."),
    ("s", "1. Position"), ("p", "The Employee is employed as a forklift operator at the Employer's Changamwe depot, Mombasa, with effect from 1st February 2019."),
    ("s", "2. Probation"), ("p", "The first {PROB} months of employment shall be a probation period."),
    ("s", "3. Remuneration"), ("p", "The Employee shall be paid a gross salary of {PAY} per month, subject to statutory deductions (PAYE, NSSF, SHIF and the Housing Levy)."),
    ("s", "4. Hours and leave"), ("p", "Normal working hours are 45 hours per week. The Employee is entitled to 21 working days of annual leave."),
    ("s", "5. Notice"), ("p", "After confirmation, either party may end this contract by giving 30 days' written notice or paying salary in lieu."),
    ("sig", "For the Employer"), ("sig", "Employee"),
]


def _emp(prob, pay):
    return [(k, t.replace("{PROB}", prob).replace("{PAY}", pay)) for k, t in EMP]


CLIENTS.append(dict(
    id="C07", file="Employment contract - Otieno (scan with stain)", out="scan_pdf", scan=dict(OFFICE, stain=True),
    client="Brian Ochieng Otieno", matter="Unfair termination and NSSF (see J02)", blocks=_emp("three (3)", "KES 32,000"),
    expect=dict(doc_type="employment_contract", sensitivity="normal",
                flags=["core.governing_law.missing", "core.notice_period(30)", "core.termination.missing"], review_queue=False, pages=1,
                note="says 'end this contract', never 'terminate': core.termination.missing fires (a known regex blind spot worth a test)"),
))
CLIENTS.append(dict(
    id="C18", file="Employment contract - Otieno - amended 2023 (scan)", out="scan_pdf", scan=OFFICE, version_of="C07",
    client="Brian Ochieng Otieno", matter="Unfair termination and NSSF (see J02)", blocks=_emp("six (6)", "KES 38,500"),
    expect=dict(doc_type="employment_contract", sensitivity="normal", flags=["core.governing_law.missing", "core.notice_period(30)", "core.termination.missing"],
                review_queue=False, pages=1, compare_with="C07", expected_changes=["clause 2: probation 3 -> 6 months", "clause 3: KES 32,000 -> KES 38,500"]),
))

# C08 payslip phone photo --------------------------------------------------------------
CLIENTS.append(dict(
    id="C08", file="Payslip March 2024 - Otieno (phone photo)", out="jpg", scan=dict(phone=True, dpi=200, noise=9, jpeg=62),
    client="Brian Ochieng Otieno", matter="Unfair termination and NSSF (see J02)",
    blocks=[
        ("c", "PWANI LOGISTICS LIMITED"), ("t", "PAYSLIP - MARCH 2024"),
        ("tbl", [["Employee", "Brian Ochieng Otieno"], ["Staff no.", "PL-0773"], ["Department", "Warehouse"]]),
        ("tbl", [["Earnings", "KES", "Deductions", "KES"], ["Basic pay", "33,500", "PAYE", "3,512"], ["House allowance", "5,000", "NSSF Tier I and II", "2,160"],
                 ["", "", "SHIF", "1,059"], ["", "", "Housing Levy", "578"], ["Gross pay", "38,500", "Total deductions", "7,309"]]),
        ("p", "NET PAY: KES 31,191"),
    ],
    expect=dict(doc_type=None, sensitivity="normal", flags=[], review_queue=False, pages=1,
                note="table extraction; links to NSSF Act s.22 and Affordable Housing Act s.4 via matter facts"),
))

# C09 NSSF statement with contribution gap --------------------------------------------
rows = [["Month", "Employer", "Amount (KES)", "Status"]]
for m in ["Jan 2023", "Feb 2023", "Mar 2023"]:
    rows.append([m, "Pwani Logistics Ltd", "2,160", "Received"])
for m in ["Apr 2023", "May 2023", "Jun 2023", "Jul 2023", "Aug 2023"]:
    rows.append([m, "Pwani Logistics Ltd", "0", "Not received"])
rows.append(["...", "", "", ""])
rows.append(["Feb 2025", "Pwani Logistics Ltd", "4,320", "Received (arrears)"])
CLIENTS.append(dict(
    id="C09", file="Member contribution statement (scan)", out="png", scan=CLEAN,
    client="Brian Ochieng Otieno", matter="Unfair termination and NSSF (see J02)",
    blocks=[("c", "MEMBER CONTRIBUTION STATEMENT (SAMPLE LAYOUT)"), ("p", "Member: Brian Ochieng Otieno     Statement date: 10 March 2025"),
            ("tbl", rows), ("p", "This statement is computer generated.")],
    expect=dict(doc_type=None, sensitivity="normal", flags=[], review_queue=False, pages=1,
                note="layout is generic, not a copy of any real NSSF form"),
))

# C10 + C17 demand letter and byte-identical duplicate --------------------------------
CLIENTS.append(dict(
    id="C10", file="Demand letter - Nuru Pharmacy to Jamhuri Plaza (scan)", out="scan_pdf", scan=CLEAN, signature=True,
    client="Nuru Pharmacy Ltd", matter="Lock-out and rent increase (see J08)",
    blocks=[
        ("c", "MFANO & WENZAKE ADVOCATES"), ("cn", "Kimathi Street, Nairobi  |  office@mfano-advocates.example"), ("r", "13th February 2026"),
        ("p", "The Directors\nJamhuri Plaza Properties Limited\nNAIROBI"),
        ("p", "Dear Sirs,"),
        ("s", "RE: UNLAWFUL LOCK-OUT OF SHOP G14, JAMHURI PLAZA"),
        ("p", "We act for Nuru Pharmacy Limited. On 12th February 2026 you locked our client's shop without any order of the Tribunal or the court."),
        ("p", "TAKE NOTICE that unless the premises are re-opened within 24 hours of this letter, we have instructions to file a reference before the Business Premises Rent Tribunal without further notice, and to seek damages and costs."),
        ("p", "Yours faithfully,"), ("sig", "MFANO & WENZAKE ADVOCATES"),
    ],
    expect=dict(doc_type="letter", sensitivity="normal", flags=[], review_queue=False, pages=1),
))

# C11 Swahili letter --------------------------------------------------------------------
CLIENTS.append(dict(
    id="C11", file="Barua ya notisi kwa mpangaji - Swahili (phone photo)", out="jpg", scan=dict(phone=True, dpi=200, noise=8, jpeg=65), language="sw",
    client="Nuru Pharmacy Ltd", matter="Lock-out and rent increase (see J08)",
    blocks=[
        ("c", "JAMHURI PLAZA PROPERTIES LIMITED"), ("r", "6 Januari 2026"),
        ("p", "Kwa: Mkurugenzi, Nuru Pharmacy Limited, Duka G14"),
        ("s", "YAH: NOTISI YA KUONGEZA KODI YA PANGO"),
        ("p", "Tunakujulisha kwamba kuanzia tarehe 1 Februari 2026 kodi ya pango ya duka nambari G14 itaongezeka kutoka shilingi 85,000 hadi shilingi 140,000 kwa mwezi."),
        ("p", "Usipolipa kodi mpya kufikia tarehe 5 Februari 2026, duka litafungwa bila notisi nyingine."),
        ("p", "Wako mwaminifu,"), ("sig", "Meneja wa Jengo"),
    ],
    expect=dict(doc_type=None, sensitivity="normal", flags=[], review_queue=False, pages=1, language="sw",
                note="needs Swahili OCR model (swa); English-only OCR will misread diacritics-free but unfamiliar words"),
))

# C12 mixed PDF: page 1 typed text layer, page 2 scanned stamped page -----------------
CLIENTS.append(dict(
    id="C12", file="Tribunal order - page 1 digital, page 2 scanned with stamp", out="mixed_pdf", scan=OFFICE, stamp="CERTIFIED TRUE COPY",
    client="Nuru Pharmacy Ltd", matter="Lock-out and rent increase (see J08)",
    blocks=[
        ("c", "REPUBLIC OF KENYA"), ("c", "IN THE BUSINESS PREMISES RENT TRIBUNAL AT NAIROBI"), ("c", "TRIBUNAL CASE NO. E640 OF 2025"),
        ("t", "ORDER"),
        ("p", "UPON hearing the advocates for the Tenant and the Landlord, and upon reading the ruling delivered on 30th April 2026, IT IS HEREBY ORDERED THAT:"),
        ("p", "1. The Landlord shall restore the Tenant to shop G14, Jamhuri Plaza, within 24 hours."),
        ("p", "2. The rent increase notice dated 6th January 2026 is set aside."),
        ("pb", ""),
        ("p", "3. Costs of the reference are awarded to the Tenant."),
        ("p", "GIVEN under my hand and the seal of the Tribunal at Nairobi on 30th April 2026."),
        ("sig", "VICE CHAIR"),
    ],
    expect=dict(doc_type="court_order", sensitivity="normal", flags=[], review_queue=False, pages=2,
                note="page 1 has a text layer (method=text); page 2 is image-only (method=ocr)"),
))

# C13 handwritten witness statement, poor ----------------------------------------------
CLIENTS.append(dict(
    id="C13", file="Witness statement - handwritten, 2 pages (poor scan)", out="scan_pdf", kind="handwritten", scan=POOR,
    client="Baraka Haulage Ltd", matter="Goods-in-transit loss (see J10)",
    hand_pages=[
        ["WITNESS STATEMENT", "Name: Samuel Kiprono", "Occupation: Driver, Baraka Haulage Ltd", "",
         "On 19th May 2025 I was driving KDH 418Q from", "Nakuru to Kisumu with 600 bags of cement.",
         "Near Kipsitet at about 4.30 am the road was wet", "and a matatu overtook from the opposite side.",
         "I swerved to avoid it and the truck overturned."],
        ["The cargo was lost and some was taken by people", "who came to the scene. I reported at the police",
         "station the same morning. I was not injured.", "", "I make this statement believing it to be true.",
         "", "Signed: ____________   Date: 21/05/2025"],
    ],
    expect=dict(doc_type=None, sensitivity="normal", flags=[], review_queue=True, pages=2, note="handwriting + poor photocopy: every page should be queued for review"),
))

# C14 medical report (sensitive) ---------------------------------------------------------
CLIENTS.append(dict(
    id="C14", file="Medical report - Grace Akinyi Ouma (scan)", out="scan_pdf", scan=CLEAN, signature=True,
    client="Grace Akinyi Ouma", matter="Medical claim refusal",
    blocks=[
        ("c", "MFANO MEDICAL CENTRE"), ("t", "MEDICAL REPORT"), ("r", "4th March 2025"),
        ("p", "Patient: Grace Akinyi Ouma     Age: 41"),
        ("p", "The patient was admitted on 14th February 2025 with acute abdominal pain and discharged on 19th February 2025 after an appendectomy."),
        ("p", "Diagnosis: acute appendicitis. There is no record in our files of any earlier treatment for this condition. In my opinion the condition was not pre-existing."),
        ("p", "Total hospital bill: KES 412,750 (itemised invoice attached)."),
        ("sig", "Consultant Surgeon"),
    ],
    expect=dict(doc_type="medical_report", sensitivity="sensitive", flags=[], review_queue=False, pages=1,
                note="must be tagged sensitive (health data, DPA s.2) -> stricter access, consent for cross-border"),
))

# C15 near-blank page ------------------------------------------------------------------
CLIENTS.append(dict(
    id="C15", file="Blank page scanned by mistake", out="scan_pdf", scan=dict(POOR, noise=18), blank=True,
    client="Grace Akinyi Ouma", matter="Medical claim refusal", blocks=[("p", " ")],
    expect=dict(doc_type=None, sensitivity="normal", flags=[], review_queue=True, pages=1, note="empty OCR output: must not crash; should be flagged"),
))

# C16 password-protected digital policy ----------------------------------------------------
CLIENTS.append(dict(
    id="C16", file="Policy certificate emailed by insurer - password protected", out="text_pdf", password="lygo-test",
    client="Grace Akinyi Ouma", matter="Medical claim refusal",
    blocks=[("c", "TAUSI GENERAL INSURANCE COMPANY LIMITED"), ("t", "CERTIFICATE OF INSURANCE"),
            ("p", "Policy number TGI/MED/2025/118204. Insured: Grace Akinyi Ouma. Period: 1 January 2025 to 31 December 2025.")],
    expect=dict(doc_type=None, sensitivity="normal", flags=[], review_queue=False, pages=1,
                note="password_protected=True: pipeline should stop and ask for the password, not OCR"),
))
