import datetime
import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.services import git_delivery


def local_timestamp(day, hour=12):
    value = datetime.datetime.combine(
        datetime.date.fromisoformat(day), datetime.time(hour, 0),
    )
    return int(value.timestamp())


class GitDeliveryLedgerTests(unittest.TestCase):
    def test_ledger_replaces_incompatible_unreleased_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TABLE delivery_observations "
                    "(repo_key TEXT, oid TEXT, pushed_at INTEGER)"
                )
                connection.execute(
                    "INSERT INTO delivery_observations VALUES ('old', 'raw-oid', 1)"
                )

            ledger = meter.GitDeliveryLedger(str(path), "test-salt")
            inserted = ledger.record(
                "repo-key", "object-key", local_timestamp("2026-09-03"), 4, 2,
            )

            self.assertTrue(inserted)
            self.assertEqual(len(ledger.rows()), 1)
            self.assertEqual(ledger.rows()[0]["object_key"], "object-key")

    def test_ledger_persists_only_hashed_keys_and_numeric_delivery_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.sqlite3"
            ledger = meter.GitDeliveryLedger(str(path), "test-salt")

            inserted = ledger.record(
                "repo-key", "object-key", local_timestamp("2026-09-03"), 12, 4,
            )

            self.assertTrue(inserted)
            self.assertEqual(ledger.rows(), [{
                "repo_key": "repo-key",
                "object_key": "object-key",
                "observed_at": local_timestamp("2026-09-03"),
                "day": "2026-09-03",
                "added": 12,
                "deleted": 4,
            }])
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(delivery_observations)"
                    )
                }
            self.assertEqual(columns, {
                "repo_key", "object_key", "observed_at", "day", "added", "deleted",
            })
            self.assertTrue({"path", "remote", "branch", "email", "message"}.isdisjoint(columns))

    def test_ledger_rejects_invalid_line_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = meter.GitDeliveryLedger(
                str(Path(tmp) / "delivery.sqlite3"), "test-salt",
            )

            for invalid in (-1, 1.2, float("inf"), True):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    ledger.record("repo-key", "object-key", 1, invalid, 0)

    def test_clear_removes_evidence_and_records_a_history_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = meter.GitDeliveryLedger(
                str(Path(tmp) / "delivery.sqlite3"), "test-salt",
            )
            ledger.record("repo-key", "object-key", 100, 4, 1)

            ledger.clear(200)

            self.assertEqual(ledger.rows(), [])
            self.assertEqual(ledger.baseline_at(), 200)


class GitDeliveryScannerTests(unittest.TestCase):
    def test_subprocess_runner_supplies_a_system_path_for_launch_agents(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "token_meter.services.git_delivery.subprocess.run",
            return_value=completed,
        ) as run:
            meter.GitDeliveryService._subprocess_runner(
                ["git", "--version"], timeout=1,
            )

        self.assertEqual(run.call_args.kwargs["env"]["PATH"], os.defpath)

    def test_scan_limits_generator_candidates_without_losing_limit_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            service._scan_candidate = mock.Mock(
                side_effect=lambda _candidate, _checked_at, remaining: (
                    0, 0, "ready", True, False, remaining,
                )
            )
            candidates = (
                {"root": f"/repo-{index}", "project": f"repo-{index}"}
                for index in range(git_delivery.MAX_REPOSITORIES + 1)
            )

            result = service.scan(candidates)

        self.assertEqual(
            service._scan_candidate.call_count, git_delivery.MAX_REPOSITORIES,
        )
        self.assertEqual(
            result["coverage"]["repositories"], git_delivery.MAX_REPOSITORIES,
        )
        self.assertIn("repository_limit", result["coverage"]["codes"])

    def test_actual_git_push_is_observed_locally_without_network_or_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "repo"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Alice"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "config", "user.email", "alice@example.com",
            ], check=True)
            (repo / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "remote", "add", "origin", str(remote),
            ], check=True)
            subprocess.run([
                "git", "-C", str(repo), "push", "-qu", "origin", "main",
            ], check=True)
            service = meter.GitDeliveryService(
                str(root / "delivery.sqlite3"), now=lambda: local_timestamp("2026-09-04"),
                salt="test-salt",
            )

            result = service.scan([{"root": str(repo), "project": "repo · a1b2c3"}])
            repeated = service.scan([{"root": str(repo), "project": "repo · a1b2c3"}])

            self.assertEqual(result["new_added"], 3)
            self.assertEqual(result["new_deleted"], 0)
            self.assertEqual(result["new_changed_lines"], 3)
            self.assertEqual(repeated["new_changed_lines"], 0)
            self.assertEqual(result["coverage"]["measured"], 1)
            self.assertNotIn("gh", json.dumps(result))
            self.assertNotIn(str(repo), json.dumps(result))

    def test_scan_uses_only_local_read_only_git_commands_and_matching_identity(self):
        first_oid = "a" * 40
        second_oid = "b" * 40
        calls = []

        def runner(argv, **_kwargs):
            calls.append(tuple(argv))
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 0, "stdout": "Alice@Example.com\n"}
            if args[0] == "for-each-ref":
                return {"returncode": 0, "stdout": "refs/remotes/origin/main\n"}
            if args[0:2] == ("reflog", "show"):
                return {"returncode": 0, "stdout": (
                    f"{second_oid}\x00update by push\x00origin/main@{{1788445800}}\n"
                    f"{first_oid}\x00update by push\x00origin/main@{{1788359400}}\n"
                )}
            if args[0] == "rev-list":
                tip = args[-1] if not args[-1].startswith("^") else args[-2]
                return {"returncode": 0, "stdout": f"{tip}\n"}
            if args[0:3] == ("show", "-s", "--format=%ae%x00%P"):
                email = "alice@example.com" if args[-1] == second_oid else "bob@example.com"
                return {"returncode": 0, "stdout": f"{email}\x00{'1' * 40}\n"}
            if args[0:3] == ("show", "--numstat", "--format="):
                return {"returncode": 0, "stdout": "10\t4\tsrc/app.py\n-\t-\tasset.png\n"}
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            result = service.scan([{"root": "/repo", "project": "repo · a1b2c3"}])

        self.assertEqual(result["new_changed_lines"], 14)
        flattened = {word for call in calls for word in call}
        self.assertTrue({"fetch", "pull", "push", "checkout", "switch", "reset", "prune"}.isdisjoint(flattened))
        self.assertNotIn("ls-remote", flattened)
        self.assertNotIn("Alice@Example.com", json.dumps(result))
        self.assertNotIn(second_oid, json.dumps(result))

    def test_missing_repository_or_identity_is_partial_not_measured_zero(self):
        def runner(argv, **_kwargs):
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 1, "stdout": ""}
            if args == ("config", "--global", "--get", "user.email"):
                return {"returncode": 1, "stdout": ""}
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            result = service.scan([{"root": "/repo", "project": "repo · a1b2c3"}])

        self.assertEqual(result["coverage"]["measured"], 0)
        self.assertEqual(result["coverage"]["partial"], 1)
        self.assertIn("identity_unavailable", result["coverage"]["codes"])

    def test_empty_push_reflog_is_partial_not_measured_zero(self):
        oid = "a" * 40

        def runner(argv, **_kwargs):
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 0, "stdout": "alice@example.com\n"}
            if args[0] == "for-each-ref":
                return {"returncode": 0, "stdout": "refs/remotes/origin/main\n"}
            if args[0:2] == ("reflog", "show"):
                return {
                    "returncode": 0,
                    "stdout": f"{oid}\x00fetch\x00origin/main@{{100}}\n",
                }
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: 200, salt="test-salt",
            )
            result = service.scan([{"root": "/repo", "project": "repo · a1b2c3"}])
            payload = service.query(
                "repo · a1b2c3", "7", [], ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertEqual(result["coverage"]["measured"], 0)
        self.assertEqual(result["coverage"]["partial"], 1)
        self.assertIn("no_push_history", result["coverage"]["codes"])
        self.assertFalse(payload["overall"]["availability"]["code_pushed"])

    def test_clear_baselines_old_reflogs_and_only_later_pushes_reappear(self):
        old_oid = "a" * 40
        new_oid = "b" * 40
        clock = [150]
        reflog = [
            f"{old_oid}\x00update by push\x00origin/main@{{100}}",
        ]

        def runner(argv, **_kwargs):
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 0, "stdout": "alice@example.com\n"}
            if args[0] == "for-each-ref":
                return {"returncode": 0, "stdout": "refs/remotes/origin/main\n"}
            if args[0:2] == ("reflog", "show"):
                return {"returncode": 0, "stdout": "\n".join(reflog) + "\n"}
            if args[0] == "rev-list":
                return {"returncode": 0, "stdout": args[-2 if args[-1].startswith("^") else -1] + "\n"}
            if args[0:3] == ("show", "-s", "--format=%ae%x00%P"):
                return {"returncode": 0, "stdout": "alice@example.com\x00" + "1" * 40 + "\n"}
            if args[0:3] == ("show", "--numstat", "--format="):
                return {"returncode": 0, "stdout": "4\t1\tapp.py\n"}
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: clock[0], salt="test-salt",
            )
            candidates = [{"root": "/repo", "project": "repo · a1b2c3"}]
            self.assertEqual(service.scan(candidates)["new_changed_lines"], 5)

            clock[0] = 200
            service.clear()
            self.assertEqual(service.scan(candidates)["new_changed_lines"], 0)
            self.assertEqual(service.ledger.rows(), [])

            reflog.insert(0, f"{new_oid}\x00update by push\x00origin/main@{{300}}")
            clock[0] = 350
            self.assertEqual(service.scan(candidates)["new_changed_lines"], 5)
            self.assertEqual(len(service.ledger.rows()), 1)


class GitDeliveryAggregationTests(unittest.TestCase):
    def test_query_exposes_efficiency_drivers_rolling_intensity_and_spend_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            ready_key = service._hash("/ready")
            service.ledger.map_project(service._hash("/ready"), ready_key)
            service.ledger.set_repository_coverage(
                ready_key, True, local_timestamp("2026-09-04"),
            )
            service.ledger.record(
                ready_key, service._hash("current-large"),
                local_timestamp("2026-08-30"), 80, 20,
            )
            service.ledger.record(
                ready_key, service._hash("current-small"),
                local_timestamp("2026-09-03"), 1, 0,
            )
            service.ledger.record(
                ready_key, service._hash("previous"),
                local_timestamp("2026-08-27"), 40, 10,
            )
            candidates = [
                {"root": "/ready", "project": "ready · a1b2c3"},
                {"root": "/unchecked", "project": "unchecked · d4e5f6"},
            ]
            spend_rows = [
                {
                    "project": "ready · a1b2c3", "day": "2026-08-30",
                    "covered_cost": 10.0, "cost_available": True,
                    "efficiency_covered_cost": 10.0,
                    "covered_output_tokens": 1000,
                    "output_available": True,
                    "reasoning_tokens": 200,
                    "reasoning_output_tokens": 1000,
                    "reasoning_available": True,
                },
                {
                    "project": "ready · a1b2c3", "day": "2026-09-03",
                    "covered_cost": 10.0, "cost_available": True,
                    "efficiency_covered_cost": 10.0,
                    "covered_output_tokens": 1000,
                    "output_available": True,
                    "reasoning_tokens": 100,
                    "reasoning_output_tokens": 1000,
                    "reasoning_available": True,
                },
                {
                    "project": "ready · a1b2c3", "day": "2026-08-27",
                    "covered_cost": 10.0, "cost_available": True,
                    "efficiency_covered_cost": 10.0,
                    "covered_output_tokens": 500,
                    "output_available": True,
                    "reasoning_tokens": 50,
                    "reasoning_output_tokens": 500,
                    "reasoning_available": True,
                },
                {
                    "project": "unchecked · d4e5f6", "day": "2026-09-03",
                    "covered_cost": 80.0, "cost_available": True,
                    "efficiency_covered_cost": 80.0,
                    "covered_output_tokens": 8000,
                    "output_available": True,
                    "reasoning_tokens": 800,
                    "reasoning_output_tokens": 8000,
                    "reasoning_available": True,
                },
            ]

            payload = service.query(
                "", "7", spend_rows,
                ["ready · a1b2c3", "unchecked · d4e5f6"], candidates,
            )

        drivers = payload["overall"]["efficiency"]
        self.assertEqual(drivers["output_per_dollar"], 100.0)
        self.assertEqual(drivers["delivery_yield"], 50.5)
        self.assertEqual(drivers["reasoning_ratio"], 0.15)
        self.assertTrue(drivers["availability"]["output_per_dollar"])
        self.assertTrue(drivers["availability"]["delivery_yield"])
        self.assertTrue(drivers["availability"]["reasoning_ratio"])
        self.assertEqual(payload["coverage"]["covered_spend"], 20.0)
        self.assertEqual(payload["coverage"]["available_spend"], 100.0)
        self.assertEqual(payload["coverage"]["spend_coverage"], 0.2)
        self.assertEqual(payload["comparison"]["output_per_dollar_pct"], 100.0)
        self.assertEqual(payload["comparison"]["delivery_yield_pct"], -49.5)
        self.assertEqual(payload["comparison"]["reasoning_ratio_pp"], 5.0)
        small_day = next(row for row in payload["days"] if row["day"] == "2026-09-03")
        self.assertEqual(small_day["spend_per_1k"], 10000.0)
        self.assertAlmostEqual(small_day["rolling_spend_per_1k"], 20000 / 101)
        self.assertEqual(small_day["efficiency"]["output_per_dollar"], 100.0)
        self.assertEqual(small_day["efficiency"]["delivery_yield"], 1.0)
        self.assertEqual(small_day["efficiency"]["reasoning_ratio"], 0.1)

    def test_covered_spend_ratio_remains_available_with_explicit_partial_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(
                repo_key, True, local_timestamp("2026-09-04"),
            )
            service.ledger.record(
                repo_key, service._hash("change"),
                local_timestamp("2026-09-03"), 7, 3,
            )

            payload = service.query(
                "repo · a1b2c3", "7",
                [
                    {"project": "repo · a1b2c3", "day": "2026-09-03", "covered_cost": 5.0, "cost_available": True},
                    {"project": "repo · a1b2c3", "day": "2026-09-03", "covered_cost": 0.0, "cost_available": False},
                ],
                ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertEqual(payload["overall"]["spend_per_1k"], 500.0)
        self.assertTrue(payload["overall"]["availability"]["cost"])
        self.assertTrue(payload["overall"]["availability"]["partial"])

    def test_query_returns_comparable_current_and_previous_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(repo_key, True, local_timestamp("2026-09-04"))
            service.ledger.record(repo_key, service._hash("current"), local_timestamp("2026-09-03"), 80, 20)
            service.ledger.record(repo_key, service._hash("previous"), local_timestamp("2026-08-27"), 40, 10)
            payload = service.query(
                "repo · a1b2c3", "7",
                [
                    {"project": "repo · a1b2c3", "day": "2026-09-03", "covered_cost": 10.0, "cost_available": True},
                    {"project": "repo · a1b2c3", "day": "2026-08-27", "covered_cost": 10.0, "cost_available": True},
                ],
                ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertEqual(payload["overall"]["changed_lines"], 100)
        self.assertEqual(payload["overall"]["covered_cost"], 10.0)
        self.assertEqual(payload["overall"]["spend_per_1k"], 100.0)
        self.assertEqual(payload["previous"]["changed_lines"], 50)
        self.assertEqual(payload["comparison"]["code_pushed_pct"], 100.0)
        self.assertEqual(payload["comparison"]["spend_per_1k_pct"], -50.0)
        self.assertEqual(len(payload["days"]), 7)

    def test_query_keeps_unmeasured_code_unavailable_and_sorts_projects_by_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            ready_key = service._hash("/ready")
            service.ledger.map_project(service._hash("/ready"), ready_key)
            service.ledger.set_repository_coverage(ready_key, True, local_timestamp("2026-09-04"))
            service.ledger.record(ready_key, service._hash("ready-change"), local_timestamp("2026-09-03"), 10, 2)
            candidates = [
                {"root": "/ready", "project": "ready · a1b2c3"},
                {"root": "/unchecked", "project": "unchecked · d4e5f6"},
            ]
            payload = service.query(
                "", "7",
                [
                    {"project": "ready · a1b2c3", "day": "2026-09-03", "covered_cost": 2.0, "cost_available": True},
                    {"project": "unchecked · d4e5f6", "day": "2026-09-03", "covered_cost": 8.0, "cost_available": True},
                ],
                ["ready · a1b2c3", "unchecked · d4e5f6"], candidates,
            )

        self.assertEqual(payload["overall"]["changed_lines"], 12)
        self.assertEqual(payload["overall"]["covered_cost"], 2.0)
        self.assertEqual(payload["coverage"]["selected_repositories"], 2)
        self.assertEqual(payload["coverage"]["comparable_repositories"], 1)
        self.assertEqual(payload["project_rows"][0]["project"], "unchecked · d4e5f6")
        self.assertFalse(payload["project_rows"][0]["availability"]["code_pushed"])
        self.assertIsNone(payload["project_rows"][0]["spend_per_1k"])
        self.assertNotIn("/unchecked", json.dumps(payload))

    def test_code_pushed_remains_available_when_covered_spend_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(repo_key, True, local_timestamp("2026-09-04"))
            service.ledger.record(repo_key, service._hash("change"), local_timestamp("2026-09-03"), 7, 3)

            payload = service.query(
                "repo · a1b2c3", "7", [], ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertTrue(payload["overall"]["availability"]["code_pushed"])
        self.assertEqual(payload["overall"]["changed_lines"], 10)
        self.assertFalse(payload["overall"]["availability"]["cost"])
        self.assertIsNone(payload["overall"]["spend_per_1k"])

    def test_all_history_query_reads_only_the_bounded_twelve_month_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(
                repo_key, True, local_timestamp("2026-09-04"),
            )
            service.ledger.record(
                repo_key, service._hash("current"),
                local_timestamp("2026-09-03"), 7, 3,
            )
            service.ledger.record(
                repo_key, service._hash("ancient"),
                local_timestamp("2020-01-01"), 1000, 1000,
            )

            with mock.patch.object(
                service.ledger, "daily_rows", wraps=service.ledger.daily_rows,
            ) as daily_rows:
                payload = service.query(
                    "repo · a1b2c3", "all", [], ["repo · a1b2c3"],
                    [{"root": "/repo", "project": "repo · a1b2c3"}],
                )

        self.assertEqual(payload["overall"]["changed_lines"], 10)
        self.assertEqual(len(payload["days"]), git_delivery.MAX_QUERY_DAYS)
        self.assertEqual(daily_rows.call_args.args[1:], ("2025-09-04", "2026-09-04"))


class GitDeliveryApplicationTests(unittest.TestCase):
    def test_install_bootstrap_scans_while_the_invoking_app_has_repository_access(self):
        sources = [{"project": "/Users/alice/Code/private-project"}]
        service = mock.Mock()
        service.scan.return_value = {
            "ok": True, "new_changed_lines": 12, "coverage": {"measured": 1},
        }
        service.project_suffix.return_value = "a1b2c3"
        with mock.patch.object(meter, "all_session_sources", return_value=sources), mock.patch.object(
            meter, "git_delivery_service", return_value=service,
        ):
            result = meter.bootstrap_git_delivery()

        self.assertEqual(result["new_changed_lines"], 12)
        service.scan.assert_called_once()
        self.assertEqual(
            service.scan.call_args.args[0][0]["root"],
            "/Users/alice/Code/private-project",
        )

    def test_each_installer_bootstraps_delivery_before_server_start(self):
        contracts = (
            ("install", '"$INSTALL_ROOT/scripts/install-launch-agent" server-only'),
            ("install-linux", '"$INSTALL_ROOT/scripts/install-systemd-user" server-only'),
            ("install-windows.ps1", "& $StartScript -ReadinessTimeoutSeconds"),
        )
        for script, start_marker in contracts:
            with self.subTest(script=script):
                installer = Path(meter._SOURCE_ROOT, "scripts", script).read_text(
                    encoding="utf-8",
                )
                self.assertLess(installer.index("bootstrap_git_delivery"), installer.index(start_marker))

    def test_candidates_are_bounded_and_never_project_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), salt="test-salt",
            )
            root = "/Users/alice/Code/private-project"
            with mock.patch.object(meter, "git_delivery_service", return_value=service):
                candidates = meter.git_delivery_candidates([
                    {"project": root},
                    {"project": root},
                    {"project": "not-a-root"},
                ])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["root"], root)
        self.assertRegex(candidates[0]["project"], r"^private-project · [0-9a-f]{6}$")
        self.assertNotIn("/Users/alice", candidates[0]["project"])
        self.assertTrue(candidates[0]["project"].endswith(service.project_suffix(root)))
        self.assertFalse(candidates[0]["project"].endswith(
            hashlib.sha256(root.encode("utf-8")).hexdigest()[:6]
        ))

    def test_clear_uses_service_baseline_before_waking_the_watcher(self):
        service = mock.Mock()
        wake = mock.Mock()
        with mock.patch.object(meter, "git_delivery_service", return_value=service), mock.patch.object(
            meter, "_git_delivery_wake", wake,
        ):
            result = meter.clear_git_delivery_activity(confirm=True)

        self.assertEqual(result, {"ok": True})
        service.clear.assert_called_once_with()
        wake.set.assert_called_once_with()

    def test_spend_rows_keep_uncovered_cost_explicit(self):
        rows = meter.delivery_spend_rows([
            {"project": "/repo", "availability": {"cost": True},
             "_day_cost": {"2026-09-03": 12.5}, "_model_daily": []},
            {"project": "/repo", "availability": {"cost": False},
             "_day_cost": {"2026-09-03": 0}, "_model_daily": []},
        ])

        label = meter.delivery_project_label("/repo")
        self.assertEqual(rows, [
            {"project": label, "day": "2026-09-03", "covered_cost": 12.5, "cost_available": True},
            {"project": label, "day": "2026-09-03", "covered_cost": 0.0, "cost_available": False},
        ])

    def test_spend_rows_project_daily_efficiency_evidence_without_model_identity(self):
        rows = meter.delivery_spend_rows([{
            "project": "/repo",
            "availability": {"cost": True},
            "_day_cost": {"2026-09-03": 12.5},
            "_model_daily": [
                {
                    "day": "2026-09-03", "executions": 3,
                    "cost_covered_executions": 2,
                    "cost_covered_cost": 10.0,
                    "cost_covered_output_tokens": 4000,
                    "reasoning_tokens": 1000,
                    "reasoning_output_tokens": 4000,
                    "reasoning_executions": 2,
                },
                {
                    "day": "2026-09-03", "executions": 1,
                    "cost_covered_executions": 0,
                    "reasoning_unavailable_executions": 1,
                },
            ],
        }])

        self.assertEqual(rows, [{
            "project": meter.delivery_project_label("/repo"),
            "day": "2026-09-03",
            "covered_cost": 12.5,
            "cost_available": True,
            "efficiency_covered_cost": 10.0,
            "covered_output_tokens": 4000,
            "output_available": True,
            "output_partial": True,
            "reasoning_tokens": 1000,
            "reasoning_output_tokens": 4000,
            "reasoning_available": True,
            "reasoning_partial": True,
        }])


class GitDeliveryHttpContractTests(unittest.TestCase):
    def request(self, method, path, body=None, headers=None):
        server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        server.timeout = 1
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            connection.close()
            worker.join(timeout=2)
            server.server_close()

    def test_get_delivery_returns_only_bounded_safe_projection(self):
        payload = {
            "ok": True,
            "projects": ["private-project · acfdbe"],
            "days": [],
            "overall": {},
            "previous": {},
            "comparison": {},
            "project_rows": [],
            "coverage": {},
        }
        with mock.patch.object(meter, "git_delivery_state", return_value=payload):
            status, body = self.request("GET", "/git-delivery?range=7")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["projects"], ["private-project · acfdbe"])
        self.assertNotIn("/Users/alice", body)

    def test_clear_requires_action_token_and_explicit_confirmation(self):
        body = json.dumps({"confirm": True})
        status, response = self.request(
            "POST", "/git-delivery/clear", body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

        self.assertEqual(status, 403)
        self.assertIn("Invalid action token", response)


class GitDashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(meter._SOURCE_ROOT, "page.html").read_text(encoding="utf-8")

    def test_git_is_a_dedicated_answer_first_page(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]

        for marker in (
            "Pushed code &times; covered spend.", "Pushed lines", "Spend / 1K",
            "Spend coverage", "Daily pushes",
            "id=d-daily-chart", "id=d-project-table", "data-delivery-sort",
            ">Projects<",
            "id=d-coverage-bar", "id=d-coverage-percent", "id=d-active-days",
            "Local Git evidence &middot; Text changes only &middot; Not a quality score.",
        ):
            self.assertIn(marker, git_page)
        self.assertIn("7-DAY SPEND / 1K LINES", self.page)
        self.assertIn("rolling_spend_per_1k", self.page)
        self.assertIn(
            "#view-git .spectrumPageActions .modelControls{grid-template-columns:repeat(2",
            self.page,
        )
        self.assertNotIn("commit", git_page.lower())
        self.assertNotIn("radial-gradient", git_page)
        self.assertIn("Last 12 months", git_page)
        for repeated_copy in (
            "Review pushed code, cost intensity, and project coverage.",
            "Period-over-period signal from locally observed successful pushes.",
            "Pushed text lines by day, paired with trailing seven-day cost intensity.",
            "Projects with period activity",
            "Text additions plus deletions observed after successful named-remote pushes.",
        ):
            self.assertNotIn(repeated_copy, git_page)

    def test_git_uses_one_compact_overview_then_trend_and_projects(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]

        for marker in (
            'class="card deliveryOverview"',
            'class=deliveryMetricGrid',
            'class=deliveryCoverageBar',
            'class=deliveryEvidenceStrip',
            '.deliveryOverview{',
            '.deliveryCoverageBar{',
            '.deliveryOverviewHead{min-height:68px',
            '.deliveryChartWrap{position:relative;height:270px',
        ):
            self.assertIn(marker, self.page)
        for removed_structure in (
            'class=deliveryBrief', 'class="card deliveryOutcome"',
            'class="card deliveryEvidence"', 'class=deliveryDrivers',
            'class=deliveryDriverGrid', 'deliveryCoverageRing',
        ):
            self.assertNotIn(removed_structure, git_page)
        self.assertEqual(git_page.count('class="card deliveryOverview"'), 1)
        self.assertLess(git_page.index('class="card deliveryOverview"'), git_page.index('class="card deliveryVisual"'))
        self.assertLess(git_page.index('class="card deliveryVisual"'), git_page.index('class="card deliveryProjects"'))

    def test_git_omits_efficiency_driver_presentation(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]

        for removed_marker in (
            "Efficiency drivers", "class=deliveryDriverStrip",
            "id=d-driver-summary", "id=d-output-dollar", "id=d-delivery-yield",
            "id=d-reasoning-ratio", "class=deliveryDriverValue",
        ):
            self.assertNotIn(removed_marker, git_page)
        for removed_code in (
            ".deliveryDriverStrip{", "function deliveryDriverChange",
            "function deliveryDriverSummary", "$('d-driver-summary')",
            "<span>Output / $</span>", "<span>Push yield</span>",
            "<span>Reasoning</span>",
        ):
            self.assertNotIn(removed_code, self.page)

    def test_git_projects_become_sortable_cards_at_narrow_width(self):
        for marker in (
            ".deliveryMobileSort{display:none}",
            "@media(max-width:700px){.deliveryMobileSort{display:grid",
            ".deliveryTableWrap thead{display:none}",
            ".deliveryTableWrap tbody{display:grid",
            "id=d-mobile-sort",
            'data-label="Covered spend"',
            "$('d-mobile-sort').addEventListener('change'",
        ):
            self.assertIn(marker, self.page)

    def test_git_overview_exposes_bounded_coverage_and_activity_evidence(self):
        for marker in (
            "coverageBar.style.setProperty('--delivery-coverage'",
            "coverageBar.setAttribute('aria-valuenow'",
            "coverageBar.classList.toggle('partial'",
            "$('d-coverage-percent').textContent",
            "$('d-active-days').textContent",
            "$('d-period-label').textContent",
            "days.filter(row=>(Number(row?.changed_lines)||0)>0).length",
            "No Git-backed projects discovered",
        ):
            self.assertIn(marker, self.page)

    def test_git_coverage_never_exposes_unavailable_as_zero(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]
        coverage_bar = git_page.split("id=d-coverage-bar", 1)[1].split("><i", 1)[0]
        load_delivery = self.page.split("async function loadDelivery()", 1)[1].split(
            "$('d-project').addEventListener", 1,
        )[0]

        self.assertNotIn("aria-valuenow=0", coverage_bar)
        self.assertIn('aria-valuetext="Spend coverage unavailable"', coverage_bar)
        self.assertIn("coverageBar.removeAttribute('aria-valuenow')", load_delivery)
        self.assertIn(
            "coverageBar.setAttribute('aria-valuetext','Spend coverage unavailable')",
            load_delivery,
        )
        self.assertIn("$('d-coverage-percent').textContent='--'", load_delivery)

    def test_git_chart_inspector_opens_for_data_and_closes_outside(self):
        self.assertIn("function selectDeliveryDay", self.page)
        self.assertIn("function dismissGitChartInspector", self.page)
        self.assertIn("y2=${y} />", self.page)
        self.assertIn("rx=2 />", self.page)
        self.assertIn("addEventListener('pointerenter'", self.page)
        self.assertIn("addEventListener('focus'", self.page)
        self.assertIn("addEventListener('click'", self.page)
        self.assertIn("if(!target?.closest('#d-chart-wrap'))dismissGitChartInspector()", self.page)
        self.assertIn("if(event.key==='Escape')dismissGitChartInspector()", self.page)
        self.assertIn("deliverySelectedDay=''", self.page)
        self.assertIn("button.setAttribute('aria-pressed','false')", self.page)
        self.assertNotIn("d-daily-chart').addEventListener('pointerleave'", self.page)
        self.assertNotIn("<span>Output / $</span>", self.page)
        self.assertNotIn("<span>Push yield</span>", self.page)
        self.assertNotIn("<span>Reasoning</span>", self.page)

    def test_git_is_the_canonical_name_and_delivery_hash_is_compatible(self):
        for marker in (
            "id=tab-git data-label=Git aria-label=Git",
            "title=\"Git · Shortcut: Option+5\"",
            "<span class=tabLabel>Git</span>",
            "<div class=view id=view-git>",
            "<h1>Git</h1>",
            "{id:'git',label:'Git'",
            "route:'git',directKey:'Digit5'",
            "if(h==='delivery')setHashRoute('git',{replace:true,apply:false})",
            "if(h==='git'||h==='delivery')",
            "showTab('git')",
            "openTopLevelRoute('git')",
            "Git evidence history",
        ):
            self.assertIn(marker, self.page)
        for removed in (
            "id=tab-delivery", "data-label=Delivery", "aria-label=Delivery",
            "<span class=tabLabel>Delivery</span>", "<h1>Delivery</h1>",
            "label:'Delivery'", "route:'delivery',directKey:'Digit5'",
        ):
            self.assertNotIn(removed, self.page)

        docs = {
            "README.md": Path(meter._SOURCE_ROOT, "README.md").read_text(encoding="utf-8"),
            "specs/USER_GUIDE.md": Path(meter._SOURCE_ROOT, "specs/USER_GUIDE.md").read_text(encoding="utf-8"),
            "specs/ARCHITECTURE.md": Path(meter._SOURCE_ROOT, "specs/ARCHITECTURE.md").read_text(encoding="utf-8"),
            "specs/SECURITY.md": Path(meter._SOURCE_ROOT, "specs/SECURITY.md").read_text(encoding="utf-8"),
            "specs/AGENTS.md": Path(meter._SOURCE_ROOT, "specs/AGENTS.md").read_text(encoding="utf-8"),
        }
        self.assertIn("### Git", docs["README.md"])
        self.assertIn("### Git", docs["specs/USER_GUIDE.md"])
        expected_order = (
            "Sessions → Spend → Models → Efficiency → Git → Learn → Tools → Settings"
        )
        self.assertIn(expected_order, " ".join(docs["specs/ARCHITECTURE.md"].split()))
        self.assertIn(expected_order, " ".join(docs["specs/AGENTS.md"].split()))
        self.assertIn("The Git page is a local-only Git reader", docs["specs/SECURITY.md"])

if __name__ == "__main__":
    unittest.main()
