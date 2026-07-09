import { localStorageStore } from 'react-admin';

// react-admin wipes its whole Store on logout (`resetStore()` clears every
// `RaStore.*` key), and the light/dark theme preference lives in that Store
// (`RaStore.theme`). So an operator who logged out — or, worse, got bounced to
// login by an expired token overnight — came back to the theme reset to default.
//
// This wrapper keeps a small allow-list of UI preferences across a reset, so the
// theme (and sidebar state) are remembered like any other personal setting while
// everything else (list filters, selections, …) is still cleared on logout.
const KEEP_ON_RESET = ['theme', 'sidebar.open'];

const buildStore = () => {
  const base = localStorageStore();
  return {
    ...base,
    reset: () => {
      const saved = KEEP_ON_RESET.map((key) => [key, base.getItem(key)]);
      base.reset();
      saved.forEach(([key, value]) => {
        if (value !== undefined) base.setItem(key, value);
      });
    },
  };
};

export default buildStore;
