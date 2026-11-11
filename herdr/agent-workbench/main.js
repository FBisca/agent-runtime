// Agent workbench: one plan's ticket graph from the orchestrator's own plan loader and ledger, in one Herdr pane.
// It follows the shown space, runs only the read-only `agent-orchestrator workbench` commands (see data.js) and
// writes nothing. Layout is the model's (model.js); the views only draw what it hands them.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { spaces, exec, watchFolder, home, theme } from "herdr";
import { createData, watchLoop, FALLBACK_MS, FLUSH_MS } from "./data.js";
import {
  layoutFor, screenFor, planRows, ticketLayers, pickTicket, relatives, inspector, planHeader, notice,
} from "./model.js";
import { headerView } from "./views/header.js";
import { planList } from "./views/plans.js";
import { layeredList, compactList } from "./views/tickets.js";
import { inspectorView } from "./views/inspector.js";
import { noticeView, loadingView } from "./views/notices.js";

// The orchestrator's ledger and run directories live under here; a change in any of them is a reason to refresh.
const STATE_DIR = "/.local/state/orchestrate";

const activeSpace = () => spaces().find((s) => s.active) ?? null;

export default class AgentWorkbench extends View {
  init(_props, cx) {
    this.data = createData({ exec });
    this.size = null;
    this.drilled = false; // narrow only: the inspector replaces the list
    this.pickerOpen = false;
    this.ticket = null;
    this.spaceKey = null;
    this.syncSpace();
    this.fetch(cx);
    cx.timer.every(FALLBACK_MS, (cx) => this.fetch(cx));
    cx.timer.every(FLUSH_MS, (cx) => this.flush(cx));
    cx.spawn(async () => watchLoop(watchFolder, home() + STATE_DIR, () => this.data.touch()));
  }

  // The refresh runs inside the task of its caller: its answers notify through that task's own context.
  fetch(cx) {
    cx.spawn(async (cx) => {
      await this.data.refresh(() => cx.notify());
      cx.notify();
    });
  }

  flush(cx) {
    cx.spawn(async (cx) => {
      await this.data.flush(() => cx.notify());
    });
  }

  // Follows the shown space; a different space is a different folder, so nothing held for the last one is shown.
  syncSpace() {
    const space = activeSpace();
    const key = space ? `${space.id}\u0000${space.folder}\u0000${space.machine ?? ""}` : null;
    if (key === this.spaceKey) return false;
    this.spaceKey = key;
    this.ticket = null;
    this.drilled = false;
    this.project = space?.name ?? null;
    if (space) this.data.setScope({ folder: space.folder, machine: space.machine });
    return true;
  }

  render(cx) {
    if (this.syncSpace()) this.fetch(cx);
    const t = theme();
    const layout = layoutFor(this.size?.width);
    const root = v_flex().id("agent-workbench").size_full().min_h(0).overflow_hidden().bg(t.background)
      .text_color(t.foreground).text_size(13)
      .on_size_change((size, cx) => { this.size = size; cx.notify(); });
    const state = this.data.state();
    const project = this.project ?? "no space";
    if (!this.spaceKey) return root.child(loadingView("No space is shown.", t));

    const header = (head) => headerView(head, {
      t, project, narrow: layout === "narrow", pickerOpen: this.pickerOpen, loading: state.plans.status === "loading",
      onPicker: (cx) => { this.pickerOpen = !this.pickerOpen; cx.notify(); },
      onRefresh: (cx) => this.fetch(cx),
    });

    if (state.plans.status === "loading") return root.child(header(null)).child(loadingView("Looking for plans…", t));
    if (state.plans.status === "error") {
      return root.child(header(null)).child(noticeView(notice(state.plans.error, { project }), t));
    }
    const rows = planRows(state.plans.value.plans);
    if (!rows.length) {
      const none = { kind: "no_plans", message: "No plans in " + project, wayOut: null };
      return root.child(header(null)).child(noticeView(notice(none, { project }), t));
    }
    const onPick = (path, cx) => {
      this.pickerOpen = false;
      this.ticket = null;
      this.drilled = false;
      this.data.select(path);
      this.fetch(cx);
      cx.notify();
    };
    const picked = rows.find((r) => r.path === state.selected) ?? null;
    const plan = state.plan;
    const head = plan?.status === "ok" ? planHeader(plan.value) : picked ? { slug: picked.slug, state: picked.look } : null;
    root.child(header(head));
    const show = (el) => root.child(v_flex().id("body").flex_1().min_h(0).overflow_y_scrollbar().child(el));

    if (this.pickerOpen || !picked) return show(planList(rows, state.selected, onPick, t));
    if (!plan || plan.status === "loading") return show(loadingView("Loading " + picked.slug + "…", t));
    if (plan.status === "error") return show(noticeView(notice(plan.error, { plan: picked.slug }), t));

    const tickets = plan.value.tickets;
    const selected = pickTicket(tickets, this.ticket);
    const layers = ticketLayers(tickets);
    const select = (id, cx) => { this.ticket = id; this.drilled = true; cx.notify(); };
    const ctx = { t, selected, kin: relatives(tickets, selected), onSelect: select };
    const detail = selected ? inspector(tickets.find((x) => x.id === selected), tickets) : null;
    const screen = screenFor(layout, this.drilled, Boolean(selected));
    if (screen === "inspector") {
      return show(inspectorView(detail, { t, onBack: (cx) => { this.drilled = false; cx.notify(); } }));
    }
    if (screen === "list") return show(compactList(layers, ctx));
    return root.child(h_flex().id("body").flex_1().min_h(0)
      .child(div().id("layers").flex_1().min_w(0).h_full().overflow_y_scrollbar().child(layeredList(layers, ctx)))
      .child(div().id("detail").w(360).flex_none().h_full().overflow_y_scrollbar().border_l(1).border_color(t.border)
        .when(Boolean(detail), (el) => el.child(inspectorView(detail, { t, onBack: null })))));
  }
}
