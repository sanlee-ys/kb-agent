# pandas

pandas is an open-source Python library that provides fast, flexible data structures—primarily the `DataFrame` and `Series`—for working with labeled and tabular data.

## What it's for
- Loading, cleaning, and transforming tabular data from sources like CSV, Excel, SQL databases, JSON, and Parquet.
- Filtering, grouping, aggregating, and reshaping data (e.g., `groupby`, `pivot_table`, `merge`, `join`).
- Handling time series data with date ranges, resampling, and rolling-window calculations.
- Exploratory data analysis and preparing data for visualization or machine learning workflows.

## Gotchas
- **Chained indexing** (e.g., `df[df.a > 0]['b'] = 1`) can trigger the `SettingWithCopyWarning` and may silently fail to modify the original DataFrame; use `.loc`/`.iloc` for explicit, reliable assignment.
- Many operations return a **new object by default** rather than modifying in place, and missing data handling (`NaN`, `None`, `pd.NA`) can behave inconsistently across dtypes—watch for unexpected type coercion (e.g., integer columns becoming floats when `NaN` is introduced).
