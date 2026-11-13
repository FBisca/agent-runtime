// The header: an open plan's way back, name, state and whole goal, with one progress bar; on the list, the project.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { badge, control, progressBar, progressText } from "../ui.js";

export function headerView(head, ctx) {
  const { t, project, narrow, loading, onBack, onRefresh, expand } = ctx;
  // An open plan is a way back, its name and its whole goal; the repository is the list's title, not the plan's.
  const open = Boolean(head) && Boolean(onBack);
  return v_flex().id("header").flex_none().gap(6).px(12).py(8).border_b(1).border_color(t.border)
    .child(h_flex().gap(8).items_center()
      .when(open, (el) => el.child(control("back", "←", (_e, cx) => onBack(cx), t))
        .child(div().flex_1().min_w(0).text_size(15).font_semibold().truncate().child(head.title)))
      .when(!open, (el) => el.child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(narrow ? "plans" : `${project} · plans`)))
      .when(open, (el) => el.child(badge(head.state.word, head.state.tone, t)))
      .when(Boolean(expand), (el) => el.child(control("expand", expand.expanded ? "Collapse" : "Expand",
        (_e, cx) => expand.onClick(cx), t, expand.expanded)))
      .child(control("refresh", loading ? "…" : "↻", (_e, cx) => onRefresh(cx), t)))
    .when(open && Boolean(head.goal), (el) =>
      // Wide wraps the whole goal; narrow clamps it to its lines by height (12px text, 16px lines).
      el.child(div().text_size(12).text_color(t.muted).whitespace_normal()
        .when(Boolean(head.goalLines), (g) => g.max_h(head.goalLines * 16).overflow_hidden())
        .child(head.goal)))
    .when(Boolean(head) && Boolean(head.progress), (el) => el
      .child(progressBar(head.progress, t))
      .child(div().text_size(11).text_color(t.dim).child(progressText(head.progress))))
    .when(Boolean(head) && Boolean(head.metrics), (el) => el.child(
      div().text_size(11).text_color(t.dim).truncate()
        .child(`wall ${head.metrics.wall} · first pass ${head.metrics.firstPass} · driver errors ${head.metrics.errors}`)))
    .when(Boolean(head) && Boolean(head.drift), (el) => el.child(
      div().text_size(11).text_color(t.blocked).truncate()
        .child(`changed since approval · approved ${head.drift.approved?.slice(0, 8)} · now ${head.drift.current?.slice(0, 8)}`)));
}
