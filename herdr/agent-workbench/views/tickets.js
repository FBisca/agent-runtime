// An open plan's tickets as cards, by layer: each card is a ticket's state, what it delivers, what it waits on, its measures
// and phase split, its agents and its PR. A card selects its ticket for the inspector. `layers` is the model's `cardLayers`.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { colorOf } from "../ui.js";
import { bar, chip, labelText } from "./charts.js";

const pill = (text, t) => div().flex_none().px(6).rounded(8).text_size(10).font_semibold().bg(t.raised).text_color(t.muted).child(text);

// An agent's name without its plan: `alpha-t2-review` is `t2-review` on a card of alpha.
const shortName = (name) => name.replace(/^.*?-(t\d+(?:-review)?)$/, "$1");

function agentPill(ticket, w, ctx) {
  const { t, onOpen } = ctx;
  return h_flex().id(`agent-${ticket.id}-${w.name}`).gap(5).items_center().pl(7).pr(3).py(1).rounded(12).bg(t.raised).text_size(11)
    .child(div().w(7).h(7).rounded_full().bg(t.working))
    .child(div().child(shortName(w.name)))
    .child(div().text_color(t.dim).child(w.status ?? "live"))
    .when(Boolean(w.open), (el) => el.child(chip("Open", t, (_e, cx) => onOpen(w.open, cx), `open-${ticket.id}-${w.name}`)));
}

function card(ticket, ctx) {
  const { t, selected, kin, onSelect, onLink } = ctx;
  const tone = colorOf(ticket.look.tone, t);
  const idle = ticket.workers.filter((w) => w.state !== "live");
  // How the card stands to the selected ticket: the graph's edges, said in words.
  const related = kin.blockers.includes(ticket.id) ? "blocks the selection" : kin.dependents.includes(ticket.id) ? "waits on the selection" : null;
  return h_flex().id(`ticket-${ticket.id}`).flex_1().min_w(0).rounded(10).overflow_hidden().bg(t.surface).border_1()
    .border_color(ticket.id === selected ? t.foreground : t.border).cursor_pointer().when(Boolean(related), (el) => el.bg(t.raised)).hover((s) => s.bg(t.hover))
    .on_click((_e, cx) => onSelect(ticket.id, cx))
    .child(div().w(4).h_full().flex_none().bg(tone))
    .child(v_flex().flex_1().min_w(0).gap(6).p(10)
      .child(h_flex().gap(8).items_center()
        .child(div().text_color(tone).child(ticket.look.glyph))
        .child(div().font_semibold().child(ticket.id))
        .child(div().text_size(11).text_color(t.dim).child(ticket.look.word))
        .child(div().flex_1())
        .when(Boolean(related), (el) => el.child(div().text_size(10).text_color(t.dim).child(related)))
        .children([ticket.size, ticket.difficulty, ticket.risk ? `risk: ${ticket.risk}` : null].filter(Boolean).map((x) => pill(x, t))))
      .child(div().text_size(12).text_color(t.foreground).whitespace_normal().max_h(34).overflow_hidden().child(ticket.summary))
      .when(ticket.after.length > 0, (el) => el.child(h_flex().gap(4).items_center().text_size(10).text_color(t.dim).child(div().child("after"))
        .children(ticket.after.map((c) => h_flex().gap(3).px(5).rounded(4).bg(t.raised)
          .child(div().text_color(t.muted).child(c.id)).child(div().text_color(colorOf(c.look.tone, t)).child(c.look.glyph))))))
      .when(ticket.measures.length > 0, (el) => el.child(h_flex().flex_wrap().gap(4).children(ticket.measures.map((x) => pill(x, t)))))
      .when(Boolean(ticket.phases), (el) => el.child(bar(ticket.phases.map((p) => ({ key: p.step, value: p.seconds, slot: p.slot })), t, `phases-${ticket.id}`)))
      .child(h_flex().flex_wrap().gap(6).items_center()
        .children(ticket.live.map((w) => agentPill(ticket, w, ctx)))
        .when(ticket.idle > 0, (el) => el.child(div().id(`idle-${ticket.id}`).text_size(10).text_color(t.dim)
          .tooltip(idle.map((w) => `${shortName(w.name)} · ${w.word}`).join("\n")).child(`${ticket.idle} not live`)))
        .child(div().flex_1())
        .when(Boolean(ticket.linkText && ticket.link), (el) => el.child(chip(ticket.linkText, t, (_e, cx) => onLink(ticket.link, cx), `pr-${ticket.id}`)))
        .when(Boolean(ticket.linkText && !ticket.link), (el) => el.child(div().text_size(11).text_color(t.dim).child(ticket.linkText)))));
}

/** The layers, each a heading and its cards `per` to a row (a short last row keeps its cards' width). `ctx` is
 *  `{t, selected, kin, onSelect, onOpen, onLink}` (`kin` from `relatives`). */
export function ticketCards(layers, per, ctx) {
  const { t } = ctx;
  return v_flex().id("ticket-layers").gap(12).children(layers.map((layer) => {
    const rows = [];
    for (let i = 0; i < layer.tickets.length; i += per) rows.push(layer.tickets.slice(i, i + per));
    return v_flex().id(`layer-${layer.layer}`).gap(6)
      .child(h_flex().gap(8).items_center().child(labelText(`Layer ${layer.layer + 1}`, t)).child(pill(`${layer.done}/${layer.tickets.length} done`, t)))
      .children(rows.map((row, i) => h_flex().id(`layer-${layer.layer}-${i}`).gap(8).items_start().children([
        ...row.map((ticket) => card(ticket, ctx)),
        ...Array(per - row.length).fill(0).map(() => div().flex_1().min_w(0)),
      ])));
  }));
}
