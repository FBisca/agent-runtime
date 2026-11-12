import test from "node:test";
import assert from "node:assert/strict";
import { panelExpand, panelToggle } from "../model.js";

// The work row as Herdr's layout() describes it: the terminals (`main`) beside the side panel that holds the workbench.
const work = ({ main = { hidden: false }, panel = {}, body = {} } = {}) => ({ id: "root", type: "row", children: [
  { id: "work", type: "column", children: [{ id: "work-body", type: "row", children: [
    { id: "main", type: "block", ...main },
    { id: "panel", type: "column", size: { px: 520 }, min: 280, ...panel, children: [{ id: "panel-body", type: "stack",
      active: "workbench", children: [{ id: "workbench", type: "block" }, { id: "files", type: "block" }], ...body }] },
  ] }] },
] });

const expanded = (panel = { size: { weight: 1 } }) => work({ main: { hidden: true }, panel });

test("S33 a workbench panel beside the terminals offers expand: main hides and the panel takes weight 1", () => {
  const e = panelExpand(work(), null);
  assert.equal(e.offer, true);
  assert.equal(e.expanded, false);
  assert.deepEqual(e.commands, [["layout.hide", { id: "main" }], ["layout.resize", { id: "panel", weight: 1 }]]);
  assert.equal(e.width, 520, "the width the panel has now, to restore on collapse");
});

test("S33 collapse shows main and restores the width the panel had, 520 when unknown", () => {
  const e = panelExpand(expanded(), 700);
  assert.equal(e.expanded, true);
  assert.equal(e.offer, false, "already expanded: collapse is offered, not expand");
  assert.deepEqual(e.commands, [["layout.show", { id: "main" }], ["layout.resize", { id: "panel", px: 700 }]]);
  assert.deepEqual(panelExpand(expanded(), null).commands, [["layout.show", { id: "main" }], ["layout.resize", { id: "panel", px: 520 }]]);
});

test("S33 closing while expanded shows main and restores the width before hiding the panel", () => {
  const toggle = panelToggle(expanded(), 700);
  assert.equal(toggle.on, true);
  assert.deepEqual(toggle.commands, [
    ["layout.show", { id: "main" }],
    ["layout.resize", { id: "panel", px: 700 }],
    ["layout.hide", { id: "panel" }],
  ]);
  assert.deepEqual(panelToggle(expanded(), null).commands.slice(1, 2), [["layout.resize", { id: "panel", px: 520 }]]);
});

test("S33 no expand is offered when the panel does not show the workbench or has no terminals beside it", () => {
  assert.equal(panelExpand(work({ panel: { hidden: true } }), null).offer, false, "a hidden panel");
  assert.equal(panelExpand(work({ body: { active: "files" } }), null).offer, false, "a panel on another panel");
  const noTerminals = work();
  noTerminals.children[0].children[0].children = noTerminals.children[0].children[0].children.filter((c) => c.id !== "main");
  assert.equal(panelExpand(noTerminals, null).offer, false, "no terminals beside the panel");
  assert.equal(panelExpand(null, null), null, "a layout with no panel");
});
