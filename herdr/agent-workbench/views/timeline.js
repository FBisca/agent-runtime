// An open plan's timeline: every ticket's phases on the plan's own clock with each merge marked when wide, and each ticket's
// phase split when narrow. `timeline` is the model's `planTimeline`; every position, word and slot is the model's.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { dataColor, colorOf } from "../ui.js";
import { duration, STEP_ORDER, stepLook } from "../model.js";
import { bar, legend, panel } from "./charts.js";

const LABEL_W = 40;

const pill = (text, t) => div().px(6).rounded(8).text_size(10).font_semibold().bg(t.raised).text_color(t.muted).child(text);

// A lane's name carries its state in its colour and the ticket's id in words, so no state is told by colour alone.
const laneLabel = (lane, t) => div().w(LABEL_W).flex_none().text_color(colorOf(lane.look.tone, t)).child(lane.id);

function clockLanes(timeline, t) {
  return v_flex().gap(5)
    .children(timeline.lanes.map((lane) => h_flex().id(`lane-${lane.id}`).gap(8).items_center().text_size(11)
      .child(laneLabel(lane, t))
      .child(div().flex_1().min_w(0).relative().h(14).rounded(3).bg(t.raised)
        .children(lane.segments.map((s, i) => div().id(`tl-${lane.id}-${i}`).absolute().top(0).h_full().rounded(3)
          .bg(dataColor(s.slot, t)).left(`${s.left * 100}%`).w(`${Math.max(0.4, s.width * 100)}%`)
          .tooltip(`${lane.id} ${s.step} · ${duration(s.seconds)}`)))
        .when(lane.merged !== null, (el) => el.child(div().id(`merged-${lane.id}`).absolute().top(-3).h(20).w(2).ml(-1)
          .left(`${lane.merged * 100}%`).bg(t.foreground).tooltip(`${lane.id} merged`))))))
    .child(h_flex().gap(8).text_size(10).text_color(t.dim)
      .child(div().w(LABEL_W).flex_none())
      .child(h_flex().flex_1().min_w(0).justify_between().border_t(1).border_color(t.border).pt(2)
        .children(timeline.ticks.map((tick) => div().child(tick.label)))))
    .child(legend([...STEP_ORDER.map((step) => ({ name: step === "merge" ? "merge wait" : step, color: dataColor(stepLook(step).slot, t) })),
      { name: "merged", color: t.foreground }], t));
}

// The narrow panel has no room for a clock: each ticket's time is one bar split by its phases.
function splitLanes(timeline, t) {
  const lanes = timeline.lanes.filter((lane) => lane.phases);
  return v_flex().gap(6)
    .children(lanes.map((lane) => h_flex().id(`split-${lane.id}`).gap(8).items_center().text_size(11)
      .child(laneLabel(lane, t))
      .child(div().flex_1().min_w(0).child(bar(lane.phases.map((p) => ({ key: p.step, value: p.seconds, slot: p.slot })), t, `split-${lane.id}`)))
      .child(div().w(64).flex_none().text_color(t.dim).truncate().child(duration(lane.phases.reduce((n, p) => n + p.seconds, 0))))))
    .child(legend(STEP_ORDER.map((step) => ({ name: step, color: dataColor(stepLook(step).slot, t) })), t));
}

export function timelineView(timeline, t) {
  if (timeline.kind === "split") return panel("Phase split", splitLanes(timeline, t), t);
  return panel("Timeline", clockLanes(timeline, t), t, pill(`${timeline.span.text} span`, t));
}
