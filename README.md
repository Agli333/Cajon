# Cajón

**CLI de Python, sin dependencias, para capturar notas y encontrarlas al instante con SQLite FTS5.**

## Qué problema resuelve

Las ideas, enlaces y recordatorios acaban dispersos entre notas, mensajes y marcadores. Cajón los guarda en una base SQLite local y permite recuperarlos desde la terminal con búsqueda por relevancia, prefijos y etiquetas. Funciona sin conexión y no depende de servicios externos.

## Características

- Alta de notas con etiquetas `#palabra` y detección de la primera URL.
- Búsqueda de texto completo con ranking `bm25`: `puert` encuentra `puerto`.
- Búsqueda sin distinguir tildes: `cancion` encuentra `canción`.
- Captura desde el portapapeles en Windows, macOS y Linux.
- Aviso de entradas muy parecidas antes de guardar.
- Edición, borrado, listado por etiqueta y exportación a Markdown.
- Colores ANSI, UTF-8 y datos almacenados localmente.

## Requisitos

- Python 3.9 o superior con FTS5 (las instalaciones oficiales lo incluyen).
- Windows, macOS o Linux.
- En Linux, `--clip` necesita `xclip` o `xsel`.

## Instalación

Clona el repositorio y comprueba que funciona:

```bash
git clone https://github.com/Agli333/Cajon.git
cd Cajon
python c.py --help
```

### Windows

Añade la carpeta del repositorio al `PATH` del usuario y abre una terminal nueva. El archivo `c.bat` ya está incluido y llama a `c.py` desde su propia carpeta, así que puedes ejecutar `c` desde cualquier directorio.

```powershell
$repo = (Get-Location).Path
$actual = [Environment]::GetEnvironmentVariable("Path", "User")
if (($actual -split ";") -notcontains $repo) {
    [Environment]::SetEnvironmentVariable("Path", "$actual;$repo", "User")
}
```

Cierra y vuelve a abrir la terminal:

```powershell
c --help
```

### macOS y Linux

Puedes crear un alias en `~/.zshrc` o `~/.bashrc`:

```bash
alias c='python3 "$HOME/Cajon/c.py"'
```

Después recarga la configuración y comprueba el comando:

```bash
source ~/.zshrc   # o source ~/.bashrc
c --help
```

También puedes hacer el script ejecutable y enlazarlo en una carpeta del `PATH`:

```bash
chmod +x c.py
ln -s "$PWD/c.py" "$HOME/.local/bin/c"
```

En Linux, instala el lector de portapapeles si vas a usar `--clip`:

```bash
sudo apt install xclip    # Debian/Ubuntu
sudo dnf install xclip    # Fedora
```

## Comandos

| Comando | Qué hace |
|---|---|
| `c "texto #etiqueta"` | Añade una entrada y extrae etiquetas y la primera URL. |
| `c --clip "#etiqueta"` | Añade el contenido del portapapeles. |
| `c buscar PALABRAS [--tag X] [--desde AAAA-MM-DD] [--todo]` | Busca por relevancia y muestra hasta 10 resultados. |
| `c ver ID` | Muestra una entrada completa. |
| `c ultimas [N]` | Muestra las últimas `N` entradas (10 por defecto). |
| `c tag ETIQUETA` | Lista las entradas con una etiqueta. |
| `c tags` | Lista todas las etiquetas y su recuento. |
| `c editar ID "texto nuevo"` | Reemplaza el texto y vuelve a extraer etiquetas y URL. |
| `c borrar ID` | Elimina una entrada. |
| `c exportar` | Genera `captura.md` con todas las entradas. |

## Ejemplos de uso

### Guardar una nota

```bash
$ c "Preparar el taller de ejemplo #proyectos https://ejemplo.com"
Guardado: id 1 · 27 oct 2026, 09:30 · #proyectos
```

### Buscar por prefijo y etiqueta

```bash
$ c buscar tall --tag proyectos
id 1 · 27 oct 2026, 09:30 · #proyectos
   Preparar el taller de ejemplo #proyectos https://ejemplo.com
   https://ejemplo.com
```

### Consultar las últimas entradas

```bash
$ c ultimas 2
Últimas 2 entradas

id 2 · 27 oct 2026, 09:31 · #personal
   Comprar café #personal

id 1 · 27 oct 2026, 09:30 · #proyectos
   Preparar el taller de ejemplo #proyectos https://ejemplo.com
   https://ejemplo.com
```

### Ver una entrada completa

```bash
$ c ver 1
id 1
Fecha: 27 oct 2026, 09:30
Etiquetas: #proyectos
URL: https://ejemplo.com
──────────────────────────────────────────────────
Preparar el taller de ejemplo #proyectos https://ejemplo.com
```

### Exportar a Markdown

```bash
$ c exportar
Exportado: 2 entradas → /ruta/a/Cajon/captura.md
```

En PowerShell, las etiquetas sueltas deben ir entre comillas porque `#` inicia un comentario:

```powershell
c --clip "#ideas"
c buscar puerto --tag "#proyectos"
```

## Guía de uso diario

- **Guardar:** `c "Comprar café #personal"`
- **Buscar:** `c buscar cafe --tag personal`
- **Ver:** `c ver 2`
- **Editar:** `c editar 2 "Comprar café de especialidad #personal"`
- **Borrar:** `c borrar 2`
- **Exportar:** `c exportar`

### Buenos hábitos

- Usa pocas etiquetas: dos o tres estables suelen ser suficientes.
- Empieza la nota con una palabra clave: `Comprar...`, `Leer...`, `Idea...`, `Decisión...`.
- Guarda lo que te funcione: comandos, enlaces, decisiones y frases que quieras recuperar.
- Revisa de vez en cuando con `c ultimas` y exporta con `c exportar`.

## Tus datos

`captura.db` y `captura.md` se crean junto a `c.py` y están excluidos mediante `.gitignore`. Tus notas permanecen en tu equipo y no se envían a GitHub ni a ningún servicio externo.

## Licencia

Distribuido bajo la licencia MIT. Consulta [LICENSE](LICENSE).

© 2026 Agli333
