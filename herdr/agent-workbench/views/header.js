// The header: the project, the plan picker, one state and one progress bar. Narrow, it keeps the plan and its state.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { badge, control, progressBar, progressText } from "../ui.js";

export function headerView(head, ctx) {
  const { t, project, narrow, pickerOpen, loading, onPicker, onRefresh } = ctx;
  const name = head ? head.slug : "plans";
  return v_flex().id("header").flex_none().gap(6).px(12).py(8).border_b(1).border_color(t.border)
    .child(h_flex().gap(8).items_center()
      .when(!narrow, (el) => el.child(div().flex_none().text_color(t.dim).child(project)))
      .child(control("plan-picker", `${name} ${pickerOpen ? "▴" : "▾"}`, (_e, cx) => onPicker(cx), t, pickerOpen))
      .child(div().flex_1())
      .when(Boolean(head), (el) => el.child(badge(head.state.word, head.state.tone, t)))
      .child(control("refresh", loading ? "…" : "↻", (_e, cx) => onRefresh(cx), t)))
    .when(Boolean(head) && !narrow && Boolean(head.goal), (el) =>
      el.child(div().text_size(12).text_color(t.muted).truncate().child(head.goal)))
    .when(Boolean(head) && Boolean(head.progress), (el) => el
      .child(progressBar(head.progress, t))
      .child(div().text_size(11).text_color(t.dim).child(progressText(head.progress))))
    .when(Boolean(head) && Boolean(head.drift), (el) => el.child(
      div().text_size(11).text_color(t.blocked).truncate()
        .child(`changed since approval · approved ${head.drift.approved?.slice(0, 8)} · now ${head.drift.current?.slice(0, 8)}`)));
}
