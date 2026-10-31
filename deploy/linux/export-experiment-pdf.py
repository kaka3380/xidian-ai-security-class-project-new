"""Render only hash-verified experiment evidence as a review draft."""
import argparse
import json
from pathlib import Path
from html import escape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from workbench.backend.services.experiment_result import verify_record

parser = argparse.ArgumentParser()
parser.add_argument('record')
parser.add_argument('output')
args = parser.parse_args()
package, result, document = verify_record(args.record)
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
styles = getSampleStyleSheet()
styles['BodyText'].wordWrap = 'CJK'
styles['BodyText'].fontSize = 9.5
styles['BodyText'].leading = 12
story = []
def paragraph(value, style='BodyText'):
    story.append(Paragraph(escape(str(value)), styles[style]))
    story.append(Spacer(1, 6))
paragraph('Synthetic Experiment Record', 'Title')
paragraph('DRAFT - PENDING HUMAN REVIEW', 'Heading2')
paragraph(f"Execution: {result['execution_status']} | Validation: {result['validation_status']}")
paragraph('Approved scope', 'Heading2')
paragraph(package['question'])
paragraph(package['hypothesis'])
paragraph('Recorded metrics', 'Heading2')
rows = [[Paragraph(escape(k), styles['BodyText']), Paragraph(escape(str(result['metrics'].get(k, 'Not available'))), styles['BodyText'])] for k in package['metrics']]
table = Table(rows, colWidths=[160, 330])
table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),0.3,colors.lightgrey),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
story.append(table)
paragraph('Provenance', 'Heading2')
for key in ('task_id','package_hash','dataset_hash','source_snapshot_hash','image'):
    paragraph(f'{key}: {package[key]}')
paragraph(f"Evidence: {document['title']}; version {document['version']}; {document['content_level']}. Plan version {package['plan_version']}.")
paragraph('Limitations and review', 'Heading2')
audit_path = output.parent / 'methodology-review.json'
if audit_path.exists():
    audit = json.loads(audit_path.read_text())
    if audit['package_hash'] != package['package_hash']:
        raise ValueError('audit/package mismatch')
    for finding in audit['findings']:
        paragraph(finding)
paragraph(result.get('failure_reason') or 'No execution error recorded.')
paragraph('One synthetic split and one seed. Missing metrics remain unavailable. Code compliance and evidence require human review. A generated PDF does not approve the findings.')
SimpleDocTemplate(str(output), title='Synthetic experiment review', author='Local research workbench', leftMargin=48, rightMargin=48, topMargin=40, bottomMargin=40).build(story)
print(output)
