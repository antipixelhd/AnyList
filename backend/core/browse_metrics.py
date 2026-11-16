"""Stable, indexable public ranking expressions; no dynamic JSON-path binds."""
from sqlalchemy import Float
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


class ImdbVotes(FunctionElement):
    type = Float()
    inherit_cache = True
    field = 'votes'


class ImdbRank(FunctionElement):
    type = Float()
    inherit_cache = True
    field = 'popularity_rank'


def metric_sql(document, field):
    if field not in ('votes', 'popularity_rank'):
        raise ValueError('Unsupported ranking field')
    raw = f"btrim({document} #>> '{{imdb,{field}}}')"
    # Unknown/malformed legacy evidence must not break writes when an index
    # evaluates it. Restrict numeric parsing before casting; prevent overflow.
    numeric = f"length({raw}) <= 128 AND {raw} ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]{{1,3}})?$'"
    return (f"CASE WHEN {numeric} THEN CASE WHEN abs(({raw})::numeric) > 1.7976931348623157e308 THEN NULL "
            f"WHEN abs(({raw})::numeric) < 4.9406564584124654e-324 THEN 0::double precision "
            f"ELSE ({raw})::double precision END END")


@compiles(ImdbVotes, 'postgresql')
@compiles(ImdbRank, 'postgresql')
def postgres_metric(element, compiler, **kwargs):
    return '(' + metric_sql(compiler.process(list(element.clauses)[0], **kwargs), element.field) + ')'


@compiles(ImdbVotes)
@compiles(ImdbRank)
def portable_metric(element, compiler, **kwargs):
    data = list(element.clauses)[0]
    return compiler.process(data['imdb'][element.field].as_string().cast(Float), **kwargs)
