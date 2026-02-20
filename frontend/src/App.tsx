import { Route, Routes } from 'react-router';

const Home = () => (
  <div className="flex min-h-screen items-center justify-center">
    <div className="mx-auto w-full max-w-2xl rounded-2xl border border-gray-200 p-8 shadow-md dark:border-neutral-600 dark:bg-neutral-800 dark:shadow-none">
      <h1 className="text-4xl font-bold">Ask Atlas</h1>
      <p className="mt-4 text-lg text-gray-600 dark:text-gray-400">
        Query the Atlas of Economic Complexity using natural language.
      </p>
    </div>
  </div>
);

export default function App() {
  return (
    <Routes>
      <Route element={<Home />} path="/" />
    </Routes>
  );
}
