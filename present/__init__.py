"""Present a finished report pack: walk a PowerPoint deck from a script, answer questions from a
question bank, run what-if on a live Excel sheet, and keep minutes.

Everything here is generic. Client scripts, question banks and cell dictionaries live outside this
repository (they are passed in by path at run time) and hold no figures: every number is read from
the client's workbook on their own PC when the talk runs.
"""
