// Home: the week's cards with a picture of each dashboard, Needs me (a row per item with its glyph and action), the active
// plans as cards (progress, recency, live agents, the others folded, PRs to merge) and the collapsed older line. A card
// opens its dashboard and a plan opens the plan; both are the model's navigation. Every word and number is the model's.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { olderLabel, cardsPerRow, duration } from "../model.js";
import { badge, control, progressBar, progressText } from "../ui.js";
import { chip, pictureView } from "./charts.js";

// One height for every card, so a row of five reads as a row whatever each picture is.
const CARD_H = 124;

const heading = (text, t, extra = null) => h_flex().gap(8).items_center().px(4).pt(10).pb(2)
  .child(div().text_size(10).font_semibold().text_color(t.dim).child(text.toUpperCase()))
  .when(Boolean(extra), (el) => el.child(extra));

const pill = (text, t) => div().px(6).rounded(8).text_size(10).font_semibold().bg(t.raised).text_color(t.muted).child(text);

function card(c, onCard, t) {
  return v_flex().id(`card-${c.id}`).relative().flex_1().min_w(0).h(CARD_H).justify_between().p(12).rounded(10).bg(t.surface)
    .border_1().border_color(t.border)
    .cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => onCard(c.id, cx))
    .child(v_flex().gap(1)
      .child(div().text_size(10).font_semibold().text_color(t.dim).truncate().child(c.label.toUpperCase()))
      .child(div().text_size(22).font_semibold().truncate().text_color(c.value === "unknown" ? t.dim : t.foreground).child(c.value))
      .child(div().text_size(10).truncate().text_color(t.dim).child(c.note ?? (c.id === "first_pass" ? "merged without a fix round" : " "))))
    .child(pictureView(c.picture, t, c.id === "ticket_time" ? duration : String));
}

// Rows of cards, not a wrap: the model says how many fit, so a short last row keeps its cards' width.
function cards(list, layout, onCard, t) {
  const per = cardsPerRow(layout);
  const rows = [];
  for (let i = 0; i < list.length; i += per) rows.push(list.slice(i, i + per));
  return v_flex().id("cards").gap(8).children(rows.map((row, r) =>
    h_flex().id(`cards-${r}`).gap(8).children([
      ...row.map((c) => card(c, onCard, t)),
      ...Array(per - row.length).fill(0).map(() => div().flex_1().min_w(0)),
    ])));
}

// One compact row per item: its glyph, who and what, the way out, and the action that opens the plan.
function needsRows(needs, onPlan, t) {
  const items = needs.flatMap((n) => n.items.map((item) => ({ ...item, plan: n.plan, path: n.path })));
  return v_flex().gap(4).children(items.map((item, i) => h_flex().id(`need-${i}`).gap(10).items_center().pr(10).rounded(8)
    .bg(t.surface).border_1().border_color(t.border).overflow_hidden()
    .child(div().w(3).h(44).bg(t.blocked))
    .child(div().w(24).h(24).flex_none().rounded_full().bg(t.raised).text_color(t.blocked).text_size(12).font_semibold()
      .flex().items_center().justify_center().child(item.glyph))
    .child(v_flex().flex_1().min_w(0).py(6).gap(1)
      .child(h_flex().gap(6).items_center().text_size(12)
        .child(div().font_semibold().truncate().child(item.plan))
        .when(Boolean(item.ticket), (el) => el.child(pill(item.ticket, t)))
        .child(div().text_color(t.foreground).truncate().child(item.title)))
      .child(div().text_size(11).text_color(t.dim).truncate().child(item.wayOut)))
    .when(Boolean(item.path), (el) => el.child(chip(item.action, t, (_e, cx) => onPlan(item.path, cx), `need-act-${i}`)))));
}

// A worker of a plan, short: the plan's own name is not repeated in it.
const shortName = (slug, name) => (name.startsWith(`${slug}-`) ? name.slice(slug.length + 1) : name);

function planCard(row, handlers, t) {
  const { onPlan, onOpen, onLink } = handlers;
  const live = row.live ?? [];
  const idle = row.idle ?? 0;
  const prs = row.prs ?? [];
  const detail = row.error ? row.error.message : row.legacy ? "no ticket graph" : progressText(row.progress);
  return v_flex().id(`plan-${row.path}`).gap(8).p(12).rounded(10).bg(t.surface).border_1().border_color(t.border)
    .child(h_flex().gap(8).items_center()
      .when(row.selectable, (el) => el.cursor_pointer().on_click((_e, cx) => onPlan(row.path, cx)))
      .child(div().flex_1().min_w(0).truncate().font_semibold().text_color(row.selectable ? t.foreground : t.muted).child(row.slug))
      .when(row.legacy, (el) => el.child(badge("legacy", "muted", t)))
      .when(Boolean(row.error), (el) => el.child(badge("error", "blocked", t)))
      .child(badge(row.look.word, row.look.tone, t)))
    .when(Boolean(row.progress), (el) => el.child(progressBar(row.progress, t)))
    .child(h_flex().gap(8).text_size(11).text_color(row.error ? t.blocked : t.dim)
      .child(div().truncate().child(detail))
      .when(Boolean(row.recency), (el) => el.child(div().child("·")).child(div().flex_none().child(row.recency))))
    .when(live.length > 0, (el) => el.child(h_flex().flex_wrap().gap(6).children(live.map((w) => h_flex().id(`live-${row.slug}-${w.name}`)
      .gap(6).items_center().pl(8).pr(4).py(2).rounded(12).bg(t.raised).text_size(11)
      .child(div().w(7).h(7).rounded_full().bg(t.working))
      .child(div().text_color(t.foreground).child(shortName(row.slug, w.name)))
      .child(div().text_color(t.dim).child(w.status ?? "live"))
      .when(Boolean(w.open), (e) => e.child(control(`open-${row.slug}-${w.name}`, "Open", (_e, cx) => onOpen(w.open, cx), t)))))))
    .when(idle > 0, (el) => el.child(div().id(`idle-${row.slug}`).text_size(11).text_color(t.dim)
      .child(`${live.length ? "+ " : ""}${idle} agent${idle === 1 ? "" : "s"} not live`)))
    .when(prs.length > 0, (el) => el.child(h_flex().flex_wrap().gap(6).items_center()
      .child(div().text_size(10).text_color(t.dim).child("TO MERGE"))
      // A PR with no GitHub link is shown and not opened.
      .children(prs.map((m, i) => (m.link
        ? chip(`${m.ticket} ${m.linkText}`, t, (_e, cx) => onLink(m.link, cx), `pr-${row.slug}-${i}`)
        : div().id(`pr-${row.slug}-${i}`).px(6).py(1).text_size(11).text_color(t.dim).child(`${m.ticket} ${m.linkText}`))))));
}

/** `home` is the model's `homeView`; `handlers` are `{onCard, onPlan, onOlder, onOpen, onLink}`. */
export function homeScreen(home, olderOpen, layout, handlers, t) {
  const needCount = home.needs.reduce((total, n) => total + n.items.length, 0);
  return v_flex().id("home").gap(6).p(12)
    .when(Boolean(home.cards), (el) => el.child(cards(home.cards, layout, handlers.onCard, t)))
    .when(needCount > 0, (el) => el.child(heading("Needs me", t, badge(`${needCount} waiting`, "blocked", t)))
      .child(needsRows(home.needs, handlers.onPlan, t)))
    .child(heading("Plans", t, pill(`${home.active.length} active`, t)))
    .child(v_flex().id("plans").gap(8).children(home.active.map((row) => planCard(row, handlers, t))))
    .when(home.older.count > 0, (el) => el
      .child(div().id("older").px(4).py(6).rounded(6).cursor_pointer().text_size(11).text_color(t.dim)
        .hover((s) => s.text_color(t.foreground)).on_click((_e, cx) => handlers.onOlder(cx)).child(olderLabel(home.older.count, olderOpen)))
      .when(olderOpen, (e) => e.child(v_flex().id("older-plans").gap(8).children(home.older.rows.map((row) => planCard(row, handlers, t))))));
}
