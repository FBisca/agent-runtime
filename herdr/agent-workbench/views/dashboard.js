// A card's dashboard: the headline (value, change against last week, the week's sparkline), the charts in one or two
// columns, and the rows behind them. `view` is the model's `dashboardView`; every number, word and key is the model's.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { chartView, chip, labelText, panel, sparkline } from "./charts.js";

// A chart that needs the whole width even when wide: a scale of tickets or days, not a figure beside another.
const FULL = new Set(["heatmap", "swimlanes", "stacked-columns"]);

function headline(head, t) {
  return h_flex().gap(20).items_end()
    .child(v_flex().gap(2)
      .child(labelText(`${head.label} · last 7 days`, t))
      .child(h_flex().gap(10).items_end()
        .child(div().text_size(32).font_semibold().text_color(head.value === "unknown" ? t.dim : t.foreground).child(head.value))
        .child(div().mb(6).px(6).py(1).rounded(4).bg(t.raised).text_size(11).text_color(t.muted).child(head.change)))
      .when(Boolean(head.note), (el) => el.child(div().text_size(11).text_color(t.dim).child(head.note))))
    .when(head.spark.length > 1, (el) => el.child(div().flex_1().min_w(0).max_w(320).child(sparkline(head.spark, t, 44))));
}

// Wide: two columns, the charts that need the width on a row of their own. Narrow: one column.
function layout(charts, columns, ctx) {
  const drawn = charts.map((c) => [c, chartView(c, ctx)]);
  if (columns === 1) return v_flex().gap(14).children(drawn.map(([, el]) => el));
  const rows = [];
  let pending = null;
  const flush = () => {
    if (pending) rows.push(pending);
    pending = null;
  };
  for (const [chart, el] of drawn) {
    if (FULL.has(chart.kind)) {
      flush();
      rows.push(el);
    } else if (pending) {
      rows.push(h_flex().gap(14).items_start().child(div().flex_1().min_w(0).child(pending)).child(div().flex_1().min_w(0).child(el)));
      pending = null;
    } else {
      pending = el;
    }
  }
  flush();
  return v_flex().gap(14).children(rows);
}

function rowLine(r, i, handlers, t) {
  return h_flex().id(`row-${i}`).gap(8).items_center().text_size(12).py(4).border_b(1).border_color(t.border)
    .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(r.label))
    .when(Boolean(r.linkText && r.link), (el) => el.child(chip(r.linkText, t, (_e, cx) => handlers.onLink(r.link, cx), `pr-${i}`)))
    .when(Boolean(r.linkText && !r.link), (el) => el.child(div().flex_none().px(6).text_size(11).text_color(t.dim).child(r.linkText)))
    .child(div().flex_none().text_color(t.dim).child(r.text));
}

/** `handlers` are `{onFilter(filter, cx), onLink(url, cx)}`: the filter lives in the navigation, the link opens GitHub. */
export function dashboardScreen(view, handlers, t) {
  const ctx = { t, height: view.chartHeight, onFilter: handlers.onFilter };
  const clear = view.filtered === null ? null : handlers.onClear;
  return v_flex().id("dashboard").gap(16).p(16)
    .child(headline(view.headline, t))
    .when(view.charts.length > 0, (el) => el.child(layout(view.charts, view.columns, ctx)))
    .when(view.rows.length > 0 || view.empty !== null, (el) => el.child(panel(
      view.filtered === null ? view.rowsTitle : `${view.rowsTitle} · ${view.filtered}`,
      v_flex().gap(6)
        .when(Boolean(clear), (e) => e.child(h_flex().gap(6).items_center().text_size(11).text_color(t.muted)
          .child(div().child(`filtered: ${view.filtered}`))
          .child(chip("clear ✕", t, (_e, cx) => clear(cx), "clear-filter"))))
        .when(view.empty !== null, (e) => e.child(div().text_size(11).text_color(t.dim).child(view.empty)))
        .children(view.rows.map((r, i) => rowLine(r, i, handlers, t))), t)));
}
