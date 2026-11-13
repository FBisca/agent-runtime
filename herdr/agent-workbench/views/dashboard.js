// A card's dashboard: the card's headline over its detail. `detail` is the model's `dashboardView`: bars (wide only)
// above the lists of the tickets behind them.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";

const CELLS = 24;

// A bar is a row of equal cells like the timeline's lanes; the largest value fills them all.
function bar(b, max, t) {
  const filled = max > 0 && typeof b.value === "number" ? Math.max(1, Math.round((b.value / max) * CELLS)) : 0;
  return h_flex().gap(8).items_center().text_size(11)
    .child(div().w(110).flex_none().truncate().text_color(t.muted).child(b.label))
    .child(h_flex().flex_1().min_w(0).children(Array.from({ length: CELLS }, (_, i) => div().flex_1().h(8).bg(i < filled ? t.working : t.raised))))
    .child(div().flex_none().text_color(t.dim).child(b.text));
}

const list = (l, t) => v_flex().gap(2).pt(8)
  .child(div().text_size(10).font_semibold().text_color(t.dim).child(l.title.toUpperCase()))
  .children(l.rows.map((r) => h_flex().gap(8).text_size(11)
    .child(div().flex_none().text_color(t.foreground).child(r.label))
    .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(r.text))));

export function dashboardScreen(card, detail, t) {
  const max = Math.max(0, ...detail.bars.map((b) => (typeof b.value === "number" ? b.value : 0)));
  return v_flex().id("dashboard").gap(4).p(12)
    .child(div().text_size(10).font_semibold().text_color(t.dim).child(`${card.label.toUpperCase()} · LAST 7 DAYS`))
    .child(div().text_size(24).font_semibold().text_color(card.value === "unknown" ? t.dim : t.foreground).child(card.value))
    .when(Boolean(card.note), (el) => el.child(div().text_size(11).text_color(t.dim).child(card.note)))
    .when(detail.bars.length > 0, (el) => el.child(v_flex().id("bars").gap(3).pt(8).children(detail.bars.map((b) => bar(b, max, t)))))
    .children(detail.lists.map((l) => list(l, t)));
}
