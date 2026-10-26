"""R180: the timings report and the store's settings, through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, reads the company once (so that surface has a
timed call of its own), then reads `perf report` and `perf settings`. Each report names the
call under the surface it came through.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('perf report', 'perf settings'))


@pytest.mark.timeout(900)
def test_the_same_timings_report_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                await matrix.call(surface, "company show", {})
                report = await matrix.call(surface, "perf report", {"since": "24h", "command": "company show"})
                settings = await matrix.call(surface, "perf settings", {})
                results[surface] = (report, settings)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    for surface, (report, settings) in results.items():
        assert report["scope"] == "installation" and report["recording"] is True, surface
        rows = {r["surface"]: r for r in report["items"]}
        assert all(r["command"] == "company show" for r in report["items"]), surface
        assert rows[surface]["count"] >= 1 and rows[surface]["p50_ms"] > 0, (surface, rows)
        assert settings["recording"] is True and settings["raw_rows"] >= 1, surface
        assert settings["raw_cap"] == 100000 and settings["path"] == "telemetry/timings.db", surface
