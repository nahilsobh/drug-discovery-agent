"""
PDF generation helper for the 20-by-30 Strategic Orchestrator.
Called by orchestrator_agent.generate_turbospeed_report().
"""

import uuid


def generate_pdf(
    filename: str,
    portfolio_summary: str,
    ceo_summary: str,
    session_data: dict,
    today,
    sotd_target: float,
    sotd_median: float,
    asset_timelines_db: dict,
    thin_layer_mdm_db: dict,
    bionemo_cache_db: dict,
    turbospeed_flag_months: float,
) -> dict:
    """Build the 6-section Turbospeed Dashboard PDF and write it to *filename*."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph,
        Spacer, Table, TableStyle,
    )

    # ── Colour palette ──────────────────────────────────────────────────────────
    ROCHE_BLUE  = colors.HexColor("#005BAC")
    DARK        = colors.HexColor("#1A2533")
    GREEN       = colors.HexColor("#27AE60")
    AMBER       = colors.HexColor("#E67E22")
    RED         = colors.HexColor("#C0392B")
    LIGHT_BLUE  = colors.HexColor("#EBF5FB")
    LIGHT_GREEN = colors.HexColor("#EAFAF1")
    LIGHT_AMBER = colors.HexColor("#FEF9E7")
    LIGHT_RED   = colors.HexColor("#FDEDEC")
    MID_GREY    = colors.HexColor("#95A5A6")
    LIGHT_GREY  = colors.HexColor("#F2F3F4")

    W, H = A4
    styles = getSampleStyleSheet()

    def style(name, **kw):
        base = styles[name] if name in styles else styles["Normal"]
        ps = ParagraphStyle(f"custom_{uuid.uuid4().hex[:6]}", parent=base, **kw)
        return ps

    TITLE   = style("Heading1", fontSize=22, textColor=ROCHE_BLUE, leading=26, spaceAfter=4)
    H2      = style("Heading2", fontSize=13, textColor=DARK,       leading=16, spaceBefore=10, spaceAfter=4)
    H3      = style("Heading3", fontSize=10, textColor=ROCHE_BLUE, leading=12, spaceBefore=8,  spaceAfter=2)
    BODY    = style("Normal",   fontSize=8.5, leading=12, textColor=DARK)
    CAPTION = style("Normal",   fontSize=7.5, leading=10, textColor=MID_GREY)
    BOLD    = style("Normal",   fontSize=8.5, leading=12, textColor=DARK, fontName="Helvetica-Bold")

    TH_STYLE = [
        ("BACKGROUND",    (0, 0), (-1, 0), ROCHE_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
        ("FONTSIZE",      (0, 1), (-1, -1), 7.5),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#BDC3C7")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]

    story = []
    add = story.append

    def rule():
        add(HRFlowable(width="100%", thickness=0.5, color=MID_GREY, spaceAfter=4))

    def ts_color(score):
        if score >= 0.70:
            return GREEN
        elif score >= 0.50:
            return AMBER
        return RED

    def ts_bg(score):
        if score >= 0.70:
            return LIGHT_GREEN
        elif score >= 0.50:
            return LIGHT_AMBER
        return LIGHT_RED

    # ── Cover ──────────────────────────────────────────────────────────────────
    add(Spacer(1, 1.2 * cm))
    add(Paragraph("20-BY-30 STRATEGIC ORCHESTRATOR", TITLE))
    add(Paragraph("TURBOSPEED DASHBOARD — ROCHE CSI HACKATHON 2026", style("Normal", fontSize=13, textColor=ROCHE_BLUE, leading=16)))
    add(Spacer(1, 0.4 * cm))
    rule()

    meta_data = [
        ["Query:", session_data.get("query", "Full portfolio Turbospeed audit")],
        ["Date:", today.strftime("%B %d, %Y")],
        ["Platform:", "Roche NVIDIA AI Factory — 3,500+ Blackwell GPUs (CUDA 12.8)"],
        ["Framework:", "WS7 Thin Layer / MDM Integration Layer (PBAC: Read-Only)"],
        ["Compliance:", "Opulus Standard QMS v1.0 — FDA 510(k) K260001 (March 26, 2026)"],
        ["Target:", f"SoTD → FiH in {sotd_target} months (median: {sotd_median} months)"],
    ]
    meta_tbl = Table(meta_data, colWidths=[3.5 * cm, 13.5 * cm])
    meta_tbl.setStyle(TableStyle([
        ("FONTNAME",  (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",  (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), ROCHE_BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    add(meta_tbl)
    add(Spacer(1, 0.5 * cm))

    # Stats box
    ts_scores = session_data.get("turbospeed_scores", [])
    on_track  = sum(1 for s in ts_scores if s.get("turbospeed_score", 0) >= 0.70)
    at_risk   = sum(1 for s in ts_scores if 0.50 <= s.get("turbospeed_score", 0) < 0.70)
    critical  = sum(1 for s in ts_scores if s.get("turbospeed_score", 0) < 0.50)
    n_levers  = sum(len(l.get("recommended_levers", [])) for l in session_data.get("turbospeed_levers", []))
    n_bionemo = len(session_data.get("bionemo_simulations", []))
    n_ihb     = len(session_data.get("ihb_validations", []))

    stats_data = [
        [f"{len(ts_scores)}", f"{on_track}", f"{at_risk}", f"{critical}", f"{n_levers}", f"{n_bionemo}"],
        ["Assets Scored", "On Track", "At Risk", "Critical", "Levers Rec.", "BioNeMo Sims"],
    ]
    stats_tbl = Table(stats_data, colWidths=[2.85 * cm] * 6, rowHeights=[0.9 * cm, 0.5 * cm])
    stats_tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, 0), ROCHE_BLUE),
        ("BACKGROUND",  (1, 0), (1, 0), GREEN),
        ("BACKGROUND",  (2, 0), (2, 0), AMBER),
        ("BACKGROUND",  (3, 0), (3, 0), RED),
        ("BACKGROUND",  (4, 0), (4, 0), colors.HexColor("#8E44AD")),
        ("BACKGROUND",  (5, 0), (5, 0), colors.HexColor("#2E86C1")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 18),
        ("FONTSIZE",    (0, 1), (-1, 1), 7.5),
        ("FONTNAME",    (0, 1), (-1, 1), "Helvetica"),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.white),
    ]))
    add(stats_tbl)
    add(Spacer(1, 0.4 * cm))

    if ceo_summary:
        add(Paragraph("Executive Summary", H2))
        add(Paragraph(ceo_summary, BODY))
    rule()

    # ── Section 1: Asset Timeline Matrix ───────────────────────────────────────
    add(Paragraph("SECTION 1 — ASSET TIMELINE MATRIX (All 20 Assets)", H2))
    add(Paragraph(
        f"SoTD median: {sotd_median} months → target: {sotd_target} months. "
        "Assets flagged red have exceeded the median — immediate lever application required.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    # Build score lookup
    score_map    = {s["asset_name"].lower(): s for s in ts_scores}
    timeline_map = {t["asset_name"].lower(): t for t in session_data.get("asset_timelines", [])}

    # Use all portfolio assets as rows; fill from timelines if available
    all_assets_db = asset_timelines_db.get("assets", [])

    hdrs  = ["Asset", "SoTD", "Mo.\nElapsed", "Phase", "Sites", "TS Score", "Status"]
    col_w = [3.2 * cm, 2.0 * cm, 1.5 * cm, 2.4 * cm, 1.2 * cm, 1.5 * cm, 4.2 * cm]
    tbl_data   = [hdrs]
    row_styles = list(TH_STYLE)

    for row_i, asset in enumerate(all_assets_db, start=1):
        aname   = asset["name"]
        aname_l = aname.lower()
        tl      = timeline_map.get(aname_l, asset)
        sc      = score_map.get(aname_l)
        mo_el   = tl.get("months_elapsed", asset.get("months_elapsed", "—"))
        phase   = tl.get("current_phase", asset.get("current_phase", "—"))
        sites   = tl.get("active_sites", asset.get("active_sites", "—"))

        if sc:
            ts_val    = sc["turbospeed_score"]
            ts_str    = f"{ts_val:.3f}"
            status_str = sc["label"]
        else:
            ts_val    = None
            ts_str    = "—"
            status_str = "Not scored"

        row = [aname, asset.get("sotd_date", "—"), str(mo_el), phase, str(sites), ts_str, status_str]
        tbl_data.append(row)

        if ts_val is not None:
            bg = ts_bg(ts_val)
            row_styles.append(("BACKGROUND", (5, row_i), (6, row_i), bg))
            row_styles.append(("TEXTCOLOR",  (5, row_i), (6, row_i), ts_color(ts_val)))
            row_styles.append(("FONTNAME",   (5, row_i), (6, row_i), "Helvetica-Bold"))
        if mo_el != "—" and float(mo_el) > turbospeed_flag_months:
            row_styles.append(("BACKGROUND", (2, row_i), (2, row_i), LIGHT_RED))

    tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle(row_styles))
    add(tbl)
    add(Spacer(1, 0.3 * cm))

    # ── Section 2: Flagged Assets & Levers ─────────────────────────────────────
    flagged    = [a for a in all_assets_db if a.get("months_elapsed", 0) > turbospeed_flag_months]
    levers_map = {l["asset_name"].lower(): l for l in session_data.get("turbospeed_levers", [])}

    if flagged:
        add(Paragraph(f"SECTION 2 — FLAGGED ASSETS & TURBOSPEED LEVERS ({len(flagged)} assets)", H2))
        add(Paragraph(
            f"{len(flagged)} assets exceed the {sotd_median}-month SoTD→FiH median. "
            "Lever recommendations shown below — total potential savings in weeks.",
            BODY,
        ))

        for asset in flagged:
            aname_l    = asset["name"].lower()
            bottleneck = asset.get("bottleneck") or "protocol_complexity"
            lever_rec  = levers_map.get(aname_l)

            add(Spacer(1, 0.3 * cm))
            add(Paragraph(
                f"{asset['name']} ({asset.get('alias', '')}) — {asset.get('indication', '')}",
                H3,
            ))
            mo_el = asset.get("months_elapsed", "?")
            add(Paragraph(
                f"Months elapsed: <b>{mo_el}</b> | Phase: {asset.get('current_phase')} | "
                f"Sites: {asset.get('active_sites')} | Bottleneck: <b>{bottleneck.replace('_', ' ').title()}</b>",
                BODY,
            ))

            if lever_rec:
                lev_hdrs = ["#", "Lever", "Category", "Saving (wks)", "Evidence"]
                lev_data = [lev_hdrs]
                for i, lv in enumerate(lever_rec.get("recommended_levers", []), 1):
                    lev_data.append([
                        str(i),
                        Paragraph(lv["name"], style("Normal", fontSize=7, leading=9)),
                        lv.get("rde_category", ""),
                        f"+{lv['time_saving_weeks']}",
                        lv.get("evidence_level", ""),
                    ])
                total_wks = lever_rec.get("total_potential_saving_weeks", 0)
                total_mo  = lever_rec.get("total_potential_saving_months", 0)
                lev_data.append(["", Paragraph(f"<b>Total potential saving</b>", BOLD), "", f"<b>+{total_wks} wks</b>", f"({total_mo} mo)"])

                lev_tbl = Table(lev_data, colWidths=[0.5 * cm, 7.5 * cm, 2.5 * cm, 1.8 * cm, 1.7 * cm])
                lev_tbl.setStyle(TableStyle(TH_STYLE + [
                    ("BACKGROUND", (0, len(lev_data) - 1), (-1, len(lev_data) - 1), LIGHT_BLUE),
                    ("FONTNAME",   (0, len(lev_data) - 1), (-1, len(lev_data) - 1), "Helvetica-Bold"),
                ]))
                add(lev_tbl)
            else:
                add(Paragraph(
                    f"No lever recommendations loaded yet — call recommend_turbospeed_levers('{asset['name']}').",
                    CAPTION,
                ))

        add(Spacer(1, 0.3 * cm))
        rule()

    # ── Section 3: Thin Layer MDM Site Intelligence ─────────────────────────────
    all_sites = thin_layer_mdm_db.get("sites", [])

    add(Paragraph("SECTION 3 — THIN LAYER MDM SITE INTELLIGENCE", H2))
    add(Paragraph(
        f"MDM-verified sites from the WS7 Thin Layer Framework. "
        f"Deduplication engine: {thin_layer_mdm_db.get('deduplication_engine', 'MDM-CANONICAL-v4')}. "
        f"PBAC: Read-Only.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    site_hdrs  = ["MDM ID", "Site Name", "City / Country", "Capabilities", "Active\nTrials", "Investigators", "Screen Fail%"]
    site_col_w = [2.0 * cm, 5.0 * cm, 2.2 * cm, 3.8 * cm, 1.3 * cm, 1.8 * cm, 1.9 * cm]
    site_data  = [site_hdrs]
    for s in all_sites[:15]:  # top 15 for space
        caps = ", ".join(s.get("capabilities", [])[:3])
        site_data.append([
            s.get("mdm_id", ""),
            Paragraph(s["name"][:50], style("Normal", fontSize=7, leading=9)),
            f"{s['city']}, {s['country']}",
            Paragraph(caps, style("Normal", fontSize=7, leading=9)),
            str(s.get("active_roche_trials", "")),
            str(s.get("qualified_investigators", "")),
            f"{int(s.get('screen_failure_rate', 0) * 100)}%",
        ])
    site_tbl = Table(site_data, colWidths=site_col_w, repeatRows=1)
    site_tbl.setStyle(TableStyle(TH_STYLE))
    add(site_tbl)
    add(Paragraph("Showing top 15 of 20 MDM-verified sites. All sites PBAC-tagged READ_ONLY.", CAPTION))
    add(Spacer(1, 0.3 * cm))
    rule()

    # ── Section 4: BioNeMo Molecular Simulations ───────────────────────────────
    bionemo_sims = session_data.get("bionemo_simulations", [])
    add(Paragraph("SECTION 4 — BIONEMO MOLECULAR SIMULATIONS", H2))
    add(Paragraph(
        f"NVIDIA BioNeMo (ESM-2 650M + MolMIM + DiffDock) — "
        f"{bionemo_cache_db.get('gpu_cluster', 'NVIDIA AI Factory — 3,500+ Blackwell GPUs')}.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    if bionemo_sims:
        bio_hdrs    = ["Compound", "Target", "Predicted IC50 (nM)", "Selectivity", "Toxicity", "hERG", "P(Success)", "Confidence", "GPU Node"]
        bio_col_w   = [2.2*cm, 1.5*cm, 2.5*cm, 1.8*cm, 1.5*cm, 1.2*cm, 1.8*cm, 1.8*cm, 2.7*cm]
        bio_data    = [bio_hdrs]
        bio_row_styles = list(TH_STYLE)
        for row_i, b in enumerate(bionemo_sims, start=1):
            tox = "YES" if b.get("toxicity_flag") else "No"
            tox_color = RED if b.get("toxicity_flag") else GREEN
            bio_data.append([
                b.get("compound", b.get("target_gene", "")),
                b.get("target_gene", ""),
                str(b.get("predicted_ic50_nm", "—")),
                str(b.get("selectivity_ratio", "—")),
                tox,
                b.get("herg_risk", "—"),
                f"{b.get('success_probability', 0):.2f}" if b.get("success_probability") else "—",
                f"{b.get('confidence', 0):.2f}" if b.get("confidence") else "—",
                b.get("gpu_node_used", "—"),
            ])
            if b.get("toxicity_flag"):
                bio_row_styles.append(("BACKGROUND", (4, row_i), (4, row_i), LIGHT_RED))
            else:
                bio_row_styles.append(("TEXTCOLOR", (4, row_i), (4, row_i), GREEN))

        bio_tbl = Table(bio_data, colWidths=bio_col_w, repeatRows=1)
        bio_tbl.setStyle(TableStyle(bio_row_styles))
        add(bio_tbl)
    else:
        add(Paragraph("No BioNeMo simulations loaded — call run_bionemo_simulation() first.", CAPTION))

    add(Spacer(1, 0.3 * cm))
    rule()

    # ── Section 5: IHB Organoid Validation ─────────────────────────────────────
    ihb_vals = session_data.get("ihb_validations", [])
    add(Paragraph("SECTION 5 — IHB ORGANOID-ON-A-CHIP VALIDATION", H2))
    add(Paragraph(
        "IHB Human Model System — cross-referenced against BioNeMo predictions. "
        "Concordance ≥0.75 = validated; <0.60 = discordant (wet-lab re-run required).",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    if ihb_vals:
        ihb_hdrs      = ["Target", "Compound Class", "Organoid Type", "Concordance", "Assays", "Validation Status", "Risk Flags"]
        ihb_col_w     = [1.8*cm, 3.0*cm, 2.5*cm, 1.8*cm, 1.2*cm, 4.0*cm, 2.7*cm]
        ihb_data      = [ihb_hdrs]
        ihb_row_styles = list(TH_STYLE)
        for row_i, v in enumerate(ihb_vals, start=1):
            conc     = v.get("concordance_rate", 0)
            conc_str = f"{conc:.2f}"
            flags    = ", ".join(v.get("risk_flags", [])) or "None"
            ihb_data.append([
                v.get("target_gene", ""),
                Paragraph(v.get("compound_class", ""), style("Normal", fontSize=7, leading=9)),
                v.get("organoid_type", ""),
                conc_str,
                str(v.get("organoid_assay_count", "—")),
                Paragraph(v.get("validation_status", ""), style("Normal", fontSize=7, leading=9)),
                Paragraph(flags, style("Normal", fontSize=7, leading=9, textColor=RED if flags != "None" else GREEN)),
            ])
            ihb_row_styles.append(("BACKGROUND", (3, row_i), (3, row_i), LIGHT_GREEN if conc >= 0.75 else (LIGHT_AMBER if conc >= 0.60 else LIGHT_RED)))

        ihb_tbl = Table(ihb_data, colWidths=ihb_col_w, repeatRows=1)
        ihb_tbl.setStyle(TableStyle(ihb_row_styles))
        add(ihb_tbl)
    else:
        add(Paragraph("No IHB organoid validations loaded — call validate_ihb_organoid() first.", CAPTION))

    add(Spacer(1, 0.3 * cm))
    rule()

    # ── Section 6: SaMD Compliance Audit (Opulus Standard) ─────────────────────
    samd_audits = session_data.get("samd_audits", [])
    add(Paragraph("SECTION 6 — SAMD COMPLIANCE AUDIT (OPULUS STANDARD)", H2))
    add(Paragraph(
        "Opulus Standard QMS v1.0 — FDA 510(k) K260001 cleared March 26, 2026. "
        "Applies to companion diagnostics (CDx), AI diagnostics, and digital biomarkers.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    if samd_audits:
        samd_hdrs    = ["Asset", "SaMD Type", "Status", "Checks\nPassed", "Gaps", "Remediation\n(weeks)", "Recommendation"]
        samd_col_w   = [2.5*cm, 2.2*cm, 2.2*cm, 1.5*cm, 0.8*cm, 1.8*cm, 5.0*cm]
        samd_data    = [samd_hdrs]
        samd_row_styles = list(TH_STYLE)
        for row_i, a in enumerate(samd_audits, start=1):
            status       = a.get("compliance_status", "")
            status_color = GREEN if status == "COMPLIANT" else (AMBER if status == "MINOR_GAPS" else RED)
            samd_data.append([
                a.get("asset_name", ""),
                a.get("samd_type", ""),
                status,
                f"{a.get('checks_passed', 0)}/{a.get('total_checks', 0)}",
                str(a.get("gap_count", 0)),
                str(a.get("remediation_weeks", 0)),
                Paragraph(a.get("recommendation", ""), style("Normal", fontSize=7, leading=9)),
            ])
            samd_row_styles.append(("TEXTCOLOR",   (2, row_i), (2, row_i), status_color))
            samd_row_styles.append(("FONTNAME",    (2, row_i), (2, row_i), "Helvetica-Bold"))
            if status == "MAJOR_GAPS":
                samd_row_styles.append(("BACKGROUND", (0, row_i), (-1, row_i), LIGHT_RED))
            elif status == "MINOR_GAPS":
                samd_row_styles.append(("BACKGROUND", (0, row_i), (-1, row_i), LIGHT_AMBER))

        samd_tbl = Table(samd_data, colWidths=samd_col_w, repeatRows=1)
        samd_tbl.setStyle(TableStyle(samd_row_styles))
        add(samd_tbl)
    else:
        add(Paragraph("No SaMD audits loaded — call audit_samd_compliance() for CDx/AI assets.", CAPTION))

    add(Spacer(1, 0.4 * cm))
    rule()

    # ── Footer ──────────────────────────────────────────────────────────────────
    add(Paragraph(
        f"Generated {today.strftime('%B %d, %Y')} by 20-by-30 Strategic Orchestrator v1.0 — "
        "Roche CSI Hackathon 2026 | NVIDIA AI Factory | One Roche",
        CAPTION,
    ))

    # ── Build PDF ───────────────────────────────────────────────────────────────
    frame = Frame(1.5*cm, 1.5*cm, W - 3*cm, H - 3*cm, id="main")
    tpl   = PageTemplate(id="main", frames=[frame])
    doc   = BaseDocTemplate(filename, pagesize=A4, pageTemplates=[tpl])
    doc.build(story)

    return {
        "sections":          6,
        "assets_in_matrix":  len(all_assets_db),
        "flagged":           len(flagged),
        "ts_scored":         len(ts_scores),
        "bionemo_sims":      len(bionemo_sims),
        "ihb_validations":   len(ihb_vals),
        "samd_audits":       len(samd_audits),
    }
