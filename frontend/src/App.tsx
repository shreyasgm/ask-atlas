import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router';
import LandingPage from '@/pages/landing';

const ChatPage = lazy(() => import('@/pages/chat'));

export default function App() {
  return (
    <Suspense>
      <Routes>
        <Route element={<LandingPage />} path="/" />
        <Route element={<ChatPage />} path="/chat/:threadId?" />
      </Routes>
    </Suspense>
  );
}
