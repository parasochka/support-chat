/**
 * Shared sx for the admin's data tables on small screens.
 *
 * A table declares a `minWidth` so its columns stay legible on a desktop; the
 * wrapper `<Box sx={{ overflowX: 'auto' }}>` then scrolls it. On a phone that
 * combination means the right-hand columns — usually the ones carrying the
 * actual content — sit off the screen edge and read as "cut off", since a
 * horizontal scroll inside the page is easy to miss.
 *
 * For a narrow table (up to ~4 short columns) the fix is to let it compress to
 * the viewport and wrap instead. Wider tables get a card layout on phones
 * (see e.g. Quality, Conversations) — no amount of squeezing makes eight
 * columns readable at 400px.
 */

/** Tighter cell padding on phones — MUI's 16px each side costs ~130px of a
 *  400px screen once a table has four columns. */
export const compactTableSx = {
  '& .MuiTableCell-root': { px: { xs: 0.75, sm: 2 } },
};

/** Table minWidth that only applies from `sm` up (plus the compact padding). */
export const wideTableSx = (minWidth) => ({
  minWidth: { xs: 'unset', sm: minWidth },
  ...compactTableSx,
});

/** A cell kept on one line on desktop, allowed to wrap on a phone. */
export const nowrapCellSx = { whiteSpace: { xs: 'normal', sm: 'nowrap' } };
