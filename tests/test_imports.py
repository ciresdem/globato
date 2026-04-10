#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Enforces correct fetchez/transformez/globato imports
"""

import ast
from pathlib import Path

# This file is in globato/tests/test_imports.py
GLOBATO_DIR = Path(__file__).resolve().parent.parent / "src" / "globato"


def get_python_files(directory):
    """Recursively yield all Python files in a directory."""

    return list(directory.rglob("*.py"))


def test_globato_imports():
    """Enforces rules on where certain utilities and classes are imported from
    to prevent circular dependencies and ensure CRS-aware geometries.
    """

    errors = []
    py_files = get_python_files(GLOBATO_DIR)

    for py_file in py_files:
        with open(py_file, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read(), filename=str(py_file))
            except SyntaxError:
                continue

        # Walk through the Abstract Syntax Tree looking for import statements
        for node in ast.walk(tree):
            # Check `from module import X` statements
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""

                for alias in node.names:
                    imported_name = alias.name

                    # parse_hook_string MUST come from fetchez.utils
                    if (
                        imported_name == "parse_hook_string"
                        and module != "fetchez.utils"
                    ):
                        errors.append(
                            f"[{py_file.name}] Violation: 'parse_hook_string' imported from '{module}'. "
                            f"Must be imported from 'fetchez.utils'."
                        )

                    # parse_source_string MUST come from globato.utils
                    if (
                        imported_name == "parse_source_string"
                        and module != "globato.utils"
                    ):
                        # Allow fetchez/utils.py to be imported in globato/utils.py itself
                        if not (
                            py_file.name == "utils.py" and module == "fetchez.utils"
                        ):
                            errors.append(
                                f"[{py_file.name}] Violation: 'parse_source_string' imported from '{module}'. "
                                f"Must be imported from 'globato.utils' (to ensure stream_data injection)."
                            )

                    # Never import the SRS-unaware Region from Fetchez
                    if imported_name == "Region" and "fetchez" in module:
                        errors.append(
                            f"[{py_file.name}] Violation: Imported 'Region' from '{module}'. "
                            f"Globato must exclusively use 'transformez.spatial.TransRegion'."
                        )

            # Check `import module.X` statements (e.g., `import fetchez.spatial`)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "fetchez.spatial" in alias.name:
                        pass

    # If any errors were accumulated, fail the test and print them all
    error_msg = "\nImport Violations Found:\n" + "\n".join(errors)
    assert not errors, error_msg
