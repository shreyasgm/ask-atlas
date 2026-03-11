import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router';
import LandingPage from '@/pages/landing';

const ChatPage = lazy(() => import('@/pages/chat'));

export default function App() {
  return (
    <Suspense>
      <a
        className="fixed top-0 left-0 z-50 -translate-y-full rounded-br-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-transform focus:translate-y-0"
        href="#main-content"
      >
        Skip to content
      </a>
      <Routes>
        <Route element={<LandingPage />} path="/" />
        <Route element={<ChatPage />} path="/chat/:threadId?" />
      </Routes>
    </Suspense>
  );
}
