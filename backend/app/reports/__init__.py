"""Report engine (E09): a typed payload rendered to PDF, PPTX and XLSX.

payload.py     the shape (no computation — figures arrive from the KPI catalog)
formatting.py  how a figure is written, once
render_*.py    one renderer per format; none may add, drop or recompute a figure
"""
