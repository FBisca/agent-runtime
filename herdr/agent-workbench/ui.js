// The look: every colour comes from the theme (`done` verified, `working` in progress, `blocked` attention, dim to do),
// and a glyph and a word always carry the state too, so no state is told by colour alone.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { theme } from "herdr";

export function colorOf(tone, t = theme()) {
  return { done: t.done, working: t.working, blocked: t.blocked }[tone] ?? t.dim;
}

export const glyph = (look, t) => div().w(14).flex_none().text_color(colorOf(look.tone, t)).child(look.glyph);

/** A small outlined word: a state, a flag. */
export const badge = (text, tone, t) =>
  div().flex_none().px(6).py(1).rounded(4).text_size(10).font_semibold().border_1()
    .border_color(colorOf(tone, t)).text_color(colorOf(tone, t)).child(text);

/** A blocked-by chip: `T1 ✓`. */
export const chip = (chipModel, t) =>
  h_flex().flex_none().gap(3).px(5).py(1).rounded(4).items_center().text_size(11).bg(t.raised).text_color(t.muted)
    .child(chipModel.id)
    .child(div().text_color(colorOf(chipModel.look.tone, t)).child(chipModel.look.glyph));

/** One segment per ticket: done, in progress, blocked, then the rest dimmed. */
export function progressBar(progress, t) {
  if (!progress || !progress.total) return div();
  const rest = progress.total - progress.done - progress.working - progress.blocked;
  const segments = [
    ...Array(progress.done).fill(t.done),
    ...Array(progress.working).fill(t.working),
    ...Array(progress.blocked).fill(t.blocked),
    ...Array(Math.max(rest, 0)).fill(t.dim),
  ];
  return h_flex().h(4).gap(2).children(segments.map((color, i) =>
    div().flex_1().h(4).rounded(2).bg(color).when(i >= segments.length - Math.max(rest, 0), (el) => el.opacity(0.35))));
}

export const progressText = (progress) => {
  if (!progress) return "";
  const parts = [`${progress.done}/${progress.total} done`];
  if (progress.working) parts.push(`${progress.working} in progress`);
  if (progress.blocked) parts.push(`${progress.blocked} blocked`);
  return parts.join(" · ");
};

export const section = (title, t, ...children) =>
  v_flex().gap(3).child(div().text_size(10).font_semibold().text_color(t.dim).child(title.toUpperCase())).children(children);

/** A clickable text control. */
export const control = (id, label, onClick, t, active = false) =>
  div().id(id).flex_none().px(6).py(2).rounded(5).text_size(11).cursor_pointer()
    .text_color(active ? t.foreground : t.dim).when(active, (el) => el.bg(t.raised))
    .hover((s) => s.bg(t.hover).text_color(t.foreground)).on_click(onClick).child(label);
