import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import './styles/reset.css';
import './styles/variables.css';
import './styles/base.css';
import './styles/layout.css';
import './styles/components.css';
import './styles/utilities.css';

// Keep the local, read-only prototype independent of Firebase configuration.
// The original authenticated workspace retains its existing entry points.
const App = lazy(() => {
  if (import.meta.env.PROD) return import('./components/AdminQaApp');
  return window.location.pathname.replace(/\/$/, '') === '/local-qa'
    ? import('./components/LocalQa')
    : import('./App');
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Suspense fallback={<p style={{ padding: 32 }}>正在打开工作台…</p>}>
      <App />
    </Suspense>
  </React.StrictMode>
);
