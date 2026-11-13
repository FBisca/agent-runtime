import test from "node:test";
import assert from "node:assert/strict";

// The work body of the workbench layout as Herdr's layout() describes it: the terminals (`main`) beside the side
// panel and its stack. Imported lazily so the file loads before the mod has the functions.
const model = () => import("../model.js");

const tree = ({ mainHidden = false, panelHidden = false, active = "workbench", size = { px: 520 } } = {}) => ({
  id: "root", type: "row", children: [{ id: "work", type: "column", children: [{ id: "work-body", type: "row", children: [
    { id: "main", type: "block", hidden: mainHidden },
    { id: "panel", type: "column", hidden: panelHidden, size, children: [{ id: "panel-body", type: "stack", active,
      children: [{ id: "workbench", type: "block" }, { id: "files", type: "block" }, { id: "changes", type: "block" }] }] },
  ] }] }],
});

test("S33 a workbench beside the terminals expands over them", async () => {
  const { panelExpand } = await model();
  const expand = panelExpand(tree());
  assert.equal(expand.expanded, false);
  assert.deepEqual(expand.commands, [["layout.hide", { id: "main" }], ["layout.resize", { id: "panel", weight: 1 }]]);
});

test("S33 an expanded workbench collapses to the width it had", async () => {
  const { panelExpand } = await model();
  const expanded = tree({ mainHidden: true, size: { weight: 1 } });
  const collapse = panelExpand(expanded, 640);
  assert.equal(collapse.expanded, true);
  assert.deepEqual(collapse.commands, [["layout.show", { id: "main" }], ["layout.resize", { id: "panel", px: 640 }]]);
  assert.deepEqual(panelExpand(expanded).commands[1], ["layout.resize", { id: "panel", px: 520 }]);
});

test("S33 closing the panel while expanded brings the terminals back first", async () => {
  const { panelToggle } = await model();
  const toggle = panelToggle(tree({ mainHidden: true, size: { weight: 1 } }), 600);
  assert.equal(toggle.on, true);
  assert.deepEqual(toggle.commands, [
    ["layout.show", { id: "main" }],
    ["layout.resize", { id: "panel", px: 600 }],
    ["layout.hide", { id: "panel" }],
  ]);
});

test("S33 no expand is offered without the panel showing the workbench or without terminals beside it", async () => {
  const { panelExpand } = await model();
  assert.equal(panelExpand(tree({ panelHidden: true })), null);
  assert.equal(panelExpand(tree({ active: "files" })), null);
  const noMain = tree();
  noMain.children[0].children[0].children.shift();
  assert.equal(panelExpand(noMain), null);
});
