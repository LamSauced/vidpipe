#!/usr/bin/env python3
"""Start vidpipe from anywhere: `python3 /path/to/vidpipe/serve.py`

Finds the `app` package relative to this file, falling back to a shallow search
so a misplaced copy of this launcher reports where things are instead of dying
on an import error.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def looks_like_root(path: Path) -> bool:
    return (path / "app" / "main.py").is_file() and (path / "static" / "index.html").is_file()


def find_root() -> Path:
    if looks_like_root(HERE):
        return HERE
    # a subdirectory, e.g. this file sat next to the project folder
    for child in sorted(HERE.iterdir()):
        if child.is_dir() and child.name not in (".venv", "venv", "__pycache__") \
                and looks_like_root(child):
            return child
    # a parent, e.g. this file was copied down into app/ or static/
    for parent in HERE.parents:
        if looks_like_root(parent):
            return parent

    found = [p.parent.parent for p in HERE.glob("*/app/main.py")]
    sys.exit(
        "Couldn't find the vidpipe files.\n\n"
        f"  serve.py is in : {HERE}\n"
        f"  looked for     : app/main.py and static/index.html\n"
        + (f"  partial match  : {found}\n" if found else "")
        + "\nMove serve.py into the folder that holds app/ and static/, or run:\n"
          "  find ~ -name main.py -path '*/app/*' -not -path '*/.venv/*'\n"
          "and start it from the directory that contains that app/ folder."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the vidpipe web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--reload", action="store_true", help="restart on code changes")
    args = parser.parse_args()

    root = find_root()
    if not (root / "app" / "__init__.py").is_file():
        (root / "app" / "__init__.py").touch()
        print(f"created missing {root / 'app' / '__init__.py'}")
    sys.path.insert(0, str(root))

    try:
        import uvicorn
    except ImportError:
        sys.exit("uvicorn isn't installed in this interpreter:\n"
                 f"  {sys.executable} -m pip install -r {root / 'requirements.txt'}")

    print(f"vidpipe ({root}) → http://{args.host}:{args.port}")
    uvicorn.run("app.main:app", host=args.host, port=args.port,
                reload=args.reload, app_dir=str(root))


if __name__ == "__main__":
    main()
