import { Route, Routes } from 'react-router';
import LandingPage from '@/pages/landing';

export default function App() {
  return (
    <Routes>
      <Route element={<LandingPage />} path="/" />
    </Routes>
  );
}
