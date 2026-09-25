"""Excel reader abstraction over openpyxl (.xlsx) and xlrd (.xls).

Both implementations return uniform `Sheet` and `Workbook` views so the parser
never branches on file format. This matters because placement slips arrive as
either format in the wild.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

Cell = str | int | float | bool | None


class CurrencyValue(float):
    """A number the workbook FORMATS as money ("$"#,##0.00, SGD …).

    Still a float — every parser treats it exactly like one — but it records
    what the raw value alone cannot: that the slip's author meant dollars.
    Placement slips put a visit count and a dollar cap in the same column as
    the same bare number (CDL GCGP WhiteCoat: co-pay 5 formatted "$", per
    policy year 5 formatted General = 5 visits), so the format is the only
    evidence that tells them apart.
    """


def _is_currency_format(fmt: object) -> bool:
    text = str(fmt or "").lower()
    return "$" in text or "sgd" in text or "¤" in text


def _numeric(value: Cell, fmt: object) -> Cell:
    if isinstance(value, float) and not isinstance(value, bool) and _is_currency_format(fmt):
        return CurrencyValue(value)
    if isinstance(value, int) and not isinstance(value, bool) and _is_currency_format(fmt):
        return CurrencyValue(value)
    return value

# Hard bound on columns read per sheet. Real slips use < 20 columns; some
# workbooks in the wild report thousands of phantom columns (stray formatting
# pushes openpyxl's max_column to ~16k), which would balloon every row with
# None cells. Generous enough for any legitimate slip, small enough to keep
# phantom-column sheets cheap.
MAX_SCAN_COLS = 256


@dataclass(frozen=True)
class CellNote:
    text: str
    author: str | None = None


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: list[list[Cell]]
    # 0-indexed (row, column) -> Excel note/comment metadata.
    comments: dict[tuple[int, int], CellNote] = field(default_factory=dict)
    # 0-indexed, end-exclusive (first_row, last_row, first_col, last_col).
    merged_ranges: tuple[tuple[int, int, int, int], ...] = ()


class Workbook(Protocol):
    @property
    def sheet_names(self) -> list[str]: ...

    def sheet(self, name: str) -> Sheet: ...

    def close(self) -> None: ...

    def __enter__(self) -> Workbook: ...

    def __exit__(self, *exc: object) -> None: ...


def open_workbook(
    path: Path | str, *, include_merged_ranges: bool = False
) -> Workbook:
    """Open a workbook. Always use as a context manager so file handles release
    on Windows before the caller tries to delete the source file.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xls":
        return _XlrdWorkbook(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _OpenpyxlWorkbook(path, include_merged_ranges=include_merged_ranges)
    raise ValueError(f"Unsupported file extension: {suffix}")


def _coerce(value: object) -> Cell:
    if value is None:
        return None
    # Date/datetime cells (openpyxl yields datetime objects) → ISO date string,
    # so they don't stringify with a misleading "00:00:00" time tail downstream.
    # datetime is a subclass of date, so check it first.
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _openpyxl_value(cell: object) -> Cell:
    return _numeric(
        _coerce(getattr(cell, "value", None)), getattr(cell, "number_format", "")
    )


def _openpyxl_comments(rows: list[list[object]]) -> dict[tuple[int, int], CellNote]:
    comments: dict[tuple[int, int], CellNote] = {}
    for row_idx, row in enumerate(rows):
        for col_idx, cell in enumerate(row):
            comment = getattr(cell, "comment", None)
            if comment is None:
                continue
            text = str(getattr(comment, "text", "") or "").strip()
            if not text:
                continue
            author = str(getattr(comment, "author", "") or "").strip() or None
            comments[(row_idx, col_idx)] = CellNote(text=text, author=author)
    return comments


class _XlrdWorkbook:
    def __init__(self, path: Path) -> None:
        import xlrd

        # on_demand=False (the default) reads the whole file into memory at
        # open time and releases the OS file handle immediately. Critical on
        # Windows where a held handle blocks the caller from deleting the
        # source temp file.
        self._wb = xlrd.open_workbook(str(path), formatting_info=True, on_demand=False)

    @property
    def sheet_names(self) -> list[str]:
        return list(self._wb.sheet_names())

    def _format_of(self, ws: object, r: int, c: int) -> str:
        try:
            xf = self._wb.xf_list[ws.cell_xf_index(r, c)]  # type: ignore[attr-defined]
            return str(self._wb.format_map[xf.format_key].format_str)
        except (AttributeError, IndexError, KeyError):
            return ""

    def sheet(self, name: str) -> Sheet:
        ws = self._wb.sheet_by_name(name)
        rows: list[list[Cell]] = []
        ncols = min(ws.ncols, MAX_SCAN_COLS)
        for r in range(ws.nrows):
            row: list[Cell] = []
            for c in range(ncols):
                cell = ws.cell(r, c)
                val = cell.value
                if val == "" or val is None:
                    row.append(None)
                else:
                    row.append(_numeric(_coerce(val), self._format_of(ws, r, c)))
            rows.append(row)
        comments: dict[tuple[int, int], CellNote] = {}
        for (rowx, colx), note in ws.cell_note_map.items():
            if colx >= MAX_SCAN_COLS:
                continue
            text = str(getattr(note, "text", "") or "").strip()
            if text:
                author = str(getattr(note, "author", "") or "").strip() or None
                comments[(int(rowx), int(colx))] = CellNote(text=text, author=author)
        return Sheet(
            name=name,
            rows=rows,
            comments=comments,
            merged_ranges=tuple(tuple(region) for region in ws.merged_cells),
        )

    def close(self) -> None:
        # xlrd with on_demand=False already released the handle; nothing to do.
        pass

    def __enter__(self) -> _XlrdWorkbook:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class _OpenpyxlWorkbook:
    def __init__(self, path: Path, *, include_merged_ranges: bool = False) -> None:
        from openpyxl import load_workbook

        self._include_merged_ranges = include_merged_ranges
        # read_only=True keeps the underlying zip handle open. Caller must
        # close() / use as a context manager so Windows releases the handle
        # before the source file is deleted.
        # Placement slips need merge metadata to distinguish a plan-level
        # headcount from a category count. Other large uploads keep streaming.
        self._wb = load_workbook(
            filename=str(path),
            read_only=not include_merged_ranges,
            data_only=True,
        )

    @property
    def sheet_names(self) -> list[str]:
        return list(self._wb.sheetnames)

    def sheet(self, name: str) -> Sheet:
        ws = self._wb[name]
        merged_ranges = (
            tuple(
                (region.min_row - 1, region.max_row, region.min_col - 1, region.max_col)
                for region in ws.merged_cells.ranges
            )
            if self._include_merged_ranges
            else ()
        )
        # A sheet's size is DECLARED in its XML (`<dimension ref="A1:AK4807"/>`)
        # and `read_only=True` trusts that declaration rather than counting.
        # Several non-Excel writers — Go Excelize, which the incumbent
        # platform's exports come from — stamp a placeholder `ref="A1"` and
        # never update it, so a 4,807-row roster reads back as ONE cell.
        # Nothing raises: the header row is a single "Entity", no column maps
        # to Staff ID, every row is skipped, and the upload reports zero
        # records with no error to explain it.
        #
        # `reset_dimensions()` drops the declared bounds so openpyxl computes
        # them while streaming. Only taken when the declaration is degenerate,
        # so the normal path keeps its phantom-column cap (some workbooks in
        # the wild report ~16k columns) — and a genuinely 1x1 sheet costs one
        # cheap extra pass.
        if (
            ((ws.max_row or 0) <= 1 or (ws.max_column or 0) <= 1)
            and hasattr(ws, "reset_dimensions")
        ):
            ws.reset_dimensions()
            # With no declared width, openpyxl sizes each row to its own last
            # populated cell — a blank row comes back empty and a row with
            # trailing blanks comes back short. Every reader here indexes by
            # POSITION, so the grid is padded back to rectangular; this is the
            # only path in the module that could return ragged rows, and a
            # positional read of one is wrong rather than loud.
            raw_cells = [list(r[:MAX_SCAN_COLS]) for r in ws.iter_rows()]
            coerced = [
                [
                    _openpyxl_value(cell)
                    for cell in row
                ]
                for row in raw_cells
            ]
            width = max((len(r) for r in coerced), default=0)
            comments = _openpyxl_comments(raw_cells)
            return Sheet(
                name=name,
                rows=[r + [None] * (width - len(r)) for r in coerced],
                comments=comments,
                merged_ranges=merged_ranges,
            )
        rows: list[list[Cell]] = []
        max_col = min(ws.max_column or MAX_SCAN_COLS, MAX_SCAN_COLS)
        raw_cells = [list(row) for row in ws.iter_rows(max_col=max_col)]
        for row in raw_cells:
            rows.append(
                [
                    _openpyxl_value(cell)
                    for cell in row
                ]
            )
        return Sheet(
            name=name,
            rows=rows,
            comments=_openpyxl_comments(raw_cells),
            merged_ranges=merged_ranges,
        )

    def close(self) -> None:
        self._wb.close()

    def __enter__(self) -> _OpenpyxlWorkbook:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
