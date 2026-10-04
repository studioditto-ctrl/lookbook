"""Samsung Health export CSVs -> SQLite.

usage: python scripts/shealth_to_db.py <csv_dir> [out.db]
Line 1 of each CSV = "<table>,<version>,<n>", line 2 = header, rows end with a trailing comma.
"""
import csv, re, sqlite3, sys
from pathlib import Path

INT_RE = re.compile(r"^-?(0|[1-9]\d{0,14})$")
REAL_RE = re.compile(r"^-?\d+\.\d+(e[+-]?\d+)?$", re.I)
PREFIX_RE = re.compile(r"^com\.samsung\.(shealth|health)\.")
TIME_COLS = ("start_time", "day_time", "create_time", "update_time", "end_time")


def table_name(src: str) -> str:
    return PREFIX_RE.sub("", src).replace(".", "_")


def col_name(c: str) -> str:
    return c.split(".")[-1]  # oxygen_saturation: "com.samsung.health.oxygen_saturation.spo2" -> spo2


def infer(vals):
    vals = [v for v in vals if v != ""]
    if not vals:
        return "TEXT"
    if all(INT_RE.match(v) for v in vals):
        return "INTEGER"
    if all(INT_RE.match(v) or REAL_RE.match(v) for v in vals):
        return "REAL"
    return "TEXT"


def load(path: Path, con: sqlite3.Connection):
    with path.open(newline="", encoding="utf-8-sig") as f:
        r = csv.reader(f)
        meta = next(r)
        header = [col_name(c) for c in next(r)]
        n = len(header)
        rows = []
        for row in r:
            if not row:
                continue
            if len(row) > n:
                row = row[:n]  # trailing comma
            row += [""] * (n - len(row))
            rows.append(row)
    t = table_name(meta[0])
    # dedupe duplicate column names after prefix stripping
    seen = {}
    for i, c in enumerate(header):
        seen[c] = seen.get(c, 0) + 1
        if seen[c] > 1:
            header[i] = f"{c}_{seen[c]}"
    types = [infer([r[i] for r in rows]) for i in range(n)]
    uuid_i = header.index("datauuid") if "datauuid" in header else None
    pk = uuid_i is not None and len({r[uuid_i] for r in rows}) == len(rows) and "" not in {r[uuid_i] for r in rows}
    cols = ", ".join(
        f'"{c}" {t_}' + (" PRIMARY KEY" if pk and i == uuid_i else "") for i, (c, t_) in enumerate(zip(header, types))
    )
    con.execute(f'DROP TABLE IF EXISTS "{t}"')
    con.execute(f'CREATE TABLE "{t}" ({cols})')

    def cast(v, ty):
        if v == "":
            return None
        return int(v) if ty == "INTEGER" else float(v) if ty == "REAL" else v

    con.executemany(
        f'INSERT INTO "{t}" VALUES ({",".join("?" * n)})',
        ([cast(v, ty) for v, ty in zip(r, types)] for r in rows),
    )
    for c in TIME_COLS:
        if c in header:
            con.execute(f'CREATE INDEX "ix_{t}_{c}" ON "{t}"("{c}")')
    con.execute(f"INSERT INTO _import_log VALUES (?,?,?,?,?)", (t, path.name, meta[1], len(rows), n))
    return t, len(rows), n


def main():
    src = Path(sys.argv[1])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "data/shealth.db")
    out.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out)
    con.execute("CREATE TABLE IF NOT EXISTS _import_log(tbl, file, schema_ver, row_count, col_count)")
    con.execute("DELETE FROM _import_log")
    for p in sorted(src.glob("*.csv")):
        t, nr, nc = load(p, con)
        print(f"{t:40s} rows={nr:6d} cols={nc}")
    views = Path(__file__).with_name("shealth_views.sql")
    if views.exists():
        con.executescript(views.read_text(encoding="utf-8"))
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
