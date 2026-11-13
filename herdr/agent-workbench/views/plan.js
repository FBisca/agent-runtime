// An open plan's body: its five cards (each with a picture of its measure), the timeline or phase split and its tickets as
// cards by layer. Every word, number and slot is the model's (`planCards`, `planTimeline`, `cardLayers`); this only lays
// them out at the width the model says fits (`planColumns`).
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { colorOf } from "../ui.js";
import { duration } from "../model.js";
import { pictureView, ring } from "./charts.js";
import { timelineView } from "./timeline.js";
import { ticketCards } from "./tickets.js";

// One height for every card, as home's: a row of five reads as a row whatever each picture is.
const CARD_H = 124;

// A picture of states (the plan's progress) is in the state's colour; one of data is the palette's, drawn by `pictureView`.
const picture = (c, t) => (c.picture?.tone
  ? div().absolute().right(10).top(10).child(ring(c.picture.value, t, 46, div(), 6, colorOf(c.picture.tone, t)))
  : pictureView(c.picture, t, c.id === "wall" ? duration : String));

function card(c, t) {
  return v_flex().id(`plan-card-${c.id}`).relative().flex_1().min_w(0).h(CARD_H).justify_between().p(12).rounded(10).bg(t.surface)
    .border_1().border_color(t.border)
    .child(v_flex().gap(1)
      .child(div().text_size(10).font_semibold().text_color(t.dim).truncate().child(c.label.toUpperCase()))
      .child(div().text_size(22).font_semibold().truncate().text_color(c.value === "unknown" ? t.dim : t.foreground).child(c.value))
      .child(div().text_size(10).truncate().text_color(t.dim).child(c.note ?? " ")))
    .child(picture(c, t));
}

// Rows of cards, not a wrap: the model says how many fit, so a short last row keeps its cards' width.
function cards(list, per, t) {
  const rows = [];
  for (let i = 0; i < list.length; i += per) rows.push(list.slice(i, i + per));
  return v_flex().id("plan-cards").gap(8).children(rows.map((row, r) => h_flex().id(`plan-cards-${r}`).gap(8).children([
    ...row.map((c) => card(c, t)),
    ...Array(per - row.length).fill(0).map(() => div().flex_1().min_w(0)),
  ])));
}

/** `view` is `{cards, timeline, layers, columns}` from the model; `ctx` is `{t, selected, kin, onSelect, onOpen, onLink}`. */
export function planBody(view, ctx) {
  const { t } = ctx;
  return v_flex().id("plan").gap(14).p(12)
    .child(cards(view.cards, view.columns.cards, t))
    .when(Boolean(view.timeline), (el) => el.child(timelineView(view.timeline, t)))
    .child(ticketCards(view.layers, view.columns.tickets, ctx));
}
