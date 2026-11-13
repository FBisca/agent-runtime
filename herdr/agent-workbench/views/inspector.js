// The one inspector: what the selected ticket delivers, what blocks it, its state, whether it is verified, its
// attempts and its gates. A value the answer does not carry is said to be unknown, never filled in.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { badge, chip, colorOf, control, section } from "../ui.js";

const when = (iso) => (iso ? String(iso).replace("T", " ").slice(0, 16) : "—");

function attemptRow(a, t, onOpen) {
  return v_flex().gap(1).py(2)
    .child(h_flex().gap(8).items_center()
      .child(div().text_size(12).font_semibold().child(a.taskId))
      .child(div().text_size(11).text_color(t.muted).child(a.kind ?? "attempt"))
      .child(div().flex_1().min_w(0).text_size(11).text_color(t.dim).truncate().child(a.status ?? "unknown"))
      .when(a.latest, (el) => el.child(badge("latest", "working", t)))
      .child(div().flex_none().text_size(11).text_color(a.claimed ? t.foreground : t.dim).child(a.claimed ? "claimed" : "not claimed")))
    .child(h_flex().gap(8).items_center()
      .child(div().flex_1().min_w(0).text_size(11).text_color(t.dim)
        .child(`${when(a.startedAt)} → ${when(a.finishedAt)} · terminal ${a.address ?? "unknown"} · ${a.terminal.word}`))
      .when(Boolean(a.terminal.open), (el) => el.child(control(`open-${a.taskId}`, "Open", (_e, cx) => onOpen(a.terminal.open, cx), t))))
    .when(Boolean(a.terminal.why), (el) => el.child(div().text_size(10).text_color(t.dim).child(a.terminal.why)));
}

function gateRow(g, t) {
  return h_flex().gap(8).items_start().text_size(12)
    .child(div().w(34).flex_none().text_color(t.muted).child(g.scenario))
    .child(div().w(52).flex_none().text_color(t.dim).child(g.phase ?? ""))
    .child(div().flex_none().font_semibold().text_color(colorOf(g.tone, t)).child(g.result ?? "unknown"))
    .child(div().flex_1().min_w(0).whitespace_normal().text_color(t.muted).child(g.reason ?? ""));
}

export function inspectorView(view, ctx) {
  const { t, onBack, onOpen } = ctx;
  const none = (text) => div().text_size(12).text_color(t.dim).child(text);
  return v_flex().id("inspector").gap(12).p(12)
    .when(Boolean(onBack), (el) => el.child(control("inspector-back", "← tickets", (_e, cx) => onBack(cx), t)))
    .child(h_flex().gap(8).items_center()
      .child(div().text_size(15).font_semibold().child(view.id))
      .child(badge(view.state.word, view.state.tone, t))
      .child(badge(view.verified.word, view.verified.tone, t)))
    .child(div().whitespace_normal().text_color(t.foreground).child(view.delivers))
    .when(Boolean(view.reason), (el) => el.child(div().whitespace_normal().text_color(t.muted).child(view.reason)))
    .when(Boolean(view.pr), (el) => el.child(div().text_size(11).text_color(t.dim).truncate().child(view.pr)))
    .child(section("Blocked by", t, view.blockers.length
      ? h_flex().flex_wrap().gap(4).children(view.blockers.map((b) => chip(b, t)))
      : none("nothing blocks it")))
    .when(view.dependents.length > 0, (el) => el.child(section("Blocks", t,
      div().text_size(12).text_color(t.muted).child(view.dependents.join(", ")))))
    .when(Boolean(view.measures), (el) => el.child(section("Measures", t, ...view.measures.map((m) =>
      h_flex().gap(8).text_size(12)
        .child(div().w(96).flex_none().text_color(t.dim).child(m.label))
        .child(div().flex_1().min_w(0).whitespace_normal().text_color(m.text === "unknown" ? t.dim : t.foreground).child(m.text))))))
    .child(section("Attempts", t, ...(view.attempts === null
      ? [none("this agent-orchestrator does not report attempts")]
      : view.attempts.length ? view.attempts.map((a) => attemptRow(a, t, onOpen)) : [none("none yet")])))
    .child(section("Gates", t, ...(view.gates === null
      ? [none("this agent-orchestrator does not report gates")]
      : view.gates.length ? view.gates.map((g) => gateRow(g, t)) : [none("none yet")])));
}
