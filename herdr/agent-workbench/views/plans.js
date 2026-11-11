// The plan list: every plan of the project, YAML with its state and progress, Markdown marked legacy.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { badge, progressText } from "../ui.js";

export function planList(rows, selected, onPick, t) {
  return v_flex().id("plan-list").gap(2).p(8).children(rows.map((row) => {
    const open = row.selectable;
    const line = h_flex().gap(8).items_center()
      .child(div().flex_1().min_w(0).truncate().font_semibold().text_color(open ? t.foreground : t.muted).child(row.slug))
      .when(row.legacy, (el) => el.child(badge("legacy", "muted", t)))
      .when(Boolean(row.error), (el) => el.child(badge("error", "blocked", t)))
      .child(badge(row.look.word, row.look.tone, t));
    const detail = row.error ? row.error.message : row.legacy ? "no ticket graph" : progressText(row.progress);
    return v_flex().id(`plan-${row.path}`).px(8).py(5).rounded(6).gap(1)
      .when(open, (el) => el.cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => onPick(row.path, cx)))
      .when(row.path === selected, (el) => el.bg(t.raised))
      .child(line)
      .child(div().text_size(11).truncate().text_color(row.error ? t.blocked : t.dim).child(detail));
  }));
}
