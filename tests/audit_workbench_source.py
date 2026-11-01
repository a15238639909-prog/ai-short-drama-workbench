"""Inventory and parse shipped source without importing it or invoking models."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


BASE = Path(__file__).resolve().parents[1]
ROOTS = ('api', 'cores', 'models', 'production', 'tools', 'web', '_legacy_8848', 'presets')


def main():
    paths = {path for path in BASE.iterdir() if path.suffix in ('.py', '.js') and path.is_file()}
    for directory in ROOTS:
        paths.update(path for path in (BASE / directory).rglob('*')
                     if path.is_file() and path.suffix in ('.py', '.js')
                     and '__pycache__' not in path.parts)
    rows = []
    node = shutil.which('node')
    for path in sorted(paths):
        raw = path.read_bytes()
        row = dict(path=path.relative_to(BASE).as_posix(), bytes=len(raw),
                   sha256=hashlib.sha256(raw).hexdigest(), status='passed')
        try:
            source = raw.decode('utf-8-sig')
            if path.suffix == '.py':
                tree = ast.parse(source, filename=str(path))
                row.update(functions=sum(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in ast.walk(tree)),
                           raises=sum(isinstance(item, ast.Raise) for item in ast.walk(tree)),
                           exception_handlers=sum(isinstance(item, ast.ExceptHandler) for item in ast.walk(tree)))
            elif node:
                result = subprocess.run([node, '--check', str(path)], capture_output=True, text=True,
                                        encoding='utf-8', errors='replace', timeout=20)
                if result.returncode:
                    raise ValueError(result.stderr[-1600:])
            else:
                row.update(status='unverified', error='Node.js unavailable')
        except (UnicodeError, SyntaxError, ValueError, subprocess.TimeoutExpired) as error:
            row.update(status='failed', error=str(error))
        rows.append(row)
    report = dict(scope='Shipped program source only; generated outputs, user data, caches, backups, model weights and tests excluded',
                  files=len(rows), python=sum(row['path'].endswith('.py') for row in rows),
                  javascript=sum(row['path'].endswith('.js') for row in rows),
                  passed=sum(row['status'] == 'passed' for row in rows),
                  failed=[row for row in rows if row['status'] == 'failed'],
                  unverified=[row for row in rows if row['status'] == 'unverified'],
                  note='Parsing confirms source syntax only; behavior is validated by separate mocked regression tests.',
                  rows=rows)
    folder = BASE / 'outputs/release_checks/text_only_story_matrix_20261026'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'all_source_inventory.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'rows'}, ensure_ascii=False))
    return 1 if report['failed'] or report['unverified'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
