// The timeline: one lane per ticket in ticket order, a segment per phase placed on the run's span (wide layouts only).
// A lane is a row of equal cells, each coloured by the phase covering its middle: only layout calls the other views use.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";

const CELLS = 60;

export function timelineView(timeline, t) {
  const colors = { work: t.working, review: t.done, ci: t.blocked };
  const cell = (lane, i) => {
    const at = (i + 0.5) / CELLS;
    const seg = lane.segments.find((s) => at >= s.left && at < s.left + s.width);
    return div().flex_1().h(8).bg(seg ? colors[seg.phase] ?? t.dim : t.raised);
  };
  return v_flex().id("timeline").flex_none().gap(3).px(12).py(8).border_b(1).border_color(t.border)
    .child(h_flex().gap(10).text_size(10).text_color(t.dim)
      .children(Object.entries(colors).map(([phase, color]) =>
        h_flex().gap(4).items_center().child(div().w(8).h(8).rounded(2).bg(color)).child(phase))))
    .children(timeline.lanes.map((lane) => h_flex().gap(8).items_center()
      .child(div().w(28).flex_none().text_size(11).text_color(t.muted).child(lane.id))
      .child(h_flex().flex_1().min_w(0).children(Array.from({ length: CELLS }, (_, i) => cell(lane, i))))));
}
