---
title: "notebook_edit — mutate Jupyter notebook cells"
description: "Insert, replace, or delete cells in a .ipynb file by 0-based index or by cell_id. The notebook is rewritten in valid nbformat v4 JSON."
---

`notebook_edit` is the structured-edit counterpart for Jupyter notebooks. It parses the `.ipynb` JSON, applies a single cell-level mutation, and writes it back. The notebook stays parseable — no string-level surgery that could corrupt the JSON envelope.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `notebook_path` | string | yes | Filesystem path to the `.ipynb` file. |
| `action` | string | yes | `insert`, `replace`, or `delete`. |
| `cell_index` | integer | one of | 0-based cell index. |
| `cell_id` | string | one of | nbformat v4 cell id. |
| `content` | string | for `insert` / `replace` | Cell source. |
| `cell_type` | string | for `insert` | `code` or `markdown`. Defaults to `code`. |

Exactly one of `cell_index` / `cell_id` is required for `replace` and `delete`. For `insert`, `cell_index` is the position to insert at.

## Example invocation

```json
{
  "notebook_path": "notebooks/analysis.ipynb",
  "action": "insert",
  "cell_index": 2,
  "cell_type": "markdown",
  "content": "## Section 2 — exploratory plots"
}
```

```python
from chimera.tools.notebook_edit import NotebookEditTool

tool = NotebookEditTool()
result = tool.execute(
    {"notebook_path": "demo.ipynb",
     "action": "replace",
     "cell_index": 0,
     "content": "import pandas as pd\nimport numpy as np\n"},
    env=local_env,
)
```

## Output sample

```
Replaced cell 0 in demo.ipynb (12 → 39 chars).
```

## Notes

- Only nbformat v4 notebooks are supported.
- Outputs and execution counts on touched cells are cleared so re-execution starts from a clean slate.

## See also

- [`edit_file`](./edit.md), [`apply_patch`](./apply-patch.md) — plain-text mutations.
