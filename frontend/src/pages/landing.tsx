import DataCoverageSection from '@/components/landing/data-coverage-section';
import Footer from '@/components/landing/footer';
import Header from '@/components/landing/header';
import HeroSection from '@/components/landing/hero-section';
import QuickStartSection from '@/components/landing/quick-start-section';

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <Header />
      <main className="flex flex-1 flex-col items-center">
        <HeroSection />
        <QuickStartSection />
        <DataCoverageSection />
      </main>
      <Footer />
    </div>
  );
}
