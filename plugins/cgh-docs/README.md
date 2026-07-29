# cgh-docs

Document parsers for [cgh](https://github.com/altikva/cgh). Once
installed, `cgh index` parses pdf, docx and xlsx files into searchable
sections, exactly like markdown headings: `search_docs`, `doc_outline`,
`fts_search` and the federated fan-out all work on them with no extra
configuration.

```bash
pip install cgh-docs
cgh index          # .pdf / .docx / .xlsx files now land in the graph
cgh plugins        # shows docs: active (parsers)
```

What each format becomes:

- **pdf**: one section per page (or per outline entry when the file has
  a table of contents), with the extracted text as the searchable body.
- **docx**: heading paragraphs become the section tree, body text
  becomes the preview, mirroring the markdown parser.
- **xlsx**: one section per sheet, with the header row and sheet size
  in the body so column names are searchable.

Parsing is best effort by design: an encrypted pdf or a corrupt
workbook yields an empty index for that file and a log line, never a
failed scan.
