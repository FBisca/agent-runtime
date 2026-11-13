"""The agent-workbench Herdr mod's pure modules (model.js, data.js) under `node --test`.

Each scenario's test runs the JS tests named with its S-ID and requires that some ran and none failed, so a
renamed or deleted JS test turns its scenario red instead of passing silently. A missing `node` fails the test
naming node: the mod's logic is only proven when its tests run.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

MOD = Path(__file__).resolve().parent.parent / "herdr" / "agent-workbench"


def node_tests(pattern=None, path=None):
    """Run `node --test` over the mod's test directory and return (passed, failed, output). A missing node is a
    failure that names node, never a skip."""
    node = shutil.which("node", path=path)
    if node is None:
        pytest.fail(
            "node is not installed: the agent-workbench mod's tests need `node` on PATH (node --test)"
        )
    command = [node, "--test", "--test-reporter=tap"]
    if pattern:
        command.append("--test-name-pattern=%s" % pattern)
    command.extend(sorted(str(p) for p in (MOD / "test").glob("*.test.js")))
    # The promoted acceptance tests stay in CI after the plan that wrote them is archived.
    command.extend(sorted(str(p) for p in (MOD / "acceptance").rglob("*.test.js")))
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, cwd=MOD)
    passed = re.search(r"^# pass (\d+)$", done.stdout, re.M)
    failed = re.search(r"^# fail (\d+)$", done.stdout, re.M)
    return (
        int(passed.group(1)) if passed else 0,
        int(failed.group(1)) if failed else -1,
        done.stdout + done.stderr,
    )


def scenario(sid, at_least):
    passed, failed, output = node_tests(pattern=r"^%s " % sid)
    assert failed == 0, output
    assert passed >= at_least, "%s: %d node tests passed, expected at least %d\n%s" % (
        sid,
        passed,
        at_least,
        output,
    )


def dashboard(sid, name, at_least):
    """The dashboard's node tests for one scenario: `<sid> dashboard ...` (the control plan's own `<sid> ...` tests share the
    ID, so the prefix names these)."""
    passed, failed, output = node_tests(pattern=r"^%s dashboard" % sid)
    assert failed == 0, output
    assert passed >= at_least, "%s %s: %d node tests passed, expected at least %d\n%s" % (
        sid,
        name,
        passed,
        at_least,
        output,
    )


def source(name):
    return (MOD / name).read_text()


def test_S12_the_mod_lists_plans_and_shows_layered_tickets_with_one_inspector():
    scenario("S12", 4)


def test_S13_a_failure_is_shown_in_its_scope_with_the_way_out_and_no_placeholder_data():
    scenario("S13", 6)


def test_S14_a_narrow_mod_shows_one_column_and_a_drill_in_inspector():
    scenario("S14", 3)


def test_S15_a_refresh_runs_one_fixed_argv_fetch_in_flight_and_drops_stale_answers():
    scenario("S15", 6)


def test_S15_the_mod_runs_no_shell_and_writes_nothing():
    files = [MOD / name for name in ("main.js", "data.js", "model.js", "ui.js")]
    files += sorted((MOD / "views").glob("*.js"))
    for file in files:
        text = file.read_text()
        assert "writeTextFile" not in text, file.name
        assert "fs/promises" not in text, file.name
        assert not re.search(r"exec\(\s*[\"'](sh|bash|zsh)[\"']", text), file.name
        assert "process.run" not in text, file.name
    main = source("main.js")
    assert "watchFolder" in main
    assert "FALLBACK_MS" in main


def test_S16_an_attempt_offers_Open_only_for_a_live_terminal_at_its_address():
    scenario("S16", 6)


def test_S7_home_gives_the_cards_needs_me_per_plan_the_active_plans_with_workers_the_prs_and_the_older_count():
    scenario("S7", 7)


def test_S8_the_mod_opens_on_home_a_plan_or_a_card_opens_over_it_and_back_returns_home():
    scenario("S8", 4)
    assert not (MOD / "views" / "plans.js").exists(), "there is no plan picker"
    assert "pickPlan" not in source("model.js")
    assert "pickerOpen" not in source("main.js")


def test_S9_a_plans_header_is_a_way_back_its_name_and_its_whole_goal():
    scenario("S9", 1)


def test_S10_a_ticket_names_its_agents_live_only_at_their_exact_address_with_Open():
    scenario("S10", 4)


def test_S11_there_is_no_Runtime_section():
    scenario("S11", 1)
    assert not (MOD / "views" / "runtime.js").exists()
    for file in [MOD / "main.js", MOD / "model.js", *sorted((MOD / "views").glob("*.js"))]:
        assert "Runtime" not in file.read_text(), file.name


# Open, the tab bar's panel button (R19), and the header's expand over the terminals (S33).
ALLOWED_COMMANDS = {
    '"agent.focus"',
    '"layout.select"',
    '"layout.show"',
    '"layout.hide"',
    '"layout.resize"',
}


def test_S16_the_mod_runs_only_the_focus_command_and_reads_terminals_without_polling():
    files = [MOD / name for name in ("main.js", "data.js", "model.js", "ui.js")]
    files += sorted((MOD / "views").glob("*.js"))
    for file in files:
        text = file.read_text()
        for command in re.findall(
            r"[\"'](?:agent|pane|terminal|space|tab|layout)\.[a-z_]+[\"']", text
        ):
            assert command in ALLOWED_COMMANDS, (file.name, command)
    assert "setInterval" not in source("main.js")
    assert "terminals()" in source("main.js")


def test_S18_the_pytest_runner_also_runs_the_node_tests_under_acceptance(tmp_path, monkeypatch):
    # A failing node test under acceptance/ must fail the runner, so the file is part of what `node --test` is given.
    (tmp_path / "acceptance").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "acceptance" / "x.test.js").write_text(
        'import test from "node:test";\ntest("S99 red", () => { throw new Error("boom"); });\n'
    )
    monkeypatch.setattr(sys.modules[__name__], "MOD", tmp_path)
    passed, failed, _ = node_tests()
    assert (passed, failed) == (0, 1)


def test_S18_node_test_runs_the_mods_pure_modules_from_pytest():
    passed, failed, output = node_tests()
    assert failed == 0, output
    assert passed >= 24, output


def test_S18_a_missing_node_fails_naming_node_instead_of_skipping(tmp_path):
    empty = str(tmp_path)
    with pytest.raises(pytest.fail.Exception, match="node"):
        node_tests(path=empty)
    assert os.path.isdir(empty)


def test_S24_the_tab_bar_opens_and_closes_the_workbench_panel():
    scenario("S24", 4)


def test_S24_the_workbench_layout_puts_the_button_beside_herdrs_tabs():
    layout = json.loads((MOD.parent / "layouts" / "workbench.json").read_text())

    def nodes(node):
        yield node
        for child in node.get("children", []):
            yield from nodes(child)

    by_id = {n["id"]: n for n in nodes(layout)}
    bar = by_id["tab-bar"]
    assert [c["id"] for c in bar["children"]] == ["tabs", "workbench-toggle"]
    assert by_id["tabs"]["content"] == {"mod": "tabs"}
    assert by_id["workbench-toggle"]["content"] == {
        "mod": "agent-workbench",
        "props": {"toggle": True},
    }
    assert "workbench" in [c["id"] for c in by_id["panel-body"]["children"]]
    assert (MOD / "workbench.svg").is_file()


def test_S33_the_header_expands_the_panel_over_the_terminals_and_collapses_it_back():
    scenario("S33", 4)


def test_S30_the_mod_opens_on_needs_me_the_active_plans_and_a_collapsed_older_line():
    scenario("S30", 3)


def test_S34_the_shipped_workbench_panel_keeps_520_and_280_with_no_max_so_weight_one_fills_the_area():
    layout = json.loads((MOD.parent / "layouts" / "workbench.json").read_text())

    def nodes(node):
        yield node
        for child in node.get("children", []):
            yield from nodes(child)

    panel = {n["id"]: n for n in nodes(layout)}["panel"]
    assert panel["size"] == {"px": 520}
    assert panel["min"] == 280
    assert "max" not in panel


def test_S31_the_header_and_inspector_show_a_plans_measures_unknown_never_zero():
    scenario("S31", 3)


def test_S32_a_timeline_shows_one_lane_per_ticket_when_wide():
    scenario("S32", 2)


def test_S7_a_dashboard_opens_on_its_value_its_change_against_last_week_and_its_sparkline_with_axes_on_every_scaled_chart():
    dashboard("S7", "headline and axes", 3)


def test_S8_each_cards_dashboard_draws_its_charts_and_no_chart_for_a_section_the_answer_lacks():
    dashboard("S8", "charts", 7)


def test_S9_a_click_filters_the_dashboards_rows_the_same_click_clears_it_and_back_clears_it():
    dashboard("S9", "filters", 3)


def test_S10_rows_name_their_unit_read_relative_time_and_open_only_a_github_pull_request():
    dashboard("S10", "rows", 3)
    main = source("main.js")
    # The one thing the mod opens is a GitHub pull request, and the mod checks it again before it opens it.
    assert main.count("open_url(") == 1
    assert re.search(r"if \(prLink\(url\)\) cx\.open_url\(url\)", main)


def test_S11_data_colours_are_the_fixed_palette_by_slot_and_no_chart_is_coloured_with_a_state_colour():
    dashboard("S11", "palette", 2)
    ui = source("ui.js")
    palette = [ui.index(hex_) for hex_ in ("#199e70", "#9085e9", "#d55181", "#008300")]
    assert palette == sorted(palette), "aqua, violet, magenta, green, in that order"
    for name in ("charts.js", "dashboard.js"):
        text = (MOD / "views" / name).read_text()
        assert not re.search(r"\bt\.(done|working|blocked)\b", text), name
        assert not re.search(r"#[0-9a-fA-F]{6}", text), name


def test_S15_a_narrow_dashboard_is_one_column_of_shorter_charts_and_home_has_two_cards_to_a_row():
    dashboard("S15", "narrow", 1)
