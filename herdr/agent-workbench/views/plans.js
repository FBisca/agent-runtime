// The plan list: every plan of the project, YAML with its state and progress, Markdown marked legacy.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { badge, progressText } from "../ui.js";

const planRow = (row, selected, onPick, t) => {
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
};

const heading = (text, t) => div().px(8).pt(6).text_size(10).font_semibold().text_color(t.dim).child(text.toUpperCase());

/** Needs me, the active plans, then the collapsed older line: what the mod opens on. */
export function planList(sections, olderOpen, selected, handlers, t) {
  const { onPick, onOlder } = handlers;
  const { needsMe, active } = sections;
  const older = sections.olderLine(olderOpen);
  return v_flex().id("plan-list").gap(2).p(8)
    .when(needsMe.length > 0, (el) => el.child(heading("Needs me", t)).children(needsMe.map((n, i) =>
      v_flex().id(`needs-${i}`).px(8).py(5).rounded(6).gap(1).bg(t.raised)
        .when(Boolean(n.path), (e) => e.cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => onPick(n.path, cx)))
        .child(h_flex().gap(8).items_center()
          .child(div().flex_1().min_w(0).truncate().font_semibold().child(n.plan))
          .child(badge(n.title, "blocked", t)))
        .child(div().text_size(11).text_color(t.muted).child(n.wayOut)))))
    .child(heading("Plans", t))
    .children(active.map((row) => planRow(row, selected, onPick, t)))
    .when(Boolean(older), (el) => el
      .child(div().id("older").px(8).py(5).rounded(6).cursor_pointer().text_size(11).text_color(t.dim)
        .hover((s) => s.bg(t.hover)).on_click((_e, cx) => onOlder(cx)).child(older.label))
      .children(older.rows.map((row) => planRow(row, selected, onPick, t))));
}
