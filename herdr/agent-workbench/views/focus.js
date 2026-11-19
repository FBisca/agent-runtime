// The FOCUS section: rastro's report for the shown space, in the workbench's card language (the agreed prototype).
// Home draws it below the week's cards; the Focus tab (props `{ focus: true }`) draws it full-width as its whole subject.
// A refusal is one dim line on home; the Focus tab shows it as a notice instead (the model's `focusNotice`).
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { badge } from "../ui.js";

const FOCUS_TONE = { late: "blocked", wait: "working", move: "done" };

const heading = (text, t, extra = null) => h_flex().gap(8).items_center().px(4).pt(10).pb(2)
  .child(div().text_size(10).font_semibold().text_color(t.dim).child(text.toUpperCase()))
  .when(Boolean(extra), (el) => el.child(extra));

const pill = (text, t) => div().px(6).rounded(8).text_size(10).font_semibold().bg(t.raised).text_color(t.muted).child(text);

export function focusSection(f, t) {
  if (f.error) {
    return div().id("focus-error").px(4).py(6).text_size(11).text_color(t.dim).truncate()
      .child(`Focus: ${f.error.message}`);
  }
  const color = (tone) => (tone ? t[FOCUS_TONE[tone]] : t.dim);
  // Hierarchy: the title owns its line; the reason carries the tone; the citation stays dim and small.
  const hero = f.hero && v_flex().id("focus-hero").gap(2).p(12).rounded(10).bg(t.surface).border_1().border_color(t.border)
    .child(h_flex().gap(10)
      .child(div().w(3).h(56).flex_none().rounded(2).bg(color(f.hero.tone)))
      .child(v_flex().flex_1().min_w(0).gap(3)
        .child(div().text_size(10).font_semibold().text_color(t.dim).child("PUT YOUR FOCUS HERE"))
        .child(div().text_size(15).font_semibold().whitespace_normal().text_color(t.foreground).child(f.hero.title))
        .child(div().text_size(12).text_color(color(f.hero.tone)).child(f.hero.why))
        .child(div().text_size(11).text_color(t.dim).truncate().child(f.hero.cite))));
  const rows = f.rows.map((row, i) => h_flex().id(`focus-row-${i}`).gap(10).items_center().pr(10).rounded(8)
    .bg(t.surface).border_1().border_color(t.border).overflow_hidden()
    .child(div().w(3).h(44).flex_none().bg(color(row.tone)))
    .child(div().w(24).h(24).flex_none().rounded_full().bg(t.raised).text_color(color(row.tone)).text_size(12).font_semibold()
      .flex().items_center().justify_center().child(row.glyph))
    .child(v_flex().flex_1().min_w(0).py(6).gap(2)
      .child(div().text_size(13).font_semibold().truncate().text_color(t.foreground).child(row.title))
      .child(h_flex().gap(6).items_center()
        .child(div().flex_1().min_w(0).text_size(11).text_color(t.dim).truncate().child(row.sub))
        .child(div().flex_none().text_size(11).font_semibold().text_color(color(row.tone)).child(row.tag)))));
  const chips = h_flex().gap(6).items_center()
    .when(Boolean(f.overdue), (el) => el.child(badge(f.overdue, "blocked", t)))
    .when(Boolean(f.asOf), (el) => el.child(pill(f.asOf, t)));
  return v_flex().id("focus").gap(6)
    .child(heading("Focus", t, chips))
    .when(Boolean(hero), (el) => el.child(hero))
    .children(rows)
    .when(!f.hero && f.rows.length === 0, (el) => el.child(div().id("focus-notice").px(4).py(6).text_size(12).text_color(t.dim).child(f.notice ?? "")))
    .when(Boolean(f.queue) || f.gaps.length > 0, (el) => el.child(h_flex().gap(8).items_center().flex_wrap().px(4).pt(2)
      .when(Boolean(f.queue), (e) => e.child(div().id("focus-queue").px(6).py(1).rounded(8).text_size(11).bg(t.raised).text_color(t.muted).child(f.queue)))
      .children(f.gaps.map((gap, i) => div().id(`focus-gap-${i}`).text_size(11).text_color(t.working).child(gap.text)))));
}

/** The Focus tab's whole screen: the report, full width. */
export const focusScreen = (f, t) => v_flex().id("focus-screen").gap(6).p(12).child(focusSection(f, t));
