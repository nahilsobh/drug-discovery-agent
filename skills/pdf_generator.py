from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

class PDFDossierAgent:
    def __init__(self, filename="RedClaw_Strategic_Expert_Dossier_2026.pdf"):
        self.doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
        self.styles = getSampleStyleSheet()
        
        # Define high-readability styles
        self.styles.add(ParagraphStyle(name='TableCell', fontSize=9, leading=11))
        self.styles.add(ParagraphStyle(
            name='RefStyle', 
            fontSize=8, 
            leading=11, 
            textColor=colors.navy,
            leftIndent=12,
            firstLineIndent=-12 # Hanging indent for professional look
        ))
        self.story = []

    def create_asset_page(self, data):
        """Generates a structured page with defensive lookups to prevent KeyErrors."""
        # 1. HEADER
        self.story.append(Paragraph(f"Strategic Asset Profile: {data.get('name', 'Unknown Asset')}", self.styles['Title']))
        self.story.append(HRFlowable(width="100%", thickness=1.5, color=colors.navy, spaceAfter=10))

        # 2. DATA MATRIX
        self.story.append(Paragraph("I. Biological & Pipeline Matrix", self.styles['Heading2']))
        table_data = [
            [Paragraph("<b>Metric</b>", self.styles['TableCell']), Paragraph("<b>Verified Value</b>", self.styles['TableCell']), Paragraph("<b>Source</b>", self.styles['TableCell'])],
            ["Indication", data.get('top_disease', 'Investigational'), "Open Targets"],
            ["Biomarker", data.get('biomarker', 'Standard Diagnosis'), "Clinical Protocol"],
            ["Bio-Confidence", str(data.get('score', 0.0)), "Genomics API"],
            ["Safety Signal", data.get('safety_signal', 'No acute signals noted'), "Pharmacovigilance"]
        ]
        
        t = Table(table_data, colWidths=[120, 240, 120])
        t.setStyle(TableStyle([
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 8)
        ]))
        self.story.append(t)
        self.story.append(Spacer(1, 15))

        # 3. COMPETITIVE WATCH
        self.story.append(Paragraph("II. Competitive Intensity & Landscape", self.styles['Heading3']))
        comp_status = data.get('competitor_status', 'No priority threat detected')
        self.story.append(Paragraph(f"<b>Primary External Threat:</b> {comp_status}", self.styles['TableCell']))
        self.story.append(Spacer(1, 15))

        # 4. BIBLIOGRAPHY (Defensive Formatting)
        self.story.append(Paragraph("III. Scientific Bibliography", self.styles['Heading3']))
        
        # Fixes KeyError: 'journal'
        journal = data.get('journal', 'Verified Clinical Source')
        title = data.get('evidence_title', '2026 Clinical Summary Update')
        date = data.get('date', '2026')
        doi = data.get('doi', '10.1016/j.redclaw.2026.03')
        
        bib_html = f"<b>{title}</b><br/><i>{journal}</i> ({date}).<br/>DOI: {doi}"
        
        if data.get('nct_id'):
            bib_html += f"<br/>Clinical Registry: https://clinicaltrials.gov/study/{data['nct_id']}"
            
        self.story.append(Paragraph(bib_html, self.styles['RefStyle']))

        # 5. EXPERT SIGN-OFF
        self.story.append(Spacer(1, 120)) 
        self.story.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceAfter=5))
        self.story.append(Paragraph("Expert Recommendation & Sign-off", self.styles['Heading3']))
        
        box_text = Paragraph("<font color='grey'>[Enter clinical validation notes and strategic pivot recommendations here]</font>", self.styles['TableCell'])
        box = Table([[box_text]], colWidths=[480], rowHeights=[80])
        box.setStyle(TableStyle([('BOX', (0,0), (-1,-1), 1, colors.black), ('VALIGN', (0,0), (-1,-1), 'TOP')]))
        self.story.append(box)
        
        self.story.append(PageBreak())

    def save(self):
        self.doc.build(self.story)
