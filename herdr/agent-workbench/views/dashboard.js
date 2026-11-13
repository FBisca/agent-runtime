// A card's dashboard: the card's headline over home. The card's detail is drawn from the totals answer.
import { div } from "gpui-kit";
import { v_flex } from "gpui-base";

export function dashboardScreen(card, t) {
  return v_flex().id("dashboard").gap(4).p(12)
    .child(div().text_size(10).font_semibold().text_color(t.dim).child(`${card.label.toUpperCase()} · LAST 7 DAYS`))
    .child(div().text_size(24).font_semibold().text_color(card.value === "unknown" ? t.dim : t.foreground).child(card.value))
    .when(Boolean(card.note), (el) => el.child(div().text_size(11).text_color(t.dim).child(card.note)));
}
