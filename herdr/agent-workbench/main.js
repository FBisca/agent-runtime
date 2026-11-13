// Agent workbench: home (the week's cards, what needs the person, the active plans) and a plan's ticket graph, from the
// orchestrator's own loader and ledger, in one Herdr pane.
// It follows the shown space, runs only the read-only `agent-orchestrator workbench` commands (see data.js) and
// writes nothing. Layout is the model's (model.js); the views only draw what it hands them.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { spaces, exec, watchFolder, home, theme, terminals, run, layout as herdrLayout, props, shared, setShared } from "herdr";
import { createData, watchLoop, FALLBACK_MS, FLUSH_MS } from "./data.js";
import {
  layoutFor, screenFor, planRows, homeView, dashboardView, initialNav, navigate, ticketLayers, pickTicket, relatives, inspector, planHeader,
  timelineOf, notice, liveTerminals, panelToggle, panelsWith, panelExpand, PANEL_PX,
} from "./model.js";
import { headerView } from "./views/header.js";
import { homeScreen } from "./views/home.js";
import { dashboardScreen } from "./views/dashboard.js";
import { layeredList, compactList } from "./views/tickets.js";
import { inspectorView } from "./views/inspector.js";
import { timelineView } from "./views/timeline.js";
import { noticeView, loadingView } from "./views/notices.js";
import { toggleView } from "./views/toggle.js";

// The orchestrator's ledger and run directories live under here; a change in any of them is a reason to refresh.
const STATE_DIR = "/.local/state/orchestrate";

// The panel's width before the expand, kept in the shared state: the tab bar's button is another instance and closes it.
const RESTORE_PX = "agent-workbench.panel_px";

const activeSpace = () => spaces().find((s) => s.active) ?? null;

const runAll = (commands) => { for (const [command, args] of commands) run(command, args); };

export default class AgentWorkbench extends View {
  init(initial, cx) {
    // The tab bar's button is this mod too (props `{ toggle: true }`): it fetches nothing and watches nothing.
    this.toggle = Boolean((initial ?? props())?.toggle);
    if (this.toggle) return;
    this.data = createData({ exec });
    this.size = null;
    this.drilled = false; // narrow only: the inspector replaces the list
    this.nav = initialNav(); // the mod opens on home; a plan or a card opens over it
    this.olderOpen = false;
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
    this.nav = initialNav();
    this.project = space?.name ?? null;
    this.machine = space?.machine ?? null;
    if (space) this.data.setScope({ folder: space.folder, machine: space.machine });
    return true;
  }

  renderToggle() {
    const missing = panelsWith(shared("workspace.panels"));
    if (missing) setShared("workspace.panels", missing);
    const toggle = panelToggle(herdrLayout(), shared(RESTORE_PX));
    const onToggle = (cx) => { runAll(toggle.commands); cx.notify(); };
    return toggleView(toggle, { t: theme(), onToggle });
  }

  // Opens a plan or a card, or goes back home. What was fetched for the screen left is old, so the data follows the nav.
  go(action, cx) {
    this.nav = navigate(this.nav, action);
    this.ticket = null;
    this.drilled = false;
    this.data.show(this.nav);
    this.fetch(cx);
    cx.notify();
  }

  // The expand is the header's button: it hides the terminals and gives the panel the work area, and collapses back.
  expandFor() {
    const expand = panelExpand(herdrLayout(), shared(RESTORE_PX));
    if (!expand) return null;
    const onClick = (cx) => {
      if (!expand.expanded) setShared(RESTORE_PX, expand.width ?? PANEL_PX);
      runAll(expand.commands);
      cx.notify();
    };
    return { expanded: expand.expanded, onClick };
  }

  render(cx) {
    if (this.toggle) return this.renderToggle();
    if (this.syncSpace()) this.fetch(cx);
    const t = theme();
    const layout = layoutFor(this.size?.width);
    const root = v_flex().id("agent-workbench").size_full().min_h(0).overflow_hidden().bg(t.background)
      .text_color(t.foreground).text_size(13)
      .on_size_change((size, cx) => { this.size = size; cx.notify(); });
    const state = this.data.state();
    const project = this.project ?? "no space";
    if (!this.spaceKey) return root.child(loadingView("No space is shown.", t));

    // An open plan's or dashboard's header has a way back home; home's has none.
    const header = (head, back = null) => headerView(head, {
      t, project, narrow: layout === "narrow", loading: state.plans.status === "loading",
      onBack: back,
      onRefresh: (cx) => this.fetch(cx),
      expand: this.expandFor(),
    });
    const back = (cx) => this.go({ type: "back" }, cx);

    if (state.plans.status === "loading") return root.child(header(null)).child(loadingView("Looking for plans…", t));
    if (state.plans.status === "error") {
      return root.child(header(null)).child(noticeView(notice(state.plans.error, { project }), t));
    }
    const rows = planRows(state.plans.value.plans);
    if (!rows.length) {
      const none = { kind: "no_plans", message: "No plans in " + project, wayOut: null };
      return root.child(header(null)).child(noticeView(notice(none, { project }), t));
    }
    const show = (el) => root.child(v_flex().id("body").flex_1().min_h(0).overflow_y_scrollbar().child(el));
    // terminals() is live: render runs again when Herdr's terminals change, so nothing here polls it.
    const machine = this.machine;
    const live = liveTerminals(terminals(), machine);
    const onOpen = (open, cx) => { run(open.command, open.args); cx.notify(); };
    const home = homeView(state.plans.value, state.totals.status === "ok" ? state.totals.value : null, live);

    if (this.nav.screen === "dashboard") {
      const card = home.cards?.find((c) => c.id === this.nav.card);
      if (!card) {
        // The totals are still coming, or did not come: a dashboard has nothing to draw from, so say so.
        const waiting = state.totals.status === "loading";
        return root.child(header({ title: "Last 7 days" }, back)).child(waiting
          ? loadingView("Loading the week…", t)
          : noticeView(notice(state.totals.error ?? { kind: "no_json", message: "no totals" }, { project }), t));
      }
      return root.child(header({ title: card.label }, back)).child(v_flex().id("body").flex_1().min_h(0).overflow_y_scrollbar()
        .child(dashboardScreen(card, dashboardView(card.id, state.totals.value, layout), t)));
    }

    const picked = this.nav.screen === "plan" ? rows.find((r) => r.path === this.nav.path) ?? null : null;
    if (this.nav.screen === "plan" && !picked) {
      // The plan left the list while it was open (its file is gone): home is where its way back goes.
      this.nav = initialNav();
      this.data.show(this.nav);
      this.fetch(cx);
    }
    if (!picked) {
      const handlers = {
        onCard: (id, cx) => this.go({ type: "card", id }, cx),
        onPlan: (path, cx) => this.go({ type: "plan", path }, cx),
        onOlder: (cx) => { this.olderOpen = !this.olderOpen; cx.notify(); },
        onOpen,
      };
      return show(homeScreen(home, this.olderOpen, layout, handlers, t));
    }
    const plan = state.plan;
    const head = plan?.status === "ok" ? planHeader(plan.value, layout) : { title: picked.slug, slug: picked.slug, state: picked.look };
    root.child(header(head, back));
    if (!plan || plan.status === "loading") return show(loadingView("Loading " + picked.slug + "…", t));
    if (plan.status === "error") return show(noticeView(notice(plan.error, { plan: picked.slug }), t));

    const tickets = plan.value.tickets;
    const selected = pickTicket(tickets, this.ticket);
    const select = (id, cx) => { this.ticket = id; this.drilled = true; cx.notify(); };
    const layers = ticketLayers(tickets, live);
    const ctx = { t, selected, kin: relatives(tickets, selected), onSelect: select, onOpen };
    const detail = selected ? inspector(tickets.find((x) => x.id === selected), tickets, live, plan.value.metrics) : null;
    const screen = screenFor(layout, this.drilled, Boolean(selected));
    if (screen === "inspector") {
      return show(inspectorView(detail, { t, onOpen, onBack: (cx) => { this.drilled = false; cx.notify(); } }));
    }
    if (screen === "list") return show(compactList(layers, ctx));
    const timeline = timelineOf(plan.value.metrics, tickets, layout);
    if (timeline) root.child(timelineView(timeline, t));
    return root.child(h_flex().id("body").flex_1().min_h(0)
      .child(div().id("layers").flex_1().min_w(0).h_full().overflow_y_scrollbar().child(layeredList(layers, ctx)))
      .child(div().id("detail").w(360).flex_none().h_full().overflow_y_scrollbar().border_l(1).border_color(t.border)
        .when(Boolean(detail), (el) => el.child(inspectorView(detail, { t, onOpen, onBack: null })))));
  }
}
