// A state shown in the place it belongs to: scoped to the project or to one plan, with the way out. Nothing here
// draws data; when there is none, this is all there is.
import { div } from "gpui-kit";
import { v_flex } from "gpui-base";
import { colorOf } from "../ui.js";

export function noticeView(notice, t) {
  const scope = `${notice.scope.kind} · ${notice.scope.name}`.toUpperCase();
  return v_flex().id(`notice-${notice.scope.kind}`).gap(4).p(12).m(8).rounded(8).border_1().border_color(colorOf(notice.tone, t))
    .child(div().text_size(10).font_semibold().text_color(t.dim).child(scope))
    .child(div().font_semibold().text_color(colorOf(notice.tone, t)).child(notice.title))
    .child(div().whitespace_normal().text_color(t.muted).child(notice.message))
    .when(Boolean(notice.wayOut), (el) => el.child(div().whitespace_normal().text_color(t.foreground).child(notice.wayOut)));
}

export const loadingView = (text, t) => div().p(12).text_size(12).text_color(t.dim).child(text);
