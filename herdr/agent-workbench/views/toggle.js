// The tab bar's Workbench button, drawn like Herdr's own Files and Changes buttons beside it.
import { svg } from "gpui-kit";
import { h_flex } from "gpui-base";

export function toggleView(toggle, { t, onToggle }) {
  const bar = h_flex().id("workbench-toggle").size_full().items_center().justify_end().pr(10)
    .bg(t.chrome).border_b(1).border_color(t.border);
  if (!toggle) return bar;
  const on = toggle.on;
  return bar.child(h_flex().id("panel-workbench").h(26).px(8).gap(5).flex_none().items_center().rounded(6)
    .text_size(12).bg(on ? t.selected : t.chrome).text_color(on ? t.foreground : t.muted)
    .hover((s) => s.bg(t.hover)).on_mouse_down("left", (_e, cx) => cx.stop_propagation())
    .on_click((_e, cx) => onToggle(cx))
    .child(svg("mods/agent-workbench/workbench.svg").size(14)).child("Workbench"));
}
