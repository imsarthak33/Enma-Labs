'use client';

import { useState, useEffect, type ReactNode } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { useAuth } from '@/lib/auth-context';
import { useFirmData, type FirmData } from '@/hooks/use-dashboard-data';
import Link from 'next/link';
import { motion, AnimatePresence } from 'framer-motion';
import {
  LogOut,
  Loader2,
  LayoutDashboard,
  Users,
  FileText,
  Settings,
  Bell,
  Search,
  TrendingUp,
  Send,
  Menu,
  X,
  MessageCircle,
} from 'lucide-react';
import { DashboardContext } from './dashboard-context';

// ─── Navigation Items ────────────────────────────────────────────────────────

const navItems = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard, href: '/dashboard' },
  { id: 'clients', label: 'Clients', icon: Users, href: '/dashboard/clients' },
  { id: 'documents', label: 'Documents', icon: FileText, href: '/dashboard/documents' },
  { id: 'chat', label: 'Chat', icon: MessageCircle, href: '/dashboard/chat' },
  { id: 'settings', label: 'Settings', icon: Settings, href: '/dashboard/settings' },
];

// ─── Layout ──────────────────────────────────────────────────────────────────

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, loading: authLoading, signOut } = useAuth();
  const [globalSearch, setGlobalSearch] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Redirect to login if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login');
    }
  }, [user, authLoading, router]);

  const { firmData, loading: firmLoading } = useFirmData(user?.id || null);

  // Redirect to onboarding if profile is not complete
  useEffect(() => {
    if (!firmLoading && user && firmData === null) {
      // No firm data found — need onboarding
      router.push('/onboarding');
    } else if (!firmLoading && firmData && !firmData.onboarding_completed) {
      router.push('/onboarding');
    }
  }, [firmData, firmLoading, user, router]);

  const handleSignOut = async () => {
    await signOut();
    router.push('/');
  };

  if (authLoading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="w-8 h-8 text-accent animate-spin" />
          <p className="text-white/30 text-sm">Loading your dashboard...</p>
        </div>
      </div>
    );
  }

  if (!user) return null;

  const getActiveNav = () => {
    if (pathname === '/dashboard') return 'overview';
    const segment = pathname.split('/')[2];
    return segment || 'overview';
  };

  return (
    <DashboardContext.Provider value={{ user, firmData, firmLoading, globalSearch, setGlobalSearch }}>
      <div className="min-h-screen bg-black text-white font-sans flex overflow-hidden h-screen">
        {/* Mobile Sidebar Overlay */}
        <AnimatePresence>
          {sidebarOpen && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden"
              onClick={() => setSidebarOpen(false)}
            />
          )}
        </AnimatePresence>

        {/* Sidebar */}
        <aside
          className={`
            fixed lg:static inset-y-0 left-0 z-50
            w-64 border-r border-white/5 bg-black/80 backdrop-blur-3xl flex flex-col shrink-0
            transform transition-transform duration-300 ease-out
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
          `}
        >
          <div className="p-6 flex items-center justify-between">
            <Link href="/" className="flex items-center gap-2 group">
              <div className="w-2 h-2 rounded-full bg-accent shadow-[0_0_12px_rgba(255,78,0,0.8)]" />
              <span className="text-white font-bold text-xl tracking-tight">Enma.</span>
            </Link>
            <button
              onClick={() => setSidebarOpen(false)}
              className="lg:hidden text-white/40 hover:text-white"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <nav className="flex-1 px-3 space-y-1 mt-4">
            {navItems.map((item) => {
              const isActive = getActiveNav() === item.id;
              return (
                <Link
                  key={item.id}
                  href={item.href}
                  onClick={() => setSidebarOpen(false)}
                  className={`
                    w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 group
                    ${isActive
                      ? 'bg-white/10 text-white border border-white/10'
                      : 'text-white/40 hover:text-white hover:bg-white/5 border border-transparent'
                    }
                  `}
                >
                  <item.icon className={`w-5 h-5 transition-colors ${isActive ? 'text-accent' : 'group-hover:text-white/60'}`} />
                  <span className="text-sm font-medium">{item.label}</span>
                  {isActive && (
                    <motion.div
                      layoutId="sidebar-indicator"
                      className="ml-auto w-1.5 h-1.5 rounded-full bg-accent"
                      transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                    />
                  )}
                </Link>
              );
            })}
          </nav>

          <div className="p-4 border-t border-white/5">
            {/* Plan Info */}
            <div className="bg-white/[0.02] border border-white/5 rounded-2xl p-4 mb-4">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-8 h-8 rounded-full bg-accent/20 flex items-center justify-center">
                  <TrendingUp className="w-4 h-4 text-accent" />
                </div>
                <span className="text-xs font-bold text-white/60 uppercase tracking-wider">
                  {firmData?.subscription_plan === 'trial' ? 'Free Trial' : 'Pro Plan'}
                </span>
              </div>
              <p className="text-[11px] text-white/30 leading-relaxed">
                {firmData?.onboarding_completed
                  ? 'Your firm is connected and processing documents.'
                  : 'Complete setup to start processing documents.'}
              </p>
            </div>

            {/* Telegram Status */}
            {firmData?.telegram_chat_id && (
              <a
                href="https://t.me/enma12bot"
                target="_blank"
                rel="noopener noreferrer"
                className="w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-[#229ED9]/60 hover:text-[#229ED9] hover:bg-[#229ED9]/5 transition-all duration-200 mb-2"
              >
                <Send className="w-4 h-4" />
                <span className="text-xs font-medium">Telegram Bot</span>
                <div className="ml-auto w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
              </a>
            )}

            {/* Sign Out */}
            <button
              onClick={handleSignOut}
              className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-white/40 hover:text-red-400 hover:bg-red-400/5 transition-all duration-200"
            >
              <LogOut className="w-5 h-5" />
              <span className="text-sm font-medium">Sign Out</span>
            </button>
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex-1 flex flex-col overflow-hidden relative">
          {/* Background Effects */}
          <div className="absolute inset-0 bg-noise pointer-events-none z-0" />
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(255,78,0,0.04),transparent)] pointer-events-none" />

          {/* Top Header */}
          <header className="h-16 border-b border-white/5 flex items-center justify-between px-4 lg:px-8 shrink-0 relative z-10 bg-black/50 backdrop-blur-xl">
            {/* Mobile Menu Button */}
            <button
              onClick={() => setSidebarOpen(true)}
              className="lg:hidden w-10 h-10 rounded-xl flex items-center justify-center text-white/40 hover:text-white hover:bg-white/5"
            >
              <Menu className="w-5 h-5" />
            </button>

            {/* Search */}
            <div className="hidden sm:flex items-center gap-3 bg-white/5 border border-white/10 rounded-full px-4 py-2 w-80 lg:w-96 transition-all focus-within:border-accent/30 focus-within:bg-white/[0.07]">
              <Search className="w-4 h-4 text-white/30 shrink-0" />
              <input
                type="text"
                value={globalSearch}
                onChange={(e) => setGlobalSearch(e.target.value)}
                placeholder="Search clients, documents, or queries..."
                className="bg-transparent border-none outline-none text-sm w-full placeholder:text-white/20"
              />
              {globalSearch && (
                <button onClick={() => setGlobalSearch('')} className="text-white/30 hover:text-white">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            <div className="flex items-center gap-3">
              {/* Notifications */}
              <button className="w-10 h-10 rounded-full border border-white/10 flex items-center justify-center text-white/40 hover:text-white hover:bg-white/5 transition-all relative">
                <Bell className="w-5 h-5" />
                <div className="absolute top-2.5 right-2.5 w-2 h-2 bg-accent rounded-full border-2 border-black" />
              </button>

              {/* User Info */}
              <div className="flex items-center gap-3 pl-3 border-l border-white/10">
                <div className="text-right hidden md:block">
                  {firmLoading ? (
                    <div className="space-y-1.5">
                      <div className="h-3 w-24 bg-white/10 rounded animate-pulse" />
                      <div className="h-2.5 w-16 bg-white/5 rounded animate-pulse" />
                    </div>
                  ) : (
                    <>
                      <p className="text-sm font-medium text-white">{firmData?.ca_name || 'Admin User'}</p>
                      <p className="text-[11px] text-white/40">{firmData?.firm_name || user?.email}</p>
                    </>
                  )}
                </div>
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-accent to-orange-600 flex items-center justify-center font-bold text-sm shadow-lg shadow-accent/20">
                  {(firmData?.ca_name || 'AU').split(' ').map((n: string) => n[0]).join('').toUpperCase().substring(0, 2)}
                </div>
              </div>
            </div>
          </header>

          {/* Page Content */}
          <div className="flex-1 overflow-y-auto custom-scrollbar relative z-10">
            <AnimatePresence mode="wait">
              <motion.div
                key={pathname}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.25, ease: 'easeOut' }}
                className="p-4 lg:p-8"
              >
                {children}
              </motion.div>
            </AnimatePresence>
          </div>
        </main>
      </div>
    </DashboardContext.Provider>
  );
}
