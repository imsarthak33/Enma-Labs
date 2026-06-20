'use client';

import { createContext, useContext } from 'react';
import type { FirmData } from '@/hooks/use-dashboard-data';

export interface DashboardContextValue {
  user: any;
  firmData: FirmData | null;
  firmLoading: boolean;
  globalSearch: string;
  setGlobalSearch: (v: string) => void;
}

export const DashboardContext = createContext<DashboardContextValue>({
  user: null,
  firmData: null,
  firmLoading: true,
  globalSearch: '',
  setGlobalSearch: () => {},
});

export const useDashboard = () => useContext(DashboardContext);
