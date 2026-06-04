# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Kuzu backend implementing the GraphDB protocol.
#
# This is a thin passthrough adapter: Kuzu's native Connection already
# matches the protocol structurally, so the adapter exists primarily as
# a named seam for the DuckDB backend to slot in alongside.

from __future__ import annotations

from typing import Any

import kuzu

from codegraph.core.protocol import GraphDB, QueryResult


class KuzuQueryResult:
    """Adapter wrapping kuzu.QueryResult to match the QueryResult protocol."""

    def __init__(self, inner: kuzu.QueryResult) -> None:
        self._inner = inner

    def has_next(self) -> bool:
        return self._inner.has_next()

    def get_next(self) -> list[Any]:
        return self._inner.get_next()

    def get_column_names(self) -> list[str]:
        return self._inner.get_column_names()


class KuzuGraphDB:
    """Adapter wrapping kuzu.Connection to match the GraphDB protocol."""

    def __init__(self, inner: kuzu.Connection) -> None:
        self._inner = inner

    def execute(self, query: str, params: dict | None = None) -> QueryResult:
        # Kuzu accepts an optional dict of parameters.
        result = self._inner.execute(query, params) if params is not None else self._inner.execute(query)
        return KuzuQueryResult(result)

    def close(self) -> None:
        self._inner.close()

    # --- Write helpers --------------------------------------------------

    def upsert_node(
        self,
        label: str,
        key_field: str,
        key_value: Any,
        props: dict[str, Any],
    ) -> None:
        """MERGE the node + SET its properties.

        Property names are statically known via the schema, so embedding
        them in the Cypher string is safe (no user-controlled identifiers).
        """
        from codegraph.core.graph_model import NODES

        # Validate label against the known set so we can't be tricked into
        # injecting arbitrary identifiers through it.
        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")

        params: dict[str, Any] = {"_key": key_value}
        merge_clause = f"MERGE (n:{label} {{{key_field}: $_key}})"
        if props:
            set_parts = []
            for i, (k, v) in enumerate(props.items()):
                bind = f"_p{i}"
                set_parts.append(f"n.{k} = ${bind}")
                params[bind] = v
            set_clause = "SET " + ", ".join(set_parts)
            query = f"{merge_clause} {set_clause}"
        else:
            query = merge_clause
        self._inner.execute(query, params)

    def ensure_edge(
        self,
        edge_type: str,
        src_key_value: Any,
        dst_key_value: Any,
        edge_props: dict[str, Any] | None = None,
    ) -> None:
        """MATCH the endpoints by key, then MERGE the edge with any props.

        Edge type is validated against the known set so the relationship
        identifier in the Cypher string is safe.
        """
        from codegraph.core.graph_model import EDGES, NODES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        edge = EDGES[edge_type]
        src = NODES[edge.src_label]
        dst = NODES[edge.dst_label]

        params: dict[str, Any] = {"_src": src_key_value, "_dst": dst_key_value}
        match = (
            f"MATCH (a:{src.label} {{{src.key_field}: $_src}}), "
            f"(b:{dst.label} {{{dst.key_field}: $_dst}})"
        )
        if edge_props:
            prop_kvs = []
            for i, (k, v) in enumerate(edge_props.items()):
                bind = f"_e{i}"
                prop_kvs.append(f"{k}: ${bind}")
                params[bind] = v
            merge_clause = f"MERGE (a)-[:{edge_type} {{{', '.join(prop_kvs)}}}]->(b)"
        else:
            merge_clause = f"MERGE (a)-[:{edge_type}]->(b)"
        self._inner.execute(f"{match} {merge_clause}", params)

    def purge_file_data(self, file_path: str) -> None:
        """DETACH DELETE every node (and its edges) tied to file_path."""
        from codegraph.core.graph_model import NODES

        params = {"_p": file_path}
        # File itself uses 'path' as key; the rest use 'file_path'.
        # File node is detached last so its IMPORTS edges go with it.
        for spec in NODES.values():
            if not spec.has_file_path:
                continue
            self._inner.execute(
                f"MATCH (n:{spec.label}) WHERE n.file_path = $_p DETACH DELETE n",
                params,
            )
        # File-level outbound IMPORTS edges
        self._inner.execute(
            "MATCH (f:File {path: $_p})-[r:IMPORTS]->() DELETE r",
            params,
        )

    def find_node_keys(
        self,
        label: str,
        where_field: str,
        where_value: Any,
    ) -> list[Any]:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        spec = NODES[label]
        result = self._inner.execute(
            f"MATCH (n:{label}) WHERE n.{where_field} = $_v RETURN n.{spec.key_field} AS k",
            {"_v": where_value},
        )
        out: list[Any] = []
        while result.has_next():
            row = result.get_next()
            out.append(row[0])
        return out

    def query_node_field(
        self,
        label: str,
        key_field: str,
        key_value: Any,
        return_field: str,
    ) -> Any | None:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        result = self._inner.execute(
            f"MATCH (n:{label}) WHERE n.{key_field} = $_v RETURN n.{return_field} AS r",
            {"_v": key_value},
        )
        if not result.has_next():
            return None
        return result.get_next()[0]

    def list_node_fields(
        self,
        label: str,
        return_fields: list[str],
    ) -> list[list[Any]]:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        select = ", ".join(f"n.{f}" for f in return_fields)
        result = self._inner.execute(f"MATCH (n:{label}) RETURN {select}")
        out: list[list[Any]] = []
        while result.has_next():
            out.append(list(result.get_next()))
        return out

    def delete_file_completely(self, file_path: str) -> None:
        # First drop everything keyed off this file via the regular purge,
        # then drop the File node + any inbound IMPORTS edges (purge leaves
        # the File node alive for the indexer's re-upsert flow).
        self.purge_file_data(file_path)
        self._inner.execute(
            "MATCH (f:File {path: $_p}) DETACH DELETE f",
            {"_p": file_path},
        )

    def find_nodes(
        self,
        label: str,
        where: dict[str, Any] | None = None,
        contains: dict[str, Any] | None = None,
        return_fields: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")

        # Build WHERE clause: AND of exact matches, OR of substring matches.
        # The two groups are AND'd together when both present.
        params: dict[str, Any] = {}
        clauses: list[str] = []

        if where:
            for i, (field, value) in enumerate(where.items()):
                bind = f"_w{i}"
                clauses.append(f"n.{field} = ${bind}")
                params[bind] = value

        if contains:
            sub_clauses: list[str] = []
            for i, (field, value) in enumerate(contains.items()):
                bind = f"_c{i}"
                sub_clauses.append(f"n.{field} CONTAINS ${bind}")
                params[bind] = value
            if sub_clauses:
                clauses.append("(" + " OR ".join(sub_clauses) + ")")

        # RETURN list: either the requested fields or every known column.
        if return_fields is None:
            return_fields = ["*"]
        if return_fields == ["*"]:
            return_clause = "n.*"
        else:
            return_clause = ", ".join(f"n.{f} AS {f}" for f in return_fields)

        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        order_clause = (
            "ORDER BY " + ", ".join(f"n.{f}" for f in order_by) if order_by else ""
        )
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        cypher = (
            f"MATCH (n:{label}) {where_clause} "
            f"RETURN {return_clause} {order_clause} {limit_clause}"
        )

        result = self._inner.execute(cypher, params) if params else self._inner.execute(cypher)
        cols = result.get_column_names()
        out: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            # Strip "n." prefix from column names so callers get clean keys
            out.append({c.removeprefix("n."): v for c, v in zip(cols, row)})
        return out

    def count_nodes(self, label: str, where: dict[str, Any] | None = None) -> int:
        from codegraph.core.graph_model import NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        params: dict[str, Any] = {}
        clauses: list[str] = []
        if where:
            for i, (field, value) in enumerate(where.items()):
                bind = f"_w{i}"
                clauses.append(f"n.{field} = ${bind}")
                params[bind] = value
        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        cypher = f"MATCH (n:{label}) {where_clause} RETURN count(n) AS c"
        result = self._inner.execute(cypher, params) if params else self._inner.execute(cypher)
        if result.has_next():
            return int(result.get_next()[0])
        return 0

    def count_edges(self, edge_type: str) -> int:
        from codegraph.core.graph_model import EDGES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        result = self._inner.execute(
            f"MATCH ()-[r:{edge_type}]->() RETURN count(r) AS c"
        )
        if result.has_next():
            return int(result.get_next()[0])
        return 0

    def find_nodes_without_incoming(
        self,
        label: str,
        edge_type: str,
        contains: dict[str, Any] | None = None,
        exclude_name_prefix: str | None = None,
        return_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        from codegraph.core.graph_model import EDGES, NODES

        if label not in NODES:
            raise ValueError(f"Unknown node label: {label!r}")
        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        spec = NODES[label]

        params: dict[str, Any] = {}
        clauses: list[str] = [f"NOT (n)<-[:{edge_type}]-()"]
        if exclude_name_prefix:
            clauses.append("NOT n.name STARTS WITH $_pref")
            params["_pref"] = exclude_name_prefix
        if contains:
            for i, (field, value) in enumerate(contains.items()):
                bind = f"_c{i}"
                clauses.append(f"n.{field} CONTAINS ${bind}")
                params[bind] = value

        if not return_fields:
            return_fields = [spec.key_field, "name", "file_path", "start_line", "end_line"]
        return_clause = ", ".join(f"n.{f} AS {f}" for f in return_fields)
        cypher = (
            f"MATCH (n:{label}) WHERE {' AND '.join(clauses)} RETURN {return_clause}"
        )
        result = self._inner.execute(cypher, params) if params else self._inner.execute(cypher)
        cols = result.get_column_names()
        out: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            out.append(dict(zip(cols, row)))
        return out

    def find_neighbors(
        self,
        edge_type: str,
        src_key: Any | None = None,
        dst_key: Any | None = None,
        src_where: dict[str, Any] | None = None,
        dst_where: dict[str, Any] | None = None,
        return_src: list[str] | None = None,
        return_dst: list[str] | None = None,
        return_edge: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        from codegraph.core.graph_model import EDGES, NODES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        edge = EDGES[edge_type]
        src = NODES[edge.src_label]
        dst = NODES[edge.dst_label]

        params: dict[str, Any] = {}
        match_parts = []

        if src_key is not None:
            match_parts.append(f"(a:{src.label} {{{src.key_field}: $_sk}})")
            params["_sk"] = src_key
        else:
            match_parts.append(f"(a:{src.label})")
        match_parts.append(f"-[r:{edge_type}]->")
        if dst_key is not None:
            match_parts.append(f"(b:{dst.label} {{{dst.key_field}: $_dk}})")
            params["_dk"] = dst_key
        else:
            match_parts.append(f"(b:{dst.label})")

        clauses: list[str] = []
        if src_where:
            for i, (field, value) in enumerate(src_where.items()):
                bind = f"_sw{i}"
                clauses.append(f"a.{field} = ${bind}")
                params[bind] = value
        if dst_where:
            for i, (field, value) in enumerate(dst_where.items()):
                bind = f"_dw{i}"
                clauses.append(f"b.{field} = ${bind}")
                params[bind] = value

        # Build the RETURN list with prefixed aliases.
        return_parts: list[str] = []
        for f in return_src or []:
            return_parts.append(f"a.{f} AS src_{f}")
        for f in return_dst or []:
            return_parts.append(f"b.{f} AS dst_{f}")
        for f in return_edge or []:
            return_parts.append(f"r.{f} AS edge_{f}")
        if not return_parts:
            # Default: dst key field so the caller has *something* to look at
            return_parts = [f"b.{dst.key_field} AS dst_{dst.key_field}"]

        match_clause = "".join(match_parts)
        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
        return_clause = ", ".join(return_parts)
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        cypher = (
            f"MATCH {match_clause} {where_clause} "
            f"RETURN {return_clause} {limit_clause}"
        )

        result = self._inner.execute(cypher, params) if params else self._inner.execute(cypher)
        cols = result.get_column_names()
        out: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            out.append(dict(zip(cols, row)))
        return out

    def reach_via_edge(
        self,
        edge_type: str,
        start_key: Any,
        max_depth: int = 1,
        return_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        from codegraph.core.graph_model import EDGES, NODES

        if edge_type not in EDGES:
            raise ValueError(f"Unknown edge type: {edge_type!r}")
        edge = EDGES[edge_type]
        src = NODES[edge.src_label]
        dst = NODES[edge.dst_label]
        return_fields = return_fields or [dst.key_field]
        return_clause = ", ".join(f"dst.{f} AS {f}" for f in return_fields)
        cypher = (
            f"MATCH (src:{src.label} {{{src.key_field}: $_k}})"
            f"-[:{edge_type}*1..{int(max_depth)}]->(dst:{dst.label}) "
            f"RETURN DISTINCT {return_clause}"
        )
        result = self._inner.execute(cypher, {"_k": start_key})
        cols = result.get_column_names()
        out: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            out.append(dict(zip(cols, row)))
        return out

    # Escape hatch for code that still needs raw Kuzu objects (Kuzu-specific
    # helpers, federation that re-opens DBs read-only). Will be removed
    # alongside Kuzu in the 0.5 release that finishes the backend swap.
    @property
    def raw(self) -> kuzu.Connection:
        return self._inner


__all__ = ["KuzuGraphDB", "KuzuQueryResult", "GraphDB", "QueryResult"]
