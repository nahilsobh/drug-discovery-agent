"""
Roche AI Factory — Strategic Discovery Agent
ReAct loop powered by Claude + tool_use.

Usage:
    python3 run_agent.py "Find gaps in Roche's neurology pipeline"
    python3 run_agent.py "Which oncology targets have strong biology but no active Roche trial?"
"""

import sys
import json
import os
import datetime
import time
import tools._allowlist  # noqa: F401 — activate egress allowlist before any tool import
import anthropic
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm

from tools.session import SESSION
from tools.constants import (
    SPONSORS, CT_URL, OT_URL, CACHE_PATH, UNIPROT_URL, OPENFDA_URL,
    MODEL, MAX_TURNS, CLAWAPI_URL, GENOMECLAW_DIR,
    COMPETITORS, ARXIV_CATS, PREVALENCE_MAP,
)
from tools.discovery import (
    search_roche_trials, get_biology, check_competitor_trials,
    _translational_confidence, find_gaps, _load_pipeline_enrichment,
    find_combinations, find_shared_targets, find_phenocopiers,
    get_pathway_context,
)
from tools.literature import (
    scan_arxiv, scan_literature, bulk_scan_literature,
)
from tools.genomeclaw import (
    _check_genomeclaw_health, fold_target, score_variant_effect,
    predict_admet, query_genomeclaw_databases,
    cluster_scaffolds, dock_compound,
)
from tools.regulatory_competitive import (
    map_regulatory_path, rank_portfolio, query_competitive_intel,
    list_pipeline_assets, monitor_competitive_signals,
    score_trial_outcome, check_orphan_eligibility,
    get_protein_structure_context, get_disease_prevalence,
)
from tools.chemistry import (
    find_hits, query_adverse_events, find_repurposing_candidates,
)
from tools.memory import recall_longterm_memory
from tools.patents import search_patents, get_patent_landscape
from tools.audit import AuditLogger, print_audit_summary


def generate_pdf_report(filename: str = None, ceo_summary: str = "") -> dict:
    """
    Generate a full structured PDF report from all findings accumulated in this session.
    Includes: cover page, executive summary, gap analysis, portfolio ranking,
    combination opportunities, literature evidence, and regulatory pathways.
    """
    if not filename:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"Roche_AI_Factory_Report_{ts}.pdf"

    doc = SimpleDocTemplate(
        filename, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )
    styles = getSampleStyleSheet()

    # ── Roche brand palette ─────────────────────────────────────────────────────
    # Primary:   #003087  Roche Blue (Pantone 294 C)
    # Secondary: #0066CC  Roche Medium Blue
    # Accent:    #E8F0FB  Roche Light Blue tint (table row alternates)
    # Divider:   #BBCDE8  Roche steel-blue separator
    # Text on dark: white / #D0E4F7 (soft white-blue for subtitles)
    NAVY   = colors.HexColor("#003087")   # Roche Primary Blue
    BLUE   = colors.HexColor("#0066CC")   # Roche Secondary Blue
    LIGHT  = colors.HexColor("#E8F0FB")   # Roche Light Blue tint
    SILVER = colors.HexColor("#D0E4F7")   # Subtitle on dark bg
    DIVIDER= colors.HexColor("#BBCDE8")   # Separator on dark bg
    YELLOW = colors.HexColor("#FFF3CD")   # Warning highlight (unchanged)

    # Cover page styles — white text because they render on the navy banner
    # Explicit leading avoids overlap when Paragraphs are packed inside Table cells
    # (spaceAfter/spaceBefore are ignored between Table rows; only padding applies)
    styles.add(ParagraphStyle("Cover",    fontSize=26, leading=34, textColor=colors.white, spaceAfter=0, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("SubCover", fontSize=13, leading=18, textColor=SILVER,       spaceAfter=0, fontName="Helvetica"))
    # Body / section styles — dark text on white/light backgrounds
    styles.add(ParagraphStyle("SectionH", fontSize=14, textColor=NAVY,  spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("SubH",     fontSize=11, textColor=NAVY,  spaceBefore=8,  spaceAfter=4, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("Body",     fontSize=9,  leading=13, spaceAfter=4, textColor=colors.HexColor("#1A1A1A")))
    styles.add(ParagraphStyle("Cell",     fontSize=8,  leading=11, textColor=colors.HexColor("#1A1A1A")))
    # CellB is ONLY used in table header rows (NAVY background) → white text
    styles.add(ParagraphStyle("CellB",    fontSize=8,  leading=11, fontName="Helvetica-Bold", textColor=colors.white))
    styles.add(ParagraphStyle("Tag",      fontSize=8,  textColor=colors.white, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("Ref",      fontSize=7.5, leading=10, textColor=NAVY, leftIndent=10, firstLineIndent=-10))

    story = []
    date_str = datetime.datetime.now().strftime("%B %d, %Y")

    def hr(color=NAVY, thick=1.5):
        return HRFlowable(width="100%", thickness=thick, color=color, spaceAfter=8)

    def section(title):
        story.append(Spacer(1, 4*mm))
        story.append(hr())
        story.append(Paragraph(title, styles["SectionH"]))

    def tbl(data, col_widths, header=True):
        t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
        style = [
            # Thin Roche-blue grid lines
            ("LINEBELOW",   (0,0), (-1,-1), 0.35, colors.HexColor("#C5D5E8")),
            ("LINEBEFORE",  (0,0), (-1,-1), 0.35, colors.HexColor("#C5D5E8")),
            ("BOX",         (0,0), (-1,-1), 0.8,  NAVY),
            ("VALIGN",      (0,0), (-1,-1), "TOP"),
            ("TOPPADDING",  (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING",(0,0), (-1,-1), 4),
            # Dark near-black text in all body cells (readable on white/light bg)
            ("TEXTCOLOR",   (0,1), (-1,-1), colors.HexColor("#1A1A1A")),
        ]
        if header:
            style += [
                # Roche Blue header: white bold text on #003087
                ("BACKGROUND", (0,0), (-1,0), NAVY),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("TOPPADDING", (0,0), (-1,0), 6),
                ("BOTTOMPADDING",(0,0),(-1,0), 6),
            ]
        for i in range(1, len(data)):
            # Odd rows: pure white; Even rows: Roche Light Blue tint (#E8F0FB)
            bg = LIGHT if i % 2 == 0 else colors.white
            style.append(("BACKGROUND", (0,i), (-1,i), bg))
        t.setStyle(TableStyle(style))
        return t

    # ── COVER PAGE ──────────────────────────────────────────────────────────────
    # Strategy: flat story elements (no fixed rowHeights) to avoid text clipping.
    # 1. Navy banner table — auto-height, content drives height.
    # 2. White metadata section — query + stats box.
    # 3. Large spacer fills the remaining page height.
    # 4. Blue footer bar.
    # 5. PageBreak forces Executive Summary onto page 2.
    PRINT_W = 170 * mm

    query_text = SESSION.get("question", "Strategic Portfolio Analysis")
    query_short = (query_text[:130] + "…") if len(query_text) > 133 else query_text

    n_gaps   = len(SESSION["gaps"])
    n_assets = len(SESSION["portfolio"])
    n_papers = sum(l.get("papers_found", 0) for l in SESSION["literature"])
    n_reg    = len(SESSION["regulatory"])

    # ── 1. Navy title banner (auto-height, no clipping) ───────────────────────
    banner = Table(
        [
            [Spacer(1, 18*mm)],                                         # row 0 — top padding
            [Paragraph("ROCHE AI FACTORY", styles["Cover"])],           # row 1 — main title
            [Spacer(1, 5*mm)],                                          # row 2 — gap between title lines
            [Paragraph("Strategic Discovery Report", styles["SubCover"])],  # row 3 — subtitle
            [Spacer(1, 10*mm)],                                         # row 4 — spacer before HR
            [HRFlowable(width="100%", thickness=0.8, color=DIVIDER, spaceAfter=0)],  # row 5
            [Spacer(1, 4*mm)],                                          # row 6 — gap after HR
            [Paragraph(
                f"<b>Generated:</b> {date_str}   ·   "
                "Sources: ClinicalTrials.gov · Open Targets · Europe PMC · ArXiv",
                ParagraphStyle("CoverMeta", fontSize=8.5, leading=12, textColor=SILVER, spaceAfter=0),
            )],                                                         # row 7 — metadata
            [Spacer(1, 14*mm)],                                         # row 8 — bottom padding
        ],
        colWidths=[PRINT_W],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), NAVY),
        ("LEFTPADDING",   (0,0), (-1,-1), 10*mm),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6*mm),
        # Zero padding on all rows — spacing is controlled by Spacer rows above
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
    ]))
    story.append(banner)

    # ── 2. Metadata section (white background) ────────────────────────────────
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(
        f"<b>Query:</b> {query_short}",
        styles["Body"],
    ))
    story.append(Spacer(1, 8*mm))

    # Stats box: NAVY numbers on white background, dark-grey labels
    stats_tbl = Table(
        [[Paragraph(
              f"<font color='#003087' size='16'><b>{n_gaps}</b></font><br/>"
              "<font color='#555555' size='7.5'>Strategic Gaps</font>",   styles["Cell"]),
          Paragraph(
              f"<font color='#003087' size='16'><b>{n_assets}</b></font><br/>"
              "<font color='#555555' size='7.5'>Assets Ranked</font>",    styles["Cell"]),
          Paragraph(
              f"<font color='#003087' size='16'><b>{n_papers}</b></font><br/>"
              "<font color='#555555' size='7.5'>Papers Reviewed</font>",  styles["Cell"]),
          Paragraph(
              f"<font color='#003087' size='16'><b>{n_reg}</b></font><br/>"
              "<font color='#555555' size='7.5'>Regulatory Paths</font>", styles["Cell"])]],
        colWidths=[40*mm]*4,
    )
    stats_tbl.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 1.2, NAVY),
        ("INNERGRID",     (0,0), (-1,-1), 0.5, BLUE),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("TOPPADDING",    (0,0), (-1,-1), 12),
        ("BOTTOMPADDING", (0,0), (-1,-1), 12),
        ("BACKGROUND",    (0,0), (-1,-1), colors.white),
    ]))
    story.append(stats_tbl)

    # ── 3. Spacer fills the remaining page so the footer sits at the bottom ───
    story.append(Spacer(1, 88*mm))

    # ── 4. Blue confidential footer bar ───────────────────────────────────────
    footer_bar = Table(
        [[Paragraph(
            "CONFIDENTIAL — Roche AI Factory · Internal Use Only · "
            "Agent: Roche AI Factory v2.0 (Claude + Tool Use)",
            ParagraphStyle("CoverFoot", fontSize=7.5, textColor=colors.white, alignment=1),
        )]],
        colWidths=[PRINT_W],
    )
    footer_bar.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), NAVY),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 4*mm),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4*mm),
    ]))
    story.append(footer_bar)
    story.append(PageBreak())

    # ── EXECUTIVE SUMMARY ───────────────────────────────────────────────────────
    section("EXECUTIVE SUMMARY")
    if ceo_summary:
        story.append(Paragraph(ceo_summary, styles["Body"]))
    else:
        top_gaps = sorted(SESSION["gaps"], key=lambda x: x.get("bio_score", 0), reverse=True)[:3]
        bullets  = "".join(
            f"<br/>• <b>{g['target']}</b> in <i>{g['disease']}</i> (Bio Score: {g['bio_score']}) — {g['status']}"
            for g in top_gaps
        ) if top_gaps else "<br/>• No gaps identified in this session."
        top_asset = SESSION["portfolio"][0] if SESSION["portfolio"] else None
        asset_txt = (
            f"<br/>• Top ranked asset: <b>{top_asset['name']}</b> "
            f"(Composite Score: {top_asset['composite_score']})"
        ) if top_asset else ""
        story.append(Paragraph(
            f"This report summarises the AI Factory's autonomous discovery session for: "
            f"<i>{query_text}</i><br/><br/>"
            f"<b>Key Findings:</b>{bullets}{asset_txt}",
            styles["Body"],
        ))

    # ── SECTION 1: GAP ANALYSIS ─────────────────────────────────────────────────
    if SESSION["gaps"]:
        section("SECTION 1 — STRATEGIC GAP ANALYSIS")
        story.append(Paragraph(
            "Gaps are indications where Open Targets biological evidence is strong (score ≥ 0.60) "
            "but Roche/Genentech has no active clinical trial.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))

        header = [
            Paragraph("Target", styles["CellB"]),
            Paragraph("Disease", styles["CellB"]),
            Paragraph("Bio Score", styles["CellB"]),
            Paragraph("Ensembl ID", styles["CellB"]),
            Paragraph("Status", styles["CellB"]),
        ]
        rows = [header]
        for g in sorted(SESSION["gaps"], key=lambda x: x.get("bio_score", 0), reverse=True):
            score = g.get("bio_score", 0)
            score_color = colors.red if score >= 0.75 else (BLUE if score >= 0.60 else colors.grey)
            rows.append([
                Paragraph(g.get("target", ""), styles["Cell"]),
                Paragraph(g.get("disease", ""), styles["Cell"]),
                Paragraph(f"<font color='#{score_color.hexval()[2:] if hasattr(score_color, 'hexval') else '003087'}'><b>{score}</b></font>", styles["Cell"]),
                Paragraph(g.get("ensembl_id", ""), styles["Cell"]),
                Paragraph(g.get("status", "STRATEGIC GAP"), styles["Cell"]),
            ])
        story.append(tbl(rows, [30*mm, 55*mm, 25*mm, 38*mm, 32*mm]))

    # ── SECTION 2: PORTFOLIO RANKING ────────────────────────────────────────────
    if SESSION["portfolio"]:
        section("SECTION 2 — PORTFOLIO RANKING")
        story.append(Paragraph(
            "Assets ranked by composite score = Bio Score × Unexplored Indications × (1 + Competitive Vacuum).",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))

        header = [
            Paragraph("#",               styles["CellB"]),
            Paragraph("Asset",           styles["CellB"]),
            Paragraph("Top Disease",     styles["CellB"]),
            Paragraph("Bio Score",       styles["CellB"]),
            Paragraph("Unexplored Inds", styles["CellB"]),
            Paragraph("Comp. Vacuum",    styles["CellB"]),
            Paragraph("Composite",       styles["CellB"]),
        ]
        rows = [header]
        for i, a in enumerate(SESSION["portfolio"], 1):
            rows.append([
                Paragraph(str(i), styles["Cell"]),
                Paragraph(f"<b>{a['name']}</b>", styles["Cell"]),
                Paragraph(a.get("top_disease", ""), styles["Cell"]),
                Paragraph(str(a.get("bio_score", "")), styles["Cell"]),
                Paragraph(str(a.get("unexplored_inds", "")), styles["Cell"]),
                Paragraph("Yes" if a.get("competitive_vacuum") else "No", styles["Cell"]),
                Paragraph(f"<b>{a.get('composite_score', '')}</b>", styles["Cell"]),
            ])
        story.append(tbl(rows, [8*mm, 32*mm, 45*mm, 20*mm, 22*mm, 22*mm, 21*mm]))

    # ── SECTION 3: COMPETITIVE INTELLIGENCE ─────────────────────────────────────
    comp_signals = SESSION.get("competitive_signals", [])
    if comp_signals:
        section("SECTION 3 — COMPETITIVE INTELLIGENCE (AZ · Pfizer · Key Competitors)")
        story.append(Paragraph(
            "Live competitor asset landscape from Citeline/ClinicalTrials.gov cross-reference. "
            "Assets that directly overlap with Roche's active pipeline are flagged.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))
        for comp in comp_signals:
            competitor_name = comp.get("competitor", comp.get("filters", {}).get("competitor", "Competitor"))
            assets = comp.get("assets", [])
            story.append(Paragraph(
                f"<b>{competitor_name}</b> — {len(assets)} oncology asset(s)",
                styles["SubH"],
            ))
            if assets:
                header = [
                    Paragraph("Asset",       styles["CellB"]),
                    Paragraph("Target",      styles["CellB"]),
                    Paragraph("Phase",       styles["CellB"]),
                    Paragraph("Indication",  styles["CellB"]),
                    Paragraph("Notes",       styles["CellB"]),
                ]
                rows = [header]
                for a in assets:
                    rows.append([
                        Paragraph(f"<b>{a.get('asset','')}</b>",     styles["Cell"]),
                        Paragraph(a.get("target", ""),               styles["Cell"]),
                        Paragraph(str(a.get("phase", "")),           styles["Cell"]),
                        Paragraph(a.get("indication", "")[:60],      styles["Cell"]),
                        Paragraph(a.get("notes", "")[:80],           styles["Cell"]),
                    ])
                story.append(tbl(rows, [38*mm, 18*mm, 14*mm, 50*mm, 50*mm]))
                story.append(Spacer(1, 4*mm))

    # ── SECTION 4: COMBINATION OPPORTUNITIES ────────────────────────────────────
    if SESSION["combinations"]:
        section("SECTION 4 — COMBINATION THERAPY OPPORTUNITIES")
        for combo in SESSION["combinations"]:
            story.append(Paragraph(f"Disease: <b>{combo['disease']}</b> — {combo['combo_trials']} combination trials found", styles["SubH"]))
            if combo["unique_pairs"]:
                header = [
                    Paragraph("Drug A",  styles["CellB"]),
                    Paragraph("Drug B",  styles["CellB"]),
                    Paragraph("NCT ID",  styles["CellB"]),
                ]
                rows = [header] + [
                    [Paragraph(p["drug_a"], styles["Cell"]),
                     Paragraph(p["drug_b"], styles["Cell"]),
                     Paragraph(p.get("nct_id",""), styles["Cell"])]
                    for p in combo["unique_pairs"]
                ]
                story.append(tbl(rows, [65*mm, 65*mm, 40*mm]))
                story.append(Spacer(1, 3*mm))

    # ── SECTION 5: LITERATURE EVIDENCE ──────────────────────────────────────────
    if SESSION["literature"]:
        section("SECTION 5 — LITERATURE EVIDENCE")
        for lit in SESSION["literature"]:
            story.append(Paragraph(
                f"<b>{lit['target']}</b> in <i>{lit['disease']}</i> — {lit['papers_found']} papers",
                styles["SubH"],
            ))
            for p in lit.get("papers", [])[:5]:
                ref = f"<b>{p.get('title','')}</b><br/>"
                if p.get("journal"):
                    ref += f"<i>{p['journal']}</i>"
                if p.get("date"):
                    ref += f" ({p['date']})"
                if p.get("doi"):
                    ref += f" · DOI: {p['doi']}"
                story.append(Paragraph(ref, styles["Ref"]))
                story.append(Spacer(1, 2*mm))

    # ── SECTION 6: REGULATORY PATHWAYS ──────────────────────────────────────────
    if SESSION["regulatory"]:
        section("SECTION 6 — REGULATORY PATHWAYS")
        for reg in SESSION["regulatory"]:
            cdx_platforms = reg.get("cdx_approved_platforms", [])
            cdx_str = "; ".join(
                f"{p['platform']} ({p['assay_type']}, {p['fda_approval_date'][:4]})"
                for p in cdx_platforms[:3]
            ) if cdx_platforms else reg.get("companion_dx", "—")

            reg_rows = [
                [Paragraph("Field",              styles["CellB"]), Paragraph("Value", styles["CellB"])],
                [Paragraph("Primary Endpoint",   styles["Cell"]), Paragraph(reg.get("primary_endpoint",""),  styles["Cell"])],
                [Paragraph("Required Biomarker", styles["Cell"]), Paragraph(reg.get("required_biomarker",""),styles["Cell"])],
                [Paragraph("Companion Dx",       styles["Cell"]), Paragraph(cdx_str,                         styles["Cell"])],
                [Paragraph("FDA Preference",     styles["Cell"]), Paragraph(reg.get("fda_preference",""),    styles["Cell"])],
                [Paragraph("Expedited Path",     styles["Cell"]), Paragraph(
                    ", ".join(reg.get("expedited_pathways", [reg.get("expedited_pathway","")])),
                    styles["Cell"])],
            ]
            story.append(KeepTogether([
                Paragraph(f"{reg['drug']} → {reg['indication']}", styles["SubH"]),
                tbl(reg_rows, [50*mm, 120*mm]),
                Spacer(1, 4*mm),
            ]))

    # ── SECTION 7: ARXIV INTELLIGENCE ───────────────────────────────────────────
    if SESSION["arxiv_papers"]:
        section("SECTION 7 — ARXIV INTELLIGENCE (Pre-Publication Signal)")
        story.append(Paragraph(
            "ArXiv preprints surface cutting-edge science 6–18 months before peer review. "
            "Includes ML-assisted drug design, AlphaFold structure predictions, and resistance mechanism papers.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))
        header = [
            Paragraph("Title",      styles["CellB"]),
            Paragraph("Categories", styles["CellB"]),
            Paragraph("Submitted",  styles["CellB"]),
            Paragraph("PDF Link",   styles["CellB"]),
        ]
        rows = [header]
        for p in SESSION["arxiv_papers"][:20]:
            cats = ", ".join(p.get("categories", [])[:3])
            rows.append([
                Paragraph(p.get("title", "")[:120], styles["Cell"]),
                Paragraph(cats, styles["Cell"]),
                Paragraph(p.get("submitted", ""), styles["Cell"]),
                Paragraph(p.get("pdf_url", ""), styles["Cell"]),
            ])
        story.append(tbl(rows, [90*mm, 35*mm, 22*mm, 33*mm]))

    # ── SECTION 8: REPURPOSING CANDIDATES ───────────────────────────────────────
    if SESSION["repurposing"]:
        section("SECTION 8 — DRUG REPURPOSING OPPORTUNITIES")
        story.append(Paragraph(
            "Approved drugs (Phase 4) that could be repositioned into a new indication. "
            "These skip Phase I entirely — the fastest regulatory path to clinic.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))
        header = [
            Paragraph("Drug",               styles["CellB"]),
            Paragraph("Approved Indication", styles["CellB"]),
            Paragraph("Target Disease",      styles["CellB"]),
            Paragraph("Year Approved",       styles["CellB"]),
        ]
        rows = [header]
        for c in SESSION["repurposing"][:20]:
            rows.append([
                Paragraph(f"<b>{c.get('drug_name','')}</b>", styles["Cell"]),
                Paragraph(c.get("approved_indication", ""), styles["Cell"]),
                Paragraph(c.get("target_disease", ""),      styles["Cell"]),
                Paragraph(str(c.get("year_approved", "")),  styles["Cell"]),
            ])
        story.append(tbl(rows, [40*mm, 55*mm, 55*mm, 30*mm]))

    # ── SECTION 9: TARGET DRUGGABILITY & ORPHAN FLAGS ──────────────────────────
    has_prot  = bool(SESSION["protein_structures"])
    has_orph  = bool(SESSION["orphan_flags"])
    if has_prot or has_orph:
        section("SECTION 9 — TARGET DRUGGABILITY & ORPHAN DISEASE FLAGS")
        story.append(Spacer(1, 2*mm))

        if has_prot:
            story.append(Paragraph("Druggability Assessment (UniProt + Open Targets Tractability)", styles["SubH"]))
            header = [
                Paragraph("Gene",          styles["CellB"]),
                Paragraph("Protein",       styles["CellB"]),
                Paragraph("Druggability",  styles["CellB"]),
                Paragraph("Modality",      styles["CellB"]),
                Paragraph("Evidence",      styles["CellB"]),
            ]
            rows = [header]
            for p in SESSION["protein_structures"]:
                sm = ", ".join(p.get("tractability_evidence", {}).get("sm", [])[:2])
                rows.append([
                    Paragraph(f"<b>{p.get('gene_symbol','')}</b>", styles["Cell"]),
                    Paragraph(p.get("protein_name", "")[:50],      styles["Cell"]),
                    Paragraph(p.get("druggability", ""),            styles["Cell"]),
                    Paragraph(p.get("recommended_modality", ""),    styles["Cell"]),
                    Paragraph(sm or "—",                            styles["Cell"]),
                ])
            story.append(tbl(rows, [25*mm, 55*mm, 28*mm, 30*mm, 42*mm]))
            story.append(Spacer(1, 4*mm))

        if has_orph:
            story.append(Paragraph("Orphan Drug Designation Eligibility", styles["SubH"]))
            header = [
                Paragraph("Disease",      styles["CellB"]),
                Paragraph("US Eligible",  styles["CellB"]),
                Paragraph("EU Eligible",  styles["CellB"]),
                Paragraph("Prevalence",   styles["CellB"]),
                Paragraph("Key Benefits", styles["CellB"]),
            ]
            rows = [header]
            for o in SESSION["orphan_flags"]:
                benefits_short = "; ".join(o.get("benefits", [])[:2])
                rows.append([
                    Paragraph(o.get("disease", ""),                                 styles["Cell"]),
                    Paragraph("YES" if o.get("us_eligible") else "No",              styles["Cell"]),
                    Paragraph("YES" if o.get("eu_eligible") else "No",              styles["Cell"]),
                    Paragraph(f"~{o.get('estimated_prevalence','?'):,}" if o.get("estimated_prevalence") else "Unknown", styles["Cell"]),
                    Paragraph(benefits_short or "—",                                styles["Cell"]),
                ])
            story.append(tbl(rows, [45*mm, 22*mm, 22*mm, 25*mm, 66*mm]))

    # ── SECTION 10: GENOMECLAW STRUCTURAL ANALYSIS ─────────────────────────────
    has_folds  = bool(SESSION["fold_results"])
    has_admet  = bool(SESSION["admet_profiles"])
    has_vars   = bool(SESSION["variant_effects"])
    if has_folds or has_admet or has_vars:
        section("SECTION 10 — GENOMECLAW STRUCTURAL & ADMET ANALYSIS")
        story.append(Paragraph(
            "3D structures predicted by GenomeClaw Boltz-1 (Rust-native, GPU-accelerated). "
            "ADMET profiles generated by genomeclaw-admet. Variant effects scored by ESM-2.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))

        if has_folds:
            story.append(Paragraph("Protein Structure Predictions (Boltz-1 pLDDT)", styles["SubH"]))
            header = [
                Paragraph("Gene / Sequence",    styles["CellB"]),
                Paragraph("Residues",            styles["CellB"]),
                Paragraph("pLDDT mean",          styles["CellB"]),
                Paragraph("Confidence",          styles["CellB"]),
                Paragraph("Elapsed (s)",         styles["CellB"]),
            ]
            rows = [header]
            for f in SESSION["fold_results"]:
                rows.append([
                    Paragraph(f"<b>{f.get('gene','')}</b>",          styles["Cell"]),
                    Paragraph(str(f.get("residues", "")),             styles["Cell"]),
                    Paragraph(str(f.get("plddt_mean", "—")),          styles["Cell"]),
                    Paragraph(f.get("confidence_label", "")[:60],     styles["Cell"]),
                    Paragraph(str(f.get("elapsed_secs", "—")),        styles["Cell"]),
                ])
            story.append(tbl(rows, [45*mm, 22*mm, 25*mm, 60*mm, 28*mm]))
            story.append(Spacer(1, 4*mm))

        if has_vars:
            story.append(Paragraph("Variant Effect Scores (ESM-2 Delta Log-Likelihood)", styles["SubH"]))
            header = [
                Paragraph("Gene",          styles["CellB"]),
                Paragraph("Variant",       styles["CellB"]),
                Paragraph("ΔLL",           styles["CellB"]),
                Paragraph("Interpretation",styles["CellB"]),
            ]
            rows = [header]
            for v in SESSION["variant_effects"]:
                rows.append([
                    Paragraph(f"<b>{v.get('gene','')}</b>",           styles["Cell"]),
                    Paragraph(v.get("variant", ""),                    styles["Cell"]),
                    Paragraph(str(v.get("delta_log_likelihood", "—")), styles["Cell"]),
                    Paragraph(v.get("interpretation", "")[:80],        styles["Cell"]),
                ])
            story.append(tbl(rows, [30*mm, 25*mm, 20*mm, 105*mm]))
            story.append(Spacer(1, 4*mm))

        if has_admet:
            story.append(Paragraph("ADMET Profiles (genomeclaw-admet)", styles["SubH"]))
            header = [
                Paragraph("Drug",            styles["CellB"]),
                Paragraph("Tier",            styles["CellB"]),
                Paragraph("hERG",            styles["CellB"]),
                Paragraph("BBB",             styles["CellB"]),
                Paragraph("Half-Life",       styles["CellB"]),
                Paragraph("Flags",           styles["CellB"]),
            ]
            rows = [header]
            for a in SESSION["admet_profiles"]:
                flags = "; ".join(a.get("red_flags", []) + a.get("minor_flags", []))
                rows.append([
                    Paragraph(f"<b>{a.get('drug','')}</b>",  styles["Cell"]),
                    Paragraph(a.get("tier", ""),             styles["Cell"]),
                    Paragraph(a.get("hERG", "—"),            styles["Cell"]),
                    Paragraph(a.get("BBB", "—"),             styles["Cell"]),
                    Paragraph(a.get("HalfLife", "—"),        styles["Cell"]),
                    Paragraph(flags[:80] or "—",             styles["Cell"]),
                ])
            story.append(tbl(rows, [40*mm, 18*mm, 38*mm, 30*mm, 22*mm, 32*mm]))

    # ── SECTION 10: PHENOCOPIERS ────────────────────────────────────────────────
    phenocopier_results = SESSION.get("phenocopiers", [])
    if phenocopier_results:
        section("SECTION 10 — PHENOCOPIER TARGET EXPANSION")
        story.append(Paragraph(
            "Genes sharing downstream biology with queried targets — novel target candidates "
            "for the same indication (Plex Research approach, AIA4S 2026). "
            "Identified via Open Targets similar-targets API or STRING functional partners.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))
        for pc in phenocopier_results:
            story.append(Paragraph(
                f"Query target: <b>{pc.get('query_target','')}</b> | "
                f"Disease context: {pc.get('disease_context','none')} | "
                f"Method: {pc.get('method','—')} | "
                f"{pc.get('phenocopiers_count', 0)} phenocopier(s) found",
                styles["Body"],
            ))
            phenos = pc.get("phenocopiers", [])
            if phenos:
                header = [
                    Paragraph("Gene",              styles["CellB"]),
                    Paragraph("Gene Name",         styles["CellB"]),
                    Paragraph("Similarity Score",  styles["CellB"]),
                    Paragraph("Disease Score",     styles["CellB"]),
                    Paragraph("Rationale",         styles["CellB"]),
                ]
                rows = [header]
                for p in phenos:
                    rows.append([
                        Paragraph(f"<b>{p.get('gene_symbol','')}</b>",      styles["Cell"]),
                        Paragraph(p.get("gene_name", "—")[:40],             styles["Cell"]),
                        Paragraph(str(p.get("similarity_score", "—")),      styles["Cell"]),
                        Paragraph(str(p.get("disease_score") or "—"),       styles["Cell"]),
                        Paragraph(p.get("rationale", "—")[:80],             styles["Cell"]),
                    ])
                story.append(tbl(rows, [22*mm, 40*mm, 28*mm, 25*mm, 65*mm]))
            story.append(Spacer(1, 4*mm))

    # ── SECTION 11: GENERATED PROTOCOLS ─────────────────────────────────────────
    protocol_results = SESSION.get("protocols", [])
    if protocol_results:
        section("SECTION 11 — GENERATED ASSAY PROTOCOLS")
        story.append(Paragraph(
            "Regulatory-aligned protocols auto-generated by the agent "
            "(Potato/Tater approach, AIA4S 2026: months of manual design → seconds). "
            "All protocols require human review before execution.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))
        for proto in protocol_results:
            story.append(Paragraph(
                f"<b>{proto.get('asset_name','')} — {proto.get('assay_type','')}</b> "
                f"({proto.get('regulatory_context','')}) | "
                f"Guideline: {proto.get('regulatory_guideline','—')} | "
                f"Target: {proto.get('target_gene','—')} | "
                f"Indication: {proto.get('indication','—')}",
                styles["SubH"],
            ))
            secs = proto.get("sections", {})
            if secs.get("1_objective"):
                story.append(Paragraph(f"Objective: {secs['1_objective']}", styles["Body"]))
            if secs.get("2_regulatory_alignment"):
                story.append(Paragraph(f"Regulatory note: {secs['2_regulatory_alignment']}", styles["Body"]))
            if secs.get("6_data_analysis"):
                story.append(Paragraph(f"Analysis: {secs['6_data_analysis']}", styles["Body"]))
            story.append(Paragraph(
                f"<i>{proto.get('note','')}</i>", styles["Body"]
            ))
            story.append(Spacer(1, 4*mm))

    # ── FOOTER ──────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Spacer(1, 20*mm))
    story.append(hr(NAVY, 2))
    story.append(Paragraph("CONFIDENTIAL — Roche AI Factory Internal Use Only", styles["Body"]))
    story.append(Paragraph(
        f"Generated by Roche AI Factory Strategic Discovery Agent · {date_str} · "
        "Sources: ClinicalTrials.gov, Open Targets Platform, Europe PMC",
        styles["Ref"],
    ))

    doc.build(story)
    return {"status": "success", "file": filename, "pages_estimated": len(story) // 10}


def save_to_cache(data: dict) -> dict:
    """Persist a discovery to the intelligence cache for future use."""
    cache = []
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            try:
                cache = json.load(f)
                if isinstance(cache, dict):
                    cache = cache.get("assets", [])
            except json.JSONDecodeError:
                cache = []

    # Upsert by name
    names = {e.get("name") for e in cache}
    if isinstance(data, list):
        for item in data:
            if item.get("name") not in names:
                cache.append(item)
                names.add(item.get("name"))
    else:
        if data.get("name") not in names:
            cache.append(data)

    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

    return {"status": "saved", "cache_size": len(cache)}


# ── Tool definitions for Claude ─────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_roche_trials",
        "description": "Search ClinicalTrials.gov for active Roche/Genentech trials in a therapeutic area. Returns trial count, NCT IDs, drugs, and phases.",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Disease or therapeutic area to search (e.g. 'breast cancer', 'Alzheimer', 'neurology')"},
                "phase": {"type": "string", "description": "Optional phase filter: '1', '2', '3', '4'"},
            },
            "required": ["therapeutic_area"],
        },
    },
    {
        "name": "get_biology",
        "description": "Query Open Targets for the top disease associations of a drug name or gene symbol. Returns bio-confidence scores per disease.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Drug name (e.g. 'Giredestrant') or gene symbol (e.g. 'ESR1')"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "check_competitor_trials",
        "description": "Check how many trials a competitor (AstraZeneca, Eli Lilly, Novartis, Pfizer, etc.) has for a given disease.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease":    {"type": "string", "description": "Disease name"},
                "competitor": {"type": "string", "description": "Competitor name"},
            },
            "required": ["disease", "competitor"],
        },
    },
    {
        "name": "find_gaps",
        "description": "Core strategic analysis: cross-references Open Targets biology with Roche's clinical pipeline to surface high-evidence indications with zero Roche trials.",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Therapeutic area to analyse"},
                "min_bio_score":    {"type": "number",  "description": "Minimum Open Targets confidence score (0.0–1.0, default 0.60)"},
            },
            "required": ["therapeutic_area"],
        },
    },
    {
        "name": "save_to_cache",
        "description": "Save a discovery or gap finding to the intelligence cache for future retrieval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"type": "object", "description": "Discovery data to persist"},
            },
            "required": ["data"],
        },
    },
    {
        "name": "rank_portfolio",
        "description": "Score and rank all Roche portfolio assets by composite opportunity: bio_score × unexplored indications × competitive vacuum. Loads from roche_pipeline.json if no assets provided.",
        "input_schema": {
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "description": "Optional list of assets to rank. Each item needs 'name' and 'id' (Ensembl ID). Omit to use the full portfolio.",
                    "items": {"type": "object"},
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_combinations",
        "description": "Find Roche drug pairs that target complementary pathways in the same disease by analysing combination arms on ClinicalTrials.gov.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease": {"type": "string", "description": "Disease to search for combination trials (e.g. 'breast cancer', 'NSCLC')"},
            },
            "required": ["disease"],
        },
    },
    {
        "name": "get_pathway_context",
        "description": (
            "Fetch KEGG pathway memberships for a human gene symbol. "
            "Returns pathway IDs and names that this gene participates in. "
            "Use before find_combinations to check whether two drugs share pathways "
            "(shared pathways = synergy potential; single-pathway = redundancy risk). "
            "Also useful for target validation: a target with many cancer-pathway memberships "
            "has stronger oncology rationale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gene": {"type": "string", "description": "HGNC gene symbol (e.g. 'EGFR', 'KRAS', 'TP53')"},
            },
            "required": ["gene"],
        },
    },
    {
        "name": "get_disease_prevalence",
        "description": (
            "Look up estimated patient prevalence for a disease to assess orphan drug eligibility "
            "or market sizing. Checks a curated rare-disease map first (high confidence), then "
            "falls back to Orphanet REST API (medium confidence). "
            "Call this before check_orphan_eligibility when prevalence is uncertain."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "disease": {"type": "string", "description": "Disease name (e.g. 'Huntington disease', 'cystic fibrosis')"},
            },
            "required": ["disease"],
        },
    },
    {
        "name": "scan_literature",
        "description": "Search Europe PMC + ArXiv in parallel for recent publications linking a drug or gene target to a specific disease. Returns titles, journals, and dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target":   {"type": "string",  "description": "Drug name or gene symbol (e.g. 'Giredestrant' or 'ESR1')"},
                "disease":  {"type": "string",  "description": "Disease name (e.g. 'breast cancer')"},
                "min_year": {"type": "integer", "description": "Optional: only return papers published in this year or later (e.g. 2024)"},
            },
            "required": ["target", "disease"],
        },
    },
    {
        "name": "search_patents",
        "description": (
            "Search US and global patents for a compound, target, or technology keyword. "
            "Uses USPTO PatentsView (US) and Lens.org (global, requires LENS_API_KEY). "
            "Use to assess IP landscape before advancing a hit or repurposing candidate. "
            "Returns patent titles, assignees, dates, and abstracts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term — compound name, target, or technology (e.g. 'KRAS G12C covalent inhibitor')",
                },
                "assignee": {
                    "type": "string",
                    "description": "Optional: filter by organisation name (e.g. 'Roche', 'AstraZeneca')",
                },
                "years_back": {
                    "type": "integer",
                    "description": "Patent lookback window in years (default 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_patent_landscape",
        "description": (
            "Build a full IP landscape for a drug target or compound — filing volume, "
            "top assignees, most recent patents, freedom-to-operate flag, and white-space note. "
            "Use after find_hits or find_repurposing_candidates to assess IP barriers before "
            "advancing a compound to map_regulatory_path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_or_compound": {
                    "type": "string",
                    "description": "Gene symbol or compound name (e.g. 'EGFR', 'sotorasib', 'CDK4/6')",
                },
                "years_back": {
                    "type": "integer",
                    "description": "Patent lookback window in years (default 10)",
                },
            },
            "required": ["target_or_compound"],
        },
    },
    {
        "name": "generate_pdf_report",
        "description": "Generate a full structured PDF report from all findings in this session. Call this as the FINAL step after all analysis is complete. The report includes gap analysis, portfolio ranking, combination opportunities, literature, and regulatory pathways.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename":    {"type": "string", "description": "Output filename (optional, auto-generated if omitted)"},
                "ceo_summary": {"type": "string", "description": "2-4 sentence executive summary written by the agent summarising the key findings and top recommended actions"},
            },
            "required": [],
        },
    },
    {
        "name": "map_regulatory_path",
        "description": "Map the regulatory pathway for a drug + indication: primary endpoint, required biomarker, companion diagnostic, and expedited pathway eligibility.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drug":       {"type": "string", "description": "Drug name"},
                "indication": {"type": "string", "description": "Target indication"},
            },
            "required": ["drug", "indication"],
        },
    },
    {
        "name": "scan_arxiv",
        "description": "Search ArXiv preprints for a target + disease. Surfaces science 6-18 months before peer review, including ML-assisted drug design and AlphaFold papers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target":      {"type": "string",  "description": "Drug name or gene symbol"},
                "disease":     {"type": "string",  "description": "Disease name"},
                "max_results": {"type": "integer", "description": "Max papers to return (default 10)"},
                "min_year":    {"type": "integer", "description": "Optional: only return papers from this year or later (e.g. 2024)"},
            },
            "required": ["target", "disease"],
        },
    },
    {
        "name": "score_trial_outcome",
        "description": "Estimate the likelihood of trial success (0.0–1.0) based on phase, enrollment, endpoint, and trial design. Helps filter 120 gaps down to the ~30 worth pursuing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nct_id_or_drug": {"type": "string", "description": "NCT ID (e.g. NCT04567890) or drug name to look up"},
                "indication":     {"type": "string", "description": "Target indication"},
            },
            "required": ["nct_id_or_drug", "indication"],
        },
    },
    {
        "name": "find_repurposing_candidates",
        "description": "Find approved drugs (Phase 4) that could be repositioned into a new indication — skipping Phase I entirely. Fastest path to clinic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_or_disease": {"type": "string", "description": "Disease name or gene target to search repurposing opportunities for"},
            },
            "required": ["target_or_disease"],
        },
    },
    {
        "name": "check_orphan_eligibility",
        "description": "Check if a disease qualifies for Orphan Drug Designation (US: <200K patients, EU: <165K). Returns eligibility, 7yr exclusivity, tax credits, and fee waiver benefits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease": {"type": "string", "description": "Disease name to check for rare disease / orphan eligibility"},
            },
            "required": ["disease"],
        },
    },
    {
        "name": "get_protein_structure_context",
        "description": "Validate target druggability before committing R&D resources. Queries UniProt for protein function + binding sites and Open Targets for tractability scores. Returns recommended modality (small molecule / antibody / PROTAC).",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_symbol": {"type": "string", "description": "HGNC gene symbol (e.g. LRRK2, ESR1, KRAS)"},
            },
            "required": ["gene_symbol"],
        },
    },
    {
        "name": "monitor_competitive_signals",
        "description": "Live 8-competitor activity dashboard for a disease. Fires all ClinicalTrials.gov queries in parallel and checks openFDA for recent approvals. Replaces static competitive_intel.json.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease":     {"type": "string", "description": "Disease to monitor competitive activity for"},
                "competitors": {"type": "array", "items": {"type": "string"}, "description": "Optional custom competitor list (defaults to 8 major pharma companies)"},
            },
            "required": ["disease"],
        },
    },
    {
        "name": "find_shared_targets",
        "description": "Find gene targets shared between two diseases above a confidence threshold. Answers: 'What targets are shared between Alzheimer's and Parkinson's with bio score > 0.7?' Uses Open Targets in parallel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease1":   {"type": "string", "description": "First disease name (e.g. 'Alzheimer disease')"},
                "disease2":   {"type": "string", "description": "Second disease name (e.g. 'Parkinson disease')"},
                "min_score":  {"type": "number", "description": "Minimum Open Targets confidence score (default 0.70)"},
            },
            "required": ["disease1", "disease2"],
        },
    },
    {
        "name": "bulk_scan_literature",
        "description": "Scan Europe PMC for recent publications across multiple targets in parallel. Answers: 'Which Roche targets had new publications in the last 6 months?' Much faster than calling scan_literature for each target individually.",
        "input_schema": {
            "type": "object",
            "properties": {
                "targets":     {"type": "array", "items": {"type": "string"}, "description": "List of gene symbols or drug names to scan"},
                "months_back": {"type": "integer", "description": "How many months back to search (default 6)"},
            },
            "required": ["targets"],
        },
    },
    {
        "name": "fold_target",
        "description": "Predict the 3D protein structure of a gene target using GenomeClaw Boltz-1. Returns pLDDT confidence score and druggability interpretation. Use BEFORE committing R&D to a target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_symbol_or_sequence": {"type": "string", "description": "HGNC gene symbol (e.g. LRRK2) or raw amino-acid sequence"},
            },
            "required": ["gene_symbol_or_sequence"],
        },
    },
    {
        "name": "score_variant_effect",
        "description": "Score the functional effect of a protein variant using GenomeClaw ESM-2. Returns delta log-likelihood and resistance risk interpretation. Use for known resistance mutations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_symbol":       {"type": "string", "description": "HGNC gene symbol (e.g. KRAS, EGFR, LRRK2)"},
                "variant_notation":  {"type": "string", "description": "Variant in standard notation (e.g. G12C, L858R, G2019S)"},
            },
            "required": ["gene_symbol", "variant_notation"],
        },
    },
    {
        "name": "predict_admet",
        "description": "Predict ADMET properties (hERG, BBB, hepatotoxicity, oral bioavailability) for a drug. Assigns TIER-1 (all clear) / TIER-2 (minor flags) / TIER-3 (red flags). Use to filter repurposing candidates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "smiles_or_drug_name": {"type": "string", "description": "Drug name (e.g. levodopa) or SMILES string"},
            },
            "required": ["smiles_or_drug_name"],
        },
    },
    {
        "name": "cluster_scaffolds",
        "description": (
            "Group a hit list into distinct scaffold clusters by Tanimoto similarity (threshold 0.5). "
            "Requires SMILES on each hit — run find_hits first with clawapi running. "
            "Use after find_hits to ensure your ADMET screen covers maximal chemical diversity: "
            "advance the representative compound from each cluster, not just the single most potent hit. "
            "Returns cluster assignments, representative SMILES, and cluster sizes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hits": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of hit dicts from find_hits — each must have a 'smiles' field",
                },
            },
            "required": ["hits"],
        },
    },
    {
        "name": "dock_compound",
        "description": (
            "Score a ligand SMILES against a binding pocket using geometry-based pose generation. "
            "Returns best docking score and score distribution across poses. "
            "More negative = tighter predicted binding. "
            "pocket_center [x,y,z] coordinates come from fold_target output or known crystal structures. "
            "IMPORTANT: scores are relative within the same pocket — use to rank multiple ligands, "
            "not to predict absolute binding affinity. More accurate than ADMET alone for "
            "prioritising which scaffold cluster to advance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ligand_smiles": {
                    "type": "string",
                    "description": "Canonical SMILES of the ligand to dock",
                },
                "pocket_center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "3D coordinates [x, y, z] of the binding pocket center",
                },
                "n_poses": {
                    "type": "integer",
                    "description": "Number of poses to generate (default 10)",
                },
            },
            "required": ["ligand_smiles", "pocket_center"],
        },
    },
    {
        "name": "query_genomeclaw_databases",
        "description": "Query gnomAD, ChemBL, BindingDB, ClinVar, STRING, and cBioPortal via genomeclaw-data. Returns binding constants, pLI scores, variant counts, and a target-richness score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_or_drug": {"type": "string", "description": "Gene symbol or drug name to query"},
                "databases":    {"type": "array", "items": {"type": "string"}, "description": "Databases to query (default: all 6). Options: gnomad, chembl, bindingdb, clinvar, string, cbioportal"},
            },
            "required": ["gene_or_drug"],
        },
    },
    {
        "name": "list_pipeline_assets",
        "description": "Fast lookup of Roche/Genentech pipeline assets from enriched knowledge base. Returns phase, status, therapeutic area, indication, modality, mechanism, and safety signals. No API calls. Use this before rank_portfolio for context, or to answer 'what does Roche have in neurology/oncology/phase 3?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Filter by therapeutic area (e.g. 'Oncology', 'Neurology', 'Hematology', 'Immunology', 'Dermatology', 'Ophthalmology', 'Rare')"},
                "phase":            {"type": "string", "description": "Filter by phase: 'approved', '3', '2', '1', 'discontinued', 'partner_licensed'"},
                "status":           {"type": "string", "description": "Filter by status: 'active', 'approved', 'discontinued', 'partner_licensed'"},
                "modality":         {"type": "string", "description": "Filter by modality: 'mAb', 'bispecific', 'small_molecule', 'ADC', 'ASO', 'mRNA', 'protein', 'other'"},
            },
            "required": [],
        },
    },
    {
        "name": "query_competitive_intel",
        "description": "Query static competitive intelligence knowledge base. Returns competitor assets by therapeutic area, competitor name, or indication. Covers AZ, Lilly, Novartis, BMS, Pfizer, MSD, AbbVie, J&J across oncology/neurology/immunology/rare/ophthalmology. No API calls — use for initial context before monitor_competitive_signals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Filter by area (e.g. 'oncology', 'neurology', 'immunology', 'rare_disease', 'ophthalmology', 'metabolic')"},
                "competitor":       {"type": "string", "description": "Filter by competitor name (e.g. 'AstraZeneca', 'Eli Lilly', 'Novartis', 'AbbVie', 'Pfizer', 'Merck', 'Johnson & Johnson', 'Bristol-Myers Squibb')"},
                "indication":       {"type": "string", "description": "Filter by indication keyword (e.g. 'breast cancer', 'multiple sclerosis', 'atopic dermatitis')"},
            },
            "required": [],
        },
    },
    {
        "name": "find_hits",
        "description": "Hit identification: query ChEMBL for known active compounds against a gene target. Returns ranked hits by IC50/Ki with compound IDs, pIC50, and assay type. Use BEFORE lead optimization to establish existing chemical matter. Answers: 'What compounds are known to inhibit KRAS/EGFR/CDK6?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "target":       {"type": "string",  "description": "Gene symbol or target name (e.g. 'KRAS', 'EGFR', 'CDK6', 'BRAF')"},
                "max_ic50_nm":  {"type": "number",  "description": "Maximum IC50/Ki in nM to filter hits (default 1000 nM = 1 µM)"},
                "max_results":  {"type": "integer", "description": "Max number of hits to return (default 10)"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "query_adverse_events",
        "description": "Post-market surveillance: query FDA Adverse Event Reporting System (FAERS) for a drug. Returns total report count, serious/fatal percentages, top MedDRA reaction terms, and a safety signal rating. Use to assess real-world safety profile of approved or late-stage drugs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drug":        {"type": "string",  "description": "Drug name — brand or generic (e.g. 'atezolizumab', 'Tecentriq', 'bevacizumab')"},
                "event_type":  {"type": "string",  "description": "Filter type: 'serious', 'fatal', or 'all' (default 'serious')"},
                "top_n":       {"type": "integer", "description": "Number of top adverse reaction terms to return (default 10)"},
            },
            "required": ["drug"],
        },
    },
    {
        "name": "recall_longterm_memory",
        "description": (
            "Retrieve confirmed hits, negative results, ADMET profiles, or adverse event signals "
            "from the persistent cross-session knowledge base. "
            "Call BEFORE find_hits or predict_admet to avoid repeating known assays — "
            "negative results in the store mean those compounds have already failed and should be skipped. "
            "query_type: 'hits' | 'negatives' | 'admet' | 'adverse_events' | 'all'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["hits", "negatives", "admet", "adverse_events", "all"],
                    "description": "Category of records to retrieve",
                },
                "target_filter": {
                    "type": "string",
                    "description": "Optional gene symbol or drug name substring to filter results (case-insensitive)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum records to return per category (default 20)",
                },
            },
            "required": ["query_type"],
        },
    },
    {
        "name": "find_phenocopiers",
        "description": (
            "Find genes that share downstream biology with a target gene — 'phenocopiers'. "
            "Genes whose perturbation produces similar transcriptomic/network profiles surface novel targets "
            "for the same indication (Plex Research Wnt/eIF2 approach, AIA4S 2026). "
            "Uses Open Targets similar-targets API; falls back to STRING functional partners. "
            "Cross-filters by disease_context if provided. "
            "Use after find_gaps to expand the target hypothesis space."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_gene": {
                    "type": "string",
                    "description": "Gene symbol of the reference target (e.g. 'EGFR', 'KRAS', 'WNT5A')",
                },
                "disease_context": {
                    "type": "string",
                    "description": "Optional disease name to filter phenocopiers by Open Targets association score >0.3",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Maximum number of phenocopiers to return (default 15)",
                },
            },
            "required": ["target_gene"],
        },
    },
]

TOOL_FN_MAP = {
    "search_roche_trials":         search_roche_trials,
    "get_biology":                 get_biology,
    "check_competitor_trials":     check_competitor_trials,
    "find_gaps":                   find_gaps,
    "save_to_cache":               save_to_cache,
    "rank_portfolio":              rank_portfolio,
    "find_combinations":           find_combinations,
    "scan_literature":             scan_literature,
    "map_regulatory_path":         map_regulatory_path,
    "generate_pdf_report":         generate_pdf_report,
    "scan_arxiv":                  scan_arxiv,
    "score_trial_outcome":         score_trial_outcome,
    "find_repurposing_candidates": find_repurposing_candidates,
    "check_orphan_eligibility":    check_orphan_eligibility,
    "get_protein_structure_context": get_protein_structure_context,
    "monitor_competitive_signals":  monitor_competitive_signals,
    "find_shared_targets":          find_shared_targets,
    "bulk_scan_literature":         bulk_scan_literature,
    "fold_target":                  fold_target,
    "score_variant_effect":         score_variant_effect,
    "predict_admet":                predict_admet,
    "query_genomeclaw_databases":   query_genomeclaw_databases,
    "list_pipeline_assets":         list_pipeline_assets,
    "query_competitive_intel":      query_competitive_intel,
    "find_hits":                    find_hits,
    "query_adverse_events":         query_adverse_events,
    "recall_longterm_memory":       recall_longterm_memory,
    "find_phenocopiers":            find_phenocopiers,
    "get_pathway_context":          get_pathway_context,
    "get_disease_prevalence":       get_disease_prevalence,
    "cluster_scaffolds":            cluster_scaffolds,
    "dock_compound":                dock_compound,
    "search_patents":               search_patents,
    "get_patent_landscape":         get_patent_landscape,
}


# ── Tool category labels for verbose logging ────────────────────────────────────
_TOOL_CATEGORY = {
    "search_roche_trials":          "DISCOVERY",
    "get_biology":                  "DISCOVERY",
    "find_gaps":                    "DISCOVERY",
    "find_hits":                    "DISCOVERY",
    "find_repurposing_candidates":  "DISCOVERY",
    "find_combinations":            "DISCOVERY",
    "find_shared_targets":          "DISCOVERY",
    "find_phenocopiers":            "DISCOVERY",
    "get_pathway_context":          "DISCOVERY",
    "check_competitor_trials":      "COMPETITIVE",
    "monitor_competitive_signals":  "COMPETITIVE",
    "query_competitive_intel":      "COMPETITIVE",
    "rank_portfolio":               "COMPETITIVE",
    "list_pipeline_assets":         "COMPETITIVE",
    "scan_literature":              "EVIDENCE",
    "scan_arxiv":                   "EVIDENCE",
    "bulk_scan_literature":         "EVIDENCE",
    "map_regulatory_path":          "REGULATORY",
    "score_trial_outcome":          "REGULATORY",
    "check_orphan_eligibility":     "REGULATORY",
    "get_disease_prevalence":       "REGULATORY",
    "query_adverse_events":         "SAFETY",
    "get_protein_structure_context":"TARGET INTEL",
    "search_patents":               "IP / PATENTS",
    "get_patent_landscape":         "IP / PATENTS",
    "fold_target":                  "GENOMECLAW",
    "score_variant_effect":         "GENOMECLAW",
    "predict_admet":                "GENOMECLAW",
    "query_genomeclaw_databases":   "GENOMECLAW",
    "cluster_scaffolds":            "GENOMECLAW",
    "dock_compound":                "GENOMECLAW",
    "recall_longterm_memory":       "MEMORY",
    "save_to_cache":                "MEMORY",
    "generate_pdf_report":          "REPORT",
}


def _print_tool_call(turn: int, call_num: int, name: str, args: dict,
                     result_str: str, elapsed: float) -> None:
    """Print a verbose, timestamped banner for each tool call."""
    ts       = datetime.datetime.now().strftime("%H:%M:%S")
    category = _TOOL_CATEGORY.get(name, "TOOL")
    args_str = json.dumps(args, separators=(", ", "="))[1:-1]   # compact, no outer braces
    preview  = result_str[:400] + ("…" if len(result_str) > 400 else "")
    bar      = "─" * 65
    print(f"\n┌{bar}")
    print(f"│  [{ts}]  Call #{call_num} (turn {turn})  ·  {category}")
    print(f"│  {name}({args_str})")
    print(f"└{bar}")
    print(f"   → {preview}")
    print(f"   ⏱  {elapsed:.2f}s\n")


def make_client() -> anthropic.Anthropic:
    """
    Build an Anthropic client from the best available credential, in priority order:

    1. ANTHROPIC_API_KEY     — Standard API key (env var or config file) — direct API
    2. ANTHROPIC_AUTH_TOKEN  — OAuth subscription token → routed via local proxy
    3. No credentials        — error with instructions

    The local proxy (proxy_server.py) converts Anthropic SDK calls into `claude -p`
    subprocess calls, allowing subscription users to run the agent without separate
    API billing credits.
    """
    config_path = "configs/api_keys.json"
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)

    api_key    = os.environ.get("ANTHROPIC_API_KEY")    or cfg.get("ANTHROPIC_API_KEY")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or cfg.get("ANTHROPIC_AUTH_TOKEN")

    if api_key and not api_key.startswith("sk-ant-YOUR"):
        print("[Auth] Using API key → direct Anthropic API")
        return anthropic.Anthropic(api_key=api_key)

    if auth_token:
        print("[Auth] Subscription token detected → starting local proxy")
        from proxy_server import start_proxy
        import httpx
        port = start_proxy()
        # SDK points to local proxy; api_key value is ignored by the proxy.
        # httpx timeout raised to 600s — claude -p can take 60-120s per turn,
        # and the default 60s causes BrokenPipe errors on the proxy side.
        return anthropic.Anthropic(
            base_url=f"http://127.0.0.1:{port}",
            api_key="proxy-auth",
            http_client=httpx.Client(timeout=600.0),
        )

    print("ERROR: No Anthropic credentials found.")
    print()
    print("Set one of the following:")
    print("  API key (direct):    export ANTHROPIC_API_KEY=sk-ant-api03-...")
    print("  Subscription proxy:  export ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-...")
    print(f"  Config file:         add either key to {config_path}")
    sys.exit(1)

SYSTEM_PROMPT = """You are the Roche AI Factory Strategic Discovery Agent.

Roche and Genentech are the same company. Always treat them as one entity.

You have 30 tools available:

DISCOVERY
- search_roche_trials    → What trials does Roche/Genentech have active in an area?
- get_biology            → What diseases does a drug or gene target have strong evidence for?
- find_gaps              → Where is biology strong but Roche has no trial? (core analysis)
- find_hits              → Hit identification: ChEMBL actives against a gene target ranked by IC50/pIC50
- find_repurposing_candidates → Approved drugs that could skip Phase I into a new indication

COMPETITIVE & PORTFOLIO
- check_competitor_trials     → Is AZ / Lilly / Novartis already in a gap? (live CT.gov query)
- monitor_competitive_signals → Live 8-competitor activity table for a disease (parallel queries)
- query_competitive_intel     → Fast offline: competitor assets, mechanisms, phase for AZ/Lilly/Novartis/etc. (30+ programs)
- rank_portfolio               → Score all portfolio assets by composite opportunity (OT + CT.gov)
- list_pipeline_assets         → Fast offline: Roche pipeline by TA/phase/modality with enriched metadata (no API calls)
- find_combinations            → Which Roche drugs target complementary pathways in the same disease?
- get_pathway_context          → KEGG pathway memberships for a gene — use before find_combinations to detect synergy vs. redundancy

EVIDENCE & REGULATORY
- scan_literature             → Recent peer-reviewed papers (Europe PMC + ArXiv in parallel); supports min_year filter
- scan_arxiv                  → ArXiv preprints only (6-18mo ahead of peer review); supports min_year filter
- bulk_scan_literature        → Scan all portfolio targets for recent papers in parallel (use for "last 6 months" questions)
- map_regulatory_path         → What endpoint, biomarker, CDx, and expedited pathway does FDA require? (30+ indications)
- score_trial_outcome         → Likelihood of trial success (0.0–1.0 score + risk factors; TA-adjusted priors built in)
- check_orphan_eligibility    → Orphan Drug Designation eligibility + 7yr exclusivity + tax credits
- get_disease_prevalence      → Live prevalence lookup (PREVALENCE_MAP → Orphanet API fallback) for orphan/market sizing
- query_adverse_events        → FDA FAERS post-market surveillance: serious/fatal rates, top MedDRA reactions

TARGET INTELLIGENCE
- get_protein_structure_context → Is this target actually druggable? (UniProt + OT tractability + 3D fold)
- find_shared_targets           → Gene targets shared between two diseases above a confidence threshold
- find_phenocopiers             → Genes sharing downstream biology with a target — novel targets for same indication

IP / PATENTS
- search_patents              → Search US + global patents by keyword or assignee (USPTO PatentsView + Lens.org)
- get_patent_landscape        → Full IP landscape for a target/compound: filing volume, top assignees, FTO flag, white-space note
  Use after find_hits or find_repurposing_candidates before map_regulatory_path to flag IP barriers early.

MEMORY
- recall_longterm_memory      → Cross-session knowledge base: prior hits, negatives, ADMET profiles, AE signals
- save_to_cache               → Persist findings to intelligence_cache.json
- generate_pdf_report         → Final step — full structured PDF report

GENOMECLAW TOOLS (local Boltz-1/ESM-2 API at 127.0.0.1:8083)
- fold_target                 → Predict 3D protein structure + pLDDT confidence (actual binding pocket geometry)
- score_variant_effect        → Does this mutation damage drug binding? (delta log-likelihood, resistance risk)
- predict_admet               → MANDATORY safety gate — hERG, BBB, hepatotoxicity, oral bioavailability (TIER-1/2/3)
- query_genomeclaw_databases  → gnomAD/ChemBL/BindingDB/ClinVar/STRING/cBioPortal in one call
- cluster_scaffolds           → Group find_hits output into scaffold series by Tanimoto ≥ 0.5 (requires SMILES on hits)
- dock_compound               → Rank ligands against a pocket by geometry-based pose score; pocket_center from fold_target

WORKFLOW GUIDANCE
- For gap questions: find_gaps → monitor_competitive_signals → scan_literature → map_regulatory_path → save_to_cache
- For portfolio overview: list_pipeline_assets(therapeutic_area=...) → rank_portfolio → find_gaps on top assets
- For competitive landscape: query_competitive_intel(therapeutic_area=...) → monitor_competitive_signals(disease) → check_competitor_trials
- For new target gaps: get_protein_structure_context → fold_target → score_variant_effect → query_genomeclaw_databases
- For cross-disease targets: find_shared_targets(disease1, disease2) → get_biology on top hits → find_gaps
- For novel target expansion: find_gaps → find_phenocopiers(top_gap_target, disease_context) → get_biology on phenocopiers
- For portfolio literature pulse: bulk_scan_literature(all_targets, months_back=6) → scan_literature on top hits
- For date-filtered literature: scan_literature(target, disease, min_year=2024) or scan_arxiv(..., min_year=2024)
- For hit identification: recall_longterm_memory(hits, target_filter=gene) FIRST → find_hits → cluster_scaffolds(hits) → dock_compound(rep_smiles, pocket_center) → predict_admet (MANDATORY — advance TIER-1 only) → score_variant_effect on key mutations
- For repurposing: find_repurposing_candidates → predict_admet (MANDATORY — advance TIER-1 only) → map_regulatory_path
- For combination questions: get_pathway_context(gene) → find_combinations → get_biology on each drug → scan_literature
- For orphan/rare disease: get_disease_prevalence(disease) → check_orphan_eligibility → map_regulatory_path
- For safety profiling: query_adverse_events → compare serious/fatal rates across drug class → flag for score_trial_outcome
- For regulatory questions: map_regulatory_path returns full FDA endpoint/biomarker/CDx/expedited pathway guidance
- For competitive urgency: monitor_competitive_signals → score_trial_outcome → score_variant_effect on known resistance mutations
- Always save high-value findings before ending
- ALWAYS call generate_pdf_report as the very last step with a concise ceo_summary

recall_longterm_memory is the cross-session knowledge base. Call it BEFORE find_hits or predict_admet
to surface prior results. Compounds in negative_results have already failed — skip them.
predict_admet is a MANDATORY gate after find_hits and find_repurposing_candidates.
Never advance a compound to map_regulatory_path or score_trial_outcome without TIER-1 ADMET clearance.
score_trial_outcome applies TA-adjusted priors: CNS -0.10 | anti-infectives +0.08 | metabolic +0.05.
find_gaps returns translational_confidence (LOW/MODERATE/HIGH) per gap — weight HIGH gaps first.
Reason step by step. Never guess tool results. Prioritise gaps with bio score > 0.70.
Final output must be concise and CEO-ready with clear action items."""


# ── Agent loop ──────────────────────────────────────────────────────────────────

def run_agent(question: str, model: str = MODEL):
    print("\n" + "=" * 65)
    print(f"  ROCHE AI FACTORY — STRATEGIC DISCOVERY AGENT")
    print(f"  Query: {question}")
    print("=" * 65 + "\n")

    SESSION["question"] = question
    _audit   = AuditLogger(model=model, query=question)
    client   = make_client()
    messages = [{"role": "user", "content": question}]
    turn               = 0
    call_counter       = 0          # global across all turns (hoisted for session_end)
    tools_called: set  = set()
    consecutive_stalls = 0          # turns with no tool_use in a row

    # Tools that must be called before the agent can write a final_answer when
    # the query explicitly asks for a PDF report.
    REPORT_REQUIRED_TOOLS = {
        "generate_pdf_report",
        "list_pipeline_assets",
        "query_competitive_intel",
        "search_clinical_trials",
        "query_patent_landscape",
    }

    # Minimum data-gathering tools required for analysis queries.
    # Prevents the model (especially via proxy) from hallucinating results
    # without actually calling any tools.
    _q = question.lower()
    ANALYSIS_KEYWORDS = ("find", "identify", "gap", "pipeline", "hit", "admet",
                         "assess", "analyze", "analys", "repurpos", "patent",
                         "landscape", "competitive", "rank", "scan", "literature")
    _is_analysis = any(kw in _q for kw in ANALYSIS_KEYWORDS)
    # At least one data source must be called before completion on analysis queries
    DATA_TOOLS = {
        "find_gaps", "find_hits", "list_pipeline_assets", "get_biology",
        "search_roche_trials", "find_repurposing_candidates", "scan_literature",
        "scan_arxiv", "bulk_scan_literature", "monitor_competitive_signals",
        "query_competitive_intel", "query_genomeclaw_databases", "fold_target",
        "predict_admet", "search_patents", "get_patent_landscape",
        "recall_longterm_memory", "rank_portfolio",
    }

    if _check_genomeclaw_health():
        print("[GenomeClaw] API reachable — fold_target / score_variant_effect / predict_admet enabled")
    else:
        print("[GenomeClaw] WARNING: API not reachable at", CLAWAPI_URL,
              "— fold/variant/ADMET tools will return offline status")

    while turn < MAX_TURNS:
        turn += 1
        ts_turn = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n══ Turn {turn}/{MAX_TURNS}  [{ts_turn}]  tools called so far: {len(tools_called)} ══")

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Collect text reasoning and tool calls
        tool_calls = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\n[Reasoning]\n{block.text.strip()}\n")
            elif block.type == "tool_use":
                tool_calls.append(block)

        # If no tool calls, check whether required tools have been called.
        # If not, inject a reminder instead of completing — prevents the model
        # from hallucinating completion without calling generate_pdf_report.
        if response.stop_reason == "end_turn" or not tool_calls:
            consecutive_stalls += 1

            # Hard-abort after 3 consecutive stalls — prevents burning all MAX_TURNS
            # on proxy text fallback responses. Caller should resubmit the job.
            if consecutive_stalls >= 3:
                print(f"\n[guard] STALL ABORT — {consecutive_stalls} consecutive turns with no "
                      "tool calls. Proxy returned text instead of tool_use. Resubmit the job.")
                break

            needs_report = "pdf" in question.lower() or "report" in question.lower()
            missing_report = REPORT_REQUIRED_TOOLS - tools_called if needs_report else set()
            missing_data = DATA_TOOLS if (_is_analysis and not (tools_called & DATA_TOOLS)) else set()
            missing = missing_report | ({"[any data tool]"} if missing_data else set())

            # Also fire the guard mid-run: if any tools have been called but we haven't
            # finished yet (no generate_pdf_report), a text-only response is a stall.
            _mid_run_stall = (
                bool(tools_called)
                and "generate_pdf_report" not in tools_called
                and not tool_calls
            )
            if _mid_run_stall and not missing:
                missing = {"[continue workflow]"}

            if missing and turn < MAX_TURNS - 1:
                if missing_data:
                    reminder = (
                        "SYSTEM REMINDER: You have not called any data-gathering tools yet. "
                        "You MUST call tools to fetch real data before writing final_answer. "
                        "Do NOT fabricate results. Start with recall_longterm_memory or list_pipeline_assets, "
                        "then proceed step by step through the analysis. Call ONE tool now."
                    )
                elif _mid_run_stall and missing == {"[continue workflow]"}:
                    reminder = (
                        "SYSTEM REMINDER: You returned text with no tool call. "
                        "You are mid-analysis — do NOT write a final_answer yet. "
                        "Call the next appropriate tool to continue the workflow."
                    )
                else:
                    reminder = (
                        f"SYSTEM REMINDER: You have not yet called {sorted(missing_report)}. "
                        "You MUST call these tools before writing your final_answer. Continue the analysis."
                    )
                print(f"[guard] Missing required tools before completion: {missing} — injecting reminder")
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": reminder})
                continue
            print("\n" + "=" * 65)
            print("  AGENT COMPLETE")
            print("=" * 65)
            break

        consecutive_stalls = 0  # reset on any successful tool-call turn

        # Execute tool calls and collect results
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []

        for call in tool_calls:
            fn   = TOOL_FN_MAP.get(call.name)
            args = call.input
            tools_called.add(call.name)
            call_counter += 1

            t0 = time.monotonic()
            if fn:
                try:
                    result = fn(**args)
                except Exception as e:
                    result = {"error": str(e)}
            else:
                result = {"error": f"Unknown tool: {call.name}"}
            elapsed = time.monotonic() - t0

            result_str = json.dumps(result)
            _print_tool_call(turn, call_counter, call.name, args, result_str, elapsed)
            _audit.log(
                turn, call_counter, call.name,
                _TOOL_CATEGORY.get(call.name, "TOOL"),
                args, result_str, elapsed,
            )
            if call.name == "generate_pdf_report" and result.get("file"):
                print(f"   📄  PDF REPORT SAVED → {result['file']}\n")
            # Truncate oversized results to stay within context window.
            # Fold/structure results can be 50-200KB; cap individual results at 20KB.
            MAX_RESULT_BYTES = 20_000
            if len(result_str) > MAX_RESULT_BYTES:
                truncated = result_str[:MAX_RESULT_BYTES]
                result_str = truncated + f'... [TRUNCATED — original {len(result_str)} chars]"'
                print(f"       [context] result truncated to {MAX_RESULT_BYTES} chars\n")
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": call.id,
                "content":     result_str,
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"\n[WARNING] Reached MAX_TURNS ({MAX_TURNS}). Forcing completion.")
        print("=" * 65)

    _audit.log_session_end(total_turns=turn, total_calls=call_counter)
    _audit.close()
    print(f"\n[Audit] Session complete. Full trail → {_audit.log_path}")
    if os.environ.get("AUDIT_SUMMARY"):
        print_audit_summary(_audit.log_path)


# ── Entry point ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Roche AI Factory Strategic Discovery Agent")
    parser.add_argument("question", help="Strategic question to answer")
    parser.add_argument("--model", default=MODEL,
                        help="Claude model ID (default: %(default)s)")
    args = parser.parse_args()
    run_agent(args.question, model=args.model)
