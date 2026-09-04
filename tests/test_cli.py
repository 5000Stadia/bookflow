"""CLI-specific behavior: grammar, errors, locks, cold start."""

import json
import multiprocessing as mp
import subprocess
import time

from tests.conftest import BIN


def test_errors_are_json_on_stderr(cli):
    err, code = cli.error("company", "new", "--legal-name", "X", "--home-currency", "usd")
    assert err["code"] == "E_VALIDATION" and code == 1
    err, code = cli.error("company", "frobnicate")
    assert err["code"] == "E_USAGE" and code == 2
    err, code = cli.error("company", "list", "--dry-run")
    assert err["code"] == "E_USAGE" and code == 2
    err, code = cli.error("company", "show")
    assert err["code"] == "E_COMPANY_NOT_FOUND" and err["details"]["source"] == "none"
    err, code = cli.error("company", "use")
    assert err["code"] == "E_VALIDATION" and code == 1
    p = cli.run("company", "show", expect=1)
    assert p.stderr.startswith("error: ") and json.loads(p.stderr.strip().splitlines()[-1])["code"] == "E_COMPANY_NOT_FOUND"


def test_global_option_positions(cli):
    a = cli.run("--json", "company", "list").stdout
    b = cli.run("company", "list", "--json").stdout
    assert json.loads(a)["count"] == json.loads(b)["count"] == 1
    out = cli.json("company", "show", "--company", "Demo Plumbing Co")
    assert out["display_name"] == "Demo Plumbing Co"
    out = cli.json("company", "show", env={"BOOKFLOW_COMPANY": "Demo Plumbing Co"})
    assert out["display_name"] == "Demo Plumbing Co"
    cli.json("company", "use", "Demo Plumbing Co")
    assert cli.json("company", "show")["display_name"] == "Demo Plumbing Co"


def test_help_lists_only_applicable_options(cli):
    h = cli.run("company", "list", "--help").stdout
    assert "--dry-run" not in h and "--company" not in h and "--json" in h
    h = cli.run("company", "show", "--help").stdout
    assert "--company" in h and "--dry-run" not in h
    h = cli.run("company", "new", "--help").stdout
    assert "--dry-run" in h and "--reason" in h and "--interactive" in h and "--address-line1" in h and "--legal-address-city" in h


def test_reason_recorded(cli):
    cli.json("organization", "new", "--name", "Reasoned", "--reason", "testing reasons", "--source-ref", "ticket-1")
    ev = cli.json("hub", "audit", "list", "--command", "organization new")["items"][0]
    assert ev["reason"] == "testing reasons" and ev["source_ref"] == "ticket-1" and ev["interface"] == "cli"


def _hold(root, seconds, started):
    from bookflow.core.locks import RootLock
    with RootLock(root, "holder"):
        started.set()
        time.sleep(seconds)


def test_db_busy_through_cli_and_library(cli, root, client):
    started = mp.Event()
    p = mp.Process(target=_hold, args=(root, 3.0, started))
    p.start()
    try:
        assert started.wait(5)
        err, code = cli.error("company", "list")
        assert err["code"] == "E_DB_BUSY" and code == 1
        assert set(err["details"]) == {"command", "held_seconds"} and err["details"]["command"] == "holder"
        from bookflow import BookflowError
        import pytest
        with pytest.raises(BookflowError) as e:
            client.company.list()
        assert e.value.code == "E_DB_BUSY"
    finally:
        p.terminate(); p.join()


def test_cold_start(cli):
    best = 10.0
    for _ in range(3):
        t = time.perf_counter()
        subprocess.run([str(BIN), "--help"], capture_output=True, check=True)
        best = min(best, time.perf_counter() - t)
    t = time.perf_counter()
    cli.run("company", "list", "--json")
    listed = time.perf_counter() - t
    print(f"cold start --help {best*1000:.0f} ms, company list {listed*1000:.0f} ms")
    budget = float(__import__("os").environ.get("BOOKFLOW_BUDGET_MS", "300")) / 1000
    assert best < budget, f"--help took {best*1000:.0f} ms (budget {budget*1000:.0f} ms; override BOOKFLOW_BUDGET_MS)"
    assert listed < 2.5 * budget, f"company list took {listed*1000:.0f} ms (budget {2.5*budget*1000:.0f} ms)"
