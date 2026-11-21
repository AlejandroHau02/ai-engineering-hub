---
name: ux-ui
description: Design, implement and critically review distinctive, accessible interfaces with task-focused flows, responsive behavior, safe DOM rendering and real visual verification.
---

# UX/UI engineering

Use for interfaces, redesigns, usability problems, frontend implementations and design-system decisions. Keep checks proportional: a small label change does not require an entire browser test matrix.

1. **Understand the task:** identify user roles, high-frequency actions, information priority and existing visual language. Question unnecessary dashboard cards and decoration with specific alternatives, not arbitrary fixed numbers.
2. **Design states deliberately:** cover loading, empty, stale, error, success, selection and permissions. Ensure asynchronous work cannot accidentally mark unseen messages read, resurrect outdated results or leave contradictory UI states.
3. **Build accessible interactions:** keyboard navigation and focus management, semantic controls, labels, contrast, visible selection (including forced-colors), and sensible browser back/forward history for mobile list/detail flows.
4. **Review responsive layout:** test narrow **and short** viewports, landscape, 200–400% zoom/reflow where practicable, long untranslated strings and touch targets. A fixed-height flex layout can collapse its only useful scroll area.
5. **Use safe DOM practices:** treat external text as untrusted. Prefer `textContent`/DOM APIs for plain text; if rich HTML is essential, use an explicitly reviewed sanitization policy. Avoid interpolating user messages into `innerHTML`.
6. **Keep live data honest:** distinguish static snapshots from live measurements, recalculate relative time and counters appropriately, and handle changes/errors without misleading users.
7. **Verify what you can:** run relevant logic tests, then inspect a **rendered browser view** at meaningful widths and heights when browser tools are available. Examine focus, scroll, state changes, contrast and keyboard behavior. State what was tested, what was only inspected statically, and what remains unverified.

Favor a small, coherent solution over adding frameworks or embellishments without product value. Do not claim WCAG compliance or visual verification solely from reading markup or CSS.
