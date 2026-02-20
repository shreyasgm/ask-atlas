import type { LucideIcon } from 'lucide-react';
import { ArrowLeftRight, Brain, Scale, Search, TrendingUp, Users } from 'lucide-react';

export interface QuickStartTile {
  description: string;
  icon: LucideIcon;
  query: string;
  title: string;
}

export interface DataCoverageCard {
  description: string;
  title: string;
}

export const QUICK_START_TILES: Array<QuickStartTile> = [
  {
    description: "What were Brazil's top 5 exports in 2022?",
    icon: TrendingUp,
    query: "What were Brazil's top 5 exports in 2022?",
    title: 'Top Exports',
  },
  {
    description: 'Trade between China and the US from 2010-2020',
    icon: ArrowLeftRight,
    query: 'Trade between China and the US from 2010-2020',
    title: 'Bilateral Trade',
  },
  {
    description: 'Which countries export semiconductors?',
    icon: Search,
    query: 'Which countries export semiconductors?',
    title: 'Product Search',
  },
  {
    description: 'Trade balance of Germany with France',
    icon: Scale,
    query: 'Trade balance of Germany with France',
    title: 'Trade Balance',
  },
  {
    description: "What is Japan's Economic Complexity Index?",
    icon: Brain,
    query: "What is Japan's Economic Complexity Index?",
    title: 'Complexity Metrics',
  },
  {
    description: 'Main trading partners of Mexico in automotive',
    icon: Users,
    query: 'Main trading partners of Mexico in automotive',
    title: 'Trading Partners',
  },
];

export const DATA_COVERAGE_CARDS: Array<DataCoverageCard> = [
  {
    description: 'Goods trade, 6-digit product codes',
    title: 'HS 1992',
  },
  {
    description: 'Updated goods classification',
    title: 'HS 2012',
  },
  {
    description: 'Alternative goods classification',
    title: 'SITC',
  },
  {
    description: 'Bilateral & unilateral services data',
    title: 'Services',
  },
];
