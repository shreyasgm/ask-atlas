import { Route, Routes } from 'react-router';
import ChatPage from '@/pages/chat';
import LandingPage from '@/pages/landing';

export default function App() {
  return (
    <Routes>
      <Route element={<LandingPage />} path="/" />
      <Route element={<ChatPage />} path="/chat" />
      <Route element={<ChatPage />} path="/chat/:threadId" />
    </Routes>
  );
}
