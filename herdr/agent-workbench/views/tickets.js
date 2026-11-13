// The tickets: layers side by side with the inspector at full width, one compact column when narrow. A layer is the
// orchestrator's topological layer; a chip is a blocked-by edge, so the list is the graph.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { compactRow } from "../model.js";
import { chip, colorOf, control, glyph } from "../ui.js";

// One agent of a ticket: the ledger's name and role, Herdr's status while live, and Open only at its exact address.
function workerLine(ticket, w, ctx) {
  const { t, onOpen } = ctx;
  return h_flex().id(`worker-${ticket.id}-${w.name}`).gap(8).items_center().pl(52).text_size(11)
    .child(div().flex_none().text_color(t.foreground).child(w.name))
    .child(div().flex_none().text_color(t.dim).child(w.role ?? "agent"))
    .child(div().flex_1().min_w(0).truncate().text_color(w.state === "live" ? t.working : t.dim)
      .child(w.state === "live" ? (w.status ?? "live") : w.word))
    .when(Boolean(w.open), (el) => el.child(control(`open-${ticket.id}-${w.name}`, "Open", (_e, cx) => onOpen(w.open, cx), t)));
}

function row(ticket, ctx) {
  const { t, selected, kin, onSelect } = ctx;
  const related = kin.blockers.includes(ticket.id) ? "blocks the selection" : kin.dependents.includes(ticket.id) ? "waits on the selection" : null;
  const line = h_flex().id(`ticket-${ticket.id}`).gap(8).px(8).py(4).rounded(6).items_center().cursor_pointer()
    .hover((s) => s.bg(t.hover))
    .when(Boolean(related), (el) => el.bg(t.raised))
    .when(ticket.id === selected, (el) => el.bg(t.selected))
    .on_click((_e, cx) => onSelect(ticket.id, cx))
    .child(glyph(ticket.look, t))
    .child(div().w(30).flex_none().text_size(12).text_color(t.muted).child(ticket.id))
    .child(div().flex_1().min_w(0).truncate()
      .text_color(ticket.look.key === "done" || ticket.look.key === "todo" ? t.muted : t.foreground).child(ticket.summary))
    .when(Boolean(related), (el) => el.child(div().flex_none().text_size(10).text_color(t.dim).child(related)))
    .when(ticket.chips.length > 0, (el) => el.child(h_flex().flex_none().gap(3).items_center()
      .child(div().text_size(11).text_color(t.dim).child("←")).children(ticket.chips.map((c) => chip(c, t)))))
    .child(div().flex_none().text_size(11).text_color(colorOf(ticket.verified.tone, t)).child(ticket.verified.word));
  return ticket.workers.length ? v_flex().gap(1).child(line).children(ticket.workers.map((w) => workerLine(ticket, w, ctx))) : line;
}

/** Layers, each with its rows, for the full-width layout. */
export function layeredList(layers, ctx) {
  const { t } = ctx;
  return v_flex().id("ticket-layers").gap(6).p(8).children(layers.map((layer) =>
    h_flex().id(`layer-${layer.layer}`).gap(8).items_start()
      .child(div().w(54).flex_none().pt(5).text_size(10).font_semibold().text_color(t.dim).child(`LAYER ${layer.layer}`))
      .child(v_flex().flex_1().min_w(0).gap(1).children(layer.tickets.map((ticket) => row(ticket, ctx))))));
}

/** One line per ticket, no layers: the narrow layout. Selecting a row drills into the inspector. */
export function compactList(layers, ctx) {
  const { t, selected, onSelect } = ctx;
  return v_flex().id("ticket-compact").gap(1).p(8).children(layers.flatMap((layer) => layer.tickets).map((ticket) => {
    const line = div().id(`ticket-${ticket.id}`).px(8).py(5).rounded(6).truncate().cursor_pointer()
      .text_color(colorOf(ticket.look.key === "done" || ticket.look.key === "todo" ? "muted" : ticket.look.tone, t))
      .hover((s) => s.bg(t.hover)).when(ticket.id === selected, (el) => el.bg(t.selected))
      .on_click((_e, cx) => onSelect(ticket.id, cx))
      .child(compactRow(ticket));
    return ticket.workers.length ? v_flex().gap(1).child(line).children(ticket.workers.map((w) => workerLine(ticket, w, ctx))) : line;
  }));
}
