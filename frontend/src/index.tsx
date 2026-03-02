import './App.css';
import 'katex/dist/katex.min.css';
import { PostHogProvider } from '@posthog/react';
import posthog from 'posthog-js';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import App from './App.tsx';
import { POSTHOG_HOST, POSTHOG_KEY } from './config.ts';
import { getSessionId } from './utils/session.ts';

if (POSTHOG_KEY) {
  posthog.init(POSTHOG_KEY, {
    api_host: POSTHOG_HOST,
    cookieless_mode: 'always',
    defaults: '2026-01-30',
    persistence: 'memory',
    person_profiles: 'identified_only',
  });
  posthog.register({ atlas_session_id: getSessionId() });
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PostHogProvider client={posthog}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </PostHogProvider>
  </StrictMode>,
);
