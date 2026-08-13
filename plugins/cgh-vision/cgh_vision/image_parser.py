# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A minimal parser for image files. It extracts no symbols and
#              no text; its only job is to make an image a KNOWN indexed
#              file. The indexer skips any file no parser claims, so without
#              this an image is never indexed and the deferred vision
#              scanner (which only runs on indexed files) never sees it.
#              One section carries the filename so the image is findable;
#              the vision scanner does the actual content extraction.

from __future__ import annotations

from pathlib import Path

from codegraph.plugin_api import BaseParser, FileIndex, SectionDef

IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"]


class ImageParser(BaseParser):
    """Register an image as an indexed file so scanners can reach it. No
    text is extracted here: images carry no scannable text, and the vision
    scanner (deferred) reads the pixels itself."""

    lang = "image"
    extensions = IMAGE_EXTENSIONS
    extracts = ["sections"]
    description = "Image files, indexed so the vision scanner can reach them"

    def parse(self, path: Path) -> FileIndex:
        idx = FileIndex(path=str(path), lang=self.lang)
        idx.sections.append(
            SectionDef(
                id=f"{path}::image",
                title=path.name,
                level=1,
                file_path=str(path),
                start_line=1,
                end_line=1,
                body_preview=f"image: {path.name}",
                anchor="image",
            )
        )
        return idx
