#!/usr/bin/env python3
"""Generate per-network map HTMLs from the FORA templates.

For each network + each template (radius map, manual editor):
  * swap the inline `window.STORE_DATA = ...;` and `window.RESID_DATA = ...;`
    lines with the network's generated store_data.js / resid_data.js;
  * replace the visible "FORA" label with the network label.

Writes custom-polygons/<network>-radius-map.html and <network>-manual-editor.html.
"""
from pathlib import Path

CP = Path(__file__).resolve().parents[2]  # custom-polygons/

TEMPLATES = {
    "radius-map": "fora-radius-map.html",
    "manual-editor": "fora-manual-editor.html",
}
NETWORKS = {
    "fora": "FORA",
    "anri-pharm": "ANRI-PHARM",
    "taistra": "TAISTRA",
}


def build(slug, label):
    src_dir = CP / "sources" / slug
    store_js = (src_dir / "store_data.js").read_text(encoding="utf-8").strip()
    resid_js = (src_dir / "resid_data.js").read_text(encoding="utf-8").strip()
    for key, tpl in TEMPLATES.items():
        lines = (CP / tpl).read_text(encoding="utf-8").split("\n")
        out = []
        for line in lines:
            if line.startswith("window.STORE_DATA = "):
                out.append(store_js)
            elif line.startswith("window.RESID_DATA = "):
                out.append(resid_js)
            else:
                out.append(line.replace("FORA", label))
        (CP / f"{slug}-{key}.html").write_text("\n".join(out), encoding="utf-8")
        print(f"wrote {slug}-{key}.html")


def main():
    for slug, label in NETWORKS.items():
        build(slug, label)


if __name__ == "__main__":
    main()
