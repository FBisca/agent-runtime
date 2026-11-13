// Home: the week's cards, Needs me (one row per plan), the active plans with their workers, the PRs to merge and the
// collapsed older line. A card opens its dashboard and a plan row opens the plan; both are the model's navigation.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { olderLabel, cardsPerRow } from "../model.js";
import { badge, control, progressText } from "../ui.js";

const heading = (text, t) => div().px(8).pt(6).text_size(10).font_semibold().text_color(t.dim).child(text.toUpperCase());

function card(c, onCard, t) {
  return v_flex().id(`card-${c.id}`).flex_1().min_w(0).gap(1).px(10).py(8).rounded(6).bg(t.raised)
    .cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => onCard(c.id, cx))
    .child(div().text_size(10).font_semibold().text_color(t.dim).truncate().child(c.label.toUpperCase()))
    .child(div().text_size(18).font_semibold().truncate().text_color(c.value === "unknown" ? t.dim : t.foreground).child(c.value))
    .child(div().text_size(10).truncate().text_color(t.dim).child(c.note ?? " "));
}

// Rows of cards, not a wrap: the model says how many fit, so a short last row keeps its cards' width.
function cards(list, layout, onCard, t) {
  const per = cardsPerRow(layout);
  const rows = [];
  for (let i = 0; i < list.length; i += per) rows.push(list.slice(i, i + per));
  return v_flex().id("cards").gap(6).px(8).pt(6).children(rows.map((row, r) =>
    h_flex().id(`cards-${r}`).gap(6).children([
      ...row.map((c) => card(c, onCard, t)),
      ...Array(per - row.length).fill(0).map(() => div().flex_1().min_w(0)),
    ])));
}

function needsRow(n, onPlan, t) {
  return v_flex().id(`needs-${n.plan}`).px(8).py(5).rounded(6).gap(2).bg(t.raised)
    .when(Boolean(n.path), (el) => el.cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => onPlan(n.path, cx)))
    .child(h_flex().gap(8).items_center()
      .child(div().flex_1().min_w(0).truncate().font_semibold().child(n.plan))
      .child(badge(String(n.items.length), "blocked", t)))
    .children(n.items.map((item, i) => v_flex().id(`needs-${n.plan}-${i}`).gap(0)
      .child(div().text_size(12).text_color(t.foreground).child(item.title))
      .child(div().text_size(11).text_color(t.muted).child(item.wayOut))));
}

// One worker of a plan: the ledger's name, role and ticket, Herdr's status while live, Open only at its exact address.
function workerLine(plan, w, onOpen, t) {
  return h_flex().id(`worker-${plan.slug}-${w.name}`).gap(8).items_center().pl(12).text_size(11)
    .child(div().flex_none().text_color(t.foreground).child(w.name))
    .child(div().flex_none().text_color(t.dim).child(w.role ?? "agent"))
    .child(div().flex_none().text_color(t.dim).child(w.ticket ?? ""))
    .child(div().flex_1().min_w(0).truncate().text_color(w.state === "live" ? t.working : t.dim)
      .child(w.state === "live" ? (w.status ?? "live") : w.word))
    .when(Boolean(w.open), (el) => el.child(control(`open-${plan.slug}-${w.name}`, "Open", (_e, cx) => onOpen(w.open, cx), t)));
}

function planRow(row, onPlan, onOpen, t) {
  const open = row.selectable;
  const line = h_flex().gap(8).items_center()
    .child(div().flex_1().min_w(0).truncate().font_semibold().text_color(open ? t.foreground : t.muted).child(row.slug))
    .when(row.legacy, (el) => el.child(badge("legacy", "muted", t)))
    .when(Boolean(row.error), (el) => el.child(badge("error", "blocked", t)))
    .child(badge(row.look.word, row.look.tone, t));
  const detail = row.error ? row.error.message : row.legacy ? "no ticket graph" : progressText(row.progress);
  return v_flex().id(`plan-${row.path}`).px(8).py(5).rounded(6).gap(1)
    .child(v_flex().gap(1)
      .when(open, (el) => el.cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => onPlan(row.path, cx)))
      .child(line)
      .child(div().text_size(11).truncate().text_color(row.error ? t.blocked : t.dim).child(detail)))
    .children((row.workers ?? []).map((w) => workerLine(row, w, onOpen, t)));
}

/** `home` is the model's `homeView`; `handlers` are `{onCard, onPlan, onOlder, onOpen}`. */
export function homeScreen(home, olderOpen, layout, handlers, t) {
  const { onCard, onPlan, onOlder, onOpen } = handlers;
  // A PR row opens its plan; the row carries the plan's name, so its path is found among the plans listed.
  const pathOf = (slug) => [...home.active, ...home.older.rows].find((r) => r.slug === slug && r.selectable)?.path ?? null;
  return v_flex().id("home").gap(2).p(8)
    .when(Boolean(home.cards), (el) => el.child(cards(home.cards, layout, onCard, t)))
    .when(home.needs.length > 0, (el) => el.child(heading("Needs me", t)).children(home.needs.map((n) => needsRow(n, onPlan, t))))
    .child(heading("Plans", t))
    .children(home.active.map((row) => planRow(row, onPlan, onOpen, t)))
    .when(home.awaitingMerge.length > 0, (el) => el.child(heading("To merge", t)).children(home.awaitingMerge.map((m, i) =>
      h_flex().id(`merge-${i}`).gap(8).px(8).py(4).rounded(6).items_center().text_size(12)
        .when(Boolean(pathOf(m.plan)), (e) => e.cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => onPlan(pathOf(m.plan), cx)))
        .child(div().flex_none().font_semibold().child(m.plan))
        .child(div().flex_none().text_color(t.muted).child(m.ticket))
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(m.pr ?? "")))))
    .when(home.older.count > 0, (el) => el
      .child(div().id("older").px(8).py(5).rounded(6).cursor_pointer().text_size(11).text_color(t.dim)
        .hover((s) => s.bg(t.hover)).on_click((_e, cx) => onOlder(cx)).child(olderLabel(home.older.count, olderOpen)))
      .children((olderOpen ? home.older.rows : []).map((row) => planRow(row, onPlan, onOpen, t))));
}
