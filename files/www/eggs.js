// Hidden extras on the remote: demo numbers past the last demo that are not demos.
// The field still says 1–100; these are for those who know.

export const EGGS = {
  // Pianobasso's latest release
  101: { title: 'Pianobasso', link: 'https://open.spotify.com/album/4hNSxY4wMhnx2VPO00Pnsr' },
};

/** The egg behind a demo number, or null. */
export const egg = (n) => (Object.prototype.hasOwnProperty.call(EGGS, n) ? EGGS[n] : null);
