"""Synthetic judgments. All parties, advocates, judges and case numbers are fictional.

Block format shared by every renderer:
  ("c", text)   centred bold line (court header)
  ("cn", text)  centred normal line
  ("t", text)   title (JUDGMENT / RULING)
  ("s", text)   sub-heading (bold, left)
  ("n", text)   numbered paragraph (auto-numbered by the renderer)
  ("p", text)   plain paragraph
  ("r", text)   right-aligned line
Citation classes used in the ground truth:
  statute_in_corpus      real section, present in the 'Acts and Laws' folder (verifiable)
  statute_not_in_corpus  real Act, not in the corpus (should resolve as "not found", not "false")
  case_in_set            fictional case that exists in THIS synthetic set (firm KB cross-reference)
  case_external          fictional case, not in any corpus (should be "not found")
  fabricated             citation that cannot exist (e.g. a section number beyond the Act) -> must FAIL verification
"""

FOOT = "SYNTHETIC TEST DOCUMENT - fictional parties and case numbers - generated for Lygo QA - not a real record"


def header(court, division, case_line, parties, kind, coram, date):
    b = [("c", "REPUBLIC OF KENYA"), ("c", court)]
    if division:
        b.append(("c", division))
    b.append(("c", case_line))
    b.append(("cn", "BETWEEN"))
    for i, (name, role) in enumerate(parties):
        if i and parties[i - 1][1] != role:
            b.append(("cn", "AND"))
        b.append(("cn", f"{name} ........ {role}"))
    b.append(("t", kind))
    b.append(("p", f"Coram: {coram}"))
    b.append(("p", f"Delivered on {date}"))
    return b


JUDGMENTS = []

# ---------------------------------------------------------------- J01 Takaful claim
JUDGMENTS.append(dict(
    id="J01", file="Amani Bakers Ltd v Salama Takaful Insurance Ltd (Commercial Case E914of2025) [2026] KEHC 99104 (KLR) (12March2026) (Judgment)",
    fmt="pdf", practice_area="insurance", sensitivity="normal", kind="judgment", court_level="High Court",
    citation="[2026] KEHC 99104 (KLR)", date="2026-03-12",
    blocks=header("IN THE HIGH COURT OF KENYA AT NAIROBI", "COMMERCIAL AND TAX DIVISION",
                  "COMMERCIAL CASE NO. E914 OF 2025",
                  [("AMANI BAKERS LIMITED", "PLAINTIFF"), ("SALAMA TAKAFUL INSURANCE LIMITED", "DEFENDANT")],
                  "JUDGMENT", "Hon. Justice R. M. Odhiambo-Wekesa", "12th March 2026") + [
        ("s", "Introduction"),
        ("n", "The Plaintiff, a bakery operating in Industrial Area, Nairobi, held a Takaful fire and allied perils cover with the Defendant under certificate number STI/FIR/2024/00871. On 3rd August 2024 a fire broke out in the Plaintiff's oven room and destroyed machinery and stock valued by the Plaintiff at KES 14,250,000."),
        ("n", "The Defendant declined the claim by a letter dated 2nd October 2024 on the ground that the Plaintiff had failed to notify the loss within 7 days and had not disclosed an earlier electrical fault. The Plaintiff seeks the sum of KES 14,250,000, interest and costs."),
        ("s", "The Plaintiff's case"),
        ("n", "The Plaintiff's managing director, Ms. Wanjiru Kibet, testified that she telephoned the Defendant's branch on the day after the fire and followed up with an email on 9th August 2024. She produced the email, the loss assessor's preliminary report and the policy schedule."),
        ("n", "She denied that any electrical fault had been reported before the policy incepted and stated that the inspection by the Defendant's surveyor in January 2024 raised no concern."),
        ("s", "The Defendant's case"),
        ("n", "The Defendant's claims manager testified that written notice was received on the ninth day after the loss and that clause 6.2 of the policy wording requires written notice within 7 days. He accepted in cross-examination that the Defendant appointed a loss adjuster on 5th August 2024."),
        ("s", "Issues for determination"),
        ("n", "The issues for determination are: (a) whether the Plaintiff gave notice of the loss as required by the policy; (b) whether there was material non-disclosure; and (c) what relief, if any, should issue."),
        ("s", "Analysis and determination"),
        ("n", "The policy was issued as Takaful insurance business, which is recognised and regulated under section 19A of the Insurance Act (Cap. 487). Nothing in that section or in the policy wording displaces the ordinary rule that a condition relied on to defeat a claim must be strictly proved by the insurer."),
        ("n", "An insurer that appoints a loss adjuster two days after a loss cannot credibly maintain that it lacked notice. I am guided by Mwangaza Holdings Ltd v Tausi Insurance Co Ltd [2019] KEHC 90211 (KLR), where the court held that actual knowledge of the insurer satisfies the purpose of a notice clause."),
        ("n", "On non-disclosure, the surveyor's report of January 2024 records the electrical installation as satisfactory. The Defendant called no evidence of any earlier fault. The defence of non-disclosure therefore fails."),
        ("n", "The right to property under Article 40 of the Constitution is not engaged directly between these private parties, and I decide the matter on the contract alone."),
        ("n", "On quantum, the assessor's final figure of KES 12,980,500 was not challenged and I adopt it in preference to the Plaintiff's own estimate."),
        ("s", "Orders"),
        ("n", "Judgment is entered for the Plaintiff against the Defendant for KES 12,980,500 with interest at court rates from the date of filing until payment in full. The Defendant shall bear the costs of the suit."),
        ("r", "R. M. ODHIAMBO-WEKESA"),
        ("r", "JUDGE"),
    ],
    citations=[
        ("section 19A of the Insurance Act", "Insurance Act s.19A", "statute_in_corpus"),
        ("Mwangaza Holdings Ltd v Tausi Insurance Co Ltd [2019] KEHC 90211 (KLR)", None, "case_external"),
        ("Article 40 of the Constitution", "Constitution Art.40", "statute_in_corpus"),
    ],
    amounts=["KES 14,250,000", "KES 12,980,500"], deadlines=["7 days"],
))

# ---------------------------------------------------------------- J02 NSSF non-remittance
JUDGMENTS.append(dict(
    id="J02", file="Otieno v Pwani Logistics Ltd (Cause E2207of2025) [2026] KEELRC 99318 (KLR) (5June2026) (Judgment)",
    fmt="docx", numbering="typed", practice_area="employment", sensitivity="normal", kind="judgment", court_level="ELRC",
    citation="[2026] KEELRC 99318 (KLR)", date="2026-06-05",
    blocks=header("IN THE EMPLOYMENT AND LABOUR RELATIONS COURT AT MOMBASA", None,
                  "CAUSE NO. E2207 OF 2025",
                  [("BRIAN OCHIENG OTIENO", "CLAIMANT"), ("PWANI LOGISTICS LIMITED", "RESPONDENT")],
                  "JUDGMENT", "Hon. Lady Justice F. A. Nduta-Mbaraka", "5th June 2026") + [
        ("s", "Background"),
        ("n", "The Claimant was employed by the Respondent as a forklift operator from 1st February 2019 at a gross monthly salary of KES 38,500. He was dismissed by a letter dated 14th March 2025 for alleged absence without leave."),
        ("n", "He claims that the dismissal was unfair, that statutory deductions for the National Social Security Fund were made from his pay but not remitted for 22 months, and that he was not paid for accrued leave."),
        ("s", "Evidence"),
        ("n", "The Claimant produced his payslips for 2023 and 2024, each of which shows a deduction of KES 2,160 described as 'NSSF Tier I and II'. He also produced a member statement from the Fund showing no contributions received between April 2023 and January 2025."),
        ("n", "The Respondent's human resources officer conceded that the remittances were delayed because of cash-flow difficulties but said the arrears were being cleared in instalments."),
        ("s", "Issues"),
        ("n", "The issues are whether the termination was fair, whether the Respondent breached its obligations in respect of social security contributions, and the remedies available."),
        ("s", "Analysis"),
        ("n", "An employer is under a duty to register itself and its employees with the Fund, as provided in section 19 of the National Social Security Fund Act, 2013. Having deducted contributions from wages, the employer holds them for the Fund and may not apply them to its own purposes."),
        ("n", "Section 22 of the same Act restricts what an employer may do with deductions made from an employee's wages. Retaining deducted contributions for 22 months is a clear breach of that provision."),
        ("n", "On the termination, the Respondent did not issue a show-cause letter or hold a hearing. A termination without a fair procedure is unfair within the meaning of section 45 of the Employment Act, 2007, and I so find."),
        ("n", "I have considered Otieno v Pwani Logistics, an earlier interlocutory ruling in this matter, and Kaluma v Bahari Freight Services Ltd [2022] KEELRC 90554 (KLR), which awarded compensation equivalent to six months' salary on similar facts."),
        ("s", "Determination"),
        ("n", "The Claimant is awarded: (a) compensation equivalent to 6 months' gross salary, KES 231,000; (b) payment in lieu of 18 days' accrued leave, KES 23,100; and (c) an order directing the Respondent to remit all outstanding contributions, with penalties, to the Fund within 30 days and to file proof of remittance in court."),
        ("n", "The Respondent shall pay the costs of the claim."),
        ("r", "F. A. NDUTA-MBARAKA"),
        ("r", "JUDGE"),
    ],
    citations=[
        ("section 19 of the National Social Security Fund Act, 2013", "NSSF Act s.19", "statute_in_corpus"),
        ("Section 22 of the same Act", "NSSF Act s.22", "statute_in_corpus"),
        ("section 45 of the Employment Act, 2007", "Employment Act s.45", "statute_not_in_corpus"),
        ("Kaluma v Bahari Freight Services Ltd [2022] KEELRC 90554 (KLR)", None, "case_external"),
    ],
    amounts=["KES 38,500", "KES 2,160", "KES 231,000", "KES 23,100"], deadlines=["30 days"],
))

# ---------------------------------------------------------------- J03 Survivors' benefits, minors (sensitive)
JUDGMENTS.append(dict(
    id="J03", file="In re Estate of JKM (Succession Cause E331of2025) [2026] KEHC 99577 (KLR) (20August2026) (Ruling)",
    fmt="docx", numbering="auto", practice_area="family", sensitivity="sensitive", kind="ruling", court_level="High Court",
    citation="[2026] KEHC 99577 (KLR)", date="2026-08-20",
    blocks=header("IN THE HIGH COURT OF KENYA AT NAKURU", "FAMILY DIVISION",
                  "SUCCESSION CAUSE NO. E331 OF 2025",
                  [("IN THE MATTER OF THE ESTATE OF JKM (DECEASED)", ""), ("MWK", "APPLICANT"), ("PNM", "RESPONDENT")],
                  "RULING", "Hon. Justice S. K. Barasa-Lemiso", "20th August 2026") + [
        ("n", "This ruling concerns an application by MWK, the widow of the deceased, for an order directing that survivors' benefits payable by the National Social Security Fund be held in trust for the three minor children of the deceased. The names of the parties and children are anonymised to protect the children."),
        ("n", "The deceased, a member of the Fund, died on 11th January 2025. The Respondent, PNM, who describes herself as a second wife under customary law, objects and asks that the benefit be shared equally between the two households."),
        ("n", "The Fund has filed a statement confirming that a survivors' pension and a funeral grant are payable, under sections 37 and 40 respectively of the National Social Security Fund Act, 2013, and that it will abide by the directions of the court."),
        ("n", "Medical records produced by the Applicant show that the youngest child has a chronic medical condition requiring monthly treatment. The court has considered these records only for the purpose of assessing need and directs that they be sealed."),
        ("n", "Article 53(2) of the Constitution requires that a child's best interests are of paramount importance in every matter concerning the child. The court must also have regard to the Children Act, 2022."),
        ("n", "The question whether the Respondent is a dependant within the meaning of the Law of Succession Act is a matter for the hearing of the cause and cannot be resolved on affidavit evidence."),
        ("n", "Pending that hearing, the survivors' pension shall be paid into an interest-earning account in the joint names of the advocates for both parties, and KES 45,000 per month shall be released to the Applicant for the maintenance of the minors."),
        ("n", "The funeral grant, having already been applied to the funeral expenses, is not in issue."),
        ("n", "Costs shall be in the cause."),
        ("r", "S. K. BARASA-LEMISO"),
        ("r", "JUDGE"),
    ],
    citations=[
        ("sections 37 and 40 ... National Social Security Fund Act, 2013", "NSSF Act s.37, s.40", "statute_in_corpus"),
        ("Article 53(2) of the Constitution", "Constitution Art.53", "statute_in_corpus"),
        ("Children Act, 2022", "Children Act", "statute_not_in_corpus"),
        ("Law of Succession Act", "Law of Succession Act", "statute_not_in_corpus"),
    ],
    amounts=["KES 45,000"], deadlines=[],
))

# ---------------------------------------------------------------- J04 Retirement scheme deregistration (+ v2)
J04_BLOCKS = header("IN THE RETIREMENT BENEFITS APPEALS TRIBUNAL AT NAIROBI", None,
                    "TRIBUNAL APPEAL NO. E019 OF 2025",
                    [("TRUSTEES OF THE MAZINGIRA STAFF PENSION SCHEME", "APPELLANTS"),
                     ("CHIEF EXECUTIVE OFFICER, RETIREMENT BENEFITS AUTHORITY", "RESPONDENT")],
                    "JUDGMENT", "C. W. Gitau-Ronoh (Chairperson), A. O. Makokha, H. S. Abdi (Members)", "{DATE}") + [
    ("s", "Introduction"),
    ("n", "The Appellants challenge the decision of the Respondent dated 17th April 2025 deregistering the Mazingira Staff Pension Scheme for failure to submit audited accounts for the years 2022 and 2023."),
    ("n", "The Scheme has 412 members and assets of approximately KES 1.3 billion. The Appellants say that the accounts were late because the sponsor changed auditors, and that deregistration is disproportionate."),
    ("s", "The law"),
    ("n", "The power to deregister a scheme is set out in section 28 of the Retirement Benefits Act, 1997. Under section 31 of the Act, the register kept by the Authority is admissible as evidence of the matters recorded in it."),
    ("n", "Article 47 of the Constitution guarantees administrative action that is expeditious, efficient, lawful, reasonable and procedurally fair, and requires written reasons where a right is adversely affected."),
    ("s", "Analysis"),
    ("n", "The record shows that the Respondent issued a notice of intention to deregister on 2nd February 2025 and gave the Appellants 14 days to respond. The Appellants responded within time, enclosing draft accounts and a letter from the new auditors."),
    ("n", "The decision letter does not engage with that response at all. A decision that ignores the representations it invited is not procedurally fair."),
    ("n", "We were referred to Trustees of Kilimo Provident Fund v Retirement Benefits Authority [2021] KERBT 90017 (KLR), where the Tribunal set aside a deregistration that had been made without considering mitigation. We agree with that approach."),
    ("s", "Orders"),
    ("n", "{ORDER1}"),
    ("n", "{ORDER2}"),
    ("n", "Each party shall bear its own costs."),
    ("r", "C. W. GITAU-RONOH"),
    ("r", "CHAIRPERSON"),
]
J04_CITES = [
    ("section 28 of the Retirement Benefits Act, 1997", "Retirement Benefits Act s.28", "statute_in_corpus"),
    ("section 31 of the Act", "Retirement Benefits Act s.31", "statute_in_corpus"),
    ("Article 47 of the Constitution", "Constitution Art.47", "statute_in_corpus"),
    ("Trustees of Kilimo Provident Fund v Retirement Benefits Authority [2021] KERBT 90017 (KLR)", None, "case_external"),
]


def _fill(blocks, **kw):
    out = []
    for k, t in blocks:
        for key, val in kw.items():
            t = t.replace("{" + key + "}", val)
        out.append((k, t))
    return out


JUDGMENTS.append(dict(
    id="J04", file="Trustees of Mazingira Staff Pension Scheme v Retirement Benefits Authority (Tribunal Appeal E019of2025) [2026] KERBT 99042 (KLR) (9July2026) (Judgment)",
    fmt="pdf", practice_area="pensions", sensitivity="normal", kind="judgment", court_level="Tribunal",
    citation="[2026] KERBT 99042 (KLR)", date="2026-07-09",
    blocks=_fill(J04_BLOCKS, DATE="9th July 2026",
                 ORDER1="The appeal is allowed and the decision of the Respondent dated 17th April 2025 is set aside.",
                 ORDER2="The Appellants shall file the audited accounts for 2022 and 2023 within 60 days of this judgment."),
    citations=J04_CITES, amounts=["KES 1.3 billion"], deadlines=["14 days", "60 days"],
))
JUDGMENTS.append(dict(
    id="J04b", file="Trustees of Mazingira Staff Pension Scheme v Retirement Benefits Authority (Tribunal Appeal E019of2025) [2026] KERBT 99042 (KLR) (16July2026) (Corrected Judgment)",
    fmt="pdf", practice_area="pensions", sensitivity="normal", kind="judgment", court_level="Tribunal",
    citation="[2026] KERBT 99042 (KLR)", date="2026-07-16", version_of="J04",
    blocks=_fill(J04_BLOCKS, DATE="9th July 2026 (corrected under the slip rule on 16th July 2026)",
                 ORDER1="The appeal is allowed and the decision of the Respondent dated 17th April 2025 is set aside. The matter is remitted to the Respondent for fresh consideration.",
                 ORDER2="The Appellants shall file the audited accounts for 2022 and 2023 within 90 days of this judgment."),
    citations=J04_CITES, amounts=["KES 1.3 billion"], deadlines=["14 days", "90 days"],
    expected_changes=["date line changed", "order 1: remittal added", "order 2: 60 days -> 90 days (numeric_or_date_change)"],
))

# ---------------------------------------------------------------- J05 Housing levy (TAT)
JUDGMENTS.append(dict(
    id="J05", file="Ujenzi Bora Contractors Ltd v Commissioner of Domestic Taxes (Tax Appeal E1102of2025) [2026] KETAT 99716 (KLR) (24September2026) (Judgment)",
    fmt="docx", numbering="auto", practice_area="tax", sensitivity="normal", kind="judgment", court_level="Tribunal",
    citation="[2026] KETAT 99716 (KLR)", date="2026-09-24",
    blocks=header("IN THE TAX APPEALS TRIBUNAL AT NAIROBI", None, "TAX APPEAL NO. E1102 OF 2025",
                  [("UJENZI BORA CONTRACTORS LIMITED", "APPELLANT"), ("COMMISSIONER OF DOMESTIC TAXES", "RESPONDENT")],
                  "JUDGMENT", "E. N. Mutua-Chelagat (Chairperson), J. K. Lusweti, M. A. Haji (Members)", "24th September 2026") + [
        ("n", "The Appellant, a building contractor with 146 employees, appeals against an assessment of KES 3,846,220 in unpaid Affordable Housing Levy, penalties and interest for the period April 2024 to March 2025."),
        ("n", "The Levy is imposed by section 4 of the Affordable Housing Act, 2024 on both employers and employees. The consequences of default are provided in section 7 of that Act."),
        ("n", "The Appellant argues that 61 of its workers were casual labourers engaged through a labour contractor, Kazi Haraka Agencies, and that the Levy for those workers was the responsibility of that contractor."),
        ("n", "The Respondent relies on muster rolls showing that the Appellant's site managers supervised and paid the workers directly, and argues that the Appellant was their employer in substance."),
        ("n", "Having examined the muster rolls, the payment vouchers and the labour supply agreement, the Tribunal finds that the Appellant controlled the work, set the hours and paid the wages. The Appellant was the employer for the purposes of the Levy."),
        ("n", "However, the Respondent computed penalties from April 2024 even though the Appellant's obligations in respect of the casual workers only became clear after the Respondent's public notice of 18th June 2024. Penalties for the period before that notice are not justified, given the duty of fairness under Article 47 of the Constitution."),
        ("n", "The appeal is partly allowed. The principal Levy of KES 2,915,400 is upheld. The Respondent shall recompute penalties and interest from 18th June 2024 and issue an amended assessment within 30 days."),
        ("n", "Each party shall bear its own costs."),
        ("r", "E. N. MUTUA-CHELAGAT"),
        ("r", "CHAIRPERSON"),
    ],
    citations=[
        ("section 4 of the Affordable Housing Act, 2024", "Affordable Housing Act s.4", "statute_in_corpus"),
        ("section 7 of that Act", "Affordable Housing Act s.7", "statute_in_corpus"),
        ("Article 47 of the Constitution", "Constitution Art.47", "statute_in_corpus"),
    ],
    amounts=["KES 3,846,220", "KES 2,915,400"], deadlines=["30 days"],
))

# ---------------------------------------------------------------- J06 Practising certificate (password-protected PDF)
JUDGMENTS.append(dict(
    id="J06", file="Mutiso v Insurance Institute of Kenya Council (Appeal E007of2026) [2026] KEHC 99852 (KLR) (2October2026) (Judgment)",
    fmt="pdf", password="lygo-test", practice_area="insurance", sensitivity="normal", kind="judgment", court_level="High Court",
    citation="[2026] KEHC 99852 (KLR)", date="2026-10-02",
    blocks=header("IN THE HIGH COURT OF KENYA AT MACHAKOS", None, "CIVIL APPEAL NO. E007 OF 2026",
                  [("DANIEL KYALO MUTISO", "APPELLANT"), ("THE COUNCIL, INSURANCE INSTITUTE OF KENYA", "RESPONDENT")],
                  "JUDGMENT", "Hon. Justice M. W. Njagi-Okumu", "2nd October 2026") + [
        ("n", "The Appellant, an insurance agent, appeals against the decision of the Respondent cancelling his registration and withdrawing his practising certificate for alleged misappropriation of premiums collected from three clients."),
        ("n", "The issue of practising certificates is provided for in section 22 of the Insurance Professionals Act, 2021, and cancellation of registration in section 28 of that Act."),
        ("n", "The Appellant was not given the complaint or the documents relied on before the hearing before the Disciplinary Committee, and the hearing proceeded in his absence on a date of which he had been given 2 days' notice."),
        ("n", "Article 50(1) of the Constitution guarantees the right to have a dispute decided in a fair hearing. A hearing on 2 days' notice, without disclosure of the case to be met, is not a fair hearing."),
        ("n", "The appeal is allowed. The cancellation is set aside and the complaint is remitted to a differently constituted Disciplinary Committee, which shall hear it within 45 days after supplying the Appellant with all documents relied on."),
        ("n", "The Respondent shall pay the costs of the appeal."),
        ("r", "M. W. NJAGI-OKUMU"),
        ("r", "JUDGE"),
    ],
    citations=[
        ("section 22 of the Insurance Professionals Act, 2021", "Insurance Professionals Act s.22", "statute_in_corpus"),
        ("section 28 of that Act", "Insurance Professionals Act s.28", "statute_in_corpus"),
        ("Article 50(1) of the Constitution", "Constitution Art.50", "statute_in_corpus"),
    ],
    amounts=[], deadlines=["2 days", "45 days"],
))

# ---------------------------------------------------------------- J07 Custody (sensitive, initials)
JUDGMENTS.append(dict(
    id="J07", file="AWN v DKO (Children Case E118of2025) [2026] KEMC 99263 (KLR) (14October2026) (Judgment)",
    fmt="docx", numbering="typed", practice_area="family", sensitivity="sensitive", kind="judgment", court_level="Magistrates Court",
    citation="[2026] KEMC 99263 (KLR)", date="2026-10-14",
    blocks=header("IN THE CHIEF MAGISTRATE'S COURT AT KISUMU", "CHILDREN'S COURT",
                  "CHILDREN CASE NO. E118 OF 2025",
                  [("AWN", "PLAINTIFF"), ("DKO", "DEFENDANT")],
                  "JUDGMENT", "Hon. L. A. Ombati-Wafula, Principal Magistrate", "14th October 2026") + [
        ("n", "The Plaintiff, the mother of two minors aged 9 and 6, seeks legal custody, care and control of the children and maintenance of KES 60,000 per month. The Defendant, their father, seeks joint custody."),
        ("n", "The children's officer's report, filed on 3rd July 2026, records that both children live with the Plaintiff, attend school near her home and are attached to both parents. The report notes that the older child has a learning disability and receives specialised tuition."),
        ("n", "The best interests of the child are paramount under Article 53(2) of the Constitution. Parental responsibility is shared by both parents unless the court orders otherwise."),
        ("n", "The Defendant's payslip shows net earnings of KES 142,300 per month. The Plaintiff earns KES 51,000 per month as a nurse."),
        ("n", "Joint legal custody is granted. Actual custody, care and control is granted to the Plaintiff. The Defendant shall have access on alternate weekends and half of every school holiday."),
        ("n", "The Defendant shall pay maintenance of KES 40,000 per month and shall meet 70% of school fees and medical expenses, including the specialised tuition."),
        ("n", "There shall be no order as to costs, this being a family matter."),
        ("r", "L. A. OMBATI-WAFULA"),
        ("r", "PRINCIPAL MAGISTRATE"),
    ],
    citations=[("Article 53(2) of the Constitution", "Constitution Art.53", "statute_in_corpus")],
    amounts=["KES 60,000", "KES 142,300", "KES 51,000", "KES 40,000"], deadlines=[],
))

# ---------------------------------------------------------------- J08 Business premises tenancy
JUDGMENTS.append(dict(
    id="J08", file="Nuru Pharmacy Ltd v Jamhuri Plaza Properties Ltd (Tribunal Case E640of2025) [2026] KEBPRT 99390 (KLR) (30April2026) (Ruling)",
    fmt="pdf", practice_area="property", sensitivity="normal", kind="ruling", court_level="Tribunal",
    citation="[2026] KEBPRT 99390 (KLR)", date="2026-04-30",
    blocks=header("IN THE BUSINESS PREMISES RENT TRIBUNAL AT NAIROBI", None, "TRIBUNAL CASE NO. E640 OF 2025",
                  [("NURU PHARMACY LIMITED", "TENANT"), ("JAMHURI PLAZA PROPERTIES LIMITED", "LANDLORD")],
                  "RULING", "Hon. G. N. Kiprop-Atieno, Vice Chair", "30th April 2026") + [
        ("n", "The Tenant has occupied shop number G14 at Jamhuri Plaza since 2018 under a lease that, by clause 3.1, renews automatically for successive terms of 2 years unless either party gives 90 days' written notice."),
        ("n", "On 6th January 2026 the Landlord issued a notice increasing rent from KES 85,000 to KES 140,000 per month with effect from 1st February 2026 and, when the Tenant refused to pay, locked the shop on 12th February 2026."),
        ("n", "A landlord may not lock out a tenant or levy distress without following the procedure for termination or alteration of terms of a controlled tenancy. The lock-out was unlawful."),
        ("n", "The right to property in Article 40 of the Constitution does not entitle a landlord to resort to self-help against a tenant in occupation."),
        ("n", "The Landlord shall restore the Tenant to the premises within 24 hours. The notice of 6th January 2026 is set aside. The Landlord may seek a review of rent in the proper manner, supported by a valuation report."),
        ("n", "Costs of the reference are awarded to the Tenant."),
        ("r", "G. N. KIPROP-ATIENO"),
        ("r", "VICE CHAIR"),
    ],
    citations=[("Article 40 of the Constitution", "Constitution Art.40", "statute_in_corpus")],
    amounts=["KES 85,000", "KES 140,000"], deadlines=["90 days", "24 hours"],
))

# ---------------------------------------------------------------- J09 Data privacy petition (cites J01 and J08: in-set)
JUDGMENTS.append(dict(
    id="J09", file="Chebet v Haraka Mobile Loans Ltd (Petition E271of2025) [2026] KEHC 99731 (KLR) (22October2026) (Judgment)",
    fmt="docx", numbering="auto", practice_area="data_protection", sensitivity="normal", kind="judgment", court_level="High Court",
    citation="[2026] KEHC 99731 (KLR)", date="2026-10-22",
    blocks=header("IN THE HIGH COURT OF KENYA AT ELDORET", "CONSTITUTIONAL AND HUMAN RIGHTS DIVISION", "PETITION NO. E271 OF 2025",
                  [("FAITH JEPKOECH CHEBET", "PETITIONER"), ("HARAKA MOBILE LOANS LIMITED", "RESPONDENT")],
                  "JUDGMENT", "Hon. Justice T. O. Wambua-Keter", "22nd October 2026") + [
        ("n", "The Petitioner borrowed KES 4,500 from the Respondent through its mobile application. When the loan fell into arrears, the Respondent sent messages to 37 people in the Petitioner's phone contacts describing her as a defaulter."),
        ("n", "She contends that this violated her right to privacy under Article 31 of the Constitution and her right to human dignity under Article 28, and that the processing of her contacts' data breached the principles in section 25 of the Data Protection Act, 2019."),
        ("n", "The Respondent says that the Petitioner consented to access to her contacts when she installed the application."),
        ("n", "Consent to access a contact list for identity verification is not consent to disclose the borrower's debt to those contacts. The disclosure served no lawful purpose and was designed to shame the Petitioner."),
        ("n", "Courts have repeatedly held that a commercial party must prove the precise terms it relies on, as in Amani Bakers Ltd v Salama Takaful Insurance Ltd [2026] KEHC 99104 (KLR), and that self-help remedies are not available where the law provides a procedure, as in Nuru Pharmacy Ltd v Jamhuri Plaza Properties Ltd [2026] KEBPRT 99390 (KLR)."),
        ("n", "A declaration is issued that the Respondent violated the Petitioner's rights under Articles 28 and 31 of the Constitution. The Respondent shall pay general damages of KES 350,000 and shall delete the contact data of third parties obtained from the Petitioner's phone within 14 days."),
        ("n", "The Respondent shall pay the costs of the petition."),
        ("r", "T. O. WAMBUA-KETER"),
        ("r", "JUDGE"),
    ],
    citations=[
        ("Article 31 of the Constitution", "Constitution Art.31", "statute_in_corpus"),
        ("Article 28", "Constitution Art.28", "statute_in_corpus"),
        ("section 25 of the Data Protection Act, 2019", "Data Protection Act s.25", "statute_not_in_corpus"),
        ("Amani Bakers Ltd v Salama Takaful Insurance Ltd [2026] KEHC 99104 (KLR)", "J01", "case_in_set"),
        ("Nuru Pharmacy Ltd v Jamhuri Plaza Properties Ltd [2026] KEBPRT 99390 (KLR)", "J08", "case_in_set"),
    ],
    amounts=["KES 4,500", "KES 350,000"], deadlines=["14 days"],
))

# ---------------------------------------------------------------- J10 Contains fabricated authorities (guardrail test)
JUDGMENTS.append(dict(
    id="J10", file="Baraka Haulage Ltd v Mlinzi Insurance Brokers Ltd (Civil Case E808of2025) [2026] KEHC 99905 (KLR) (29October2026) (Judgment)",
    fmt="pdf", practice_area="insurance", sensitivity="normal", kind="judgment", court_level="High Court",
    citation="[2026] KEHC 99905 (KLR)", date="2026-10-29",
    blocks=header("IN THE HIGH COURT OF KENYA AT KISUMU", None, "CIVIL CASE NO. E808 OF 2025",
                  [("BARAKA HAULAGE LIMITED", "PLAINTIFF"), ("MLINZI INSURANCE BROKERS LIMITED", "DEFENDANT")],
                  "JUDGMENT", "Hon. Justice K. N. Achieng-Mwita", "29th October 2026") + [
        ("n", "The Plaintiff, a haulage company, instructed the Defendant, an insurance broker, to renew goods-in-transit cover for its fleet of 12 trucks. The Defendant collected a premium of KES 1,184,000 but failed to place the cover with any insurer."),
        ("n", "On 19th May 2025 one of the trucks overturned at Kericho and its cargo, valued at KES 6,420,000, was lost. No insurer accepted the claim because no policy existed."),
        ("n", "The Defendant's advocates submitted that a broker's liability is limited to a refund of the premium by section 312 of the Insurance Act and by the decision in Kamau v Imara Brokers Ltd [2018] KECA 45512 (KLR)."),
        ("n", "No such section exists in the Insurance Act, which has no section numbered 312, and counsel was unable to produce a copy of the decision cited, which could not be found in any law report. The court reminds advocates of their duty to verify every authority placed before it."),
        ("n", "A broker who accepts instructions and a premium undertakes to exercise reasonable care to place the cover. The Defendant breached that duty and is liable for the loss that the cover would have met."),
        ("n", "Judgment is entered for the Plaintiff for KES 6,420,000 with interest from 19th May 2025, and costs."),
        ("r", "K. N. ACHIENG-MWITA"),
        ("r", "JUDGE"),
    ],
    citations=[
        ("section 312 of the Insurance Act", "Insurance Act s.312", "fabricated"),
        ("Kamau v Imara Brokers Ltd [2018] KECA 45512 (KLR)", None, "fabricated"),
    ],
    amounts=["KES 1,184,000", "KES 6,420,000"], deadlines=[],
))

# ---------------------------------------------------------------- J11 Old photocopied ruling (scanned, OCR)
JUDGMENTS.append(dict(
    id="J11", file="Wekesa v Tumaini Sacco Society Ltd (Civil Suit 412of2014) [2015] KEMC 98117 (KLR) (17March2015) (Ruling) - scanned",
    fmt="scan_pdf", practice_area="cooperatives", sensitivity="normal", kind="ruling", court_level="Magistrates Court",
    citation="[2015] KEMC 98117 (KLR)", date="2015-03-17",
    blocks=header("IN THE CHIEF MAGISTRATE'S COURT AT BUNGOMA", None, "CIVIL SUIT NO. 412 OF 2014",
                  [("PETER SIMIYU WEKESA", "PLAINTIFF"), ("TUMAINI SACCO SOCIETY LIMITED", "DEFENDANT")],
                  "RULING", "Hon. J. M. Nabwire-Opiyo, Senior Resident Magistrate", "17th March 2015") + [
        ("n", "The Defendant has raised a preliminary objection that this court lacks jurisdiction because the dispute, concerning the refund of the Plaintiff's deposits of KES 286,400, is a dispute between a co-operative society and a member."),
        ("n", "Disputes of that nature fall within the exclusive jurisdiction of the Co-operative Tribunal, and the Plaintiff has not shown any reason why the Tribunal cannot determine it."),
        ("n", "The preliminary objection is upheld and the suit is struck out with no order as to costs. The Plaintiff is at liberty to file his claim before the Co-operative Tribunal."),
        ("r", "J. M. NABWIRE-OPIYO"),
        ("r", "SENIOR RESIDENT MAGISTRATE"),
    ],
    citations=[], amounts=["KES 286,400"], deadlines=[],
    scan=dict(dpi=150, skew=1.2, noise=14, blur=0.6, jpeg=55, photocopy=True),
))
