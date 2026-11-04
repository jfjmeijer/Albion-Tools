"""Maakt helper/items_index.txt (AlbionId -> item-ID) uit ao-bin-dumps formatted/items.txt."""
import re
import sys

src, dst = sys.argv[1], sys.argv[2]
pat = re.compile(r"^\s*(\d+):\s+(\S+)")
n = 0
with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8", newline="\n") as out:
    for line in f:
        m = pat.match(line)
        if m:
            out.write(m.group(1) + " " + m.group(2) + "\n")
            n += 1
print("items:", n)
