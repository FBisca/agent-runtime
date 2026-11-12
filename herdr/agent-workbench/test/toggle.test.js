import test from "node:test";
import assert from "node:assert/strict";
import { panelToggle, panelsWith, WORKBENCH_PANEL } from "../model.js";

// The work column of a layout as Herdr's layout() describes it: the side panel and its stack of panels.
const tree = (panel, body) => ({ id: "root", type: "row", children: [{ id: "work", type: "column", children: [
  { id: "panel", type: "column", ...panel, children: [{ id: "panel-body", type: "stack", ...body }] },
] }] });
const stack = (active) => ({
  active, children: [{ id: "workbench", type: "block" }, { id: "files", type: "block" }, { id: "changes", type: "block" }],
});

test("S24 a hidden panel, or one showing another panel, is opened on the workbench", () => {
  for (const [panel, active] of [[{ hidden: true }, "workbench"], [{ hidden: false }, "files"], [{ hidden: true }, "changes"]]) {
    const toggle = panelToggle(tree(panel, stack(active)));
    assert.equal(toggle.on, false, JSON.stringify([panel, active]));
    assert.deepEqual(toggle.commands, [
      ["layout.select", { stack: "panel-body", child: "workbench" }],
      ["layout.show", { id: "panel" }],
    ]);
  }
});

test("S24 a panel showing the workbench is closed", () => {
  const toggle = panelToggle(tree({ hidden: false }, stack("workbench")));
  assert.equal(toggle.on, true);
  assert.deepEqual(toggle.commands, [["layout.hide", { id: "panel" }]]);
});

test("S24 a layout with no side panel, or none holding the workbench, offers no button", () => {
  assert.equal(panelToggle(null), null);
  assert.equal(panelToggle({ id: "root", type: "row", children: [] }), null);
  const noWorkbench = tree({ hidden: false }, { active: "files", children: [{ id: "files", type: "block" }] });
  assert.equal(panelToggle(noWorkbench), null);
});

test("S24 the workbench joins the panels Herdr's tabs mod keeps per space, once, after the person's own", () => {
  assert.deepEqual(panelsWith(["files", "changes"]), ["files", "changes", WORKBENCH_PANEL]);
  assert.deepEqual(panelsWith([]), [WORKBENCH_PANEL], "the person turned the others off: they stay off");
  assert.deepEqual(panelsWith(undefined), ["files", "changes", WORKBENCH_PANEL], "unset reads as Herdr's default");
  assert.equal(panelsWith(["files", WORKBENCH_PANEL]), null, "already there: nothing to write");
});
