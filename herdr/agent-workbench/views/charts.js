// The charts of a dashboard: each draws one chart object of the model's `dashboardView` (its numbers, its axis, its keys
// and the slots of the data palette) and decides nothing else. Colours come from `dataColor` in ui.js and the theme's
// neutrals; the state colours colour states only, so none is used here.
import { div, PathBuilder, Background } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { dataColor } from "../ui.js";
import { stepLook, duration } from "../model.js";

const GUTTER = 48;
const LABEL_W = 140;
const VALUE_W = 150;
// The slot of a chart that draws one series (the rate, the counts, the heat): violet.
const SINGLE = 1;

export const labelText = (text, t) => div().text_size(10).font_semibold().text_color(t.dim).child(text.toUpperCase());

/** A titled panel: the frame every chart sits in. */
export const panel = (title, body, t) => v_flex().gap(10).p(14).rounded(10).bg(t.surface).border_1().border_color(t.border)
  .child(labelText(title, t)).child(body);

// ---- pieces ----------------------------------------------------------------------------------------------------------

const dot = (color) => div().w(8).h(8).rounded_full().flex_none().bg(color);

const grid = (t) => [0, 50, 100].map((y) => window.paint_path(
  PathBuilder.stroke(1).dash_array([3, 4]).move_to("0%", `${y}%`).line_to("100%", `${y}%`).build(), t.border).absolute().size_full());

/** The y axis of a vertical chart: the model's words for its top, half and 0. */
const yAxis = (axis, t, h) => v_flex().w(GUTTER).flex_none().h(h).justify_between().items_end().pr(8).text_size(10).text_color(t.dim)
  .child(div().mt(-6).child(axis.labels[2])).child(div().child(axis.labels[1])).child(div().mb(-6).child(axis.labels[0]));

/** The x axis of a horizontal chart: its words under the scale, 0 at the left. */
const xAxis = (axis, t, before = LABEL_W, after = 0) => h_flex().gap(8).text_size(10).text_color(t.dim)
  .child(div().w(before).flex_none())
  .child(h_flex().flex_1().min_w(0).justify_between().border_t(1).border_color(t.border).pt(2)
    .child(div().child(axis.labels[0])).child(div().child(axis.labels[1])).child(div().child(axis.labels[2])))
  .when(after > 0, (el) => el.child(div().w(after).flex_none()));

const valueCell = (text, t, w = VALUE_W) => h_flex().w(w).flex_none().justify_end().text_color(t.dim).child(div().truncate().child(text));

/** A hatched fill: `bg` takes a colour only, so the pattern is painted as a path over the box. */
const hatched = (el, t) => el.relative().overflow_hidden().border_1().border_color(t.border)
  .child(window.paint_path(
    PathBuilder.fill().add_polygon([["0%", "0%"], ["100%", "0%"], ["100%", "100%"], ["0%", "100%"]], true).build(),
    Background.pattern_slash(t.dim, 2, 6)).absolute().size_full());

const legend = (items, t) => h_flex().flex_wrap().gap(12).children(items.map(({ name, color, hatch }) => h_flex().gap(5).items_center()
  .text_size(11)
  .child(div().w(10).h(10).rounded(3).map((el) => (hatch ? hatched(el, t) : el.bg(color))))
  .child(div().text_color(t.muted).child(name))));

/** A small outlined text control that clears a filter or opens a PR. */
export const chip = (text, t, onClick, id) => div().id(id).flex_none().px(6).py(1).rounded(4).text_size(11).bg(t.raised).text_color(t.muted)
  .cursor_pointer().hover((s) => s.bg(t.hover).text_color(t.foreground)).on_click(onClick).child(text);

// ---- a sparkline and a ring (no scale: the number beside them is the reading) ------------------------------------------

/** The week as a line: a gap where a day has no value, never a drop to 0. */
export function sparkline(values, t, h) {
  const real = values.filter((v) => v !== null);
  if (real.length < 2) return div().h(h);
  const max = Math.max(...real, 1e-9);
  const n = values.length;
  const at = (i, v) => [`${(i / (n - 1)) * 100}%`, `${100 - (v / max) * 90 - 5}%`];
  const color = dataColor(SINGLE, t);
  const runs = [];
  values.forEach((v, i) => {
    if (v === null) runs.push(null);
    else if (runs.length && runs.at(-1)) runs.at(-1).push(at(i, v));
    else runs.push([at(i, v)]);
  });
  const lines = runs.filter((run) => run && run.length > 1).map((run) => {
    let line = PathBuilder.stroke(2).move_to(...run[0]);
    for (const point of run.slice(1)) line = line.line_to(...point);
    return window.paint_path(line.build(), color).absolute().size_full();
  });
  const lone = values.flatMap((v, i) => (v !== null && values[i - 1] == null && values[i + 1] == null ? [at(i, v)] : []));
  return div().relative().w_full().h(h).children(lines)
    .children(lone.map(([x, y]) => div().absolute().left(x).top(y).ml(-3).mt(-3).w(6).h(6).rounded_full().bg(color)));
}

/** A ring: the share `frac` of a full turn, over a track. Arcs in percentages of a square box. */
export function ring(frac, t, size, center) {
  const R = 42;
  const at = (a) => [`${50 + R * Math.sin(a)}%`, `${50 - R * Math.cos(a)}%`];
  const track = PathBuilder.stroke(10).move_to(...at(0)).arc_to(`${R}%`, `${R}%`, 0, false, true, ...at(Math.PI))
    .arc_to(`${R}%`, `${R}%`, 0, false, true, ...at(2 * Math.PI - 1e-4)).build();
  const f = Math.max(0.001, Math.min(0.999, frac));
  const value = PathBuilder.stroke(10).move_to(...at(0)).arc_to(`${R}%`, `${R}%`, 0, f > 0.5, true, ...at(2 * Math.PI * f)).build();
  return div().relative().w(size).h(size).flex_none()
    .child(window.paint_path(track, t.raised).absolute().size_full())
    .child(window.paint_path(value, dataColor(SINGLE, t)).absolute().size_full())
    .child(v_flex().absolute().size_full().items_center().justify_center().child(center));
}

// ---- the charts of the model ---------------------------------------------------------------------------------------------

function stackedColumns(chart, ctx) {
  const { t, height, onFilter } = ctx;
  const { axis, days, series } = chart;
  const columns = h_flex().absolute().size_full().gap(8).items_end().children(days.map((day, i) => v_flex()
    .id(`day-${i}`).flex_1().h_full().justify_end().cursor_pointer().opacity(day.dim ? 0.35 : 1)
    .tooltip(`${day.label} · ${day.total}${day.parts.length ? ` — ${day.parts.map((p) => `${p.key} ${p.value}`).join(", ")}` : ""}`)
    .on_click((_e, cx) => onFilter(day.filter, cx))
    .child(v_flex().w_full().h(`${(day.total / axis.max) * 100}%`).gap(2)
      .children(day.parts.slice().reverse().map((p) => div().w_full().rounded(3).bg(dataColor(p.slot, t))
        .flex_basis(0).flex_grow(p.value))))));
  return v_flex().gap(8)
    .child(v_flex().gap(4)
      .child(h_flex().child(yAxis(axis, t, height)).child(div().flex_1().min_w(0).relative().h(height).children(grid(t)).child(columns)))
      .child(h_flex().child(div().w(GUTTER).flex_none()).child(h_flex().flex_1().gap(8).children(days.map((d, i) => div().flex_1()
        .text_center().text_size(10).text_color(i === days.length - 1 ? t.foreground : t.dim).child(d.label))))))
    .child(legend(series.map((s) => ({ name: s.key, color: dataColor(s.slot, t) })), t));
}

/** Horizontal bars on one scale, with its axis under them. */
function bars(chart, ctx) {
  const { t } = ctx;
  const { axis, rows } = chart;
  return v_flex().gap(6)
    .children(rows.map((r) => h_flex().gap(8).items_center().text_size(11)
      .child(div().w(LABEL_W).flex_none().truncate().text_color(t.muted).child(r.label))
      .child(div().flex_1().min_w(0).h(10).rounded(4).bg(t.raised)
        .child(div().h_full().rounded(4).bg(dataColor(SINGLE, t)).w(`${r.value > 0 ? Math.max(1, (r.value / axis.max) * 100) : 0}%`)))
      .child(valueCell(r.text, t, 60))))
    .child(xAxis(axis, t, LABEL_W, 60 + 8));
}

/** The whole split in one bar, in percent, each part a click filter, with a legend that names every colour. */
function percentStack(chart, ctx) {
  const { t, onFilter } = ctx;
  const { axis, parts } = chart;
  return v_flex().gap(6)
    .child(h_flex().w_full().h(18).gap(2).children(parts.map((p, i) => div().id(`seg-${i}`).h_full().rounded(4)
      .bg(dataColor(p.slot, t, p.shade)).flex_basis(0).flex_grow(p.value).cursor_pointer().opacity(p.dim ? 0.35 : 1)
      .tooltip(`${p.label} · ${p.value} (${Math.round(p.share * 100)}%)`)
      .on_click((_e, cx) => onFilter(p.filter, cx)))))
    .child(h_flex().justify_between().text_size(10).text_color(t.dim).border_t(1).border_color(t.border).pt(2)
      .children(axis.labels.map((word) => div().child(word))))
    .child(h_flex().flex_wrap().gap(6).children(parts.map((p, i) => h_flex().id(`reason-${i}`).gap(5).items_center().px(6).py(2)
      .rounded(4).bg(t.raised).text_size(11).cursor_pointer().opacity(p.dim ? 0.5 : 1)
      .hover((s) => s.bg(t.hover)).on_click((_e, cx) => onFilter(p.filter, cx))
      .child(div().w(10).h(10).rounded(3).bg(dataColor(p.slot, t, p.shade)))
      .child(div().text_color(t.muted).child(p.label))
      .child(div().text_color(t.dim).child(`${p.value}`)))));
}

/** Plans down, causes across: a cell is the count in violet, the stronger the more; a click selects its plan and cause. */
function heatmap(chart, ctx) {
  const { t, onFilter } = ctx;
  const hue = (intensity) => dataColor(SINGLE, t, 0.16 + intensity * 0.84);
  return v_flex().gap(3)
    .child(h_flex().gap(3).child(div().w(120).flex_none())
      .children(chart.causes.map((c) => div().flex_1().text_center().text_size(10).text_color(t.dim).truncate().child(c))))
    .children(chart.plans.map((plan) => h_flex().gap(3).items_center()
      .child(div().w(120).flex_none().truncate().text_size(11).text_color(t.muted).child(plan))
      .children(chart.cells.filter((c) => c.plan === plan).map((c) => div().id(`cell-${c.plan}-${c.cause}`).flex_1().h(26).rounded(4)
        .flex().items_center().justify_center().bg(c.value ? hue(c.intensity) : t.raised).text_size(11)
        .text_color(c.intensity > 0.5 ? t.foreground : t.muted).cursor_pointer().opacity(c.dim ? 0.4 : 1)
        .tooltip(`${c.plan} · ${c.cause} · ${c.value}`)
        .on_click((_e, cx) => onFilter(c.filter, cx)).child(c.value ? String(c.value) : "")))))
    .child(div().pt(4).text_size(10).text_color(t.dim).child(`darker is more · most ${chart.max}`));
}

/** Per plan: its tickets, solid where tokens were measured and hatched where they were not. */
function coverage(chart, ctx) {
  const { t } = ctx;
  const { axis, rows } = chart;
  return v_flex().gap(6)
    .children(rows.map((r, i) => h_flex().gap(8).items_center().text_size(11)
      .child(div().w(LABEL_W).flex_none().truncate().text_color(t.muted).child(r.label))
      .child(h_flex().flex_1().min_w(0).h(12)
        .child(h_flex().h_full().gap(2).w(`${((r.measured + r.unmeasured) / axis.max) * 100}%`)
          .when(r.measured > 0, (el) => el.child(div().id(`m-${i}`).h_full().rounded(3).bg(dataColor(SINGLE, t))
            .flex_basis(0).flex_grow(r.measured).tooltip(`${r.label}: ${r.measured} measured`)))
          .when(r.unmeasured > 0, (el) => el.child(div().id(`u-${i}`).h_full().rounded(3).map((e) => hatched(e, t))
            .flex_basis(0).flex_grow(r.unmeasured).tooltip(`${r.label}: ${r.unmeasured} not measured`)))))
      .child(valueCell(r.text, t, 110))))
    .child(xAxis(axis, t, LABEL_W, 110 + 8))
    .child(legend([{ name: "measured", color: dataColor(SINGLE, t) }, { name: "not measured", hatch: true }], t));
}

/** A lane per ticket of where its time went, a segment per measured phase on one scale. */
function swimlanes(chart, ctx) {
  const { t } = ctx;
  const { axis, lanes } = chart;
  return v_flex().gap(6)
    .children(lanes.map((lane, i) => h_flex().gap(8).items_center().text_size(11)
      .child(div().w(LABEL_W).flex_none().truncate().text_color(t.muted).child(lane.label))
      .child(div().flex_1().min_w(0).relative().h(14).children(lane.segments.map((s) => div().id(`lane-${i}-${s.step}`)
        .absolute().top(0).h(14).rounded(3).left(`${s.left * 100}%`).w(`${Math.max(s.width * 100, 0.4)}%`)
        .bg(dataColor(s.slot, t)).tooltip(`${s.step} ${duration(s.seconds)}`))))
      .child(valueCell(lane.text, t, 70))))
    .child(xAxis(axis, t, LABEL_W, 70 + 8))
    .child(legend(["work", "review", "ci", "merge"].map((step) => ({ name: step, color: dataColor(stepLook(step).slot, t) })), t));
}

/** Per step: a bar to the median and a tick at the p90 (a CLI without the p90 draws the median alone). */
function spread(chart, ctx) {
  const { t } = ctx;
  const { axis, rows } = chart;
  const at = (v) => `${Math.min(100, (v / axis.max) * 100)}%`;
  return v_flex().gap(8)
    .children(rows.map((r) => h_flex().gap(8).items_center().text_size(11)
      .child(h_flex().w(80).flex_none().gap(5).items_center().child(dot(dataColor(stepLook(r.step).slot, t))).child(div().text_color(t.muted).child(r.step)))
      .child(div().flex_1().min_w(0).relative().h(14)
        .when(r.p90 !== null, (el) => el.child(div().absolute().top(6).h(2).left(0).w(at(r.p90)).bg(t.raised)))
        .child(div().absolute().top(2).h(10).left(0).rounded(3).w(at(Math.max(r.median, axis.max / 100))).bg(dataColor(stepLook(r.step).slot, t)))
        .when(r.p90 !== null, (el) => el.child(div().absolute().top(0).h(14).w(2).ml(-1).left(at(r.p90)).bg(t.muted))))
      .child(valueCell(r.p90 === null ? `median ${duration(r.median)}` : `median ${duration(r.median)} · p90 ${duration(r.p90)}`, t, 150))))
    .child(xAxis(axis, t, 80 + 8, 150 + 8));
}

const DRAW = { "stacked-columns": stackedColumns, bars, stack: percentStack, heatmap, coverage, swimlanes, spread };

/** One chart in its panel, from the model's chart object. `ctx` is `{t, height, onFilter}`. */
export function chartView(chart, ctx) {
  const { t } = ctx;
  if (chart.kind === "ring") {
    return panel(chart.title, ring(chart.value, t, 132, v_flex().items_center()
      .child(div().text_size(22).font_semibold().child(chart.text))
      .child(div().text_size(10).text_color(t.dim).child("first time"))), t);
  }
  const draw = DRAW[chart.kind];
  return draw ? panel(chart.title, draw(chart, ctx), t) : null;
}
