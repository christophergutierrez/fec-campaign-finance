from __future__ import annotations

import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

_PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def load_sql(relative_path: str) -> str:
    return (SQL_DIR / relative_path).read_text()


def render_sql(relative_path: str, **tokens: str) -> str:
    sql = load_sql(relative_path)
    for name, value in tokens.items():
        sql = sql.replace(f"/*{{{name}}}*/", value)
    return sql


def bind_named(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    values: list[Any] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"SQL parameter ${name} was not provided")
        values.append(params[name])
        return "?"

    return _PARAM_RE.sub(replace, sql), values


def run_sql(con, relative_path: str, **params: Any):
    sql, values = bind_named(load_sql(relative_path), params)
    return con.execute(sql, values)


def run_rendered_sql(con, relative_path: str, tokens: dict[str, str], **params: Any):
    sql, values = bind_named(render_sql(relative_path, **tokens), params)
    return con.execute(sql, values)


def install_temp_views(con) -> None:
    for view in [
        "views/candidate_committees.sql",
        "views/clean_individual_contributions.sql",
        "views/clean_independent_expenditures.sql",
        "views/candidate_money.sql",
    ]:
        con.execute(load_sql(view))
