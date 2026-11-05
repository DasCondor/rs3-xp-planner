"""Write the dashboard's inline <script> blocks to page.js so CI can run `node --check` on them."""
import pathlib
import re

page = pathlib.Path(__file__).resolve().parent.parent / "rsxp" / "static" / "index.html"
scripts = re.findall(r"<script>(.*?)</script>", page.read_text(encoding="utf-8"), re.S)
pathlib.Path("page.js").write_text("\n;\n".join(scripts), encoding="utf-8")
print(f"wrote page.js ({len(scripts)} inline scripts)")
