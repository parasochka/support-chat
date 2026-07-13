import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

// Vite fires this when a module preload of a dynamic import fails — the
// signature of a tab that outlived a redeploy (its index.html points at old
// hashed chunks the server no longer has). One forced reload fetches the fresh
// index.html; the sessionStorage guard (shared with App.jsx's importWithReload)
// stops a reload loop when the deploy itself is broken.
window.addEventListener('vite:preloadError', (event) => {
  if (!sessionStorage.getItem('np_chunk_reload')) {
    sessionStorage.setItem('np_chunk_reload', '1');
    event.preventDefault();
    window.location.reload();
  }
});

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
