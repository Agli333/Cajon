#!/usr/bin/env python3
"""c: capturas rápidas desde la terminal.

Todo se guarda en captura.db, junto a este script. Solo usa la biblioteca
estándar de Python.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import locale
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

VERSION = "1.0"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "captura.db"
EXPORT_PATH = BASE_DIR / "captura.md"

SIMILARITY_THRESHOLD = 0.86
MAX_RESULTS = 10
MESES = ("ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic")

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
HIGHLIGHT = "\033[1;33m"

_COLOR = False

URL_RE = re.compile(r"""\b(?:https?://|www\.)[^\s<>"']+""", re.IGNORECASE)
TAG_RE = re.compile(r"(?<![\w#])#([\w-]+)", re.UNICODE)
WORD_RE = re.compile(r"\w+", re.UNICODE)


class CLIError(Exception):
    """Error que se muestra al usuario sin traza de Python."""


class CLIParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        translations = (
            ("the following arguments are required:", "faltan argumentos obligatorios:"),
            ("unrecognized arguments:", "argumentos no reconocidos:"),
            ("invalid choice:", "opción no válida:"),
            ("invalid int value:", "valor entero no válido:"),
        )
        for source, target in translations:
            message = message.replace(source, target)
        self.print_usage(sys.stderr)
        raise CLIError(message)


def setup_console() -> None:
    """Fuerza UTF-8 en la salida y activa ANSI en la consola de Windows."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _enable_windows_ansi() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [ctypes.c_int]
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetConsoleMode.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
        ]
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

        enabled = False
        for handle_id in (-11, -12):  # salida estándar y error estándar
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if handle and kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                    enabled = True
        return enabled
    except Exception:
        return False


def colors_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    force = os.environ.get("FORCE_COLOR", "").lower()
    if force and force not in ("0", "false", "no"):
        return True
    if not sys.stdout.isatty():
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return _enable_windows_ansi()


def paint(text: str, *styles: str) -> str:
    if not _COLOR or not styles:
        return text
    return "".join(styles) + text + RESET


def show_error(message: str) -> None:
    print(f"{paint('Error:', RED + BOLD)} {message}", file=sys.stderr)


def format_date(value: str) -> str:
    try:
        parsed = dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value
    return f"{parsed.day} {MESES[parsed.month - 1]} {parsed.year}, {parsed:%H:%M}"


def normalize_tag(value: str) -> str:
    tag = value.strip()
    if tag.startswith("#"):
        tag = tag[1:]
    tag = tag.casefold()
    if not tag:
        raise CLIError("La etiqueta está vacía.")
    if not re.fullmatch(r"[\w-]+", tag, re.UNICODE):
        raise CLIError(
            f"Etiqueta no válida: {value!r}. Usa letras, números, _ o -."
        )
    return tag


def extract_tags(text: str, extra_tags=()) -> list[str]:
    """Extrae #etiquetas, ignorando los # de las URLs."""
    without_urls = URL_RE.sub(" ", text or "")
    raw_tags = TAG_RE.findall(without_urls)
    raw_tags.extend(extra_tags)

    tags: list[str] = []
    seen: set[str] = set()
    for raw in raw_tags:
        try:
            tag = normalize_tag(raw)
        except CLIError:
            continue
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def extract_url(text: str) -> str:
    match = URL_RE.search(text or "")
    if not match:
        return ""
    url = match.group(0).rstrip(".,;:!?*")
    pairs = {")": "(", "]": "[", "}": "{"}
    while url and url[-1] in pairs and url.count(url[-1]) > url.count(pairs[url[-1]]):
        url = url[:-1]
    return url


def format_tags(etiquetas: str) -> str:
    tags = etiquetas.split() if etiquetas else []
    if not tags:
        return paint("(sin etiquetas)", DIM)
    return " ".join(paint(tag, MAGENTA) for tag in tags)


def preview(text: str, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) > limit:
        clean = clean[:limit].rstrip() + "…"
    return clean


def colorize_snippet(fragment: str) -> str:
    fragment = re.sub(r"\s+", " ", fragment or "").strip()
    if _COLOR:
        return fragment.replace("\x01", HIGHLIGHT).replace("\x02", RESET)
    return fragment.replace("\x01", "").replace("\x02", "")


def make_fts_query(words: list[str]) -> str:
    tokens = WORD_RE.findall(" ".join(words))
    tokens = [token for token in tokens if token.strip("_")]
    if not tokens:
        raise CLIError("La búsqueda no contiene palabras válidas.")
    return " AND ".join(f'"{token}"*' for token in tokens[:50])


def connect() -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
    except sqlite3.Error as exc:
        raise CLIError(f"No se pudo abrir la base de datos {DB_PATH}: {exc}") from exc

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        init_db(conn)
    except Exception:
        conn.close()
        raise
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entradas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            texto TEXT NOT NULL,
            etiquetas TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entradas_fecha ON entradas(fecha)"
    )

    fts_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entradas_fts'"
    ).fetchone()

    if not fts_exists:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE entradas_fts USING fts5(
                    texto,
                    etiquetas,
                    url,
                    content='entradas',
                    content_rowid='id',
                    tokenize="unicode61 remove_diacritics 2"
                )
                """
            )
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "fts5" in message or "no such module" in message:
                raise CLIError(
                    "Esta instalación de SQLite no incluye FTS5. "
                    "Usa una versión oficial de Python 3 con FTS5 habilitado."
                ) from exc
            raise

    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS entradas_ai AFTER INSERT ON entradas BEGIN
            INSERT INTO entradas_fts(rowid, texto, etiquetas, url)
            VALUES (new.id, new.texto, new.etiquetas, new.url);
        END;

        CREATE TRIGGER IF NOT EXISTS entradas_ad AFTER DELETE ON entradas BEGIN
            INSERT INTO entradas_fts(entradas_fts, rowid, texto, etiquetas, url)
            VALUES ('delete', old.id, old.texto, old.etiquetas, old.url);
        END;

        CREATE TRIGGER IF NOT EXISTS entradas_au AFTER UPDATE ON entradas BEGIN
            INSERT INTO entradas_fts(entradas_fts, rowid, texto, etiquetas, url)
            VALUES ('delete', old.id, old.texto, old.etiquetas, old.url);
            INSERT INTO entradas_fts(rowid, texto, etiquetas, url)
            VALUES (new.id, new.texto, new.etiquetas, new.url);
        END;
        """
    )
    conn.commit()

    if not fts_exists:
        # Si la base ya tenía entradas antes de crear el índice, lo rellenamos.
        conn.execute("INSERT INTO entradas_fts(entradas_fts) VALUES ('rebuild')")
        conn.commit()


def comparison_text(text: str) -> str:
    clean = URL_RE.sub(" ", text or "")
    clean = TAG_RE.sub(" ", clean)
    clean = re.sub(r"[^\w\s]", " ", clean, flags=re.UNICODE)
    return re.sub(r"\s+", " ", clean.casefold()).strip()


def find_similar(conn: sqlite3.Connection, text: str):
    new_text = comparison_text(text)
    if not new_text:
        new_text = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    if not new_text:
        return None

    best_row = None
    best_ratio = 0.0
    for row in conn.execute(
        "SELECT id, fecha, texto, etiquetas FROM entradas ORDER BY id DESC"
    ):
        old_text = comparison_text(row["texto"])
        if not old_text:
            old_text = re.sub(r"\s+", " ", row["texto"].casefold()).strip()
        if not old_text:
            continue
        if new_text == old_text:
            return row
        if len(new_text) < 12 or len(old_text) < 12:
            continue

        matcher = difflib.SequenceMatcher(None, new_text, old_text)
        if matcher.real_quick_ratio() < SIMILARITY_THRESHOLD:
            continue
        if matcher.quick_ratio() < SIMILARITY_THRESHOLD:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_row = row

    return best_row if best_ratio >= SIMILARITY_THRESHOLD else None


def show_duplicate(row) -> None:
    print(
        f"{paint('Aviso:', YELLOW + BOLD)} ya existe una entrada muy parecida "
        f"(id {row['id']})."
    )
    print(
        f"  {paint(format_date(row['fecha']), DIM)}  "
        f"{format_tags(row['etiquetas'])}"
    )
    print(f"  {preview(row['texto'], 140)}")
    print(paint("Se guardará de todos modos.", DIM))


def read_clipboard() -> str:
    if sys.platform.startswith("win"):
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            raise CLIError("No se encontró PowerShell para leer el portapapeles.")
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Get-Clipboard -Raw"
            ),
        ]
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CLIError(f"No se pudo leer el portapapeles: {exc}") from exc
        if result.returncode != 0:
            detail = decode_bytes(result.stderr).strip()
            raise CLIError(
                "PowerShell no pudo leer el portapapeles."
                + (f" {detail}" if detail else "")
            )
        return normalize_clipboard(decode_bytes(result.stdout))

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["pbpaste"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CLIError(f"No se pudo leer el portapapeles con pbpaste: {exc}") from exc
        if result.returncode != 0:
            detail = decode_bytes(result.stderr).strip()
            raise CLIError(
                "pbpaste no pudo leer el portapapeles."
                + (f" {detail}" if detail else "")
            )
        return normalize_clipboard(decode_bytes(result.stdout))

    candidates = [
        (["xclip", "-selection", "clipboard", "-o"], "xclip"),
        (["xsel", "--clipboard", "--output"], "xsel"),
    ]
    available = [(cmd, name) for cmd, name in candidates if shutil.which(cmd[0])]
    if not available:
        raise CLIError(
            "No se encontró xclip ni xsel. Instala uno de ellos "
            "(por ejemplo: sudo apt install xclip) para usar --clip en Linux."
        )

    errors = []
    for command, name in available:
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{name}: {exc}")
            continue
        if result.returncode == 0:
            return normalize_clipboard(decode_bytes(result.stdout))
        errors.append(f"{name}: {decode_bytes(result.stderr).strip()}")
    raise CLIError("No se pudo leer el portapapeles. " + " | ".join(errors))


def decode_bytes(data: bytes) -> str:
    encodings = ["utf-8-sig", "utf-8"]
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower() not in ("utf-8", "utf8"):
        encodings.append(preferred)
    encodings.append("cp1252")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def normalize_clipboard(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        raise CLIError("El portapapeles está vacío.")
    return text


def require_entry(conn: sqlite3.Connection, entry_id: int):
    row = conn.execute(
        "SELECT * FROM entradas WHERE id = ?", (entry_id,)
    ).fetchone()
    if row is None:
        raise CLIError(f"No existe una entrada con id {entry_id}.")
    return row


def render_summary(row, fragment: str | None = None, show_url: bool = True) -> None:
    head = (
        f"{paint('id ' + str(row['id']), BOLD + CYAN)}"
        f" {paint('·', DIM)} {paint(format_date(row['fecha']), DIM)}"
        f" {paint('·', DIM)} {format_tags(row['etiquetas'])}"
    )
    print(head)

    body = fragment if fragment and fragment.strip() else preview(row["texto"])
    if body:
        print("   " + body)
    if show_url and row["url"]:
        print("   " + paint(row["url"], BLUE + UNDERLINE))


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_add(argv: list[str]) -> int:
    parser = CLIParser(
        prog="c",
        description="Añade una captura. Extrae las etiquetas #palabra y la primera URL.",
        epilog=(
            "Ejemplos:\n"
            '  c "Preparar el taller de ejemplo #proyectos https://ejemplo.com"\n'
            '  c --clip "#ideas"\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "texto",
        nargs="*",
        metavar="TEXTO",
        help="texto de la entrada; con --clip, solo etiquetas #palabra",
    )
    parser.add_argument(
        "--clip",
        action="store_true",
        help="usa el contenido del portapapeles como texto",
    )
    args = parser.parse_args(argv)

    if args.clip:
        for token in args.texto:
            if not token.lstrip().startswith("#"):
                raise CLIError(
                    "Con --clip solo puedes indicar etiquetas, por ejemplo: "
                    'c --clip "#ideas"'
                )
        text = read_clipboard()
        extra_tags = extract_tags(" ".join(args.texto))
        tags = extract_tags(text, extra_tags=extra_tags)
    else:
        if not args.texto:
            raise CLIError('Falta el texto. Ejemplo: c "Una idea #proyectos"')
        text = " ".join(args.texto).strip()
        if not text:
            raise CLIError("El texto está vacío.")
        tags = extract_tags(text)

    url = extract_url(text)
    fecha = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    etiquetas = " ".join(f"#{tag}" for tag in tags)

    conn = connect()
    try:
        similar = find_similar(conn, text)
        if similar is not None:
            show_duplicate(similar)

        cursor = conn.execute(
            "INSERT INTO entradas (fecha, texto, etiquetas, url) VALUES (?, ?, ?, ?)",
            (fecha, text, etiquetas, url),
        )
        conn.commit()
        entry_id = cursor.lastrowid
    finally:
        conn.close()

    print(
        f"{paint('Guardado:', GREEN + BOLD)} id {entry_id}"
        f" {paint('·', DIM)} {format_date(fecha)}"
        f" {paint('·', DIM)} {format_tags(etiquetas)}"
    )
    return 0


def cmd_search(args) -> int:
    query = make_fts_query(args.palabras)
    tag = normalize_tag(args.tag) if args.tag else None
    desde = None
    if args.desde:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.desde):
            raise CLIError("La fecha de --desde debe tener el formato AAAA-MM-DD.")
        try:
            dt.datetime.strptime(args.desde, "%Y-%m-%d")
        except ValueError as exc:
            raise CLIError("La fecha de --desde no es válida.") from exc
        desde = args.desde

    sql = """
        SELECT e.*,
               snippet(entradas_fts, 0, char(1), char(2), '…', 18) AS fragmento,
               bm25(entradas_fts, 10.0, 2.0, 0.5) AS puntuacion
        FROM entradas_fts
        JOIN entradas e ON e.id = entradas_fts.rowid
        WHERE entradas_fts MATCH ?
    """
    params: list[object] = [query]
    if tag:
        sql += " AND instr(' ' || e.etiquetas || ' ', ?) > 0"
        params.append(f" #{tag} ")
    if desde:
        sql += " AND e.fecha >= ?"
        params.append(desde)
    sql += " ORDER BY puntuacion ASC, e.fecha DESC, e.id DESC"

    conn = connect()
    try:
        if args.todo:
            rows = conn.execute(sql, params).fetchall()
            truncated = False
        else:
            rows = conn.execute(sql + " LIMIT ?", params + [MAX_RESULTS + 1]).fetchall()
            truncated = len(rows) > MAX_RESULTS
            rows = rows[:MAX_RESULTS]
    finally:
        conn.close()

    if not rows:
        print("Sin resultados.")
        return 0

    for index, row in enumerate(rows):
        if index:
            print()
        fragment = colorize_snippet(row["fragmento"])
        render_summary(row, fragment=fragment)

    if truncated:
        print()
        print(
            paint(
                "Mostrando los 10 resultados más relevantes. "
                "Usa --todo para ver todos.",
                DIM,
            )
        )
    return 0


def cmd_view(args) -> int:
    conn = connect()
    try:
        row = require_entry(conn, args.id)
    finally:
        conn.close()

    print(f"{paint('id', BOLD + CYAN)} {row['id']}")
    print(f"{paint('Fecha:', BOLD)} {format_date(row['fecha'])}")
    print(f"{paint('Etiquetas:', BOLD)} {format_tags(row['etiquetas'])}")
    print(f"{paint('URL:', BOLD)} {paint(row['url'], BLUE + UNDERLINE) if row['url'] else paint('—', DIM)}")
    print(paint("─" * 50, DIM))
    print(row["texto"])
    return 0


def cmd_latest(args) -> int:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM entradas ORDER BY fecha DESC, id DESC LIMIT ?",
            (args.n,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("Aún no hay entradas.")
        return 0

    heading = "Última entrada" if len(rows) == 1 else f"Últimas {len(rows)} entradas"
    print(paint(heading, BOLD))
    print()
    for index, row in enumerate(rows):
        if index:
            print()
        render_summary(row)
    return 0


def cmd_tag(args) -> int:
    tag = normalize_tag(args.etiqueta)
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM entradas
            WHERE instr(' ' || etiquetas || ' ', ?) > 0
            ORDER BY fecha DESC, id DESC
            """,
            (f" #{tag} ",),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"No hay entradas con #{tag}.")
        return 0

    print(paint(f"Entradas con #{tag} ({len(rows)})", BOLD))
    print()
    for index, row in enumerate(rows):
        if index:
            print()
        render_summary(row)
    return 0


def cmd_tags(args) -> int:
    conn = connect()
    try:
        rows = conn.execute("SELECT etiquetas FROM entradas").fetchall()
    finally:
        conn.close()

    counts: dict[str, int] = {}
    for row in rows:
        for tag in row["etiquetas"].split():
            counts[tag] = counts.get(tag, 0) + 1

    if not counts:
        print("Todavía no hay etiquetas.")
        return 0

    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    width = max(len(tag) for tag, _ in ordered)
    print(paint(f"Etiquetas ({len(ordered)})", BOLD))
    print()
    for tag, count in ordered:
        print(f"{paint(tag.ljust(width), MAGENTA)}  {paint(str(count), DIM)}")
    return 0


def cmd_edit(args) -> int:
    text = " ".join(args.texto).strip()
    if not text:
        raise CLIError("El texto nuevo está vacío.")

    tags = extract_tags(text)
    etiquetas = " ".join(f"#{tag}" for tag in tags)
    url = extract_url(text)

    conn = connect()
    try:
        require_entry(conn, args.id)
        conn.execute(
            "UPDATE entradas SET texto = ?, etiquetas = ?, url = ? WHERE id = ?",
            (text, etiquetas, url, args.id),
        )
        conn.commit()
    finally:
        conn.close()

    print(
        f"{paint('Actualizado:', GREEN + BOLD)} id {args.id}"
        f" {paint('·', DIM)} {format_tags(etiquetas)}"
    )
    return 0


def cmd_delete(args) -> int:
    conn = connect()
    try:
        row = require_entry(conn, args.id)
        conn.execute("DELETE FROM entradas WHERE id = ?", (args.id,))
        conn.commit()
    finally:
        conn.close()

    print(
        f"{paint('Borrado:', GREEN + BOLD)} id {args.id} "
        f"{paint('·', DIM)} {preview(row['texto'], 100)}"
    )
    return 0


def cmd_export(args) -> int:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM entradas ORDER BY fecha ASC, id ASC"
        ).fetchall()
    finally:
        conn.close()

    lines = ["# Capturas", ""]
    if not rows:
        lines.append("_Todavía no hay entradas._")
    else:
        lines.append(
            f"_Exportado el {format_date(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}._"
        )
        lines.append("")

        for index, row in enumerate(rows):
            if index:
                lines.extend(["", "---", ""])
            lines.append(f"## id {row['id']} · {format_date(row['fecha'])}")
            lines.append("")
            lines.append(f"**Etiquetas:** {row['etiquetas'] or '—'}")
            if row["url"]:
                lines.append(f"**URL:** <{row['url']}>")
            lines.append("")
            lines.append(row["texto"])

    content = "\n".join(lines).rstrip() + "\n"
    try:
        with open(EXPORT_PATH, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except OSError as exc:
        raise CLIError(f"No se pudo escribir {EXPORT_PATH}: {exc}") from exc

    print(
        f"{paint('Exportado:', GREEN + BOLD)} {len(rows)} "
        f"{'entrada' if len(rows) == 1 else 'entradas'} → {EXPORT_PATH}"
    )
    return 0


# ---------------------------------------------------------------------------
# Argumentos y entrada
# ---------------------------------------------------------------------------

def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("debe ser un número entero") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("debe ser mayor que cero")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = CLIParser(
        prog="c",
        usage=(
            '%(prog)s "texto"\n'
            '       %(prog)s --clip ["#etiqueta"]\n'
            "       %(prog)s COMANDO [opciones]"
        ),
        description="Capturas rápidas con búsqueda de texto completo.",
        epilog=(
            "Añadir:\n"
            '  c "texto #etiqueta"           Añade una entrada\n'
            '  c --clip "#etiqueta"          Añade el portapapeles\n'
            "\n"
            "Ejemplos:\n"
            '  c "Preparar el taller de ejemplo #proyectos https://ejemplo.com"\n'
            "  c buscar puert --tag proyectos\n"
            "  c buscar cancion --desde 2026-01-01 --todo\n"
            "  c ultimas 5\n"
            "  c tag proyectos\n"
            "  c tags\n"
            "  c ver 1\n"
            '  c editar 1 "Texto nuevo #otra"\n'
            "  c borrar 1\n"
            "  c exportar"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="comando", title="Comandos", metavar="COMANDO", parser_class=CLIParser
    )

    search = sub.add_parser(
        "buscar",
        prog="c buscar",
        help="busca por relevancia (FTS5, prefijos)",
        description="Busca entradas por relevancia. Las palabras se buscan como prefijos.",
    )
    search.add_argument("palabras", nargs="+", metavar="PALABRAS")
    search.add_argument("--tag", metavar="ETIQUETA", help="filtra por etiqueta")
    search.add_argument(
        "--desde", metavar="AAAA-MM-DD", help="solo entradas desde esa fecha"
    )
    search.add_argument(
        "--todo", action="store_true", help="muestra todos los resultados, sin límite"
    )
    search.set_defaults(func=cmd_search)

    view = sub.add_parser("ver", prog="c ver", help="muestra una entrada completa")
    view.add_argument("id", type=positive_int, metavar="ID")
    view.set_defaults(func=cmd_view)

    latest = sub.add_parser("ultimas", prog="c ultimas", help="muestra las últimas entradas")
    latest.add_argument(
        "n", nargs="?", type=positive_int, default=10, metavar="N",
        help="cantidad (por defecto 10)",
    )
    latest.set_defaults(func=cmd_latest)

    tag = sub.add_parser("tag", prog="c tag", help="lista las entradas con una etiqueta")
    tag.add_argument("etiqueta", metavar="ETIQUETA")
    tag.set_defaults(func=cmd_tag)

    tags = sub.add_parser("tags", prog="c tags", help="lista todas las etiquetas y su recuento")
    tags.set_defaults(func=cmd_tags)

    edit = sub.add_parser(
        "editar", prog="c editar", help="reemplaza el texto de una entrada",
        description="Reemplaza el texto y vuelve a extraer etiquetas y URL.",
    )
    edit.add_argument("id", type=positive_int, metavar="ID")
    edit.add_argument("texto", nargs="+", metavar="TEXTO")
    edit.set_defaults(func=cmd_edit)

    delete = sub.add_parser("borrar", prog="c borrar", help="elimina una entrada")
    delete.add_argument("id", type=positive_int, metavar="ID")
    delete.set_defaults(func=cmd_delete)

    export = sub.add_parser("exportar", prog="c exportar", help="genera captura.md con todo")
    export.set_defaults(func=cmd_export)

    return parser


COMMANDS = {
    "buscar", "ver", "ultimas", "tag", "tags", "editar", "borrar", "exportar"
}


def main(argv: list[str] | None = None) -> int:
    global _COLOR

    setup_console()
    _COLOR = colors_enabled()

    arguments = list(sys.argv[1:] if argv is None else argv)

    try:
        if not arguments or arguments[0] in ("-h", "--help"):
            build_parser().print_help()
            return 0
        if arguments[0] == "--version":
            print(f"c {VERSION}")
            return 0
        if arguments[0] in COMMANDS:
            parser = build_parser()
            args = parser.parse_args(arguments)
            return args.func(args)
        return cmd_add(arguments)
    except CLIError as exc:
        show_error(str(exc))
        return 1
    except sqlite3.Error as exc:
        show_error(f"Error de base de datos: {exc}")
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        show_error("Operación cancelada.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
