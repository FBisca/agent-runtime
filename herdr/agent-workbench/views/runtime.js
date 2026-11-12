// The Runtime section: live agent terminals that no attempt of the loaded plans names, as the tree Herdr reports. It
// belongs to no plan and no ticket, so it makes up neither.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { control, section } from "../ui.js";

function agentRow(a, ctx) {
  const { t, onOpen } = ctx;
  return h_flex().id(`runtime-${a.address}`).gap(8).items_center().py(2).pl(a.depth * 16)
    .child(div().flex_none().text_color(t.dim).child(a.depth ? "└" : "●"))
    .child(div().flex_none().text_size(12).font_semibold().child(a.address))
    .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(a.name))
    .when(Boolean(a.status), (el) => el.child(div().flex_none().text_size(11).text_color(t.dim).child(a.status)))
    .when(a.depth === 0 && Boolean(a.parent), (el) => el.child(div().flex_none().text_size(11).text_color(t.dim).child("from " + a.parent)))
    .child(control(`open-runtime-${a.address}`, "Open", (_e, cx) => onOpen(a.open, cx), t));
}

/** Nothing is drawn when there is no unplanned live agent. */
export function runtimeView(agents, ctx) {
  if (!agents.length) return div();
  return v_flex().id("runtime").p(8).child(section("Runtime", ctx.t,
    div().text_size(11).text_color(ctx.t.dim).child("live agents no attempt of these plans names"),
    ...agents.map((a) => agentRow(a, ctx))));
}
